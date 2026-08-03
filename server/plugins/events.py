"""Extension Event Gateway for the JoeOS Plugin Platform.

A bounded, permission-checked, privacy-filtered event surface. Extensions
subscribe only to approved event classes with explicit scoping; the gateway
enforces per-plugin rate limits and queue bounds so a misbehaving extension
cannot flood JoeOS or hide behind unbounded queues.
"""

from __future__ import annotations

import sqlite3
import threading
import time
import json
from datetime import datetime, timezone
from typing import Callable, Dict, Optional, Sequence, Tuple

from .permissions import PermissionManager

APPROVED_EVENT_CLASSES: Dict[str, str] = {
    "application.lifecycle": "events.subscribe",
    "workspace.changed": "events.subscribe",
    "project.opened": "events.subscribe",
    "project.closed": "events.subscribe",
    "file.opened": "events.subscribe",
    "file.saved": "events.subscribe",
    "git.state_changed": "events.subscribe",
    "task.changed": "events.subscribe",
    "mission.changed": "events.subscribe",
    "agent.changed": "events.subscribe",
    "model.changed": "events.subscribe",
    "notification.created": "events.subscribe",
    "command.invoked": "events.subscribe",
    "settings.changed": "events.subscribe",
    "plugin.lifecycle_changed": "events.subscribe",
}

MAX_EVENTS_PER_PLUGIN = 200
DEFAULT_RATE_LIMIT_PER_MINUTE = 120


def approved_event_class(event_class: str) -> bool:
    return event_class in APPROVED_EVENT_CLASSES


class EventGateway:
    """Persists and delivers bounded event traffic for extensions."""

    def __init__(
        self,
        connection_factory: Callable[[], sqlite3.Connection],
        permissions: PermissionManager,
        lifecycle_probe=None,
        now_provider=None,
    ) -> None:
        self._connection_factory = connection_factory
        self._permissions = permissions
        self._lifecycle_probe = lifecycle_probe or (lambda plugin_id: "active")
        self._now = now_provider or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()
        self._rate_counts: Dict[str, list] = {}

    def subscribe(
        self,
        *,
        plugin_id: str,
        event_class: str,
    ) -> str:
        if not approved_event_class(event_class):
            raise ValueError("unknown event class: %r" % event_class)
        if self._lifecycle_probe(plugin_id) != "active":
            raise PermissionError("plugin is not active.")
        if not self._permissions.granted(plugin_id=plugin_id, permission="events.subscribe"):
            raise PermissionError("plugin lacks the events.subscribe permission.")
        return "subscribed:" + event_class

    def publish(
        self,
        *,
        plugin_id: str,
        event_class: str,
        payload: Optional[dict] = None,
    ) -> None:
        if not approved_event_class(event_class):
            raise ValueError("unknown event class: %r" % event_class)
        if self._lifecycle_probe(plugin_id) != "active":
            raise PermissionError("plugin is not active.")
        if self._over_rate_limit(plugin_id):
            raise PermissionError("plugin exceeded its event rate limit.")
        now = self._now().isoformat()
        safe_payload = self._privacy_filter(event_class, payload or {})
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO plugin_events (plugin_id, kind, payload, recorded_at)
                VALUES (?, ?, ?, ?)
                """,
                (plugin_id, event_class, json.dumps(safe_payload), now),
            )
            connection.execute(
                """
                DELETE FROM plugin_events WHERE id NOT IN (
                    SELECT id FROM plugin_events ORDER BY id DESC LIMIT ?
                )
                """,
                (MAX_EVENTS_PER_PLUGIN,),
            )

    def _over_rate_limit(self, plugin_id: str) -> bool:
        window = 60.0
        now = time.monotonic()
        counts = self._rate_counts.setdefault(plugin_id, [])
        counts = [stamp for stamp in counts if now - stamp < window]
        self._rate_counts[plugin_id] = counts
        if len(counts) >= DEFAULT_RATE_LIMIT_PER_MINUTE:
            return True
        counts.append(now)
        return False

    @staticmethod
    def _privacy_filter(event_class: str, payload: dict) -> dict:
        # Never leak full file contents or unrestricted payloads through events.
        allowed_keys = {"file_path", "project_id", "workspace_id", "task_id", "command_id"}
        return {key: value for key, value in payload.items() if key in allowed_keys}

    def recent(self, *, plugin_id: str, limit: int = 50) -> Tuple[dict, ...]:
        count = max(1, min(200, int(limit)))
        with self._connection_factory() as connection:
            rows = connection.execute(
                """
                SELECT * FROM plugin_events
                WHERE plugin_id = ? ORDER BY id DESC LIMIT ?
                """,
                (plugin_id, count),
            ).fetchall()
        return tuple(
            {
                "id": row["id"],
                "kind": row["kind"],
                "payload": row["payload"],
                "recorded_at": row["recorded_at"],
            }
            for row in rows
        )


class PermissionError(RuntimeError):
    pass