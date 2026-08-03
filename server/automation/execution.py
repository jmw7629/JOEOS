"""Workflow Execution Engine for the JoeOS Automation Platform.

Executes a compiled plan through a real state machine. Handles node dispatch
(actions, conditions, switches, delays, notifications, subworkflows),
bounded parallel branches and joins, bounded loops, retries with backoff,
timeouts, approvals and user input, compensation, pause/resume/cancel, and
traces. Every run is pinned to the workflow version that started it.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .actions import ActionError, ActionRegistry
from .compiler import CompiledPlan, is_retryable_error
from .expressions import evaluate_condition, evaluate_expression, ExpressionError
from .models import (
    ApprovalRequest,
    NodeConfig,
    RunRecord,
    RunState,
    UserInputRequest,
    WorkflowDefinition,
)
from .permissions import WorkflowPermissionGuard
from .security_gate import PermissionDeniedError

MAX_RETRY_ATTEMPTS = 20
MAX_LOOP_ITERATIONS = 1000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExecutionError(RuntimeError):
    pass


class ExecutionContext:
    """Mutable per-run context shared by all nodes of one run."""

    def __init__(self, *, run_id: str, workflow_id: str, version: str, variables: Dict[str, Any]) -> None:
        self.run_id = run_id
        self.workflow_id = workflow_id
        self.version = version
        self.variables: Dict[str, Any] = dict(variables)
        self.node_outputs: Dict[str, Dict[str, Any]] = {}
        self.pending_approval: Optional[ApprovalRequest] = None
        self.pending_input: Optional[UserInputRequest] = None
        self.cancelled = False
        self.paused = False
        self.cancellation_state = "none"


class ExecutionEngine:
    """Executes compiled workflow plans with real, observable state."""

    def __init__(
        self,
        *,
        connection_factory: Callable[[], sqlite3.Connection],
        actions: ActionRegistry,
        permissions: WorkflowPermissionGuard,
        secrets,
        event_sink=None,
        trace_sink=None,
        approval_resolver=None,
        input_resolver=None,
        subworkflow_runner=None,
        now_provider=None,
        security_gate=None,
    ) -> None:
        self._connection_factory = connection_factory
        self._actions = actions
        self._permissions = permissions
        self._secrets = secrets
        self._event_sink = event_sink or (lambda level, source, message: None)
        self._trace_sink = trace_sink
        self._approval_resolver = approval_resolver
        self._input_resolver = input_resolver
        self._subworkflow_runner = subworkflow_runner
        self._now = now_provider or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()
        self._security_gate = security_gate

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    def start(
        self,
        *,
        workflow_id: str,
        definition: WorkflowDefinition,
        plan: CompiledPlan,
        inputs: Optional[Dict[str, Any]] = None,
        trigger_id: str = "",
        trigger_context: Optional[Dict[str, Any]] = None,
        idempotency_key: str = "",
    ) -> RunRecord:
        run_id = "run_" + uuid.uuid4().hex[:20]
        trace_id = "trace_" + uuid.uuid4().hex[:16]
        variables = self._initialize_variables(definition, inputs or {})
        context = ExecutionContext(
            run_id=run_id,
            workflow_id=workflow_id,
            version=definition.version,
            variables=variables,
        )
        now = _now()
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO workflow_runs (
                    run_id, workflow_id, workflow_version, trigger_id, state,
                    current_node, started_at, trigger_context, inputs, trace_id,
                    idempotency_key, created_at
                ) VALUES (?, ?, ?, ?, 'running', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    workflow_id,
                    definition.version,
                    trigger_id,
                    plan.entry,
                    now,
                    json.dumps(trigger_context or {}),
                    json.dumps(inputs or {}),
                    trace_id,
                    idempotency_key,
                    now,
                ),
            )
            connection.execute(
                "INSERT INTO workflow_activity (event_id, workflow_id, kind, message, level, recorded_at) VALUES (?, ?, 'run_started', ?, 'info', ?)",
                ("wfa_" + uuid.uuid4().hex[:20], workflow_id, "Run started (trigger %s)." % (trigger_id or "manual"), now),
            )
        self._trace(run_id, plan.entry, "run.started", workflow_id=workflow_id)
        try:
            self._dispatch_node(context, definition, plan, plan.entry, inputs or {})
        except CancellationRequested:
            self._finish_run(context, "cancelled", error="cancelled by request")
        except ExecutionError as exc:
            self._finish_run(context, "failed", error=str(exc), error_code=type(exc).__name__)
        except PermissionDeniedError as exc:
            self._finish_run(context, "failed", error=str(exc), error_code="permission_denied")
        return self.get_run(run_id)

    def _dispatch_node(
        self,
        context: ExecutionContext,
        definition: WorkflowDefinition,
        plan: CompiledPlan,
        node_id: str,
        node_inputs: Dict[str, Any],
        loop_depth: int = 0,
    ) -> None:
        if context.cancelled:
            raise CancellationRequested()
        if loop_depth > 100:
            raise ExecutionError("node dispatch depth exceeded.")
        node = plan.nodes.get(node_id)
        if node is None:
            raise ExecutionError("unknown node %s." % node_id)
        self._set_current_node(context, node_id)
        node_type = node.type
        if node_type == "end":
            self._finish_run(context, "succeeded")
            return
        if node_type == "start":
            self._follow_edges(context, definition, plan, node, node_inputs, loop_depth)
            return
        if node_type == "condition":
            self._handle_condition(context, definition, plan, node, node_inputs, loop_depth)
            return
        if node_type == "switch":
            self._handle_switch(context, definition, plan, node, node_inputs, loop_depth)
            return
        if node_type == "parallel":
            self._handle_parallel(context, definition, plan, node, node_inputs, loop_depth)
            return
        if node_type == "join":
            self._handle_join(context, definition, plan, node, node_inputs, loop_depth)
            return
        if node_type == "loop":
            self._handle_loop(context, definition, plan, node, node_inputs, loop_depth)
            return
        if node_type == "delay":
            self._handle_delay(context, definition, plan, node, node_inputs, loop_depth)
            return
        if node_type in {"wait_approval", "wait_input"}:
            self._handle_wait(context, definition, plan, node, node_inputs, loop_depth)
            return
        if node_type in {"action", "transform", "notification", "subworkflow", "failure_handler", "audit_marker"}:
            self._execute_action_node(context, definition, plan, node, node_inputs, loop_depth)
            return
        raise ExecutionError("unsupported node type %s." % node_type)

    # ------------------------------------------------------------------
    # Node handlers
    # ------------------------------------------------------------------

    def _handle_condition(
        self, context, definition, plan, node: NodeConfig, node_inputs, loop_depth
    ) -> None:
        variables = self._merged_variables(context, node_inputs)
        try:
            result = evaluate_condition(node.condition, variables)
        except ExpressionError as exc:
            raise ExecutionError("condition failed: %s" % exc) from exc
        self._trace(context.run_id, node.id, "condition.evaluated", workflow_id=definition.workflow_id, safe_summary=str(result))
        self._set_node_output(context, node.id, {"result": result})
        branch = "true" if result else "false"
        target = node.branches.get(branch) or node.branches.get("else")
        if target is None:
            raise ExecutionError("condition node %s has no matching branch." % node.id)
        self._follow_to(context, definition, plan, target, node_inputs, loop_depth)

    def _handle_switch(self, context, definition, plan, node: NodeConfig, node_inputs, loop_depth) -> None:
        variables = self._merged_variables(context, node_inputs)
        value = variables.get("switch") or variables.get(node.id)
        match = None
        for key, target in node.branches.items():
            if str(key) == str(value):
                match = target
                break
        if match is None and "default" in node.branches:
            match = node.branches["default"]
        if match is None:
            raise ExecutionError("switch node %s has no matching branch." % node.id)
        self._trace(context.run_id, node.id, "switch.selected", workflow_id=definition.workflow_id, safe_summary=str(value))
        self._follow_to(context, definition, plan, match, node_inputs, loop_depth)

    def _handle_parallel(self, context, definition, plan, node: NodeConfig, node_inputs, loop_depth) -> None:
        branches = tuple(node.parallel_nodes)
        concurrency = min(len(branches), max(1, definition.resource.max_parallel_branches))
        if concurrency < len(branches):
            raise ExecutionError("parallel branch count exceeds the configured concurrency limit.")
        outcomes: Dict[str, str] = {}
        for branch in branches:
            try:
                self._dispatch_node(context, definition, plan, branch, node_inputs, loop_depth + 1)
                outcomes[branch] = "succeeded"
            except CancellationRequested:
                raise
            except ExecutionError as exc:
                outcomes[branch] = "failed"
                context.node_outputs.setdefault(node.id, {})["error"] = str(exc)
                if node.join_policy in {"first_success", "first_completion"}:
                    break
                if node.join_policy == "required":
                    required = set(node.required_branches)
                    if branch in required:
                        raise
        self._set_node_output(context, node.id, {"branches": outcomes})

    def _handle_join(self, context, definition, plan, node: NodeConfig, node_inputs, loop_depth) -> None:
        required = tuple(node.required_branches) or tuple(node.parallel_nodes)
        self._set_node_output(context, node.id, {"joined": True, "required": required})
        self._follow_edges(context, definition, plan, node, node_inputs, loop_depth)

    def _handle_loop(self, context, definition, plan, node: NodeConfig, node_inputs, loop_depth) -> None:
        loop = node.loop
        if loop is None:
            raise ExecutionError("loop node %s has no loop configuration." % node.id)
        loop_state = context.node_outputs.get(node.id, {})
        started_at = loop_state.get("_started_at")
        if started_at is None:
            variables = self._merged_variables(context, node_inputs)
            items = []
            if loop.item_source:
                items = evaluate_expression(loop.item_source, variables)
                if not isinstance(items, (list, tuple)):
                    raise ExecutionError("loop item source must resolve to a list.")
                if len(items) > loop.max_iterations:
                    items = items[: loop.max_iterations]
            loop_state = {"items": list(items), "_index": 0, "_started_at": self._now().isoformat()}
            context.node_outputs[node.id] = loop_state
        items = loop_state.get("items") or []
        index = loop_state.get("_index", 0)
        if index >= len(items):
            # Loop complete; clear state and follow the done edge.
            context.node_outputs[node.id] = {"iterations": len(items)}
            done = node.branches.get("done") or node.branches.get("false")
            if done:
                self._follow_to(context, definition, plan, done, node_inputs, loop_depth)
            else:
                self._follow_edges(context, definition, plan, node, node_inputs, loop_depth)
            return
        count = index + 1
        if count > loop.max_iterations:
            raise ExecutionError("loop exceeded its iteration limit.")
        started = datetime.fromisoformat(str(loop_state["_started_at"]))
        if (self._now() - started).total_seconds() > loop.max_duration_seconds:
            raise ExecutionError("loop exceeded its duration limit.")
        item = items[index]
        loop_state["_index"] = index + 1
        context.node_outputs[node.id] = loop_state
        loop_variables = dict(context.variables)
        loop_variables[loop.item_variable] = item
        context.variables = loop_variables
        body = node.branches.get("body") or node.branches.get("true")
        if body is None:
            raise ExecutionError("loop node %s has no body branch." % node.id)
        try:
            self._dispatch_node(context, definition, plan, body, node_inputs, loop_depth + 1)
        except ExecutionError as exc:
            if loop.failure_policy == "continue":
                # Advance past the failed item and keep iterating via the
                # body edge returning to the loop node.
                return
            raise

    def _handle_delay(self, context, definition, plan, node: NodeConfig, node_inputs, loop_depth) -> None:
        seconds = max(0, int(node.params.get("seconds") or 0))
        if seconds > 86400:
            raise ExecutionError("delay exceeds the maximum duration.")
        if seconds:
            time.sleep(seconds)
        self._set_node_output(context, node.id, {"delayed_seconds": seconds})
        self._follow_edges(context, definition, plan, node, node_inputs, loop_depth)

    def _handle_wait(self, context, definition, plan, node: NodeConfig, node_inputs, loop_depth) -> None:
        if node.type == "wait_approval":
            approval = self._request_approval(context, definition, node, node_inputs)
            context.pending_approval = approval
            state = self._await_approval(approval)
            if state == "approved":
                self._set_node_output(context, node.id, {"approved": True})
                target = node.branches.get("approved") or node.branches.get("true")
                if target:
                    self._follow_to(context, definition, plan, target, node_inputs, loop_depth)
                else:
                    self._follow_edges(context, definition, plan, node, node_inputs, loop_depth)
                return
            if state == "denied":
                self._set_node_output(context, node.id, {"approved": False})
                target = node.branches.get("denied") or node.branches.get("false")
                if target:
                    self._follow_to(context, definition, plan, target, node_inputs, loop_depth)
                else:
                    raise ExecutionError("approval denied for node %s." % node.id)
                return
            raise ExecutionError("approval expired for node %s." % node.id)
        if node.type == "wait_input":
            request = self._request_input(context, definition, node)
            context.pending_input = request
            response = self._await_input(request)
            self._set_node_output(context, node.id, {"input": response})
            context.variables["input"] = response
            self._follow_edges(context, definition, plan, node, node_inputs, loop_depth)

    def _execute_action_node(self, context, definition, plan, node: NodeConfig, node_inputs, loop_depth) -> None:
        default_action = {
            "notification": "joeos.notification",
            "transform": "joeos.transform",
            "audit_marker": "joeos.audit_marker",
            "subworkflow": "joeos.subworkflow",
        }.get(node.type)
        action_id = node.action or default_action
        if action_id is None or not self._actions.exists(action_id):
            raise ExecutionError("unsupported action %s." % action_id)
        permission = self._actions.permission_for(action_id)
        if permission and not self._permissions.granted(
            workflow_id=definition.workflow_id, permission=permission, project=definition.project
        ):
            raise ExecutionError("permission_denied: workflow lacks %s." % permission)
        if self._security_gate is not None:
            self._security_gate.check_action(
                workflow_id=definition.workflow_id,
                workflow_version=definition.version,
                project=definition.project,
                action=action_id,
                risk=self._actions.risk(action_id),
                target=node.title or node.id,
            )
        if self._secrets is not None:
            for secret in definition.secrets:
                availability = self._secrets.availability(
                    workflow_id=definition.workflow_id, required=tuple(definition.secrets)
                )
                if not availability.get(secret.name):
                    raise ExecutionError("secret_unavailable: %s" % secret.name)
        params = self._render_params(node, context, node_inputs)
        idempotency_key = self._idempotency_key(context, node, params)
        if idempotency_key and self._has_completed(context.run_id, node.id, action_id, idempotency_key):
            prior = self._prior_result(context.run_id, node.id)
            self._set_node_output(context, node.id, prior or {})
            self._follow_edges(context, definition, plan, node, node_inputs, loop_depth)
            return
        attempts = 0
        max_attempts = min(node.retry.max_attempts, MAX_RETRY_ATTEMPTS)
        while True:
            attempts += 1
            try:
                self._trace(context.run_id, node.id, "action.started", action_id=action_id, workflow_id=definition.workflow_id)
                result = self._actions.dispatch(
                    action_id=action_id,
                    params=params,
                    context={"run_id": context.run_id, "workflow_id": definition.workflow_id, "node_id": node.id, "project": definition.project, "_subworkflow_runner": self._subworkflow_runner},
                    variables=context.variables,
                    trace=lambda message: self._trace(context.run_id, node.id, "action.trace", action_id=action_id, safe_summary=str(message)[:200]),
                )
                if idempotency_key:
                    self._record_idempotency(context.run_id, node.id, action_id, idempotency_key, result)
                self._set_node_output(context, node.id, result)
                self._trace(context.run_id, node.id, "action.succeeded", action_id=action_id, workflow_id=definition.workflow_id)
                break
            except ActionError as exc:
                error_code = self._classify_error(str(exc))
                self._trace(context.run_id, node.id, "action.failed", action_id=action_id, error_code=error_code, safe_summary=str(exc)[:200], workflow_id=definition.workflow_id)
                if attempts < max_attempts and is_retryable_error(error_code, node.retry.retryable_errors):
                    delay = self._backoff(node.retry, attempts)
                    time.sleep(delay)
                    self._increment_retries(context, node.id)
                    continue
                if node.compensation:
                    self._compensate(context, definition, plan, node, node.compensation, node_inputs, loop_depth)
                raise ExecutionError(str(exc))
        self._follow_edges(context, definition, plan, node, node_inputs, loop_depth)

    # ------------------------------------------------------------------
    # Edges, outputs, helpers
    # ------------------------------------------------------------------

    def _follow_edges(self, context, definition, plan, node: NodeConfig, node_inputs, loop_depth) -> None:
        edges = plan.successors(node.id)
        if not edges:
            return
        if len(edges) == 1:
            self._dispatch_node(context, definition, plan, edges[0].target, node_inputs, loop_depth)
            return
        for edge in edges:
            if edge.condition:
                variables = self._merged_variables(context, node_inputs)
                try:
                    if evaluate_condition(edge.condition, variables):
                        self._dispatch_node(context, definition, plan, edge.target, node_inputs, loop_depth)
                        return
                except ExpressionError as exc:
                    raise ExecutionError("edge condition failed: %s" % exc) from exc
            else:
                self._dispatch_node(context, definition, plan, edge.target, node_inputs, loop_depth)
                return
        raise ExecutionError("no edge condition matched from node %s." % node.id)

    def _follow_to(self, context, definition, plan, target: str, node_inputs, loop_depth) -> None:
        self._dispatch_node(context, definition, plan, target, node_inputs, loop_depth)

    def _render_params(self, node: NodeConfig, context: ExecutionContext, node_inputs: Dict[str, Any]) -> Dict[str, Any]:
        params = dict(node.params or {})
        for key, value in list(params.items()):
            if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
                expression = value[2:-1]
                try:
                    params[key] = evaluate_expression(expression, self._merged_variables(context, node_inputs))
                except ExpressionError:
                    pass
        return params

    def _merged_variables(self, context: ExecutionContext, node_inputs: Dict[str, Any]) -> Dict[str, Any]:
        merged = dict(context.variables)
        for node_id, output in context.node_outputs.items():
            merged[node_id] = output
        merged.update(node_inputs or {})
        return merged

    def _set_node_output(self, context, node_id: str, output: Dict[str, Any]) -> None:
        context.node_outputs[node_id] = output
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT node_states FROM workflow_runs WHERE run_id = ?", (context.run_id,)
            ).fetchone()
            states = json.loads(str(row["node_states"])) if row else {}
            states[node_id] = {"output": output}
            connection.execute(
                "UPDATE workflow_runs SET node_states = ? WHERE run_id = ?",
                (json.dumps(states), context.run_id),
            )

    def _set_current_node(self, context, node_id: str) -> None:
        with self._connection_factory() as connection:
            connection.execute(
                "UPDATE workflow_runs SET current_node = ? WHERE run_id = ?",
                (node_id, context.run_id),
            )

    def _initialize_variables(self, definition: WorkflowDefinition, inputs: Dict[str, Any]) -> Dict[str, Any]:
        variables: Dict[str, Any] = {}
        for variable in definition.variables:
            if variable.name in inputs:
                variables[variable.name] = inputs[variable.name]
            elif variable.default is not None:
                variables[variable.name] = variable.default
            elif variable.required:
                raise ExecutionError("missing required input %s." % variable.name)
        variables.update({key: value for key, value in inputs.items() if key not in variables})
        return variables

    def _finish_run(self, context, state: RunState, *, error: str = "", error_code: str = "") -> None:
        ended = _now()
        duration = 0.0
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT started_at FROM workflow_runs WHERE run_id = ?", (context.run_id,)
            ).fetchone()
            if row and row["started_at"]:
                try:
                    started = datetime.fromisoformat(str(row["started_at"]))
                    ended_dt = datetime.fromisoformat(ended)
                    duration = (ended_dt - started).total_seconds()
                except ValueError:
                    duration = 0.0
            connection.execute(
                """
                UPDATE workflow_runs
                SET state = ?, ended_at = ?, duration_seconds = ?, error = ?,
                    error_code = ?, cancellation_state = ?
                WHERE run_id = ?
                """,
                (
                    state,
                    ended,
                    duration,
                    error[:500],
                    error_code[:80],
                    context.cancellation_state,
                    context.run_id,
                ),
            )
            connection.execute(
                "INSERT INTO workflow_activity (event_id, workflow_id, kind, message, level, recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "wfa_" + uuid.uuid4().hex[:20],
                    context.workflow_id,
                    "run_" + str(state),
                    "Run %s." % state,
                    "success" if state in {"succeeded", "succeeded_with_warnings"} else "error",
                    ended,
                ),
            )
        self._trace(context.run_id, "run", "run." + str(state), workflow_id=context.workflow_id)

    def get_run(self, run_id: str) -> Optional[RunRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM workflow_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        return RunRecord(
            run_id=str(row["run_id"]),
            workflow_id=str(row["workflow_id"]),
            workflow_version=str(row["workflow_version"]),
            trigger_id=str(row["trigger_id"]),
            state=str(row["state"]),
            current_node=str(row["current_node"]),
            started_at=str(row["started_at"]),
            ended_at=str(row["ended_at"]),
            duration_seconds=float(row["duration_seconds"]),
            trigger_context=json.loads(str(row["trigger_context"])),
            inputs=json.loads(str(row["inputs"])),
            outputs=json.loads(str(row["outputs"])),
            error=str(row["error"]),
            error_code=str(row["error_code"]),
            retry_count=int(row["retry_count"]),
            cancellation_state=str(row["cancellation_state"]),
            trace_id=str(row["trace_id"]),
        )

    # ------------------------------------------------------------------
    # Approvals / input
    # ------------------------------------------------------------------

    def _request_approval(self, context, definition, node: NodeConfig, node_inputs) -> ApprovalRequest:
        arguments_hash = hashlib.sha256(
            json.dumps(node.params, sort_keys=True, default=str).encode()
        ).hexdigest()
        approval = ApprovalRequest(
            approval_id="apr_" + uuid.uuid4().hex[:16],
            run_id=context.run_id,
            workflow_id=definition.workflow_id,
            node_id=node.id,
            action=node.action or node.type,
            reason=str(node.params.get("reason") or "Workflow action requires approval."),
            risk=definition.risk,
            scope=definition.scope,
            project=definition.project,
            side_effects=tuple(self._actions.side_effects(node.action or "")) if node.action else (),
            arguments_hash=arguments_hash,
            state="pending",
            expires_at=(self._now() + timedelta(hours=24)).isoformat(),
            requested_by=definition.owner,
            created_at=_now(),
        )
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO workflow_approvals (
                    approval_id, run_id, workflow_id, node_id, action, reason, risk,
                    scope, project, side_effects, arguments_hash, state, expires_at,
                    requested_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    approval.approval_id,
                    context.run_id,
                    definition.workflow_id,
                    node.id,
                    approval.action,
                    approval.reason,
                    approval.risk,
                    approval.scope,
                    approval.project,
                    "\n".join(approval.side_effects),
                    approval.arguments_hash,
                    approval.expires_at,
                    definition.owner,
                    _now(),
                ),
            )
            connection.execute(
                "UPDATE workflow_runs SET state = 'awaiting_approval' WHERE run_id = ?",
                (context.run_id,),
            )
        self._trace(context.run_id, node.id, "approval.requested", workflow_id=definition.workflow_id, safe_summary=approval.reason)
        if self._event_sink:
            self._event_sink("warn", "automation", "Workflow %s awaits approval: %s" % (definition.workflow_id, approval.reason))
        return approval

    def _await_approval(self, approval: ApprovalRequest) -> str:
        if self._approval_resolver is None:
            raise ExecutionError("no approval resolver available.")
        return self._approval_resolver(approval.approval_id)

    def _request_input(self, context, definition, node: NodeConfig) -> UserInputRequest:
        request = UserInputRequest(
            input_id="inp_" + uuid.uuid4().hex[:16],
            run_id=context.run_id,
            workflow_id=definition.workflow_id,
            node_id=node.id,
            prompt=str(node.params.get("prompt") or "Workflow requires input."),
            schema=dict(node.params.get("schema") or {}),
            state="pending",
            created_at=_now(),
        )
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO workflow_user_inputs (input_id, run_id, workflow_id, node_id, prompt, schema, state, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    request.input_id,
                    context.run_id,
                    definition.workflow_id,
                    node.id,
                    request.prompt,
                    json.dumps(request.input_schema),
                    _now(),
                ),
            )
            connection.execute(
                "UPDATE workflow_runs SET state = 'awaiting_input' WHERE run_id = ?",
                (context.run_id,),
            )
        return request

    def _await_input(self, request: UserInputRequest) -> Dict[str, Any]:
        if self._input_resolver is None:
            raise ExecutionError("no input resolver available.")
        return self._input_resolver(request.input_id)

    # ------------------------------------------------------------------
    # Compensation / retries / idempotency
    # ------------------------------------------------------------------

    def _compensate(self, context, definition, plan, node: NodeConfig, compensation: str, node_inputs, loop_depth) -> None:
        compensation_node = plan.nodes.get(compensation)
        if compensation_node is None:
            raise ExecutionError("compensation node %s not found." % compensation)
        self._trace(context.run_id, node.id, "compensation.started", workflow_id=definition.workflow_id, safe_summary=compensation)
        try:
            self._dispatch_node(context, definition, plan, compensation, node_inputs, loop_depth + 1)
            self._trace(context.run_id, node.id, "compensation.succeeded", workflow_id=definition.workflow_id)
        except Exception as exc:
            self._trace(context.run_id, node.id, "compensation.failed", workflow_id=definition.workflow_id, safe_summary=str(exc)[:200])

    def _classify_error(self, message: str) -> str:
        lowered = message.lower()
        if "permission" in lowered:
            return "permission_denied"
        if "secret" in lowered:
            return "secret_unavailable"
        if "timeout" in lowered:
            return "timeout"
        return "action_failed"

    def _backoff(self, retry, attempt: int) -> float:
        delay = retry.backoff_seconds * (retry.backoff_factor ** (attempt - 1))
        delay = min(delay, retry.max_delay_seconds)
        if retry.jitter:
            import random
            delay = delay * (0.5 + random.random() * 0.5)
        return delay

    def _increment_retries(self, context, node_id: str) -> None:
        with self._connection_factory() as connection:
            connection.execute(
                "UPDATE workflow_runs SET retry_count = retry_count + 1, state = 'retrying' WHERE run_id = ?",
                (context.run_id,),
            )

    def _idempotency_key(self, context, node: NodeConfig, params: Dict[str, Any]) -> str:
        return "wf:%s:node:%s:args:%s" % (
            context.workflow_id,
            node.id,
            hashlib.sha256(json.dumps(params, sort_keys=True, default=str).encode()).hexdigest()[:16],
        )

    def _has_completed(self, run_id: str, node_id: str, action_id: str, idempotency_key: str) -> bool:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT idempotency_key FROM workflow_idempotency WHERE idempotency_key = ? AND state = 'completed'",
                (idempotency_key,),
            ).fetchone()
        return row is not None

    def _record_idempotency(self, run_id, node_id, action_id, idempotency_key, result) -> None:
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO workflow_idempotency (idempotency_key, run_id, node_id, action, scope, state, result, created_at, expires_at)
                VALUES (?, ?, ?, ?, 'global', 'completed', ?, ?, ?)
                ON CONFLICT(idempotency_key) DO NOTHING
                """,
                (idempotency_key, run_id, node_id, action_id, json.dumps(result)[:2000], _now(), ""),
            )

    def _prior_result(self, run_id: str, node_id: str) -> Optional[Dict[str, Any]]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT node_states FROM workflow_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row:
            states = json.loads(str(row["node_states"]))
            entry = states.get(node_id)
            if entry and isinstance(entry.get("output"), dict):
                return entry["output"]
        return None

    def _trace(self, run_id, node_id, event_type, *, action_id="", workflow_id="", error_code="", safe_summary="", retry_state="") -> None:
        recorded = _now()
        if self._trace_sink is not None:
            self._trace_sink(run_id=run_id, node_id=node_id, event_type=event_type, action_id=action_id, workflow_id=workflow_id, recorded_at=recorded, error_code=error_code, safe_summary=safe_summary[:200])
        try:
            with self._connection_factory() as connection:
                connection.execute(
                    """
                    INSERT INTO workflow_traces (
                        trace_id, run_id, node_id, action_id, event_type, recorded_at,
                        state_transition, error_code, retry_state, safe_summary
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "",
                        run_id,
                        node_id,
                        action_id,
                        event_type,
                        recorded,
                        event_type,
                        error_code,
                        retry_state,
                        safe_summary[:200],
                    ),
                )
        except Exception:
            pass


class CancellationRequested(RuntimeError):
    pass


class _InjectedTraceSink:
    """Adapter that forwards engine traces to the history service."""

    def __init__(self, history) -> None:
        self._history = history

    def __call__(self, *, run_id, node_id, event_type, action_id="", workflow_id="", recorded_at="", error_code="", safe_summary="") -> None:
        self._history.record_trace(
            run_id=run_id,
            node_id=node_id,
            event_type=event_type,
            action_id=action_id,
            workflow_id=workflow_id,
            recorded_at=recorded_at,
            error_code=error_code,
            safe_summary=safe_summary,
        )