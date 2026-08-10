"""Authoritative agent and action-governance control plane (Phase P3B).

The backend is authoritative for providers, models, agent profiles/versions,
runs/tasks, the tool catalog, immutable action proposals, deterministic policy
evaluation, and cryptographically bound approvals. Models emit structured tool
requests that become immutable proposals; policy decides; humans approve with
the enrolled approval key. No privileged action ever executes: approved
privileged actions stop at `approved_awaiting_executor`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Awaitable, Callable, Dict, List, Optional
from uuid import UUID, uuid4

from server.identity.crypto import verify_p256_signature

from .events import ControlEventEmitter
from .repository import SQLiteControlStore
from .storage import (
    ActionProposalRecord,
    ApprovalChallengeRecord,
    ApprovalDecisionRecord,
    ApprovalRequestRecord,
    CouncilDefinitionRecord,
    CouncilRunRecord,
    PolicyDecisionRecord,
    now_ms,
    sha256_hex,
)

APPROVAL_DOMAIN = (
    "JOEOS-ACTION-APPROVAL-V1\0"
    "{challenge_id}\0{proposal_id}\0{proposal_digest}\0{policy_decision_id}\0"
    "{approval_request_id}\0{approver_user_id}\0{approver_device_id}\0"
    "{organization_id}\0{workspace_id}\0{requested_decision}\0{risk}\0"
    "{nonce}\0{expires_at}"
)

CAPABILITY_REQUIRED_CAP = "capability.required"
CAPABILITY_DENIED_CAP = "capability.denied"
ACTION_PROPOSE_CAP = "action.propose"
ACTION_READ_CAP = "action.read"
ACTION_CANCEL_CAP = "action.cancel"
APPROVAL_READ_CAP = "approval.read"
APPROVAL_DECIDE = {
    "medium": "approval.decide.medium",
    "high": "approval.decide.high",
    "critical": "approval.decide.critical",
}
AGENT_READ_CAP = "agent.read"
AGENT_MANAGE_CAP = "agent.manage"
AGENT_RUN_CAP = "agent.run"
TOOL_READ_CAP = "tool.read"
POLICY_READ_CAP = "policy.read"

DANGEROUS_PARAMETER_PATTERNS = (
    re.compile(r"(?i)\b(bash|sh|/bin/|cmd\.exe|powershell)\b"),
    re.compile(r"\$[{(\w]"),
    re.compile(r"(?i)\b(password|secret|api[_-]?key|token|private[_-]?key)\s*[:=]"),
)


class ActionError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.public_message = message


class ActionDeniedError(ActionError):
    pass


class ActionNotFoundError(ActionError):
    pass


class ActionCapabilityError(ActionDeniedError):
    pass


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def proposal_payload_digest(
    *,
    tool_id: UUID,
    tool_version: str,
    action_type: str,
    parameters: str,
    canonical_target: str,
    risk: str,
    reversibility: str,
) -> str:
    return sha256_hex(
        canonical_json(
            {
                "tool_id": str(tool_id),
                "tool_version": tool_version,
                "action_type": action_type,
                "parameters": json.loads(parameters),
                "target": canonical_target,
                "risk": risk,
                "reversibility": reversibility,
            }
        )
    )


class ActionService:
    """Coordinates the authoritative agent/action control plane."""

    proposal_ttl_ms = 24 * 60 * 60 * 1000
    approval_ttl_ms = 60 * 60 * 1000
    challenge_ttl_ms = 5 * 60 * 1000
    policy_version = "p3b-1"

    def __init__(
        self,
        store: SQLiteControlStore,
        *,
        device_repository: Optional[object] = None,
        agent_executor: Optional[Callable[[List[Dict[str, str]], List[Dict], Dict], Awaitable[Dict]]] = None,
        council_executor: Optional[Callable[[Dict, str], Awaitable[Dict]]] = None,
        event_sink: Optional[Callable[[str, str, str], None]] = None,
        now: Callable[[], int] = now_ms,
    ) -> None:
        self._store = store
        self._device_repository = device_repository
        self._executor = agent_executor or (lambda messages, tools, decision: asyncio.sleep(0) or {"content": ""})
        self._council_executor = council_executor
        self._events = ControlEventEmitter(event_sink)
        self._now = now

    def prepare(self) -> None:
        self._store.prepare()

    def recover_after_restart(self) -> int:
        return self._store.recover_stale_runs(self._now())

    # ------------------------------------------------------------------
    # Providers / models
    # ------------------------------------------------------------------

    def register_provider(self, principal: Dict, *, key, display_name, provider_type,
                          location, transport="http", endpoint_reference="",
                          auth_reference_type="none", streaming=False, tool_calling=False,
                          structured_output=False, context_window=0, privacy_class="restricted",
                          allowed_data_classes="restricted") -> Dict:
        self._require(principal, AGENT_MANAGE_CAP)
        record = self._store.upsert_provider(
            id=uuid4(), key=key, display_name=display_name, provider_type=provider_type,
            location=location, transport=transport, endpoint_reference=endpoint_reference,
            auth_reference_type=auth_reference_type, status="active", health="unknown",
            streaming=streaming, tool_calling=tool_calling, structured_output=structured_output,
            context_window=context_window, privacy_class=privacy_class,
            allowed_data_classes=allowed_data_classes, now=self._now(),
        )
        self._emit(principal, "provider.registered", data={"key": key})
        return provider_payload(record)

    def set_provider_status(self, principal: Dict, provider_id: UUID, status: str, health: str) -> bool:
        self._require(principal, AGENT_MANAGE_CAP)
        updated = self._store.set_provider_state(provider_id, status, health, self._now())
        self._emit(principal, "provider.updated", data={"provider": str(provider_id), "status": status})
        return updated

    def list_providers(self, principal: Dict) -> List[Dict]:
        self._require(principal, POLICY_READ_CAP)
        return [provider_payload(r) for r in self._store.list_providers()]

    def register_model(self, principal: Dict, *, provider_id, key, display_name, model_identifier,
                       streaming=False, tool_calling=False, structured_output=False,
                       vision=False, reasoning=False, context_limit=0, output_limit=0,
                       privacy_class="restricted", allowed_data_classes="restricted") -> Dict:
        self._require(principal, AGENT_MANAGE_CAP)
        provider = self._store.get_provider(provider_id)
        if provider is None:
            raise ActionNotFoundError(404, "provider_not_found", "The provider does not exist.")
        record = self._store.upsert_model(
            id=uuid4(), provider_id=provider_id, key=key, display_name=display_name,
            model_identifier=model_identifier, streaming=streaming, tool_calling=tool_calling,
            structured_output=structured_output, vision=vision, reasoning=reasoning,
            context_limit=context_limit, output_limit=output_limit,
            privacy_class=privacy_class, allowed_data_classes=allowed_data_classes,
            now=self._now(),
        )
        self._emit(principal, "model.registered", data={"key": key, "provider": str(provider_id)})
        return model_payload(record)

    def set_model_status(self, principal: Dict, model_id: UUID, status: str) -> bool:
        self._require(principal, AGENT_MANAGE_CAP)
        updated = self._store.set_model_state(model_id, status, self._now())
        self._emit(principal, "model.updated", data={"model": str(model_id), "status": status})
        return updated

    def list_models(self, principal: Dict) -> List[Dict]:
        self._require(principal, POLICY_READ_CAP)
        return [model_payload(r) for r in self._store.list_models()]

    # ------------------------------------------------------------------
    # Agent profiles
    # ------------------------------------------------------------------

    def create_agent(self, principal: Dict, *, key, display_name, description="", purpose="",
                     system_instructions="", allowed_tools="", denied_tools="",
                     required_capabilities="", max_delegation_depth=0, max_parallel_tasks=1,
                     max_runtime_ms=0, max_token_budget=0, data_boundary="restricted",
                     approval_policy="backend", default_provider_policy="backend",
                     default_model_policy="backend") -> Dict:
        self._require(principal, AGENT_MANAGE_CAP)
        org = principal["organization"]["id"]
        ws = principal["workspace"]["id"]
        profile, version_id = self._store.create_agent(
            id=uuid4(), organization_id=org, workspace_id=ws, key=key,
            display_name=display_name, description=description, purpose=purpose,
            system_instructions=system_instructions, allowed_tools=allowed_tools,
            denied_tools=denied_tools, required_capabilities=required_capabilities,
            max_delegation_depth=max_delegation_depth, max_parallel_tasks=max_parallel_tasks,
            max_runtime_ms=max_runtime_ms, max_token_budget=max_token_budget,
            data_boundary=data_boundary, approval_policy=approval_policy,
            default_provider_policy=default_provider_policy,
            default_model_policy=default_model_policy,
            created_by=principal["user"]["id"], now=self._now(),
        )
        self._emit(principal, "agent.created", data={"agent": str(profile.id), "key": key})
        return agent_payload(profile, version_id)

    def update_agent(self, principal: Dict, agent_id: UUID, expected_revision: int, **changes) -> Dict:
        self._require(principal, AGENT_MANAGE_CAP)
        agent = self._store.get_agent(agent_id)
        if agent is None:
            raise ActionNotFoundError(404, "agent_not_found", "The agent does not exist.")
        if agent.organization_id != principal["organization"]["id"]:
            raise ActionDeniedError(403, "cross_workspace_denied", "Cross-organization agent access is denied.")
        if agent.revision != expected_revision:
            raise ActionError(409, "revision_conflict", "The agent changed; reload and retry.")
        merged = {**agent_payload(agent, ""), **changes}
        # Editing creates a new immutable version; the profile revision bumps.
        version_id = uuid4()
        digest = sha256_hex(canonical_json({k: merged.get(k) for k in (
            "key", "display_name", "description", "system_instructions", "allowed_tools",
            "denied_tools", "required_capabilities", "max_delegation_depth",
            "max_parallel_tasks", "max_runtime_ms", "max_token_budget", "data_boundary",
            "approval_policy", "default_provider_policy", "default_model_policy",
        )}))
        with self._store._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            # Persist policy changes on the authoritative profile row so runtime
            # model/provider binding actually takes effect (the version digest
            # records them too).
            connection.execute(
                "UPDATE control_agents SET revision=revision+1, updated_at=?, "
                "default_provider_policy=?, default_model_policy=? WHERE id=?",
                (
                    self._now(),
                    changes.get("default_provider_policy", agent.default_provider_policy),
                    changes.get("default_model_policy", agent.default_model_policy),
                    str(agent_id),
                ),
            )
            connection.execute(
                "UPDATE control_agent_versions SET superseded=1 WHERE agent_id=? AND superseded=0",
                (str(agent_id),),
            )
            connection.execute(
                """
                INSERT INTO control_agent_versions(version_id, agent_id, configuration_digest, created_by, created_at, superseded)
                VALUES (?, ?, ?, ?, ?, 0)
                """,
                (str(version_id), str(agent_id), digest, str(principal["user"]["id"]), self._now()),
            )
            connection.commit()
        self._emit(principal, "agent.updated", data={"agent": str(agent_id)})
        updated = self._store.get_agent(agent_id)
        return agent_payload(updated, version_id)  # type: ignore[arg-type]

    def set_agent_status(self, principal: Dict, agent_id: UUID, status: str) -> bool:
        self._require(principal, AGENT_MANAGE_CAP)
        updated = self._store.set_agent_state(agent_id, status, self._now())
        self._emit(principal, "agent.disabled" if status != "active" else "agent.updated",
                   data={"agent": str(agent_id), "status": status})
        return updated

    def list_agents(self, principal: Dict) -> List[Dict]:
        self._require(principal, AGENT_READ_CAP)
        org = principal["organization"]["id"]
        ws = principal["workspace"]["id"]
        return [agent_payload(a, self._latest_version_id(a.id)) for a in self._store.list_agents(org, ws)]

    def get_agent(self, principal: Dict, agent_id: UUID) -> Dict:
        self._require(principal, AGENT_READ_CAP)
        agent = self._store.get_agent(agent_id)
        if agent is None:
            raise ActionNotFoundError(404, "agent_not_found", "The agent does not exist.")
        if agent.organization_id != principal["organization"]["id"] or (
            agent.workspace_id is not None and agent.workspace_id != principal["workspace"]["id"]
        ):
            raise ActionDeniedError(403, "cross_workspace_denied", "Cross-workspace agent access is denied.")
        return agent_payload(agent, self._latest_version_id(agent.id))

    def list_agent_versions(self, principal: Dict, agent_id: UUID) -> List[Dict]:
        self._require(principal, AGENT_READ_CAP)
        return [{"version_id": v.version_id, "agent_id": v.agent_id,
                 "configuration_digest": v.configuration_digest, "superseded": v.superseded}
                for v in self._store.list_agent_versions(agent_id)]

    # ------------------------------------------------------------------
    # Agent runs
    # ------------------------------------------------------------------

    def start_agent_run(
        self,
        principal: Dict,
        *,
        agent_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        model_preference: Optional[str] = None,
        parent_run_id: Optional[UUID] = None,
        delegation_depth: int = 0,
        objective: str = "",
    ) -> Dict:
        self._require(principal, AGENT_RUN_CAP)
        agent = self._store.get_agent(agent_id)
        if agent is None or agent.status != "active":
            raise ActionDeniedError(403, "agent_disabled", "The agent is disabled or unknown.")
        if agent.organization_id != principal["organization"]["id"]:
            raise ActionDeniedError(403, "cross_workspace_denied", "Cross-workspace agent access is denied.")
        version_id = self._latest_version_id(agent_id)
        provider, model = self._select_provider_model(principal, agent, model_preference)
        run = self._store.create_run(
            id=uuid4(), conversation_id=conversation_id, message_id=message_id,
            agent_id=agent_id, agent_version_id=version_id,
            requested_by=principal["user"]["id"], parent_run_id=parent_run_id,
            delegation_depth=delegation_depth, objective=objective[:4000],
            trace_id=str(uuid4()), now=self._now(),
        )
        self._store.update_run_state(
            run.id, status="running", now=self._now(),
            provider_id=provider.id if provider else None,
            model_id=model.id if model else None,
        )
        self._emit(principal, "agent.run.queued", run_id=run.id, conversation_id=conversation_id,
                   data={"agent": str(agent_id)})
        self._emit(principal, "agent.run.started", run_id=run.id, conversation_id=conversation_id,
                   data={"provider": provider.key if provider else None,
                         "model": model.key if model else None})
        return run_payload(run, provider_key=provider.key if provider else None,
                           model_key=model.key if model else None)

    async def execute_agent_run(self, principal: Dict, run_id: UUID) -> Dict:
        """Execute a queued/running agent run through the injected executor.

        The executor is the ONLY place a model call happens for agent runs; the
        service never fabricates output. The result is persisted, bounded, and
        the run transitions to a terminal state."""
        self._require(principal, AGENT_RUN_CAP)
        run = self._store.get_run(run_id)
        if run is None:
            raise ActionNotFoundError(404, "run_not_found", "The run does not exist.")
        if run.status not in ("queued", "running", "waiting_for_tool", "waiting_for_approval"):
            return run_payload(self._store.get_run(run_id))
        agent = self._store.get_agent(run.agent_id)
        if agent is None:
            self._store.set_run_result(run_id, status="failed", result="", now=self._now(),
                                       failure="agent_missing")
            return run_payload(self._store.get_run(run_id))
        provider = self._store.get_provider(run.provider_id) if run.provider_id else None
        model = self._store.get_model(run.model_id) if run.model_id else None
        if provider is None or model is None or provider.status != "active":
            self._store.set_run_result(run_id, status="failed", result="", now=self._now(),
                                       failure="provider_unavailable")
            return run_payload(self._store.get_run(run_id))
        decision = {
            "provider": provider.key if provider else None,
            "model": model.key if model else None,
            "agent": agent.key,
            "objective": run.objective,
        }
        messages = self._build_agent_messages(agent, run.objective)
        tools = self._agent_tool_schemas(agent)
        try:
            outcome = await self._executor(messages, tools, decision)
            content = (outcome or {}).get("content", "") or ""
            token_usage = int((outcome or {}).get("token_usage") or 0)
            status = "succeeded"
            failure = ""
        except ActionDeniedError:
            raise
        except Exception as error:  # noqa: BLE001 - normalized to a typed failure
            content = ""
            token_usage = 0
            status = "failed"
            failure = _normalize_agent_error(error)
        self._store.set_run_result(run_id, status=status, result=content[:32000],
                                   now=self._now(), token_usage=token_usage, failure=failure)
        event = "agent.run.completed" if status == "succeeded" else "agent.run.failed"
        self._emit(principal, event, run_id=run_id, conversation_id=run.conversation_id,
                   data={"provider": provider.key, "model": model.key, "status": status})
        final = self._store.get_run(run_id)
        return run_payload(final, provider_key=provider.key, model_key=model.key,
                           result=self._store.get_run_output(run_id))

    async def delegate_agent_run(
        self,
        principal: Dict,
        *,
        parent_run_id: UUID,
        child_agent_id: UUID,
        objective: str,
    ) -> Dict:
        """Create and execute a REAL child AgentRun for a delegated task.

        The child is a separate authoritative run with its own agent version,
        provider, model, and persisted result. A bounded delegation depth
        prevents runaway agent chains; cycles are impossible because a run can
        only be created forward (never back to an ancestor)."""
        self._require(principal, AGENT_RUN_CAP)
        parent = self._store.get_run(parent_run_id)
        if parent is None:
            raise ActionNotFoundError(404, "run_not_found", "The parent run does not exist.")
        depth = parent.delegation_depth + 1
        parent_agent = self._store.get_agent(parent.agent_id)
        max_depth = int(parent_agent.max_delegation_depth) if parent_agent else 0
        if max_depth < 0:
            # Negative is invalid configuration; treat as no delegation.
            raise ActionDeniedError(403, "delegation_disabled",
                                    "This agent does not permit delegation.")
        if max_depth == 0 or depth > max_depth:
            raise ActionDeniedError(403, "delegation_limit_reached",
                                    "The delegation depth limit is reached.")
        child = self.start_agent_run(
            principal, agent_id=child_agent_id,
            conversation_id=parent.conversation_id, message_id=parent.message_id,
            parent_run_id=parent_run_id, delegation_depth=depth, objective=objective,
        )
        self._emit(principal, "agent.run.delegated", run_id=parent_run_id,
                   conversation_id=parent.conversation_id,
                   data={"child_run": str(child["id"]), "agent": str(child_agent_id)})
        await self.execute_agent_run(principal, child["id"])
        return run_payload(self._store.get_run(child["id"]))

    def create_task_graph(
        self,
        principal: Dict,
        *,
        run_id: UUID,
        tasks: List[Dict],
    ) -> Dict:
        """Create a real TaskGraph for a run.

        Each task carries title/objective/assigned_agent_id/dependencies
        (a comma-separated list of parent task ids within this graph). Tasks are
        created in `ready`/`waiting_for_dependency` state based on whether their
        dependencies exist; execution is driven by `execute_task_graph`."""
        self._require(principal, AGENT_RUN_CAP)
        created = []
        by_key = {}
        tasks = [self._task_as_dict(t) for t in tasks]
        for task in tasks:
            deps = [d.strip() for d in (task.get("dependencies") or "").split(",") if d.strip()]
            resolved_deps = []
            for dep in deps:
                if dep not in by_key:
                    raise ActionDeniedError(400, "unknown_task_dependency",
                                            "Task dependency %s does not exist." % dep)
                resolved_deps.append(str(by_key[dep]))
            task_id = uuid4()
            by_key[task["key"]] = task_id
            state = "ready" if not resolved_deps else "waiting_for_dependency"
            raw_agent = task.get("assigned_agent_id")
            assigned = None
            if raw_agent:
                assigned = raw_agent if isinstance(raw_agent, UUID) else UUID(str(raw_agent))
            record = self._store.create_task(
                id=task_id, run_id=run_id, parent_task_id=None,
                title=task.get("title") or task.get("key"),
                objective=task.get("objective") or "",
                assigned_agent_id=assigned,
                dependencies=",".join(resolved_deps),
                now=self._now(),
            )
            if state == "waiting_for_dependency":
                self._store.update_task_state(task_id, state="waiting_for_dependency", now=self._now())
            created.append(task_payload(record))
            self._emit(principal, "agent.task.created", run_id=run_id, task_id=task_id,
                       data={"title": task.get("title") or task.get("key")})
        return {"tasks": created}

    def _task_dependencies_met(self, task) -> bool:
        deps = [d for d in task.dependencies.split(",") if d]
        if not deps:
            return True
        for dep in deps:
            parent = self._store.get_task(UUID(dep))
            if parent is None or parent.state != "succeeded":
                return False
        return True

    async def execute_task_graph(self, principal: Dict, run_id: UUID) -> Dict:
        """Execute a real TaskGraph: compute ready tasks, run each ready task
        through the assigned agent (a real child AgentRun per task), persist
        results, and propagate failure/cancellation through dependencies.

        Bounded: tasks execute sequentially (this VPS runs model calls one at a
        time), and the graph terminates when all tasks are terminal."""
        self._require(principal, AGENT_RUN_CAP)
        tasks = self._store.list_tasks(run_id)
        if not tasks:
            raise ActionNotFoundError(404, "no_tasks", "The run has no tasks.")
        remaining = [t for t in tasks if t.state in ("ready", "waiting_for_dependency", "running")]
        guard = 0
        while remaining and guard < 64:
            guard += 1
            progressed = False
            for task in list(remaining):
                if task.state == "waiting_for_dependency":
                    if self._task_dependencies_met(task):
                        self._store.update_task_state(task.id, state="ready", now=self._now())
                        task = self._store.get_task(task.id)
                        progressed = True
                    continue
                if task.state != "ready":
                    continue
                if task.assigned_agent_id is None:
                    self._store.update_task_state(task.id, state="failed", now=self._now(),
                                                  failure="no_agent_assigned")
                    progressed = True
                    continue
                self._store.update_task_state(task.id, state="running", now=self._now())
                try:
                    child = await self.delegate_agent_run(
                        principal, parent_run_id=run_id,
                        child_agent_id=task.assigned_agent_id,
                        objective=task.objective,
                    )
                    if child["status"] == "succeeded":
                        self._store.update_task_state(
                            task.id, state="succeeded", now=self._now(),
                            output_reference=child.get("result", "")[:2000],
                        )
                    else:
                        self._store.update_task_state(
                            task.id, state="failed", now=self._now(),
                            failure=child.get("failure") or "task_agent_failed",
                        )
                except Exception as error:  # noqa: BLE001
                    self._store.update_task_state(
                        task.id, state="failed", now=self._now(),
                        failure=_normalize_agent_error(error),
                    )
                self._emit(principal, "agent.task.completed", run_id=run_id, task_id=task.id,
                           data={"state": self._store.get_task(task.id).state})
                progressed = True
            if not progressed:
                # Only dependency-failure cycles should stall; mark them blocked.
                for task in remaining:
                    if task.state == "waiting_for_dependency":
                        blocked = any(
                            self._store.get_task(UUID(d)) is not None
                            and self._store.get_task(UUID(d)).state == "failed"
                            for d in task.dependencies.split(",") if d
                        )
                        if blocked:
                            self._store.update_task_state(task.id, state="blocked", now=self._now())
                            progressed = True
                if not progressed:
                    break
            remaining = [t for t in self._store.list_tasks(run_id)
                         if t.state in ("ready", "waiting_for_dependency", "running")]
        return {
            "run_id": str(run_id),
            "tasks": [task_payload(t) for t in self._store.list_tasks(run_id)],
        }

    @staticmethod
    def _task_as_dict(task) -> Dict:
        if isinstance(task, dict):
            return task
        if hasattr(task, "model_dump"):
            return task.model_dump()
        return {k: getattr(task, k) for k in (
            "key", "title", "objective", "assigned_agent_id", "dependencies")}

    def _build_agent_messages(self, agent, objective: str) -> List[Dict[str, str]]:
        messages: List[Dict[str, str]] = []
        if agent.system_instructions:
            messages.append({"role": "system", "content": agent.system_instructions})
        if objective:
            messages.append({"role": "user", "content": objective[:4000]})
        return messages

    def _agent_tool_schemas(self, agent) -> List[Dict]:
        allowed = {t for t in agent.allowed_tools.split(",") if t} if agent.allowed_tools else set()
        if not allowed:
            return []
        schemas = []
        for tool in self._store.list_tools():
            if tool.key in allowed and tool.status == "active":
                try:
                    schema = json.loads(tool.input_schema) if tool.input_schema else {}
                except json.JSONDecodeError:
                    schema = {}
                schemas.append({"type": "function", "function": {
                    "name": tool.key, "description": tool.description,
                    "parameters": schema or {"type": "object", "properties": {}},
                }})
        return schemas

    def cancel_agent_run(self, principal: Dict, run_id: UUID) -> bool:
        self._require(principal, ACTION_CANCEL_CAP)
        run = self._store.get_run(run_id)
        if run is None:
            return False
        cancelled = self._store.update_run_state(
            run_id, status="cancelled", now=self._now(), cancellation="operator cancelled"
        )
        self._emit(principal, "agent.run.cancelled", run_id=run_id,
                   conversation_id=run.conversation_id)
        return cancelled

    def overview(self, principal: Dict) -> Dict:
        """Authoritative Agent Fabric overview for the browser and CLI."""
        self._require(principal, AGENT_READ_CAP)
        agents = [agent_payload(a, self._latest_version_id(a.id))
                  for a in self._store.list_agents(principal["organization"]["id"],
                                                   principal["workspace"]["id"])]
        providers = [provider_payload(p) for p in self._store.list_providers()]
        models = [model_payload(m) for m in self._store.list_models()]
        tools = [tool_payload(t) for t in self._store.list_tools()]
        runs = []
        for agent in agents:
            try:
                runs.extend(self._store.list_runs_for_agent(agent["id"])[:5])
            except Exception:  # noqa: BLE001
                continue
        return {
            "schema_version": 1,
            "agents": {
                "total": len(agents),
                "active": len([a for a in agents if a["status"] == "active"]),
                "items": agents,
            },
            "providers": {
                "total": len(providers),
                "active": len([p for p in providers if p["status"] == "active"]),
                "items": providers,
            },
            "models": {
                "total": len(models),
                "active": len([m for m in models if m["status"] == "active"]),
                "items": models,
            },
            "tools": {
                "total": len(tools),
                "items": [{"key": t["key"], "category": t["category"],
                           "risk": t["risk"], "status": t["status"]} for t in tools],
            },
            "recent_runs": [run_payload(r) for r in runs[:10]],
        }

    def mission(self, principal: Dict) -> Dict:
        """Real-time Agent Mission Control snapshot.

        Structured live view of everything the agent team is doing right now:
        running runs (with agent/model/provider/elapsed), recent completions and
        failures (with duration/tokens/result), and honest aggregate stats.
        This is the authoritative source the Mission Control console consumes.
        """
        self._require(principal, AGENT_READ_CAP)
        now = self._now()
        day_start = now - (now % 86_400_000)  # UTC midnight (ms)
        agents = [agent_payload(a, self._latest_version_id(a.id))
                  for a in self._store.list_agents(principal["organization"]["id"],
                                                   principal["workspace"]["id"])]
        by_agent = {str(a["id"]): a for a in agents}
        providers = {p.id: p for p in self._store.list_providers()}
        models = {m.id: m for m in self._store.list_models()}
        all_runs = []
        for agent in agents:
            try:
                all_runs.extend(self._store.list_runs_for_agent(agent["id"]))
            except Exception:  # noqa: BLE001 - a missing run list must not break the feed
                continue

        def enrich(r) -> Dict:
            provider = providers.get(r.provider_id)
            model = models.get(r.model_id)
            agent = by_agent.get(str(r.agent_id))
            return {
                "id": r.id,
                "agent_id": str(r.agent_id),
                "agent": (agent.get("display_name") if agent else None) or str(r.agent_id)[:8],
                "agent_key": agent.get("key") if agent else None,
                "status": r.status,
                "objective": (getattr(r, "objective", "") or "")[:200],
                "provider": provider.key if provider else str(r.provider_id or "")[:8],
                "model": model.key if model else str(r.model_id or "")[:8],
                "started_at": r.started_at,
                "completed_at": r.completed_at,
                "duration_ms": (r.completed_at - r.started_at)
                    if (r.started_at and r.completed_at) else None,
                "token_usage": r.token_usage,
                "failure": (r.failure or "")[:300],
            }

        running = []
        recent = []
        completed_today = failed_today = tokens_today = 0
        for r in all_runs:
            entry = enrich(r)
            if r.status == "running":
                entry["elapsed_ms"] = now - r.started_at if r.started_at else None
                running.append(entry)
                continue
            recent.append(entry)
            if r.completed_at and r.completed_at >= day_start:
                if r.status == "succeeded":
                    completed_today += 1
                elif r.status == "failed":
                    failed_today += 1
                tokens_today += r.token_usage or 0
        recent.sort(key=lambda e: e["completed_at"] or e["started_at"] or 0, reverse=True)
        return {
            "schema_version": 1,
            "stats": {
                "running_count": len(running),
                "active_agents": len({r["agent_id"] for r in running}),
                "completed_today": completed_today,
                "failed_today": failed_today,
                "tokens_today": tokens_today,
            },
            "running": running[:25],
            "recent": recent[:40],
        }

    def get_agent_run(self, principal: Dict, run_id: UUID) -> Dict:
        self._require(principal, AGENT_READ_CAP)
        run = self._store.get_run(run_id)
        if run is None:
            raise ActionNotFoundError(404, "run_not_found", "The run does not exist.")
        provider = self._store.get_provider(run.provider_id) if run.provider_id else None
        model = self._store.get_model(run.model_id) if run.model_id else None
        return run_payload(run, provider_key=provider.key if provider else None,
                           model_key=model.key if model else None,
                           result=self._store.get_run_output(run.id))

    def list_agent_runs(self, principal: Dict, agent_id: UUID) -> List[Dict]:
        self._require(principal, AGENT_READ_CAP)
        runs = self._store.list_runs_for_agent(agent_id)
        return [run_payload(r) for r in runs]

    def list_run_delegations(self, principal: Dict, run_id: UUID) -> List[Dict]:
        self._require(principal, AGENT_READ_CAP)
        return [run_payload(r) for r in self._store.list_child_runs(run_id)]

    def list_run_tasks(self, principal: Dict, run_id: UUID) -> List[Dict]:
        self._require(principal, AGENT_READ_CAP)
        return [task_payload(t) for t in self._store.list_tasks(run_id)]

    def create_task(self, principal: Dict, *, run_id: UUID, title, objective="",
                    parent_task_id=None, assigned_agent_id=None, dependencies="") -> Dict:
        self._require(principal, AGENT_RUN_CAP)
        task = self._store.create_task(
            id=uuid4(), run_id=run_id, parent_task_id=parent_task_id, title=title,
            objective=objective, assigned_agent_id=assigned_agent_id, dependencies=dependencies,
            now=self._now(),
        )
        self._emit(principal, "agent.task.created", run_id=run_id, task_id=task.id)
        return task_payload(task)

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    def register_tool(self, principal: Dict, *, key, display_name, description, version,
                      category, input_schema, output_schema="", capability_requirements="",
                      risk, side_effect, approval_policy="backend",
                      execution_availability="unavailable", executor_type="none",
                      data_class_limits="restricted", target_constraints="") -> Dict:
        self._require(principal, AGENT_MANAGE_CAP)
        record = self._store.register_tool(
            id=uuid4(), key=key, display_name=display_name, description=description,
            version=version, category=category, input_schema=canonical_json(input_schema),
            output_schema=canonical_json(output_schema) if output_schema else "{}",
            capability_requirements=capability_requirements, risk=risk, side_effect=side_effect,
            approval_policy=approval_policy, execution_availability=execution_availability,
            executor_type=executor_type, data_class_limits=data_class_limits,
            target_constraints=target_constraints, now=self._now(),
        )
        self._emit(principal, "tool.registered", data={"key": key, "risk": risk})
        return tool_payload(record)

    def list_tools(self, principal: Dict) -> List[Dict]:
        self._require(principal, TOOL_READ_CAP)
        return [tool_payload(t) for t in self._store.list_tools()]

    # ------------------------------------------------------------------
    # Action proposals + policy + approvals
    # ------------------------------------------------------------------

    def propose_action(
        self,
        principal: Dict,
        *,
        tool_key: str,
        parameters: Dict,
        target: str,
        conversation_id: Optional[UUID] = None,
        conversation_run_id: Optional[UUID] = None,
        agent_run_id: Optional[UUID] = None,
        task_id: Optional[UUID] = None,
        original_request: str = "",
    ) -> Dict:
        self._require(principal, ACTION_PROPOSE_CAP)
        tool = self._store.get_tool_by_key(tool_key)
        if tool is None or tool.status != "active":
            raise ActionDeniedError(403, "tool_unknown", "The tool is unknown or disabled.")
        self._validate_parameters(tool, parameters, target)
        canonical_params = canonical_json(parameters)
        now = self._now()
        payload_digest = proposal_payload_digest(
            tool_id=tool.id, tool_version=tool.version, action_type=tool.key,
            parameters=canonical_params, canonical_target=target, risk=tool.risk,
            reversibility=tool.side_effect,
        )
        proposal = ActionProposalRecord(
            id=uuid4(), organization_id=principal["organization"]["id"],
            workspace_id=principal["workspace"]["id"], conversation_id=conversation_id,
            conversation_run_id=conversation_run_id, agent_run_id=agent_run_id,
            task_id=task_id, proposer_user_id=principal["user"]["id"],
            proposer_agent_id=None, tool_id=tool.id, tool_version=tool.version,
            action_type=tool.key, parameters=canonical_params, canonical_target=target,
            summary=self._authoritative_summary(tool, target, parameters),
            expected_effect="", reversibility=tool.side_effect, risk=tool.risk,
            required_capabilities=tool.capability_requirements, requested_at=now,
            expires_at=now + self.proposal_ttl_ms, state="proposed",
            proposal_version=1, previous_proposal_id=None, payload_digest=payload_digest,
            policy_snapshot_id=None, trace_id=str(uuid4()), revision=1,
            original_request=original_request[:2000],
        )
        self._store.create_proposal(proposal)
        self._emit(principal, "action.proposed", proposal_id=proposal.id,
                   conversation_id=conversation_id, run_id=agent_run_id, task_id=task_id,
                   data={"tool": tool.key, "risk": tool.risk})
        return self._evaluate_proposal(principal, proposal)

    def _evaluate_proposal(self, principal: Dict, proposal: ActionProposalRecord) -> Dict:
        decision = self._evaluate_policy(principal, proposal)
        self._store.save_policy_decision(decision)
        self._store.transition_proposal(
            proposal.id, "approval_required" if decision.result == "approval_required" else
            ("policy_denied" if decision.result == "deny" else "approved_awaiting_executor"),
            self._now(), policy_snapshot_id=decision.id,
        )
        self._emit(principal, "action.policy_evaluated", proposal_id=proposal.id,
                   data={"result": decision.result, "reason_codes": decision.reason_codes})
        if decision.result == "approval_required":
            request = self._store.create_approval_request(
                id=uuid4(), proposal_id=proposal.id, proposal_digest=proposal.payload_digest,
                policy_decision_id=decision.id,
                required_capability=decision.required_capabilities,
                required_approval_count=decision.required_approval_count,
                separation_of_duties=decision.separation_of_duties,
                step_up_required=decision.step_up_required, now=self._now(),
                expires_at=self._now() + self.approval_ttl_ms,
            )
            self._emit(principal, "action.approval_required", proposal_id=proposal.id,
                       approval_request_id=request.id, data={"risk": proposal.risk})
            self._emit(principal, "approval.requested", proposal_id=proposal.id,
                       approval_request_id=request.id)
            return {
                "proposal": proposal_payload(self._store.get_proposal(proposal.id)),
                "policy_decision": policy_payload(decision),
                "approval_request": approval_request_payload(request),
            }
        if decision.result == "deny":
            self._emit(principal, "action.denied", proposal_id=proposal.id,
                       data={"reason_codes": decision.reason_codes})
        else:
            self._emit(principal, "action.execution_unavailable", proposal_id=proposal.id)
        return {
            "proposal": proposal_payload(self._store.get_proposal(proposal.id)),
            "policy_decision": policy_payload(decision),
        }

    def _evaluate_policy(self, principal: Dict, proposal: ActionProposalRecord) -> PolicyDecisionRecord:
        reason_codes: List[str] = []
        denied = False
        capabilities = set(principal.get("capabilities") or [])
        for required in proposal.required_capabilities.split(","):
            if required and required.strip() not in capabilities:
                reason_codes.append(CAPABILITY_REQUIRED_CAP + ":" + required.strip())
                denied = True
        if principal.get("user", {}).get("status") not in (None, "active"):
            reason_codes.append("user.not_active")
            denied = True
        if proposal.workspace_id != principal["workspace"]["id"]:
            reason_codes.append("workspace.mismatch")
            denied = True
        if denied:
            return self._decision(proposal, "deny", reason_codes, required_count=0,
                                  separation=False, step_up="none", expiration=proposal.expires_at)
        risk = proposal.risk
        side_effect = proposal.reversibility
        if risk in ("informational", "low") and side_effect in ("none", "local_ephemeral"):
            return self._decision(proposal, "allow_read_only", ["policy.low_risk_read_only"],
                                  required_count=0, separation=False, step_up="none",
                                  expiration=proposal.expires_at)
        # Medium/high/critical and any persistent/external/financial/destructive
        # side effect requires explicit human approval.
        required_capability = APPROVAL_DECIDE.get(risk, "approval.decide.medium")
        step_up = "approval_key" if risk in ("high", "critical") else "session"
        separation = True
        return self._decision(
            proposal, "approval_required",
            ["policy.approval_required:%s" % risk],
            required_count=1, separation=separation, step_up=step_up,
            expiration=proposal.expires_at, required_capability=required_capability,
        )

    def _decision(self, proposal, result, reason_codes, *, required_count,
                  separation, step_up, expiration, required_capability="") -> PolicyDecisionRecord:
        policy_snapshot = canonical_json({
            "version": self.policy_version,
            "proposal_risk": proposal.risk,
            "proposal_reversibility": proposal.reversibility,
            "required_capability": required_capability,
        })
        return PolicyDecisionRecord(
            id=uuid4(), proposal_id=proposal.id, result=result,
            reason_codes=",".join(reason_codes),
            explanation=",".join(reason_codes), required_capabilities=required_capability,
            required_approval_count=required_count, separation_of_duties=separation,
            step_up_required=step_up, expiration=expiration,
            policy_version=self.policy_version, policy_snapshot=policy_snapshot,
            policy_digest=sha256_hex(policy_snapshot), evaluated_at=self._now(), revision=1,
        )

    def get_proposal(self, principal: Dict, proposal_id: UUID) -> Dict:
        self._require(principal, ACTION_READ_CAP)
        proposal = self._store.get_proposal(proposal_id)
        if proposal is None:
            raise ActionNotFoundError(404, "proposal_not_found", "The proposal does not exist.")
        if proposal.workspace_id != principal["workspace"]["id"]:
            raise ActionDeniedError(403, "cross_workspace_denied", "Cross-workspace proposal access is denied.")
        return proposal_payload(proposal)

    def get_policy_decision(self, principal: Dict, decision_id: UUID) -> Dict:
        self._require(principal, POLICY_READ_CAP)
        decision = self._store.get_policy_decision(decision_id)
        if decision is None:
            raise ActionNotFoundError(404, "decision_not_found", "The policy decision does not exist.")
        return policy_payload(decision)

    def list_proposals(self, principal: Dict, state: Optional[str] = None) -> List[Dict]:
        self._require(principal, ACTION_READ_CAP)
        return [proposal_payload(p) for p in self._store.list_proposals(principal["workspace"]["id"], state)]

    def list_approvals(self, principal: Dict) -> List[Dict]:
        self._require(principal, APPROVAL_READ_CAP)
        result = []
        for request in self._store.list_approval_requests(principal["workspace"]["id"]):
            result.append({
                "approval_request": approval_request_payload(request),
                "proposal": proposal_payload(self._store.get_proposal(request.proposal_id)) if self._store.get_proposal(request.proposal_id) else None,
                "decisions": [approval_decision_payload(d) for d in self._store.approval_decisions_for_request(request.id)],
            })
        return result

    def get_approval(self, principal: Dict, approval_id: UUID) -> Dict:
        self._require(principal, APPROVAL_READ_CAP)
        request = self._store.get_approval_request(approval_id)
        if request is None:
            raise ActionNotFoundError(404, "approval_not_found", "The approval request does not exist.")
        proposal = self._store.get_proposal(request.proposal_id)
        if proposal is None or proposal.workspace_id != principal["workspace"]["id"]:
            raise ActionDeniedError(403, "cross_workspace_denied", "Cross-workspace approval access is denied.")
        return {
            "approval_request": approval_request_payload(request),
            "proposal": proposal_payload(proposal),
            "policy_decision": policy_payload(self._store.get_policy_decision(request.policy_decision_id)) if self._store.get_policy_decision(request.policy_decision_id) else None,
            "decisions": [approval_decision_payload(d) for d in self._store.approval_decisions_for_request(request.id)],
        }

    # ------------------------------------------------------------------
    # Approvals + challenge
    # ------------------------------------------------------------------

    def create_approval_challenge(
        self, principal: Dict, *, proposal_id: UUID, approval_request_id: UUID,
        policy_decision_id: UUID, requested_decision: str, approver_device_id: UUID,
    ) -> Dict:
        self._require(principal, "approval.decide.low")
        proposal = self._store.get_proposal(proposal_id)
        if proposal is None:
            raise ActionNotFoundError(404, "proposal_not_found", "The proposal does not exist.")
        request = self._store.get_approval_request(approval_request_id)
        if request is None or request.proposal_id != proposal_id:
            raise ActionDeniedError(403, "approval_request_mismatch", "The approval request does not match.")
        if request.step_up_required != "approval_key":
            raise ActionDeniedError(403, "step_up_not_required", "This approval does not require a challenge.")
        challenge_id = uuid4()
        nonce = str(uuid4())
        expires_at = self._now() + self.challenge_ttl_ms
        message = APPROVAL_DOMAIN.format(
            challenge_id=str(challenge_id), proposal_id=str(proposal_id),
            proposal_digest=proposal.payload_digest, policy_decision_id=str(policy_decision_id),
            approval_request_id=str(approval_request_id),
            approver_user_id=str(principal["user"]["id"]),
            approver_device_id=str(approver_device_id),
            organization_id=str(principal["organization"]["id"]),
            workspace_id=str(principal["workspace"]["id"]),
            requested_decision=requested_decision, risk=proposal.risk,
            nonce=nonce, expires_at=expires_at,
        )
        challenge = ApprovalChallengeRecord(
            id=challenge_id, proposal_id=proposal_id, proposal_digest=proposal.payload_digest,
            policy_decision_id=policy_decision_id, approval_request_id=approval_request_id,
            approver_user_id=principal["user"]["id"], approver_device_id=approver_device_id,
            organization_id=principal["organization"]["id"],
            workspace_id=principal["workspace"]["id"],
            requested_decision=requested_decision, risk=proposal.risk, nonce=nonce,
            issued_at=self._now(), expires_at=expires_at, state="open", signed_message=message,
        )
        self._store.create_approval_challenge(challenge)
        return {"challenge_id": challenge_id, "message": message, "expires_at": expires_at}

    def submit_approval_decision(
        self,
        principal: Dict,
        *,
        proposal_id: UUID,
        approval_request_id: UUID,
        decision: str,
        reason: str = "",
        signature_b64url: Optional[str] = None,
        challenge_id: Optional[UUID] = None,
        approver_device_id: Optional[UUID] = None,
        auth_strength: str = "session",
        step_up_evidence: str = "",
    ) -> Dict:
        self._require(principal, "approval.decide.low")
        proposal = self._store.get_proposal(proposal_id)
        request = self._store.get_approval_request(approval_request_id)
        if proposal is None or request is None:
            raise ActionDeniedError(403, "approval_not_found", "The approval is not found.")
        if request.proposal_digest != proposal.payload_digest:
            raise ActionDeniedError(409, "digest_changed", "The proposal changed; a new approval is required.")
        if request.expires_at <= self._now() or request.status != "pending":
            raise ActionDeniedError(403, "approval_not_active", "The approval is expired or not pending.")
        if proposal.workspace_id != principal["workspace"]["id"]:
            raise ActionDeniedError(403, "cross_workspace_denied", "Cross-workspace approval is denied.")
        required = request.required_capability
        if required and required not in (principal.get("capabilities") or []):
            raise ActionDeniedError(403, "capability.denied", "The approver lacks the required capability.")
        if request.separation_of_duties and decision == "approve" and proposal.proposer_user_id == principal["user"]["id"]:
            raise ActionDeniedError(403, "self_approval_denied", "Self-approval is denied.")
        if request.step_up_required == "approval_key":
            if signature_b64url is None or challenge_id is None or approver_device_id is None:
                raise ActionDeniedError(403, "challenge_required", "An approval challenge signature is required.")
            self._verify_approval_challenge(
                principal, proposal, request, challenge_id, signature_b64url,
                approver_device_id, decision, auth_strength,
            )
            auth_strength = "approval_key"
            step_up_evidence = step_up_evidence or str(challenge_id)
        decision_digest = sha256_hex(canonical_json({
            "request_id": str(approval_request_id), "proposal_id": str(proposal_id),
            "proposal_digest": proposal.payload_digest, "decision": decision,
            "approver": str(principal["user"]["id"]), "decided_at": self._now(),
        }))
        record = ApprovalDecisionRecord(
            id=uuid4(), approval_request_id=approval_request_id, proposal_id=proposal_id,
            proposal_digest=proposal.payload_digest, decision=decision,
            approver_user_id=principal["user"]["id"],
            approver_device_id=approver_device_id or principal.get("device_id") or UUID(int=0),
            approver_session_id=principal.get("session_id") or UUID(int=0),
            approver_organization_id=principal["organization"]["id"],
            approver_workspace_id=principal["workspace"]["id"],
            auth_strength=auth_strength, step_up_evidence=step_up_evidence,
            reason=reason[:240], decided_at=self._now(), revocation_state="none",
            decision_digest=decision_digest, revision=1,
        )
        self._store.record_approval_decision(record)
        self._emit(principal, "approval.decision_recorded", proposal_id=proposal_id,
                   approval_request_id=approval_request_id,
                   data={"decision": decision, "auth_strength": auth_strength})
        approvals = self._store.approval_decisions_for_request(approval_request_id)
        distinct_approvers = {d.approver_user_id for d in approvals}
        if decision == "deny":
            self._store.update_approval_request_status(approval_request_id, "denied", self._now())
            self._store.transition_proposal(proposal_id, "denied", self._now())
            self._emit(principal, "action.denied", proposal_id=proposal_id,
                       approval_request_id=approval_request_id)
        elif len(distinct_approvers) >= request.required_approval_count:
            self._store.update_approval_request_status(approval_request_id, "approved", self._now())
            self._store.transition_proposal(proposal_id, "approved_awaiting_executor", self._now())
            self._emit(principal, "action.approved", proposal_id=proposal_id,
                       approval_request_id=approval_request_id)
            self._emit(principal, "action.execution_unavailable", proposal_id=proposal_id)
        return {"approval_request": approval_request_payload(request),
                "proposal": proposal_payload(self._store.get_proposal(proposal_id))}  # type: ignore[arg-type]

    def _verify_approval_challenge(self, principal, proposal, request, challenge_id,
                                   signature_b64url, approver_device_id, decision, auth_strength) -> None:
        challenge = self._store.get_approval_challenge(challenge_id)
        if challenge is None or challenge.state != "open":
            raise ActionDeniedError(403, "challenge_not_open", "The approval challenge is not open.")
        if challenge.expires_at <= self._now():
            self._store.update_approval_challenge_state(challenge_id, "expired", self._now())
            raise ActionDeniedError(403, "challenge_expired", "The approval challenge has expired.")
        if challenge.proposal_id != proposal.id or challenge.proposal_digest != proposal.payload_digest:
            raise ActionDeniedError(409, "digest_changed", "The proposal changed; a new challenge is required.")
        if challenge.approval_request_id != request.id:
            raise ActionDeniedError(403, "approval_mismatch", "The challenge does not match the approval.")
        if challenge.approver_user_id != principal["user"]["id"]:
            raise ActionDeniedError(403, "approver_mismatch", "The challenge is not bound to this approver.")
        if challenge.workspace_id != principal["workspace"]["id"]:
            raise ActionDeniedError(403, "cross_workspace_denied", "Cross-workspace approval is denied.")
        if challenge.requested_decision != decision:
            raise ActionDeniedError(403, "decision_mismatch", "The signed decision does not match.")
        from server.identity.repository import SQLiteDeviceIdentityRepository
        device_repo = getattr(self, "_device_repository", None)
        if device_repo is None:
            raise ActionError(500, "device_repository_unavailable", "The device repository is unavailable.")
        device = device_repo.get_device(approver_device_id)
        if device is None or device.state == "revoked":
            raise ActionDeniedError(403, "device_revoked", "The approval device is revoked.")
        try:
            verify_p256_signature(
                device.approval_public_key, challenge.signed_message.encode("ascii"), signature_b64url
            )
        except Exception as error:  # noqa: BLE001
            raise ActionDeniedError(403, "signature_invalid", "The approval signature is invalid.") from error
        self._store.update_approval_challenge_state(challenge_id, "solved", self._now())

    def revoke_proposal(self, principal: Dict, proposal_id: UUID) -> bool:
        self._require(principal, ACTION_CANCEL_CAP)
        proposal = self._store.get_proposal(proposal_id)
        if proposal is None:
            return False
        self._store.invalidate_approval_requests_for_proposal(proposal_id, "revoked", self._now())
        transitioned = self._store.transition_proposal(proposal_id, "revoked", self._now())
        self._emit(principal, "action.revoked", proposal_id=proposal_id)
        return transitioned

    def expire_proposals(self) -> int:
        now = self._now()
        expired = 0
        for ws in self._store.list_workspace_ids():
            for proposal in self._store.list_proposals(ws):
                if proposal.state in ("proposed", "approval_required", "validating"):
                    if proposal.expires_at <= now:
                        self._store.invalidate_approval_requests_for_proposal(proposal.id, "expired", now)
                        if self._store.transition_proposal(proposal.id, "expired", now):
                            expired += 1
        return expired

    # ------------------------------------------------------------------
    # Council
    # ------------------------------------------------------------------

    def create_council(self, principal: Dict, *, name, purpose="", member_agent_ids,
                       chair_agent_id=None, quorum_rule="majority", maximum_rounds=1,
                       disagreement_policy="record", output_schema="") -> Dict:
        self._require(principal, AGENT_MANAGE_CAP)
        definition = self._store.create_council_definition(
            id=uuid4(), organization_id=principal["organization"]["id"],
            workspace_id=principal["workspace"]["id"], name=name, purpose=purpose,
            member_agents=",".join(str(a) for a in member_agent_ids),
            chair_agent=chair_agent_id, quorum_rule=quorum_rule,
            maximum_rounds=maximum_rounds, disagreement_policy=disagreement_policy,
            output_schema=output_schema, now=self._now(),
        )
        self._emit(principal, "council.created", data={"council": str(definition.id)})
        return council_payload(definition)

    async def run_council(self, principal: Dict, *, council_id: UUID, objective: str,
                          conversation_id: Optional[UUID] = None, message_id: Optional[UUID] = None) -> Dict:
        self._require(principal, AGENT_RUN_CAP)
        definition = self._store.get_council_definition(council_id)
        if definition is None or definition.status != "active":
            raise ActionDeniedError(403, "council_disabled", "The council is disabled or unknown.")
        if definition.workspace_id != principal["workspace"]["id"]:
            raise ActionDeniedError(403, "cross_workspace_denied", "Cross-workspace council access is denied.")
        snapshot = council_payload(definition)
        run = self._store.create_council_run(
            id=uuid4(), council_definition_id=council_id, conversation_id=conversation_id,
            message_id=message_id, council_snapshot=canonical_json(_json_safe(snapshot)),
            trace_id=str(uuid4()), now=self._now(),
        )
        self._emit(principal, "council.started", conversation_id=conversation_id,
                   data={"council_run": str(run.id)})
        member_ids = [a for a in definition.member_agents.split(",") if a]
        member_run_ids: List[str] = []
        results: List[str] = []
        failed = 0
        for index, agent_id in enumerate(member_ids):
            member_run_id = uuid4()
            self._store.create_council_member_run(
                id=member_run_id, council_run_id=run.id, agent_id=UUID(agent_id),
                objective=objective, now=self._now(),
            )
            member_run_ids.append(str(member_run_id))
            try:
                member_result = await self._execute_council_member(
                    principal, member_run_id, UUID(agent_id), objective
                )
                results.append(member_result.get("content", ""))
                self._store.update_council_member_run(
                    member_run_id, status="succeeded", result=(member_result.get("content", ""))[:32000],
                    now=self._now(),
                )
                self._emit(principal, "council.member_completed",
                           data={"council_run": str(run.id), "member": agent_id,
                                 "member_run": str(member_run_id)})
            except Exception:  # noqa: BLE001
                failed += 1
                self._store.update_council_member_run(
                    member_run_id, status="failed", failure="member_failed", now=self._now(),
                )
        quorum = 1 if quorum_ok(definition.quorum_rule, len(member_ids), failed) else 0
        if quorum:
            recommendation = "\n".join(results)
            state = "completed"
        else:
            recommendation = ""
            state = "failed"
        self._store.update_council_run(
            run.id, state=state, member_run_ids=",".join(member_run_ids),
            rounds=definition.maximum_rounds, final_recommendation=recommendation,
            dissents="", proposed_action_ids="", now=self._now(),
        )
        self._emit(principal, "council.completed" if quorum else "council.failed",
                   data={"council_run": str(run.id)})
        return council_run_payload(self._store.get_council_run(run.id))  # type: ignore[arg-type]

    async def _execute_council_member(self, principal: Dict, member_run_id: UUID,
                                      agent_id: UUID, objective: str) -> Dict:
        """Run one real council member AgentRun and return its structured output."""
        member_run = self.start_agent_run(
            principal, agent_id=agent_id,
            conversation_id=member_run_id, message_id=member_run_id,
            objective=objective, delegation_depth=1,
        )
        self._store.update_council_member_run(
            member_run_id, status="running", now=self._now(),
            agent_version_id=UUID(str(member_run["agent_version_id"])),
            provider_id=UUID(str(member_run["provider_id"])) if member_run.get("provider_id") else None,
            model_id=UUID(str(member_run["model_id"])) if member_run.get("model_id") else None,
        )
        executed = await self.execute_agent_run(principal, member_run["id"])
        if executed["status"] != "succeeded":
            raise RuntimeError("council member run failed")
        return {"content": executed.get("result", "")}

    def get_council_run(self, principal: Dict, run_id: UUID) -> Dict:
        self._require(principal, AGENT_READ_CAP)
        run = self._store.get_council_run(run_id)
        if run is None:
            raise ActionNotFoundError(404, "council_run_not_found", "The council run does not exist.")
        return council_run_payload(run)

    def list_councils(self, principal: Dict) -> List[Dict]:
        self._require(principal, AGENT_READ_CAP)
        return [council_payload(c) for c in self._store.list_councils(principal["organization"]["id"])]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _select_provider_model(self, principal: Dict, agent, model_preference: Optional[str]):
        """Backend-authoritative provider/model selection. Local-only data never
        routes to a non-local provider. Disabled providers/models are rejected.

        Model binding order (highest authority first):
          1. explicit caller model_preference (validated against the registry)
          2. the agent's configured default_model_policy (an installed model key)
          3. the first active model of the selected provider (never fabricated)."""
        providers = self._store.list_providers()
        available = [p for p in providers if p.status == "active"]
        if not available:
            raise ActionDeniedError(503, "provider_unavailable", "No active provider is available.")
        all_models = self._store.list_models()
        preferred = next((m for m in all_models if m.key == model_preference), None)
        if model_preference is not None and preferred is None:
            raise ActionDeniedError(403, "model_disabled", "The requested model is unknown or disabled.")
        if preferred is None and agent.default_model_policy and agent.default_model_policy != "backend":
            preferred = next((m for m in all_models if m.key == agent.default_model_policy), None)
        local_only = agent.data_boundary == "restricted"
        if local_only:
            available = [p for p in available if p.location == "local"]
            if not available:
                raise ActionDeniedError(503, "provider_unavailable", "No local provider is available.")
        provider_key = getattr(agent, "default_provider_policy", "backend")
        provider = preferred.provider_id if preferred else available[0]
        if provider_key and provider_key != "backend":
            keyed = next((p for p in available if p.key == provider_key), None)
            if keyed is not None:
                provider = keyed.id
        provider_record = next((p for p in available if p.id == provider), available[0])
        models = [m for m in self._store.list_models(provider_record.id) if m.status == "active"]
        if not models:
            raise ActionDeniedError(503, "model_unavailable", "No active model is available for the provider.")
        if preferred is not None and preferred.provider_id != provider_record.id:
            # A cross-provider model binding falls back to the provider's own
            # active inventory; the used model is reported, never fabricated.
            preferred = None
        model = preferred if preferred and preferred.status == "active" else models[0]
        return provider_record, model

    def _validate_parameters(self, tool, parameters: Dict, target: str) -> None:
        if not isinstance(parameters, dict):
            raise ActionDeniedError(400, "malformed_parameters", "Parameters must be an object.")
        schema = json.loads(tool.input_schema) if tool.input_schema else {}
        allowed = set(schema.get("properties", {}).keys())
        extra = set(parameters.keys()) - allowed
        if extra and not schema.get("additionalProperties"):
            raise ActionDeniedError(400, "undeclared_parameter",
                                    "Undeclared parameter(s): %s" % ",".join(sorted(extra)))
        for key, value in parameters.items():
            if isinstance(value, str):
                for pattern in DANGEROUS_PARAMETER_PATTERNS:
                    if pattern.search(value):
                        raise ActionDeniedError(400, "unsafe_parameter", "Parameter %s is unsafe." % key)
        if not target or len(target) > 512:
            raise ActionDeniedError(400, "invalid_target", "The target is missing or too long.")
        if ".." in target or target.startswith("/") or ("://" in target and not target.startswith("file:")):
            raise ActionDeniedError(400, "invalid_target", "The target form is unsupported.")

    @staticmethod
    def _authoritative_summary(tool, target: str, parameters: Dict) -> str:
        param_tail = ",".join(sorted(parameters.keys())[:5])
        return "%s(%s) on %s" % (tool.key, param_tail, target)

    def _latest_version_id(self, agent_id: UUID) -> UUID:
        versions = self._store.list_agent_versions(agent_id)
        return versions[-1].version_id if versions else UUID(int=0)

    def _require(self, principal: Dict, capability: str) -> None:
        if capability not in (principal.get("capabilities") or []):
            raise ActionCapabilityError(403, CAPABILITY_DENIED_CAP,
                                        "This principal is not granted the %s capability." % capability)

    def _emit(self, principal: Dict, event: str, **kwargs) -> None:
        kwargs.setdefault("organization_id", principal["organization"]["id"])
        kwargs.setdefault("workspace_id", principal["workspace"]["id"])
        self._events.emit(event, **kwargs)


def _normalize_agent_error(error: Exception) -> str:
    """Map an executor exception to a bounded, non-leaking error code."""
    text = str(error)
    lowered = text.lower()
    if "not reachable" in lowered or "connect" in lowered or "timeout" in lowered:
        return "OLLAMA_UNAVAILABLE"
    if "model not found" in lowered or "not found" in lowered and "model" in lowered:
        return "MODEL_NOT_FOUND"
    if "load" in lowered or "loading" in lowered:
        return "MODEL_LOADING"
    if "timeout" in lowered:
        return "MODEL_TIMEOUT"
    if "json" in lowered or "invalid" in lowered:
        return "STRUCTURED_OUTPUT_INVALID"
    return type(error).__name__[:60] or "AGENT_RUN_FAILED"


def quorum_ok(rule: str, total: int, failed: int) -> bool:
    if total == 0:
        return False
    succeeded = total - failed
    if rule == "unanimous":
        return succeeded == total and total >= 1
    return succeeded >= (total // 2 + 1)


def _json_safe(value):
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, UUID):
        return str(value)
    return value


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------


def provider_payload(r) -> Dict:
    return {"id": r.id, "key": r.key, "display_name": r.display_name,
            "provider_type": r.provider_type, "location": r.location,
            "transport": r.transport, "endpoint_reference": r.endpoint_reference,
            "auth_reference_type": r.auth_reference_type, "status": r.status,
            "health": r.health, "streaming": r.streaming, "tool_calling": r.tool_calling,
            "structured_output": r.structured_output, "context_window": r.context_window,
            "privacy_class": r.privacy_class, "allowed_data_classes": r.allowed_data_classes,
            "revision": r.revision}


def model_payload(r) -> Dict:
    return {"id": r.id, "provider_id": r.provider_id, "key": r.key,
            "display_name": r.display_name, "model_identifier": r.model_identifier,
            "status": r.status, "capabilities": r.capabilities,
            "context_limit": r.context_limit, "output_limit": r.output_limit,
            "streaming": r.streaming, "structured_output": r.structured_output,
            "tool_calling": r.tool_calling, "vision": r.vision, "reasoning": r.reasoning,
            "privacy_class": r.privacy_class, "allowed_data_classes": r.allowed_data_classes,
            "cost_note": r.cost_note, "revision": r.revision}


def agent_payload(r, version_id) -> Dict:
    return {"id": r.id, "organization_id": r.organization_id,
            "workspace_id": r.workspace_id, "key": r.key, "display_name": r.display_name,
            "description": r.description, "purpose": r.purpose, "status": r.status,
            "system_instructions": r.system_instructions,
            "instruction_version": r.instruction_version, "allowed_tools": r.allowed_tools,
            "denied_tools": r.denied_tools, "required_capabilities": r.required_capabilities,
            "max_delegation_depth": r.max_delegation_depth,
            "max_parallel_tasks": r.max_parallel_tasks, "max_runtime_ms": r.max_runtime_ms,
            "max_token_budget": r.max_token_budget, "data_boundary": r.data_boundary,
            "memory_policy": r.memory_policy, "approval_policy": r.approval_policy,
            "default_provider_policy": r.default_provider_policy,
            "default_model_policy": r.default_model_policy,
            "revision": r.revision, "latest_version_id": version_id}


def run_payload(r, provider_key=None, model_key=None, result=None) -> Dict:
    return {"id": r.id, "conversation_id": r.conversation_id, "message_id": r.message_id,
            "agent_id": r.agent_id, "agent_version_id": r.agent_version_id,
            "provider_id": r.provider_id, "model_id": r.model_id, "status": r.status,
            "parent_run_id": r.parent_run_id, "delegation_depth": r.delegation_depth,
            "requested_by": r.requested_by, "objective": getattr(r, "objective", "") or "",
            "started_at": r.started_at,
            "completed_at": r.completed_at, "cancellation": r.cancellation,
            "failure": r.failure, "result": result if result is not None else (getattr(r, "result", "") or ""),
            "token_usage": r.token_usage, "trace_id": r.trace_id,
            "provider_key": provider_key, "model_key": model_key, "revision": r.revision}


def task_payload(r) -> Dict:
    return {"id": r.id, "run_id": r.run_id, "parent_task_id": r.parent_task_id,
            "title": r.title, "objective": r.objective, "state": r.state,
            "assigned_agent_id": r.assigned_agent_id, "dependencies": r.dependencies,
            "output_reference": r.output_reference, "failure": r.failure,
            "created_at": r.created_at, "started_at": r.started_at,
            "completed_at": r.completed_at, "revision": r.revision}


def tool_payload(r) -> Dict:
    return {"id": r.id, "key": r.key, "display_name": r.display_name,
            "description": r.description, "version": r.version, "category": r.category,
            "input_schema": r.input_schema, "output_schema": r.output_schema,
            "capability_requirements": r.capability_requirements, "risk": r.risk,
            "side_effect": r.side_effect, "approval_policy": r.approval_policy,
            "execution_availability": r.execution_availability,
            "executor_type": r.executor_type, "data_class_limits": r.data_class_limits,
            "target_constraints": r.target_constraints, "status": r.status,
            "revision": r.revision}


def proposal_payload(r) -> Dict:
    return {"id": r.id, "organization_id": r.organization_id,
            "workspace_id": r.workspace_id, "conversation_id": r.conversation_id,
            "conversation_run_id": r.conversation_run_id, "agent_run_id": r.agent_run_id,
            "task_id": r.task_id, "proposer_user_id": r.proposer_user_id,
            "proposer_agent_id": r.proposer_agent_id, "tool_id": r.tool_id,
            "tool_version": r.tool_version, "action_type": r.action_type,
            "parameters": r.parameters, "canonical_target": r.canonical_target,
            "summary": r.summary, "expected_effect": r.expected_effect,
            "reversibility": r.reversibility, "risk": r.risk,
            "required_capabilities": r.required_capabilities, "requested_at": r.requested_at,
            "expires_at": r.expires_at, "state": r.state,
            "proposal_version": r.proposal_version,
            "previous_proposal_id": r.previous_proposal_id,
            "payload_digest": r.payload_digest,
            "policy_snapshot_id": r.policy_snapshot_id, "trace_id": r.trace_id,
            "revision": r.revision}


def policy_payload(r) -> Dict:
    return {"id": r.id, "proposal_id": r.proposal_id, "result": r.result,
            "reason_codes": r.reason_codes, "explanation": r.explanation,
            "required_capabilities": r.required_capabilities,
            "required_approval_count": r.required_approval_count,
            "separation_of_duties": r.separation_of_duties,
            "step_up_required": r.step_up_required, "expiration": r.expiration,
            "policy_version": r.policy_version, "policy_snapshot": r.policy_snapshot,
            "policy_digest": r.policy_digest, "evaluated_at": r.evaluated_at,
            "revision": r.revision}


def approval_request_payload(r) -> Dict:
    return {"id": r.id, "proposal_id": r.proposal_id, "proposal_digest": r.proposal_digest,
            "policy_decision_id": r.policy_decision_id,
            "required_capability": r.required_capability,
            "required_approval_count": r.required_approval_count,
            "separation_of_duties": r.separation_of_duties,
            "step_up_required": r.step_up_required, "status": r.status,
            "created_at": r.created_at, "expires_at": r.expires_at, "revision": r.revision}


def approval_decision_payload(r) -> Dict:
    return {"id": r.id, "approval_request_id": r.approval_request_id,
            "proposal_id": r.proposal_id, "proposal_digest": r.proposal_digest,
            "decision": r.decision, "approver_user_id": r.approver_user_id,
            "approver_device_id": r.approver_device_id,
            "approver_session_id": r.approver_session_id,
            "approver_organization_id": r.approver_organization_id,
            "approver_workspace_id": r.approver_workspace_id,
            "auth_strength": r.auth_strength, "step_up_evidence": r.step_up_evidence,
            "reason": r.reason, "decided_at": r.decided_at,
            "revocation_state": r.revocation_state, "decision_digest": r.decision_digest,
            "revision": r.revision}


def council_payload(r) -> Dict:
    return {"id": r.id, "organization_id": r.organization_id,
            "workspace_id": r.workspace_id, "name": r.name, "purpose": r.purpose,
            "member_agents": r.member_agents, "chair_agent": r.chair_agent,
            "quorum_rule": r.quorum_rule, "maximum_rounds": r.maximum_rounds,
            "disagreement_policy": r.disagreement_policy, "output_schema": r.output_schema,
            "status": r.status, "revision": r.revision}


def council_run_payload(r) -> Dict:
    return {"id": r.id, "conversation_id": r.conversation_id, "message_id": r.message_id,
            "council_definition_id": r.council_definition_id,
            "council_snapshot": r.council_snapshot, "state": r.state,
            "member_run_ids": r.member_run_ids, "rounds": r.rounds,
            "final_recommendation": r.final_recommendation, "dissents": r.dissents,
            "proposed_action_ids": r.proposed_action_ids, "created_at": r.created_at,
            "completed_at": r.completed_at, "trace_id": r.trace_id, "revision": r.revision}
