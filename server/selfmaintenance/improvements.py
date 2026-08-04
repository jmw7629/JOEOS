"""Evidence-based improvement proposals for the Continuous Improvement loop.

Proposals are detected only from real, authoritative state (backups, recovery
flags, memory hygiene, schema). A proposal never self-approves: transitioning
to `applied` requires the operator to call `apply`, which the API gates behind
governance approval. Every proposal carries the concrete evidence used to make
it and an explicit `apply_action` bound to a real service operation.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple

from .models import ImprovementProposal

Executor = Callable[[...], object]  # accepts no positional arguments by convention


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ImprovementRegistry:
    """Persists improvement proposals and resolves them with real executors."""

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._executors: Dict[str, Callable[[], object]] = {}

    def _prepare(self) -> None:
        with self._connection_factory() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS improvements (
                    improvement_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    category TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    state TEXT NOT NULL,
                    apply_action TEXT,
                    detail TEXT NOT NULL,
                    proposed_at TEXT NOT NULL,
                    resolved_at TEXT
                )
                """
            )

    def register_executor(self, apply_action: str, executor: Callable[[], object]) -> None:
        self._executors[apply_action] = executor

    def has_executor(self, apply_action: str) -> bool:
        return apply_action in self._executors

    def record(self, proposal: ImprovementProposal) -> None:
        self._prepare()
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO improvements
                    (improvement_id, title, category, evidence, priority, state,
                     apply_action, detail, proposed_at, resolved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal.improvement_id,
                    proposal.title,
                    proposal.category,
                    json.dumps(list(proposal.evidence)),
                    proposal.priority,
                    proposal.state,
                    proposal.apply_action,
                    proposal.detail,
                    proposal.proposed_at,
                    proposal.resolved_at,
                ),
            )

    def list(self) -> List[ImprovementProposal]:
        self._prepare()
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM improvements ORDER BY proposed_at DESC, improvement_id"
            ).fetchall()
        return [self._row(row) for row in rows]

    def _row(self, row) -> ImprovementProposal:
        return ImprovementProposal(
            improvement_id=row["improvement_id"],
            title=row["title"],
            category=row["category"],
            evidence=tuple(json.loads(row["evidence"] or "[]")),
            priority=row["priority"],
            state=row["state"],
            apply_action=row["apply_action"],
            detail=row["detail"],
            proposed_at=row["proposed_at"],
            resolved_at=row["resolved_at"],
        )

    def get(self, improvement_id: str) -> Optional[ImprovementProposal]:
        for proposal in self.list():
            if proposal.improvement_id == improvement_id:
                return proposal
        return None

    def set_state(self, improvement_id: str, state: str, resolved_at: Optional[str] = None) -> None:
        with self._connection_factory() as connection:
            connection.execute(
                "UPDATE improvements SET state = ?, resolved_at = ? WHERE improvement_id = ?",
                (state, resolved_at or _now_iso(), improvement_id),
            )

    def apply(self, improvement_id: str) -> Tuple[bool, str]:
        """Execute the proposal's bound action. Returns (success, detail)."""
        proposal = self.get(improvement_id)
        if proposal is None:
            return (False, "improvement not found")
        if proposal.state != "approved":
            return (False, "improvement must be approved before it can be applied")
        action = proposal.apply_action
        executor = self._executors.get(action) if action else None
        if not action:
            self.set_state(improvement_id, "not_actionable")
            return (False, "proposal has no automated action")
        if executor is None:
            return (False, "no executor is wired for action %r" % action)
        try:
            result = executor()
        except Exception as exc:
            self.set_state(improvement_id, "dismissed")
            return (False, "executor failed: %s: %s" % (type(exc).__name__, exc))
        self.set_state(improvement_id, "applied")
        detail = "applied %s" % action
        if result is not None and not isinstance(result, bool):
            detail = "%s; result: %s" % (detail, result)
        return (True, detail)


def detect(observations: Dict[str, object], proposed_at: Optional[str] = None) -> List[ImprovementProposal]:
    """Detect improvement proposals from real observations.

    `observations` is a closed set of keys supplied by the coordinator from
    live state. Proposals are only emitted when real state warrants them; a
    healthy signal produces no proposal.
    """
    stamp = proposed_at or _now_iso()
    proposals: List[ImprovementProposal] = []

    backups = observations.get("verified_backups")
    if backups is not None and int(backups) == 0:
        count = int(observations.get("total_backups", 0))
        proposals.append(
            ImprovementProposal(
                improvement_id="backup.initial",
                title="Create and verify an initial backup",
                category="recovery",
                evidence=("No verified backup exists (%d backup(s) total)." % count,),
                priority="high",
                state="proposed",
                apply_action="create_backup",
                detail="A verified backup is the first line of recovery. Create and verify one now.",
                proposed_at=stamp,
            )
        )

    memory_due = observations.get("memory_due")
    if memory_due is not None and int(memory_due) > 0:
        proposals.append(
            ImprovementProposal(
                improvement_id="memory.expire",
                title="Expire due memory records",
                category="hygiene",
                evidence=("%d memory record(s) are due for expiry." % int(memory_due),),
                priority="medium",
                state="proposed",
                apply_action="expire_memory",
                detail="Review and expire memory records whose retention has lapsed.",
                proposed_at=stamp,
            )
        )

    if observations.get("safe_mode"):
        proposals.append(
            ImprovementProposal(
                improvement_id="recovery.exit_safe_mode",
                title="Exit Safe Mode after recovery",
                category="recovery",
                evidence=("Safe Mode is currently active, restricting plugins, workflows, agents, and remote clients.",),
                priority="high",
                state="proposed",
                apply_action="exit_safe_mode",
                detail="Safe Mode limits functionality. Exit it once the issue is resolved.",
                proposed_at=stamp,
            )
        )

    if observations.get("repair_mode"):
        proposals.append(
            ImprovementProposal(
                improvement_id="recovery.exit_repair_mode",
                title="Exit Repair Mode after repair",
                category="recovery",
                evidence=("Repair Mode is currently active.",),
                priority="high",
                state="proposed",
                apply_action="exit_repair_mode",
                detail="Repair Mode pauses non-essential activity. Exit it once repair completes.",
                proposed_at=stamp,
            )
        )

    if observations.get("no_telemetry"):
        proposals.append(
            ImprovementProposal(
                improvement_id="telemetry.first_sample",
                title="Record the first telemetry sample",
                category="observability",
                evidence=("No telemetry sample has been recorded yet.",),
                priority="low",
                state="not_actionable",
                apply_action=None,
                detail="Telemetry is recorded automatically by the collector on its first cycle.",
                proposed_at=stamp,
            )
        )

    if observations.get("future_schema"):
        proposals.append(
            ImprovementProposal(
                improvement_id="schema.future_detected",
                title="Database schema is newer than this version supports",
                category="integrity",
                evidence=("A store declares a schema version newer than supported.",),
                priority="high",
                state="not_actionable",
                apply_action=None,
                detail="Writes are blocked until the application version can support the schema. Update the application.",
                proposed_at=stamp,
            )
        )

    return proposals


def reconcile(existing: List[ImprovementProposal], detected: List[ImprovementProposal]) -> List[ImprovementProposal]:
    """Merge detected proposals into the persisted set without clobbering
    a proposal that the operator has already resolved or approved."""
    terminal = {"applied", "dismissed"}
    by_id = {}
    for existing in existing:
        by_id[existing.improvement_id] = existing
    for proposal in detected:
        current = by_id.get(proposal.improvement_id)
        if current is None:
            by_id[proposal.improvement_id] = proposal
        elif current.state not in terminal and current.state != "approved":
            # refresh evidence/title while preserving the unresolved identity
            by_id[proposal.improvement_id] = proposal
    return sorted(by_id.values(), key=lambda p: (p.proposed_at, p.improvement_id))