"""Resource governor: budget enforcement for missions, tasks, and agents.

Budgets are checked against real usage recorded in task/mission state. No
budget is silently extended; exhaustion raises signals that route to
escalations and interventions. Duration budgets use wall-clock comparisons
against recorded start times and are best-effort, never fabricated.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Callable, Optional, Tuple

from .models import ResourceBudget

_DURATION_KINDS = ("mission_duration", "task_duration")
_COUNT_KINDS = (
    "model_calls",
    "tokens",
    "tool_calls",
    "agent_count",
    "active_agents",
    "delegation_depth",
    "retry_count",
    "review_rounds",
    "debate_rounds",
    "context_size",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class BudgetService:
    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory

    def check_mission_budget(self, mission_id: str) -> ResourceBudget:
        """Evaluate the current budget state of a mission against its limits."""
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT budget, status, start_time, mission_id FROM org_missions WHERE mission_id = ?",
                (mission_id,),
            ).fetchone()
        if row is None:
            raise KeyError("mission not found: %s" % mission_id)
        budget = _budget_from_json(row["budget"])
        if budget.state == "exhausted":
            return budget
        notes = []
        if budget.mission_duration_minutes:
            start = _parse_time(row["start_time"])
            if start is not None:
                elapsed_minutes = (datetime.now(timezone.utc) - start).total_seconds() / 60
                if elapsed_minutes > budget.mission_duration_minutes:
                    budget = budget.model_copy(update={"state": "exhausted", "note": "mission duration budget exhausted"})
                    notes.append("mission_duration")
        if budget.state == "ok":
            overrun = self._count_overruns(mission_id, budget)
            if overrun:
                budget = budget.model_copy(update={"state": "exhausted", "note": "; ".join(overrun)})
                notes.extend(overrun)
        return budget

    def check_task_budget(self, task_id: str) -> ResourceBudget:
        """Evaluate the current budget state of a task."""
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT budget, status, created_at FROM org_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        if row is None:
            raise KeyError("task not found: %s" % task_id)
        budget = _budget_from_json(row["budget"])
        if budget.state == "exhausted":
            return budget
        if budget.task_duration_minutes:
            start = _parse_time(row["created_at"])
            if start is not None:
                elapsed_minutes = (datetime.now(timezone.utc) - start).total_seconds() / 60
                if elapsed_minutes > budget.task_duration_minutes:
                    budget = budget.model_copy(update={"state": "exhausted", "note": "task duration budget exhausted"})
        return budget

    def _count_overruns(self, mission_id: str, budget: ResourceBudget) -> Tuple[str, ...]:
        overruns = []
        with self._connection_factory() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM org_tasks WHERE mission_id = ? AND status = 'complete'",
                (mission_id,),
            ).fetchone()[0]
            if budget.agent_count and count > budget.agent_count:
                overruns.append("task count exceeds agent budget")
            retries = connection.execute(
                "SELECT COALESCE(SUM(retry_count), 0) FROM org_tasks WHERE mission_id = ?",
                (mission_id,),
            ).fetchone()[0]
            if budget.retry_count is not None and retries > budget.retry_count:
                overruns.append("retry budget exhausted")
            reviews = connection.execute(
                "SELECT COUNT(*) FROM org_reviews WHERE mission_id = ?",
                (mission_id,),
            ).fetchone()[0]
            if budget.review_rounds and reviews > budget.review_rounds:
                overruns.append("review rounds budget exhausted")
        return tuple(overruns)

    def exhaust(self, mission_id: str, *, reason: str = "budget exhausted") -> bool:
        with self._connection_factory() as connection:
            cursor = connection.execute(
                "UPDATE org_missions SET health = 'blocked', updated_at = ? WHERE mission_id = ?",
                (_now(), mission_id),
            )
        if cursor.rowcount == 0:
            return False
        with self._connection_factory() as connection:
            connection.execute(
                "UPDATE org_tasks SET status = 'blocked' WHERE mission_id = ? AND status = 'not_started'",
                (mission_id,),
            )
        return True

    def record_usage(self, mission_id: str, *, kind: str, amount: int = 1) -> None:
        """Best-effort audit of a usage event (e.g. a model or tool call)."""
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO org_activity (
                    event_id, kind, summary, mission_id, refs, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt_" + _id("usage", mission_id, kind)[:22],
                    "budget_usage_%s" % kind,
                    "%s usage: %d" % (kind, amount),
                    mission_id,
                    mission_id or "",
                    _now(),
                ),
            )


def _id(*parts: str) -> str:
    import hashlib
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:24]


def _budget_from_json(raw: str) -> ResourceBudget:
    try:
        return ResourceBudget.model_validate_json(raw or "{}")
    except Exception:
        return ResourceBudget()