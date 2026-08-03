"""Run history and trace storage for the JoeOS Automation Platform.

Bounded, redacted history of runs, node results, and traces. Secret values,
hidden reasoning, private documents, and raw model output are never stored.
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Callable, Optional, Tuple

from .models import RunRecord

MAX_RUN_HISTORY = 1000
MAX_TRACES_PER_RUN = 1000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunHistory:
    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()

    def list_runs(
        self,
        *,
        workflow_id: Optional[str] = None,
        state: Optional[str] = None,
        limit: int = 50,
        before: Optional[str] = None,
    ) -> Tuple[RunRecord, ...]:
        count = max(1, min(200, int(limit)))
        clauses: list = []
        params: list = []
        if workflow_id:
            clauses.append("workflow_id = ?")
            params.append(workflow_id)
        if state:
            clauses.append("state = ?")
            params.append(state)
        if before:
            clauses.append("run_id < ?")
            params.append(before)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(count)
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM workflow_runs" + where + " ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return tuple(self._row_to_run(row) for row in rows)

    def traces(self, *, run_id: str, limit: int = 200) -> Tuple[dict, ...]:
        count = max(1, min(MAX_TRACES_PER_RUN, int(limit)))
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM workflow_traces WHERE run_id = ? ORDER BY id LIMIT ?",
                (run_id, count),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def record_trace(self, *, run_id, node_id="", event_type="", action_id="", workflow_id="", recorded_at="", error_code="", safe_summary="") -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO workflow_traces (
                    trace_id, run_id, node_id, action_id, event_type, recorded_at,
                    state_transition, error_code, retry_state, safe_summary
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', ?)
                """,
                (
                    "trace_" + uuid.uuid4().hex[:16],
                    run_id,
                    node_id,
                    action_id,
                    event_type,
                    recorded_at or _now(),
                    event_type,
                    error_code,
                    safe_summary[:200],
                ),
            )
            connection.execute(
                """
                DELETE FROM workflow_traces WHERE id NOT IN (
                    SELECT id FROM workflow_traces WHERE run_id = ? ORDER BY id DESC LIMIT ?
                )
                """,
                (run_id, MAX_TRACES_PER_RUN),
            )

    def prune_runs(self, *, retention_count: int = MAX_RUN_HISTORY) -> int:
        with self._lock, self._connection_factory() as connection:
            cursor = connection.execute(
                """
                DELETE FROM workflow_runs WHERE run_id NOT IN (
                    SELECT run_id FROM workflow_runs ORDER BY created_at DESC LIMIT ?
                )
                """,
                (retention_count,),
            )
        return cursor.rowcount

    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> RunRecord:
        import json as _json
        return RunRecord(
            run_id=str(row["run_id"]),
            workflow_id=str(row["workflow_id"]),
            workflow_version=str(row["workflow_version"]),
            trigger_id=str(row["trigger_id"]),
            state=str(row["state"]),
            current_node=str(row["current_node"]),
            started_at=str(row["started_at"]),
            ended_at=str(row["ended_at"]),
            duration_seconds=float(row["duration_seconds"]),
            trigger_context=_json.loads(str(row["trigger_context"])),
            inputs=_json.loads(str(row["inputs"])),
            outputs=_json.loads(str(row["outputs"])),
            error=str(row["error"]),
            error_code=str(row["error_code"]),
            retry_count=int(row["retry_count"]),
            cancellation_state=str(row["cancellation_state"]),
            trace_id=str(row["trace_id"]),
        )


class WorkflowHealthService:
    """Real workflow health derived from validity, schedule, permission, and run state."""

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory

    def summarize(self, *, workflow_id: str, definition_enabled: bool, definition_status: str) -> str:
        states = []
        if not definition_enabled:
            return "inactive"
        if definition_status in {"invalid", "quarantined"}:
            return definition_status
        with self._connection_factory() as connection:
            recent = connection.execute(
                """
                SELECT state, created_at FROM workflow_runs
                WHERE workflow_id = ? ORDER BY created_at DESC LIMIT 5
                """,
                (workflow_id,),
            ).fetchall()
            schedule = connection.execute(
                "SELECT health_state, enabled FROM workflow_schedules WHERE workflow_id = ? LIMIT 1",
                (workflow_id,),
            ).fetchone()
        if schedule is not None and not bool(schedule["enabled"]):
            states.append("inactive")
        failures = sum(1 for row in recent if row["state"] == "failed")
        if failures >= 3:
            return "failing"
        if any(row["state"] == "failed" for row in recent):
            return "degraded"
        if schedule is not None and str(schedule["health_state"]) == "unhealthy":
            return "attention required"
        return "healthy"