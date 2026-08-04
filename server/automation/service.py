"""AutomationService facade: one authoritative entry point into the JoeOS
Automation and Workflow Platform.

Composes the Workflow Registry, Validator, Compiler, Execution Engine, Trigger
Registry, Schedule Service, Action Registry, Secret Broker, permission guard,
idempotency/dedup/concurrency/locks/rate limits, history, health, templates,
dry run, simulation, and test runner. All services share one SQLite database.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .actions import ActionError, ActionRegistry, default_handlers
from .compiler import CompiledPlan, compile_workflow, validate_definition
from .execution import ExecutionEngine, _InjectedTraceSink
from .history import RunHistory, WorkflowHealthService
from .models import (
    ApprovalRequest,
    RunRecord,
    ScheduleRecord,
    UserInputRequest,
    WorkflowDefinition,
    WorkflowOverview,
    WorkflowRecord,
)
from .permissions import WorkflowPermissionGuard
from .safety import (
    ConcurrencyGovernor,
    ConcurrencyLimit,
    IdempotencyService,
    RateLimiter,
    ResourceLockManager,
)
from .schedules import ScheduleError, ScheduleService
from .secrets import WorkflowSecretBroker
from .storage import AutomationStorage
from .triggers import TriggerRegistry
from .workflows import WorkflowError, WorkflowRegistry, parse_definition


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AutomationService:
    def __init__(
        self,
        data_dir: str,
        *,
        master_key: bytes,
        joeos_version: str = "2.0.0",
        event_sink=None,
        memory_proposer=None,
        agent_api=None,
        git_reader=None,
        communications=None,
        security_gate=None,
        governance_blocked=None,
        approval_resolver=None,
        input_resolver=None,
    ) -> None:
        self.storage = AutomationStorage(data_dir)
        self.storage.prepare()
        self._data_dir = Path(data_dir)
        self._joeos_version = joeos_version
        self._event_sink = event_sink
        self.communications = communications
        self.security_gate = security_gate
        self._governance_blocked = governance_blocked or (lambda: (False, ""))

        self.workflows = WorkflowRegistry(self._connection_factory)
        self.permissions = WorkflowPermissionGuard(self._connection_factory)
        self.history = RunHistory(self._connection_factory)
        self.health_service = WorkflowHealthService(self._connection_factory)
        self.schedules = ScheduleService(self._connection_factory)
        self.triggers = TriggerRegistry(self._connection_factory)
        self.secrets = WorkflowSecretBroker(self._connection_factory, master_key)
        self.idempotency = IdempotencyService(self._connection_factory)
        self.concurrency = ConcurrencyGovernor(self._connection_factory)
        self.locks = ResourceLockManager(self._connection_factory)
        self.rate_limiter = RateLimiter()

        self.actions = ActionRegistry(
            default_handlers(
                event_sink=event_sink,
                memory_proposer=memory_proposer,
                agent_api=agent_api,
                git_reader=git_reader,
                communications=communications,
            )
        )
        trace_sink = _InjectedTraceSink(self.history)
        self.engine = ExecutionEngine(
            connection_factory=self._connection_factory,
            actions=self.actions,
            permissions=self.permissions,
            secrets=self.secrets,
            event_sink=event_sink,
            trace_sink=trace_sink,
            approval_resolver=approval_resolver,
            input_resolver=input_resolver,
            security_gate=security_gate,
        )

    def _connection_factory(self):
        connection = self.storage.connect()
        return _BorrowedConnection(connection)

    # ------------------------------------------------------------------
    # Workflow registry
    # ------------------------------------------------------------------

    def create_workflow(self, definition: WorkflowDefinition, *, creator: str = "user") -> WorkflowRecord:
        validate_definition(definition)
        if self.workflows.get_record(definition.workflow_id) is not None:
            raise WorkflowError("workflow already exists.")
        record = self.workflows.create(definition, creator=creator)
        self._sync_triggers(definition)
        return record

    def update_workflow(self, definition: WorkflowDefinition, *, creator: str = "user") -> WorkflowRecord:
        validate_definition(definition)
        record = self.workflows.update(definition, creator=creator)
        self._sync_triggers(definition)
        self._refresh_health(definition.workflow_id)
        return record

    def get_workflow(self, workflow_id: str) -> Optional[WorkflowRecord]:
        return self.workflows.get_record(workflow_id)

    def list_workflows(self) -> Tuple[WorkflowRecord, ...]:
        return self.workflows.list_records()

    def enable_workflow(self, workflow_id: str) -> WorkflowRecord:
        blocked, reason = self._governance_blocked()
        if blocked:
            raise WorkflowError("governance: %s" % reason)
        record = self.require_workflow(workflow_id)
        if record.definition.status in {"invalid", "quarantined"}:
            raise WorkflowError("cannot enable a %s workflow." % record.definition.status)
        validate_definition(record.definition)
        # Ensure declared permissions are granted before enabling.
        try:
            self.permissions.verify_declared(
                workflow_id=workflow_id, definition_required=record.definition.required_permissions
            )
        except ValueError as exc:
            raise WorkflowError(str(exc)) from exc
        self._verify_secret_availability(record)
        return self.workflows.set_state(workflow_id, status="enabled", enabled=True)

    def disable_workflow(self, workflow_id: str) -> WorkflowRecord:
        return self.workflows.set_state(workflow_id, status="disabled", enabled=False)

    def pause_workflow(self, workflow_id: str) -> WorkflowRecord:
        return self.workflows.set_state(workflow_id, status="paused", enabled=False)

    def _verify_secret_availability(self, record: WorkflowRecord) -> None:
        if not record.definition.secrets:
            return
        availability = self.secrets.availability(
            workflow_id=record.workflow_id,
            required=tuple(record.definition.secrets),
        )
        missing = [name for name, ok in availability.items() if not ok]
        if missing:
            raise WorkflowError("required secrets are unavailable: %s" % ", ".join(missing))

    # ------------------------------------------------------------------
    # Scheduling / triggers
    # ------------------------------------------------------------------

    def schedule_workflow(
        self,
        *,
        workflow_id: str,
        recurrence,
        timezone: str = "UTC",
        missed_run_policy: str = "skip",
        overlap_policy: str = "skip",
    ) -> ScheduleRecord:
        self.require_workflow(workflow_id)
        return self.schedules.upsert(
            workflow_id=workflow_id,
            recurrence=recurrence,
            timezone_name=timezone,
            missed_run_policy=missed_run_policy,
            overlap_policy=overlap_policy,
        )

    def list_schedules(self) -> Tuple[ScheduleRecord, ...]:
        return self.schedules.list_all()

    def preview_schedule(self, recurrence, count: int = 10) -> Tuple[str, ...]:
        return self.schedules.preview_occurrences(recurrence, count=count)

    def _sync_triggers(self, definition: WorkflowDefinition) -> None:
        self.triggers.sync(workflow_id=definition.workflow_id, triggers=definition.triggers)
        # Maintain a schedule for scheduled triggers.
        for trigger in definition.triggers:
            if trigger.type == "scheduled" and trigger.schedule is not None:
                self.schedules.upsert(
                    workflow_id=definition.workflow_id,
                    recurrence=trigger.schedule,
                    timezone_name=trigger.schedule.timezone,
                    missed_run_policy=trigger.missed_run_policy,
                    overlap_policy=trigger.overlap_policy,
                )

    def check_due_schedules(self) -> Tuple[RunRecord, ...]:
        started: list = []
        for schedule in self.schedules.due_now():
            workflow = self.workflows.get_record(schedule.workflow_id)
            if workflow is None or not workflow.enabled:
                self.schedules.advance(schedule.schedule_id)
                continue
            overlap = self._respect_overlap(schedule, workflow.workflow_id)
            if not overlap:
                self.schedules.advance(schedule.schedule_id)
                continue
            try:
                run = self.run_workflow(
                    workflow.workflow_id,
                    trigger_id=schedule.schedule_id,
                    trigger_context={"schedule_id": schedule.schedule_id},
                )
                started.append(run)
            except Exception:
                self.schedules.set_health(schedule.schedule_id, "unhealthy")
            finally:
                self.schedules.advance(schedule.schedule_id)
        return tuple(started)

    def _respect_overlap(self, schedule: ScheduleRecord, workflow_id: str) -> bool:
        if schedule.overlap_policy == "skip":
            return not self._has_active_runs(workflow_id)
        if schedule.overlap_policy == "queue":
            return True
        if schedule.overlap_policy == "cancel_previous":
            self.cancel_active_runs(workflow_id)
            return True
        if schedule.overlap_policy == "parallel_bounded":
            return self._active_run_count(workflow_id) < 2
        if schedule.overlap_policy == "deduplicate":
            return not self._has_active_runs(workflow_id)
        return True

    def _has_active_runs(self, workflow_id: str) -> bool:
        return self._active_run_count(workflow_id) > 0

    def _active_run_count(self, workflow_id: str) -> int:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM workflow_runs WHERE workflow_id = ? AND state IN ('queued','preparing','running','waiting','delayed','retrying')",
                (workflow_id,),
            ).fetchone()
        return int(row[0])

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def run_workflow(
        self,
        workflow_id: str,
        *,
        trigger_id: str = "",
        trigger_context: Optional[dict] = None,
        inputs: Optional[dict] = None,
        idempotency_key: str = "",
    ) -> RunRecord:
        blocked, reason = self._governance_blocked()
        if blocked:
            raise WorkflowError("governance: %s" % reason)
        record = self.require_workflow(workflow_id)
        if not record.enabled:
            raise WorkflowError("workflow is not enabled.")
        definition = record.definition
        validate_definition(definition)
        plan = compile_workflow(definition)
        # Concurrency enforcement per workflow.
        try:
            self.concurrency.acquire(
                key="workflow:" + workflow_id,
                owner="run",
                max_count=definition.resource.max_active_runs,
            )
        except ConcurrencyLimit as exc:
            raise WorkflowError(str(exc)) from exc
        try:
            return self.engine.start(
                workflow_id=workflow_id,
                definition=definition,
                plan=plan,
                inputs=inputs,
                trigger_id=trigger_id,
                trigger_context=trigger_context,
                idempotency_key=idempotency_key,
            )
        finally:
            self.concurrency.release(key="workflow:" + workflow_id, owner="run")

    def get_run(self, run_id: str) -> Optional[RunRecord]:
        return self.engine.get_run(run_id)

    def list_runs(self, *, workflow_id: Optional[str] = None, state: Optional[str] = None, limit: int = 50) -> Tuple[RunRecord, ...]:
        return self.history.list_runs(workflow_id=workflow_id, state=state, limit=limit)

    def traces(self, run_id: str) -> Tuple[dict, ...]:
        return self.history.traces(run_id=run_id)

    def cancel_run(self, run_id: str) -> RunRecord:
        with self._connection_factory() as connection:
            connection.execute(
                "UPDATE workflow_runs SET state = 'cancelled', cancellation_state = 'requested', ended_at = ? WHERE run_id = ? AND state NOT IN ('succeeded','failed','cancelled')",
                (_now(), run_id),
            )
        return self.engine.get_run(run_id)

    def cancel_active_runs(self, workflow_id: str) -> int:
        with self._connection_factory() as connection:
            cursor = connection.execute(
                "UPDATE workflow_runs SET state = 'cancelled', cancellation_state = 'cancelled', ended_at = ? WHERE workflow_id = ? AND state IN ('queued','preparing','running','waiting','delayed','retrying')",
                (_now(), workflow_id),
            )
        return cursor.rowcount

    def cancel_active_runs_all(self) -> int:
        """Cancel every active workflow run across all workflows (Emergency Stop)."""
        with self._connection_factory() as connection:
            cursor = connection.execute(
                "UPDATE workflow_runs SET state = 'cancelled', cancellation_state = 'cancelled', ended_at = ? WHERE state IN ('queued','preparing','running','waiting','delayed','retrying')",
                (_now(),),
            )
        return cursor.rowcount

    # ------------------------------------------------------------------
    # Approvals / input
    # ------------------------------------------------------------------

    def approvals(self, *, workflow_id: Optional[str] = None, state: Optional[str] = None, limit: int = 50) -> Tuple[dict, ...]:
        clauses: list = []
        params: list = []
        if workflow_id:
            clauses.append("workflow_id = ?")
            params.append(workflow_id)
        if state:
            clauses.append("state = ?")
            params.append(state)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(max(1, min(200, int(limit))))
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM workflow_approvals" + where + " ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def resolve_approval(self, approval_id: str, *, decision: str, approver: str = "user") -> dict:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM workflow_approvals WHERE approval_id = ?", (approval_id,)
            ).fetchone()
            if row is None:
                raise WorkflowError("approval not found.")
            if str(row["state"]) != "pending":
                raise WorkflowError("approval is already resolved.")
            connection.execute(
                "UPDATE workflow_approvals SET state = ?, resolved_at = ? WHERE approval_id = ?",
                (decision, _now(), approval_id),
            )
            if decision == "approved":
                connection.execute(
                    "UPDATE workflow_runs SET state = 'running' WHERE run_id = ?",
                    (str(row["run_id"]),),
                )
        return {"approval_id": approval_id, "decision": decision}

    def inputs(self, *, limit: int = 50) -> Tuple[dict, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM workflow_user_inputs ORDER BY created_at DESC LIMIT ?",
                (max(1, min(200, int(limit))),),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def provide_input(self, input_id: str, *, response: dict) -> dict:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM workflow_user_inputs WHERE input_id = ?", (input_id,)
            ).fetchone()
            if row is None:
                raise WorkflowError("input request not found.")
            connection.execute(
                "UPDATE workflow_user_inputs SET state = 'provided', response = ? WHERE input_id = ?",
                (__import__("json").dumps(response), input_id),
            )
            connection.execute(
                "UPDATE workflow_runs SET state = 'running' WHERE run_id = ?",
                (str(row["run_id"]),),
            )
        return {"input_id": input_id, "state": "provided"}

    # ------------------------------------------------------------------
    # Permissions / secrets
    # ------------------------------------------------------------------

    def grant_permission(self, workflow_id: str, permission: str, *, scope: str = "global", scope_target: str = "") -> None:
        self.require_workflow(workflow_id)
        self.permissions.grant(workflow_id=workflow_id, permission=permission, scope=scope, scope_target=scope_target)

    def revoke_permission(self, workflow_id: str, permission: str, *, scope_target: str = "") -> None:
        self.permissions.revoke(workflow_id=workflow_id, permission=permission, scope_target=scope_target)

    def permission_grants(self, workflow_id: str) -> tuple:
        return self.permissions.grants_for(workflow_id=workflow_id)

    def set_secret(self, name: str, value: str, *, scope: str = "global") -> dict:
        return self.secrets.set(name=name, value=value, scope=scope)

    def revoke_secret(self, name: str, *, scope: str = "global") -> None:
        self.secrets.revoke(name=name, scope=scope)

    def secret_references(self) -> tuple:
        return self.secrets.references()

    # ------------------------------------------------------------------
    # Overview / health / diagnostics
    # ------------------------------------------------------------------

    def overview(self) -> WorkflowOverview:
        records = self.workflows.list_records()
        enabled = [r for r in records if r.enabled]
        with self._connection_factory() as connection:
            running = connection.execute(
                "SELECT COUNT(*) FROM workflow_runs WHERE state IN ('queued','preparing','running','waiting','delayed','retrying')"
            ).fetchone()[0]
            waiting = connection.execute(
                "SELECT COUNT(*) FROM workflow_runs WHERE state IN ('awaiting_approval','awaiting_input')"
            ).fetchone()[0]
            failed_recently = connection.execute(
                "SELECT COUNT(*) FROM workflow_runs WHERE state = 'failed' AND created_at >= datetime('now','-1 day')"
            ).fetchone()[0]
            pending_approvals = connection.execute(
                "SELECT COUNT(*) FROM workflow_approvals WHERE state = 'pending'"
            ).fetchone()[0]
        schedules = self.schedules.list_all()
        unhealthy = [s for s in schedules if s.health_state == "unhealthy"]
        next_workflow = None
        due = [s for s in schedules if s.enabled and s.next_run]
        if due:
            next_workflow = min(due, key=lambda s: s.next_run or "").workflow_id
        return WorkflowOverview(
            workflows_total=len(records),
            workflows_enabled=len(enabled),
            running=int(running),
            waiting=int(waiting),
            failed_recently=int(failed_recently),
            pending_approvals=int(pending_approvals),
            unhealthy_schedules=len(unhealthy),
            next_scheduled_workflow=next_workflow,
            generated_at=_now(),
        )

    def _refresh_health(self, workflow_id: str) -> None:
        record = self.workflows.get_record(workflow_id)
        if record is None:
            return
        health = self.health_service.summarize(
            workflow_id=workflow_id,
            definition_enabled=record.enabled,
            definition_status=record.definition.status,
        )
        self.workflows.set_state(workflow_id, status=record.definition.status, health=health)

    def health(self) -> Tuple[dict, ...]:
        return tuple(
            {
                "workflow_id": record.workflow_id,
                "name": record.name,
                "enabled": record.enabled,
                "health": self.health_service.summarize(
                    workflow_id=record.workflow_id,
                    definition_enabled=record.enabled,
                    definition_status=record.definition.status,
                ),
                "status": record.definition.status,
            }
            for record in self.workflows.list_records()
        )

    def stuck_runs(self) -> Tuple[dict, ...]:
        """Detect runs active too long without a state change."""
        with self._connection_factory() as connection:
            rows = connection.execute(
                """
                SELECT * FROM workflow_runs
                WHERE state IN ('running','waiting','delayed','retrying')
                  AND started_at < datetime('now', '-1 hour')
                ORDER BY started_at
                LIMIT 20
                """
            ).fetchall()
        return tuple(
            {
                "run_id": str(row["run_id"]),
                "workflow_id": str(row["workflow_id"]),
                "state": str(row["state"]),
                "current_node": str(row["current_node"]),
                "started_at": str(row["started_at"]),
                "attention": True,
            }
            for row in rows
        )

    def activity(self, *, workflow_id: Optional[str] = None, limit: int = 50) -> Tuple[dict, ...]:
        clauses = ""
        params: list = []
        if workflow_id:
            clauses = " WHERE workflow_id = ?"
            params.append(workflow_id)
        params.append(max(1, min(200, int(limit))))
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM workflow_activity" + clauses + " ORDER BY recorded_at DESC LIMIT ?",
                params,
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def storage_stats(self) -> dict:
        return {
            "path": self.storage.path(),
            "size_bytes": self.storage.size_bytes(),
            "version": 1,
        }

    def backup(self) -> Optional[str]:
        return self.storage.backup_to(str(self._data_dir))

    def action_catalog(self) -> Tuple[dict, ...]:
        return self.actions.list_catalog()

    def require_workflow(self, workflow_id: str) -> WorkflowRecord:
        record = self.workflows.get_record(workflow_id)
        if record is None:
            raise WorkflowError("workflow %s not found." % workflow_id)
        return record


class _BorrowedConnection:
    """Context manager wrapper that never closes the shared connection."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def __enter__(self) -> sqlite3.Connection:
        return self._connection

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self._connection.commit()
        else:
            self._connection.rollback()