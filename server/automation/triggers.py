"""Trigger Registry for the JoeOS Automation Platform.

Evaluates manual, scheduled, event, condition, and plugin triggers against
workflow definitions. Triggers declare their event schema, scope, dedup key,
and health. Events are treated as untrusted input; a workflow never infers
authority from event content.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Callable, Dict, Optional, Sequence, Tuple

from .models import TriggerConfig, TriggerType
from .expressions import evaluate_condition

APPROVED_EVENT_CLASSES = frozenset(
    {
        "application.lifecycle",
        "workspace.changed",
        "project.opened",
        "project.closed",
        "file.opened",
        "file.saved",
        "git.state_changed",
        "task.changed",
        "mission.changed",
        "agent.changed",
        "model.changed",
        "notification.created",
        "command.invoked",
        "settings.changed",
        "plugin.lifecycle_changed",
        "build.failed",
        "build.succeeded",
        "test.failed",
        "service.degraded",
        "service.unavailable",
    }
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TriggerError(RuntimeError):
    pass


class TriggerRegistry:
    """Persists and evaluates workflow triggers."""

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()
        self._seen: Dict[str, float] = {}
        from time import monotonic
        self._monotonic = monotonic

    def sync(self, *, workflow_id: str, triggers: Sequence[TriggerConfig]) -> None:
        now = _now()
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "DELETE FROM workflow_triggers WHERE workflow_id = ?", (workflow_id,)
            )
            for trigger in triggers:
                connection.execute(
                    """
                    INSERT INTO workflow_triggers (
                        trigger_id, workflow_id, config, enabled, health_state,
                        last_event, last_ignored_reason, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'healthy', '', '', ?, ?)
                    """,
                    (
                        trigger.trigger_id,
                        workflow_id,
                        trigger.model_dump_json(),
                        1 if trigger.enabled else 0,
                        now,
                        now,
                    ),
                )

    def evaluate(
        self,
        *,
        workflow_id: str,
        trigger: TriggerConfig,
        variables: Dict[str, object],
        event_payload: Optional[dict] = None,
    ) -> bool:
        """Evaluate a trigger against typed variables; returns whether it fires."""
        if not trigger.enabled:
            return False
        if trigger.condition:
            merged = dict(variables or {})
            if event_payload:
                merged["event"] = event_payload
            try:
                if not evaluate_condition(trigger.condition, merged):
                    self._record_ignored(trigger.trigger_id, "condition not met")
                    return False
            except Exception:
                self._record_ignored(trigger.trigger_id, "condition evaluation failed")
                return False
        if trigger.type == "event":
            if not event_payload:
                return False
            if trigger.dedup_key and not self._is_unique(trigger.trigger_id, trigger.dedup_key, event_payload):
                self._record_ignored(trigger.trigger_id, "deduplicated")
                return False
        self._record_last_event(trigger.trigger_id)
        return True

    def _is_unique(self, trigger_id: str, dedup_key: str, event_payload: dict) -> bool:
        value = event_payload.get(dedup_key) or event_payload.get("event_id") or event_payload
        digest = hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()
        marker = trigger_id + ":" + digest
        if marker in self._seen:
            return False
        self._seen[marker] = self._monotonic()
        # Bound the dedup cache.
        if len(self._seen) > 5000:
            cutoff = self._monotonic() - 3600
            self._seen = {k: v for k, v in self._seen.items() if v >= cutoff}
        return True

    def _record_ignored(self, trigger_id: str, reason: str) -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                UPDATE workflow_triggers
                SET last_ignored_reason = ?, updated_at = ?
                WHERE trigger_id = ?
                """,
                (reason, _now(), trigger_id),
            )

    def _record_last_event(self, trigger_id: str) -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE workflow_triggers SET last_event = ?, updated_at = ? WHERE trigger_id = ?",
                (_now(), _now(), trigger_id),
            )

    def list_for(self, workflow_id: str) -> Tuple[dict, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM workflow_triggers WHERE workflow_id = ? ORDER BY trigger_id",
                (workflow_id,),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def set_health(self, trigger_id: str, health: str) -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE workflow_triggers SET health_state = ?, updated_at = ? WHERE trigger_id = ?",
                (health, _now(), trigger_id),
            )


def validate_trigger_type(trigger_type: TriggerType) -> None:
    if trigger_type not in TriggerType.__args__:
        raise TriggerError("unknown trigger type.")