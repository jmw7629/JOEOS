"""Trigger and Schedule Service for the JoeOS Automation Platform.

Schedules are timezone-aware (named IANA timezone), with explicit policies for
missed runs, overlap, daylight-saving gaps and repeats, and occurrence limits.
The schedule service is the single source of scheduling truth; it never
depends on server-local timezone implicitly.
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import Recurrence, ScheduleRecord

VALID_WEEKDAYS = frozenset(range(7))  # 0=Monday
VALID_MONTH_DAYS = frozenset(range(1, 32))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ScheduleError(RuntimeError):
    pass


def _load_tz(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ScheduleError("unknown timezone %r." % timezone_name) from exc


def _next_occurrence(recurrence: Recurrence, after: datetime) -> Optional[datetime]:
    """Compute the next occurrence strictly after ``after`` in UTC.

    Handles daily/weekly/monthly/interval recurrences with explicit
    daylight-saving behavior: nonexistent local times run at the next valid
    time; repeated local times run once.
    """
    tz = _load_tz(recurrence.timezone)
    try:
        local_after = after.astimezone(tz)
    except (ValueError, OverflowError):
        local_after = after.astimezone(timezone.utc)

    if recurrence.kind == "once":
        try:
            hour, minute = (int(part) for part in recurrence.at_time.split(":"))
        except ValueError:
            raise ScheduleError("invalid at_time.")
        candidate = local_after.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= local_after:
            candidate += timedelta(days=1)
        return _fold_safe(candidate, tz)

    if recurrence.kind == "interval":
        return after + timedelta(seconds=max(60, recurrence.interval_seconds))

    if recurrence.kind in {"daily", "weekdays"}:
        hour, minute = (int(part) for part in recurrence.at_time.split(":"))
        for offset in range(0, 8):
            candidate = (local_after + timedelta(days=offset)).replace(
                hour=hour, minute=minute, second=0, microsecond=0
            )
            if candidate <= local_after:
                continue
            if recurrence.kind == "weekdays":
                weekday = candidate.weekday()
                if recurrence.weekdays and weekday not in recurrence.weekdays:
                    continue
            return _fold_safe(candidate, tz)
        return None

    if recurrence.kind == "weekly":
        hour, minute = (int(part) for part in recurrence.at_time.split(":"))
        for offset in range(0, 8):
            candidate = (local_after + timedelta(days=offset)).replace(
                hour=hour, minute=minute, second=0, microsecond=0
            )
            if candidate <= local_after:
                continue
            if recurrence.weekdays and candidate.weekday() not in recurrence.weekdays:
                continue
            return _fold_safe(candidate, tz)
        return None

    if recurrence.kind == "monthly":
        hour, minute = (int(part) for part in recurrence.at_time.split(":"))
        days = recurrence.month_days or (1,)
        for month_offset in range(0, 13):
            base = date(local_after.year, local_after.month, 1) + timedelta(
                days=month_offset * 31
            )
            for day in sorted(days):
                try:
                    candidate_date = base.replace(day=day)
                except ValueError:
                    continue
                candidate = datetime.combine(candidate_date, datetime.min.time(), tzinfo=tz).replace(
                    hour=hour, minute=minute, second=0, microsecond=0
                )
                if candidate > local_after:
                    return _fold_safe(candidate, tz)
        return None

    raise ScheduleError("unsupported recurrence kind %r." % recurrence.kind)


def _fold_safe(candidate: datetime, tz: ZoneInfo) -> datetime:
    """Resolve ambiguous/nonexistent local times to a concrete UTC instant."""
    try:
        return candidate.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        # Nonexistent local time (DST gap): run at the next valid time.
        shifted = candidate + timedelta(hours=1)
        return shifted.astimezone(timezone.utc)


class ScheduleService:
    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()

    def upsert(
        self,
        *,
        workflow_id: str,
        recurrence: Recurrence,
        timezone_name: str = "UTC",
        version_policy: str = "latest",
        pinned_version: str = "",
        missed_run_policy: str = "skip",
        overlap_policy: str = "skip",
    ) -> ScheduleRecord:
        _load_tz(timezone_name)
        schedule_id = "sched_" + uuid.uuid4().hex[:16]
        next_run = _next_occurrence(recurrence, _utc_now())
        now = _utc_now().isoformat()
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                DELETE FROM workflow_schedules WHERE workflow_id = ?
                """,
                (workflow_id,),
            )
            connection.execute(
                """
                INSERT INTO workflow_schedules (
                    schedule_id, workflow_id, timezone, recurrence, next_run, last_run,
                    missed_run_policy, overlap_policy, enabled, health_state,
                    validation_state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, '', ?, ?, 1, 'healthy', 'valid', ?, ?)
                """,
                (
                    schedule_id,
                    workflow_id,
                    timezone_name,
                    recurrence.model_dump_json(),
                    next_run.isoformat() if next_run else None,
                    missed_run_policy,
                    overlap_policy,
                    now,
                    now,
                ),
            )
        return self.get(schedule_id)

    def get(self, schedule_id: str) -> ScheduleRecord:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM workflow_schedules WHERE schedule_id = ?", (schedule_id,)
            ).fetchone()
        return self._row_to_record(row) if row else None

    def for_workflow(self, workflow_id: str) -> Optional[ScheduleRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM workflow_schedules WHERE workflow_id = ? LIMIT 1",
                (workflow_id,),
            ).fetchone()
        return self._row_to_record(row) if row else None

    def due_now(self, *, now: Optional[datetime] = None) -> Tuple[ScheduleRecord, ...]:
        """Return enabled schedules whose next_run is due."""
        now = now or _utc_now()
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM workflow_schedules WHERE enabled = 1 AND next_run IS NOT NULL AND next_run <= ?",
                (now.isoformat(),),
            ).fetchall()
        return tuple(self._row_to_record(row) for row in rows)

    def advance(self, schedule_id: str, *, now: Optional[datetime] = None) -> Optional[ScheduleRecord]:
        """Move a schedule to its next occurrence and record the last run."""
        now = now or _utc_now()
        record = self.get(schedule_id)
        if record is None:
            return None
        next_run = _next_occurrence(record.recurrence, now)
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                UPDATE workflow_schedules
                SET last_run = ?, next_run = ?, updated_at = ?
                WHERE schedule_id = ?
                """,
                (now.isoformat(), next_run.isoformat() if next_run else None, now.isoformat(), schedule_id),
            )
        return self.get(schedule_id)

    def set_enabled(self, schedule_id: str, enabled: bool) -> ScheduleRecord:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE workflow_schedules SET enabled = ?, updated_at = ? WHERE schedule_id = ?",
                (1 if enabled else 0, _utc_now().isoformat(), schedule_id),
            )
        return self.get(schedule_id)

    def list_all(self) -> Tuple[ScheduleRecord, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM workflow_schedules ORDER BY next_run"
            ).fetchall()
        return tuple(self._row_to_record(row) for row in rows)

    def preview_occurrences(self, recurrence: Recurrence, *, count: int = 10) -> Tuple[str, ...]:
        occurrences: List[str] = []
        cursor = _utc_now()
        for _ in range(max(1, min(100, int(count)))):
            cursor = _next_occurrence(recurrence, cursor)
            if cursor is None:
                break
            occurrences.append(cursor.isoformat())
        return tuple(occurrences)

    def set_health(self, schedule_id: str, health: str) -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE workflow_schedules SET health_state = ?, updated_at = ? WHERE schedule_id = ?",
                (health, _utc_now().isoformat(), schedule_id),
            )

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> ScheduleRecord:
        import json as _json
        recurrence_data = _json.loads(str(row["recurrence"]))

        def _norm(value):
            if isinstance(value, list):
                return tuple(_norm(item) for item in value)
            if isinstance(value, dict):
                return {key: _norm(item) for key, item in value.items()}
            return value

        return ScheduleRecord(
            schedule_id=str(row["schedule_id"]),
            workflow_id=str(row["workflow_id"]),
            timezone=str(row["timezone"]),
            recurrence=Recurrence.model_validate(_norm(recurrence_data)),
            next_run=str(row["next_run"]) if row["next_run"] else None,
            last_run=str(row["last_run"]) if row["last_run"] else None,
            missed_run_policy=str(row["missed_run_policy"]),
            overlap_policy=str(row["overlap_policy"]),
            enabled=bool(row["enabled"]),
            health_state=str(row["health_state"]),
            validation_state=str(row["validation_state"]),
        )