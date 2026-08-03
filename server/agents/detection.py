"""Deadlock, loop, and stagnation detection for mission task graphs.

Detection is derived from authoritative task state, not guesswork:
- deadlock: a non-empty cycle of 'blocked' tasks with no ready task in between;
- loop: a task whose status oscillates (e.g. repeatedly cycling through
  executing/awaiting reviews) beyond the mission retry/review budget;
- stagnation: an active mission that has not changed state for an extended
  time and has no completed work.

Findings are recorded as DetectionEvent rows and are never auto-resolved
without evidence.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Callable, Optional, Tuple

from .models import DetectionEvent

DEFAULT_STAGNATION_GRACE_MINUTES = 30
DEFAULT_REVIEW_LOOP_LIMIT = 12


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(*parts: str) -> str:
    import hashlib
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:24]


class DetectionService:
    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory

    def scan_mission(self, mission_id: str, *, stagnation_minutes: int = DEFAULT_STAGNATION_GRACE_MINUTES) -> Tuple[DetectionEvent, ...]:
        """Run all detections for a mission and return open/new findings."""
        events = []
        deadlock = self._detect_deadlock(mission_id)
        if deadlock:
            events.append(deadlock)
        loop = self._detect_loop(mission_id)
        if loop:
            events.append(loop)
        stagnation = self._detect_stagnation(mission_id, stagnation_minutes=stagnation_minutes)
        if stagnation:
            events.append(stagnation)
        return tuple(events)

    def _detect_deadlock(self, mission_id: str) -> Optional[DetectionEvent]:
        with self._connection_factory() as connection:
            tasks = connection.execute(
                "SELECT task_id, status FROM org_tasks WHERE mission_id = ?", (mission_id,)
            ).fetchall()
            deps = connection.execute(
                "SELECT source_task_id, target_task_id FROM org_task_dependencies WHERE mission_id = ?",
                (mission_id,),
            ).fetchall()
        status = {r["task_id"]: r["status"] for r in tasks}
        parents = {}
        for d in deps:
            parents.setdefault(d["source_task_id"], set()).add(d["target_task_id"])
        blocked_cycle = self._find_cycle_among_blocked(status, parents)
        if not blocked_cycle:
            return None
        detail = "Deadlock: cyclic dependency among blocked tasks %s." % ", ".join(blocked_cycle)
        event = DetectionEvent(
            detection_id="det_" + _id("deadlock", mission_id)[:22],
            kind="deadlock",
            mission_id=mission_id,
            task_ids=blocked_cycle,
            agent_ids=(),
            detail=detail,
            state="open",
            created_at=_now(),
            updated_at=_now(),
        )
        self._upsert(event)
        return event

    def _find_cycle_among_blocked(self, status: dict, parents: dict) -> Tuple[str, ...]:
        blocked = {tid for tid, st in status.items() if st == "blocked"}
        if not blocked:
            return ()
        adj = {tid: [] for tid in blocked}
        for source, targets in parents.items():
            if source in blocked:
                adj[source] = [t for t in targets if t in blocked]
        visiting, visited, stack, cycles = set(), set(), [], []

        def dfs(node):
            visiting.add(node)
            stack.append(node)
            for nxt in adj.get(node, []):
                if nxt in visiting:
                    idx = stack.index(nxt)
                    cycle = tuple(stack[idx:] + [nxt])
                    if cycle not in cycles:
                        cycles.append(cycle)
                elif nxt not in visited:
                    dfs(nxt)
            stack.pop()
            visiting.discard(node)
            visited.add(node)

        for node in sorted(adj):
            if node not in visited:
                dfs(node)
        return cycles[0] if cycles else ()

    def _detect_loop(self, mission_id: str) -> Optional[DetectionEvent]:
        with self._connection_factory() as connection:
            retries = connection.execute(
                "SELECT COALESCE(SUM(retry_count), 0) FROM org_tasks WHERE mission_id = ?",
                (mission_id,),
            ).fetchone()[0]
            reviews = connection.execute(
                "SELECT COUNT(*) FROM org_reviews WHERE mission_id = ?", (mission_id,)
            ).fetchone()[0]
        if retries + reviews < DEFAULT_REVIEW_LOOP_LIMIT:
            return None
        detail = "Excessive retry/review activity (retries=%d, reviews=%d) suggests a work loop." % (retries, reviews)
        event = DetectionEvent(
            detection_id="det_" + _id("loop", mission_id)[:22],
            kind="loop",
            mission_id=mission_id,
            task_ids=(),
            agent_ids=(),
            detail=detail,
            state="open",
            created_at=_now(),
            updated_at=_now(),
        )
        self._upsert(event)
        return event

    def _detect_stagnation(self, mission_id: str, *, stagnation_minutes: int) -> Optional[DetectionEvent]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT updated_at, status FROM org_missions WHERE mission_id = ?", (mission_id,)
            ).fetchone()
        if row is None:
            return None
        if row["status"] in {"completed", "cancelled", "failed", "timed_out", "archived"}:
            return None
        updated = _parse_time(row["updated_at"])
        if updated is None:
            return None
        elapsed_minutes = (datetime.now(timezone.utc) - updated).total_seconds() / 60
        if elapsed_minutes < stagnation_minutes:
            return None
        detail = "Mission stagnant: no state change for %.0f minutes." % elapsed_minutes
        event = DetectionEvent(
            detection_id="det_" + _id("stagnation", mission_id)[:22],
            kind="stagnation",
            mission_id=mission_id,
            task_ids=(),
            agent_ids=(),
            detail=detail,
            state="open",
            created_at=_now(),
            updated_at=_now(),
        )
        self._upsert(event)
        return event

    def resolve(self, detection_id: str, *, resolution: str) -> Optional[DetectionEvent]:
        record = self.detection(detection_id)
        if record is None:
            return None
        now = _now()
        updated = record.model_copy(update={"state": "resolved", "resolution": resolution, "updated_at": now})
        with self._connection_factory() as connection:
            connection.execute(
                "UPDATE org_detections SET state = 'resolved', resolution = ?, updated_at = ? WHERE detection_id = ?",
                (resolution, now, detection_id),
            )
        return updated

    def detection(self, detection_id: str) -> Optional[DetectionEvent]:
        with self._connection_factory() as connection:
            row = connection.execute("SELECT * FROM org_detections WHERE detection_id = ?", (detection_id,)).fetchone()
        return _detection_from_row(row) if row else None

    def detections(self, *, mission_id: Optional[str] = None, state: Optional[str] = None, limit: int = 100) -> Tuple[DetectionEvent, ...]:
        clauses, params = [], []
        if mission_id:
            clauses.append("mission_id = ?")
            params.append(mission_id)
        if state:
            clauses.append("state = ?")
            params.append(state)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM org_detections%s ORDER BY created_at DESC LIMIT ?" % where,
                params + [max(1, min(500, limit))],
            ).fetchall()
        return tuple(_detection_from_row(row) for row in rows)

    def open_count(self) -> int:
        with self._connection_factory() as connection:
            row = connection.execute("SELECT COUNT(*) FROM org_detections WHERE state = 'open'").fetchone()
        return int(row[0] if row else 0)

    def _upsert(self, record: DetectionEvent) -> None:
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO org_detections (
                    detection_id, kind, mission_id, task_ids, agent_ids, detail,
                    evidence, state, resolution, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.detection_id, record.kind, record.mission_id,
                    "|".join(record.task_ids), "|".join(record.agent_ids),
                    record.detail, "|".join(record.evidence), record.state,
                    record.resolution, record.created_at, record.updated_at,
                ),
            )


def _parse_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _detection_from_row(row) -> DetectionEvent:
    return DetectionEvent(
        detection_id=row["detection_id"], kind=row["kind"], mission_id=row["mission_id"],
        task_ids=tuple(x for x in row["task_ids"].split("|") if x),
        agent_ids=tuple(x for x in row["agent_ids"].split("|") if x),
        detail=row["detail"], evidence=tuple(x for x in row["evidence"].split("|") if x),
        state=row["state"], resolution=row["resolution"],
        created_at=row["created_at"], updated_at=row["updated_at"],
    )