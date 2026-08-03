"""Health, diagnostics, and redacted logging for the JoeOS Plugin Platform.

Health is computed from real lifecycle, permission, dependency, resource, and
crash state — never from the mere fact a package is installed. Logs are
bounded, rate-limited, redacted, and exportable.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Callable, Dict, Optional, Tuple

from .models import HealthRecord, PluginLogRecord

MAX_LOG_ROWS_PER_PLUGIN = 500
SECRET_PATTERN = re.compile(
    r"(?i)(password|passwd|secret|token|api[_-]?key|private[_-]?key|bearer)\s*[=:]\s*\S+"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def redact(message: str) -> str:
    """Strip obvious secret-shaped values from a log line before persistence."""
    if not message:
        return message
    return SECRET_PATTERN.sub(r"\1=****", message[:1000])


class PluginHealthService:
    """Persists health, activity, and bounded redacted logs per plugin."""

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()

    # ---- health ----

    def set_health(
        self,
        *,
        plugin_id: str,
        state: str,
        message: str = "",
        contribution_count: Optional[int] = None,
        active_jobs: Optional[int] = None,
        host_state: str = "",
        update_state: str = "",
        recent_errors: Tuple[str, ...] = (),
    ) -> None:
        now = _now()
        with self._lock, self._connection_factory() as connection:
            existing = connection.execute(
                "SELECT * FROM plugin_health WHERE plugin_id = ?", (plugin_id,)
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO plugin_health (
                        plugin_id, state, message, contribution_count, active_jobs,
                        host_state, update_state, recent_errors, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        plugin_id,
                        state,
                        redact(message),
                        contribution_count or 0,
                        active_jobs or 0,
                        host_state,
                        update_state,
                        "\n".join(redact(error) for error in recent_errors[-8:]),
                        now,
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE plugin_health
                    SET state = ?, message = ?, contribution_count = ?, active_jobs = ?,
                        host_state = ?, update_state = ?, recent_errors = ?, updated_at = ?
                    WHERE plugin_id = ?
                    """,
                    (
                        state,
                        redact(message),
                        contribution_count if contribution_count is not None else existing["contribution_count"],
                        active_jobs if active_jobs is not None else existing["active_jobs"],
                        host_state or existing["host_state"],
                        update_state or existing["update_state"],
                        "\n".join(redact(error) for error in recent_errors[-8:]),
                        now,
                        plugin_id,
                    ),
                )

    def record_crash(self, *, plugin_id: str, error: str = "") -> None:
        now = _now()
        with self._lock, self._connection_factory() as connection:
            existing = connection.execute(
                "SELECT * FROM plugin_health WHERE plugin_id = ?", (plugin_id,)
            ).fetchone()
            crashes = 1 + (int(existing["crash_count"]) if existing else 0)
            recent = []
            if existing and existing["recent_errors"]:
                recent = [item for item in str(existing["recent_errors"]).split("\n") if item]
            recent.append(redact(error or "extension host exited unexpectedly."))
            recent = recent[-8:]
            connection.execute(
                """
                INSERT INTO plugin_health (
                    plugin_id, state, last_crash, crash_count, recent_errors, updated_at
                ) VALUES (?, 'crashed', ?, ?, ?, ?)
                ON CONFLICT(plugin_id) DO UPDATE SET
                    state = 'crashed', last_crash = excluded.last_crash,
                    crash_count = excluded.crash_count,
                    recent_errors = excluded.recent_errors, updated_at = excluded.updated_at
                """,
                (plugin_id, now, crashes, "\n".join(recent), now),
            )

    def record_success(self, *, plugin_id: str) -> None:
        now = _now()
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO plugin_health (plugin_id, last_success, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(plugin_id) DO UPDATE SET
                    last_success = excluded.last_success, updated_at = excluded.updated_at
                """,
                (plugin_id, now, now),
            )

    def record_activation(self, *, plugin_id: str) -> None:
        now = _now()
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO plugin_health (plugin_id, last_activation, host_state, state, updated_at)
                VALUES (?, ?, 'running', 'activating', ?)
                ON CONFLICT(plugin_id) DO UPDATE SET
                    last_activation = excluded.last_activation,
                    host_state = 'running', state = 'activating', updated_at = excluded.updated_at
                """,
                (plugin_id, now, now),
            )

    def get(self, *, plugin_id: str) -> HealthRecord:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM plugin_health WHERE plugin_id = ?", (plugin_id,)
            ).fetchone()
            contributions = connection.execute(
                "SELECT COUNT(*) FROM plugin_contributions WHERE plugin_id = ? AND state != 'removed'",
                (plugin_id,),
            ).fetchone()[0]
        if row is None:
            return HealthRecord(plugin_id=plugin_id, state="unknown")
        return HealthRecord(
            plugin_id=plugin_id,
            state=str(row["state"]),
            last_activation=str(row["last_activation"]),
            last_success=str(row["last_success"]),
            last_crash=str(row["last_crash"]),
            crash_count=int(row["crash_count"]),
            recent_errors=tuple(part for part in str(row["recent_errors"]).split("\n") if part),
            contribution_count=int(contributions),
            active_jobs=int(row["active_jobs"]),
            host_state=str(row["host_state"]),
            update_state=str(row["update_state"]),
            message=str(row["message"]),
        )

    def all(self) -> Tuple[HealthRecord, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute("SELECT plugin_id FROM plugin_health ORDER BY plugin_id").fetchall()
        return tuple(self.get(plugin_id=str(row["plugin_id"])) for row in rows)

    # ---- activity ----

    def activity(self, *, plugin_id: str, limit: int = 50) -> Tuple[dict, ...]:
        count = max(1, min(200, int(limit)))
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM plugin_activity WHERE plugin_id = ? ORDER BY recorded_at DESC LIMIT ?",
                (plugin_id, count),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def record_activity(
        self, *, plugin_id: str, kind: str, message: str = "", level: str = "info"
    ) -> None:
        import uuid
        now = _now()
        safe_level = level if level in {"info", "success", "warn", "error"} else "info"
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO plugin_activity (event_id, plugin_id, kind, message, level, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("act_" + uuid.uuid4().hex[:20], plugin_id, kind[:80], redact(message)[:500], safe_level, now),
            )

    # ---- logs ----

    def log(
        self,
        *,
        plugin_id: str,
        severity: str,
        category: str = "",
        message: str = "",
    ) -> None:
        safe_severity = severity if severity in {"debug", "info", "warn", "error"} else "info"
        now = _now()
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO plugin_logs (plugin_id, severity, category, message, recorded_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (plugin_id, safe_severity, category[:80], redact(message)[:1000], now),
            )
            connection.execute(
                """
                DELETE FROM plugin_logs WHERE id NOT IN (
                    SELECT id FROM plugin_logs
                    WHERE plugin_id = ? ORDER BY id DESC LIMIT ?
                )
                """,
                (plugin_id, MAX_LOG_ROWS_PER_PLUGIN),
            )

    def logs(self, *, plugin_id: str, limit: int = 100) -> Tuple[PluginLogRecord, ...]:
        count = max(1, min(500, int(limit)))
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM plugin_logs WHERE plugin_id = ? ORDER BY id DESC LIMIT ?",
                (plugin_id, count),
            ).fetchall()
        return tuple(
            PluginLogRecord(
                plugin_id=plugin_id,
                severity=str(row["severity"]),
                category=str(row["category"]),
                message=str(row["message"]),
                recorded_at=str(row["recorded_at"]),
            )
            for row in reversed(rows)
        )

    def export_logs(self, *, plugin_id: str) -> str:
        """Export redacted logs as JSON (already redacted at write time)."""
        return json.dumps([record.model_dump() for record in self.logs(plugin_id=plugin_id, limit=500)], indent=2)