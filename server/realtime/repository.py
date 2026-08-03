from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Callable, List

from .models import AuditEventRecord

_SAFE_SEVERITIES = frozenset({"info", "success", "warn", "error"})


def _parse_utc(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return datetime(1970, 1, 1, tzinfo=timezone.utc)


def _severity(level: str) -> str:
    return level if level in _SAFE_SEVERITIES else "info"


class SQLiteEventRepository:
    """Read-only resumable audit-event cursor over the shared events table."""

    def __init__(
        self,
        connection_factory: Callable[[], sqlite3.Connection],
    ) -> None:
        self._connection_factory = connection_factory

    def latest_cursor(self) -> int:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT id FROM events ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return int(row["id"]) if row is not None else 0

    def oldest_cursor(self) -> int:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT id FROM events ORDER BY id ASC LIMIT 1"
            ).fetchone()
        return int(row["id"]) if row is not None else 0

    def fetch_after(self, cursor: int, limit: int) -> List[AuditEventRecord]:
        start = max(0, int(cursor))
        count = max(1, min(100, int(limit)))
        with self._connection_factory() as connection:
            rows = connection.execute(
                """
                SELECT id, recorded_at, level, source, message
                FROM events
                WHERE id > ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (start, count),
            ).fetchall()
        records: List[AuditEventRecord] = []
        for row in rows:
            records.append(
                AuditEventRecord(
                    event_id=int(row["id"]),
                    occurred_at=_parse_utc(str(row["recorded_at"])),
                    source=str(row["source"])[:80],
                    severity=_severity(str(row["level"])),
                    message=str(row["message"])[:500],
                )
            )
        return records
