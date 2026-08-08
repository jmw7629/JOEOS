"""Deterministic scheduling for autonomous operations.

Pure functions for computing occurrence times. Reuses the DST-safe recurrence
logic from the automation platform's schedule service; never depends on the
server-local timezone as the intended schedule timezone.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional

from .models import TriggerSpec
from server.automation.schedules import Recurrence, _next_occurrence


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def occurrence_key(automation_id: str, scheduled_for_iso: str, revision: int) -> str:
    raw = "%s|%s|%s" % (automation_id, scheduled_for_iso, revision)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def next_occurrence(trigger: TriggerSpec, after_iso: str) -> Optional[str]:
    if trigger.kind == "one_time":
        return None
    if trigger.kind == "condition_watch":
        after = _parse_utc(after_iso)
        return (after + timedelta(seconds=max(300, trigger.check_interval_seconds))).isoformat()
    if trigger.kind == "event":
        return None
    schedule = trigger.schedule
    if schedule is None:
        return None
    recurrence = Recurrence(
        kind=schedule.kind,
        at_time=schedule.at_time,
        weekdays=tuple(schedule.weekdays),
        month_days=tuple(schedule.month_days),
        interval_seconds=schedule.interval_seconds,
        timezone=trigger.timezone,
    )
    after = _parse_utc(after_iso)
    result = _next_occurrence(recurrence, after)
    return result.isoformat() if result is not None else None


def initial_occurrence(trigger: TriggerSpec) -> Optional[str]:
    if trigger.kind == "one_time":
        return trigger.scheduled_for
    return next_occurrence(trigger, _now_iso())
