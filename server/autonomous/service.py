"""Authoritative autonomous operations service.

Coordinates AutomationDefinition lifecycle (create/edit/pause/resume/archive),
occurrence computation, and the durable scheduler-facing operations. Execution
is delegated to the existing AgentFabric via the injected ``agent_runner``
callable, so automations use exactly the same AgentRun -> ProviderRegistry ->
ModelRegistry -> Ollama -> delegation/TaskGraph/ToolBroker path as interactive
agents. Automations gain zero extra authority.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple

from .models import (
    AutomationDefinition,
    AutomationDefinitionCreate,
    AutomationRun,
    RetryPolicySpec,
    TriggerSpec,
)
from .scheduling import initial_occurrence, next_occurrence, occurrence_key
from .storage import AutonomousStore, DuplicateOccurrenceError

logger = logging.getLogger(__name__)

AGENT_KEYS = {
    "auto": "joeos.joe",
    "joe": "joeos.joe",
    "architect": "joeos.architect",
    "builder": "joeos.builder",
    "researcher": "joeos.researcher",
    "verifier": "joeos.verifier",
    "security": "joeos.security",
    "council": "council",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid() -> str:
    return uuid.uuid4().hex


def _sid(value) -> str:
    """Normalize a principal id (UUID object or string) to a string."""
    if value is None:
        return ""
    return str(value)


def _retryable(error_category: str, policy: RetryPolicySpec) -> bool:
    return error_category in policy.retryable_errors


class AutonomousError(RuntimeError):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.public_message = message


class AutonomousDeniedError(AutonomousError):
    pass


class AutonomousNotFoundError(AutonomousError):
    pass


class AutonomousService:
    def __init__(
        self,
        store: AutonomousStore,
        *,
        agent_runner: Optional[Callable[[Dict, str, Dict], Dict]] = None,
        principal_resolver: Optional[Callable[[Dict], Dict]] = None,
        notification_sink: Optional[Callable[[Dict], None]] = None,
        event_sink: Optional[Callable[[str, str, str], None]] = None,
        now: Callable[[], str] = _now_iso,
    ) -> None:
        self._store = store
        self._agent_runner = agent_runner
        self._principal_resolver = principal_resolver or (lambda p: p)
        self._notification_sink = notification_sink
        self._event_sink = event_sink
        self._now = now
        self._lock = threading.RLock()

    def prepare(self) -> None:
        self._store.prepare()

    def recover_after_restart(self) -> int:
        now = self._now()
        return self._store.recover_expired_leases(now, now)

    # ------------------------------------------------------------------
    # Definition lifecycle
    # ------------------------------------------------------------------

    def create_definition(self, principal: Dict, payload: AutomationDefinitionCreate) -> AutomationDefinition:
        now = self._now()
        automation_id = "aut_" + _uid()[:16]
        trigger = payload.trigger
        if payload.timezone:
            trigger = trigger.model_copy(update={"timezone": payload.timezone})
        first = initial_occurrence(trigger)
        definition = AutomationDefinition(
            id=automation_id,
            organization_id=_sid(principal["organization"]["id"]),
            workspace_id=_sid(principal["workspace"]["id"]),
            owner_principal_id=_sid(principal["user"]["id"]),
            name=payload.name.strip(),
            description=payload.description,
            objective=payload.objective.strip(),
            agent_ref=payload.agent_ref,
            trigger=trigger,
            enabled=payload.enabled,
            state="active" if payload.enabled else "draft",
            next_run_at=first or "",
            concurrency_policy=payload.concurrency_policy,
            missed_run_policy=payload.missed_run_policy,
            retry_policy=payload.retry_policy,
            notification_policy=payload.notification_policy,
            created_at=now,
            updated_at=now,
        )
        self._store.insert_definition(definition)
        self._emit(principal, "automation.created",
                   {"automation": automation_id, "agent": payload.agent_ref})
        if payload.run_now:
            self.run_now(principal, automation_id)
        return definition

    def get_definition(self, principal: Dict, automation_id: str) -> AutomationDefinition:
        definition = self._store.get_definition(automation_id)
        if definition is None:
            raise AutonomousNotFoundError(404, "automation_not_found", "The automation does not exist.")
        self._assert_workspace(principal, definition)
        return definition

    def list_definitions(self, principal: Dict, state: Optional[str] = None) -> List[AutomationDefinition]:
        return self._store.list_definitions(principal["workspace"]["id"], state)

    def update_definition(self, principal: Dict, automation_id: str,
                          payload: AutomationDefinitionCreate) -> AutomationDefinition:
        existing = self.get_definition(principal, automation_id)
        if existing.state == "archived":
            raise AutonomousDeniedError(403, "automation_archived", "The automation is archived.")
        now = self._now()
        trigger = payload.trigger
        if payload.timezone:
            trigger = trigger.model_copy(update={"timezone": payload.timezone})
        updated = AutomationDefinition(
            id=existing.id,
            organization_id=existing.organization_id,
            workspace_id=existing.workspace_id,
            owner_principal_id=existing.owner_principal_id,
            name=payload.name.strip(),
            description=payload.description,
            objective=payload.objective.strip(),
            agent_ref=payload.agent_ref,
            trigger=trigger,
            enabled=payload.enabled,
            state=existing.state if existing.state != "draft" else ("active" if payload.enabled else "draft"),
            next_run_at=initial_occurrence(trigger) or "",
            last_run_at=existing.last_run_at,
            concurrency_policy=payload.concurrency_policy,
            missed_run_policy=payload.missed_run_policy,
            retry_policy=payload.retry_policy,
            notification_policy=payload.notification_policy,
            created_at=existing.created_at,
            updated_at=now,
            revision=existing.revision + 1,
        )
        self._store.update_definition(updated)
        self._emit(principal, "automation.updated", {"automation": automation_id})
        return updated

    def set_state(self, principal: Dict, automation_id: str, state: str) -> AutomationDefinition:
        allowed = {
            "active": ("paused", "draft", "disabled", "archived"),
            "paused": ("active", "draft", "disabled"),
            "disabled": ("active", "paused", "draft", "archived"),
            "draft": ("active", "disabled"),
            "archived": ("active", "paused", "disabled"),
        }
        existing = self.get_definition(principal, automation_id)
        if state not in allowed or existing.state not in allowed[state]:
            raise AutonomousDeniedError(
                403, "invalid_state_transition",
                "Cannot transition automation from %s to %s." % (existing.state, state),
            )
        now = self._now()
        updated = existing.model_copy(update={
            "state": state,
            "updated_at": now,
            "revision": existing.revision + 1,
        })
        if state == "active" and existing.state == "paused":
            # Resume: compute the next valid time respecting missed-run policy.
            updated = updated.model_copy(update={"next_run_at": initial_occurrence(existing.trigger) or ""})
        self._store.update_definition(updated)
        self._emit(principal, "automation.state_changed",
                   {"automation": automation_id, "state": state})
        return updated

    def archive_definition(self, principal: Dict, automation_id: str) -> AutomationDefinition:
        existing = self.get_definition(principal, automation_id)
        now = self._now()
        updated = existing.model_copy(update={
            "state": "archived", "enabled": False,
            "next_run_at": "", "updated_at": now, "revision": existing.revision + 1,
        })
        self._store.update_definition(updated)
        self._emit(principal, "automation.archived", {"automation": automation_id})
        return updated

    def run_now(self, principal: Dict, automation_id: str) -> AutomationRun:
        definition = self.get_definition(principal, automation_id)
        if definition.state not in ("active", "paused", "draft"):
            raise AutonomousDeniedError(403, "automation_not_runnable", "The automation is not runnable.")
        return self._create_run(principal, definition, "manual", self._now(), run_now=True)

    # ------------------------------------------------------------------
    # Scheduler-facing operations
    # ------------------------------------------------------------------

    def due_now(self) -> List[AutomationDefinition]:
        now = self._now()
        return self._store.list_due_definitions(now)

    def claim_and_run(self, definition: AutomationDefinition, scheduled_for: str) -> AutomationRun:
        """Claim a due occurrence and execute it (or queue it). Returns the run."""
        now = self._now()
        occurrence = occurrence_key(definition.id, scheduled_for, definition.revision)
        existing = self._store.get_run_by_occurrence(definition.id, occurrence)
        if existing is not None and existing.state in ("succeeded", "failed", "cancelled"):
            # Already terminal; advance and do not duplicate.
            return existing
        if existing is not None and existing.state in ("queued", "running", "retry_wait"):
            return existing
        run = AutomationRun(
            id="run_" + _uid()[:16],
            automation_id=definition.id,
            occurrence_key=occurrence,
            trigger_kind=definition.trigger.kind,
            scheduled_for=scheduled_for,
            triggered_at=now,
            state="queued",
            created_at=now,
        )
        try:
            self._store.insert_run(run)
        except DuplicateOccurrenceError:
            return self._store.get_run_by_occurrence(definition.id, occurrence)  # type: ignore[return-value]
        self._store.save_definition_snapshot(run.id, definition.id, definition, now)
        self._store.advance_definition_next(definition.id, definition.trigger, definition.revision, now)
        return run

    def advance_definition(self, automation_id: str) -> None:
        """Advance next_run_at after an occurrence, respecting policies."""
        definition = self._store.get_definition(automation_id)
        if definition is None:
            return
        now = self._now()
        nxt = next_occurrence(definition.trigger, now)
        updated = definition.model_copy(update={
            "next_run_at": nxt or "",
            "last_run_at": now,
            "updated_at": now,
            "revision": definition.revision,
        })
        self._store.update_definition(updated)

    def list_runs_for_definition(self, principal: Dict, automation_id: str,
                                 limit: int = 50) -> List[AutomationRun]:
        self.get_definition(principal, automation_id)
        return self._store.list_runs(automation_id, limit)

    def get_run_for_definition(self, principal: Dict, automation_id: str,
                               run_id: str) -> AutomationRun:
        self.get_definition(principal, automation_id)
        run = self._store.get_run(run_id)
        if run is None or run.automation_id != automation_id:
            raise AutonomousNotFoundError(404, "run_not_found", "The automation run does not exist.")
        return run

    # ------------------------------------------------------------------
    # Scheduler support (used by AutonomousScheduler)
    # ------------------------------------------------------------------

    def get_run(self, run_id: str) -> Optional[AutomationRun]:
        return self._store.get_run(run_id)

    def claim_run(self, run_id: str, *, worker: str, now_iso: str,
                  lease_expires_iso: str) -> bool:
        return self._store.claim_run(run_id, worker=worker, now_iso=now_iso,
                                     lease_expires_iso=lease_expires_iso)

    def is_retryable(self, error_category: str, definition: AutomationDefinition) -> bool:
        return _retryable(error_category, definition.retry_policy)

    def update_run_with_retry(self, run_id: str, *, attempt: int, error_category: str,
                              next_retry_at: str, agent_run_id: str = "",
                              provider_key: str = "", model_key: str = "") -> AutomationRun:
        run = self._store.get_run(run_id)
        if run is None:
            raise AutonomousNotFoundError(404, "run_not_found", "The automation run does not exist.")
        updated = run.model_copy(update={
            "attempt": attempt,
            "state": "retry_wait",
            "error_category": error_category,
            "next_retry_at": next_retry_at,
            "agent_run_id": agent_run_id,
            "provider_key": provider_key,
            "model_key": model_key,
            "revision": run.revision,
        })
        return self._store.update_run(updated)

    def notify_retry(self, run: AutomationRun) -> None:
        if self._notification_sink is None:
            return
        self._notification_sink({
            "run": run,
            "definition": self._store.get_definition(run.automation_id),
            "kind": "retry",
        })

    def notify_failure(self, run: AutomationRun) -> None:
        if self._notification_sink is None:
            return
        self._notification_sink({
            "run": run,
            "definition": self._store.get_definition(run.automation_id),
            "kind": "failure",
        })

    def complete_run(self, run_id: str, *, state: str, result_summary: str = "",
                     error_category: str = "", agent_run_id: str = "", task_graph_id: str = "",
                     approval_id: str = "", execution_id: str = "",
                     provider_key: str = "", model_key: str = "") -> AutomationRun:
        run = self._store.get_run(run_id)
        if run is None:
            raise AutonomousNotFoundError(404, "run_not_found", "The automation run does not exist.")
        now = self._now()
        updated = run.model_copy(update={
            "state": state,
            "result_summary": result_summary[:12000],
            "error_category": error_category,
            "agent_run_id": agent_run_id,
            "task_graph_id": task_graph_id,
            "approval_id": approval_id,
            "execution_id": execution_id,
            "provider_key": provider_key,
            "model_key": model_key,
            "started_at": run.started_at or now,
            "completed_at": now if state in ("succeeded", "failed", "cancelled") else run.completed_at,
            "revision": run.revision,
        })
        updated = self._store.update_run(updated)
        self._notify(updated, definition=None)
        return updated

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _create_run(self, principal: Dict, definition: AutomationDefinition,
                    trigger_kind: str, scheduled_for: str, *, run_now: bool = False) -> AutomationRun:
        now = self._now()
        occurrence = occurrence_key(definition.id, scheduled_for, definition.revision)
        existing = self._store.get_run_by_occurrence(definition.id, occurrence)
        if existing is not None:
            return existing
        run = AutomationRun(
            id="run_" + _uid()[:16],
            automation_id=definition.id,
            occurrence_key=occurrence,
            trigger_kind=trigger_kind,
            scheduled_for=scheduled_for,
            triggered_at=now,
            state="queued",
            created_at=now,
        )
        try:
            self._store.insert_run(run)
        except DuplicateOccurrenceError:
            return self._store.get_run_by_occurrence(definition.id, occurrence)  # type: ignore[return-value]
        self._store.save_definition_snapshot(run.id, definition.id, definition, now)
        if run_now:
            self._store.update_run(run.model_copy(update={"state": "running", "started_at": now}))
            run = self._store.get_run(run.id)  # type: ignore[assignment]
        return run  # type: ignore[return-value]

    def _assert_workspace(self, principal: Dict, definition: AutomationDefinition) -> None:
        if definition.workspace_id != principal["workspace"]["id"]:
            raise AutonomousDeniedError(403, "cross_workspace_denied",
                                        "Cross-workspace automation access is denied.")

    def _emit(self, principal: Dict, event: str, data: Dict) -> None:
        if self._event_sink is None:
            return
        try:
            envelope = {
                "org": principal["organization"]["id"],
                "ws": principal["workspace"]["id"],
                "event": event,
                "ts": self._now(),
                "data": data,
            }
            self._event_sink("info", "autonomous", json.dumps(envelope, sort_keys=True)[:480])
        except Exception:  # pragma: no cover - defensive
            pass

    def _notify(self, run: AutomationRun, definition: Optional[AutomationDefinition]) -> None:
        if self._notification_sink is None:
            return
        try:
            self._notification_sink({
                "run": run,
                "definition": definition,
            })
        except Exception:  # pragma: no cover - defensive
            logger.exception("automation notification failed")
