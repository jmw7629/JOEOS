"""Organizational memory proposals.

Accepted lessons are proposed, never self-promoted. Each proposal records who
proposed it, what evidence supports it, and who reviewed it. A proposal is
accepted, rejected, or superseded by an explicit reviewer decision; it is never
silently promoted to accepted.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Callable, Optional, Tuple

from .models import OrgMemoryProposal


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(*parts: str) -> str:
    import hashlib
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:24]


class MemoryProposalService:
    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory

    def propose(self, record: OrgMemoryProposal) -> OrgMemoryProposal:
        now = _now()
        stored = record.model_copy(update={"state": "proposed", "created_at": now, "updated_at": now})
        self._upsert(stored)
        return stored

    def review(self, proposal_id: str, *, action: str, note: str = "", reviewer: str = "user") -> Optional[OrgMemoryProposal]:
        if action not in {"accept", "reject", "supersede"}:
            return None
        record = self.get(proposal_id)
        if record is None or record.state != "proposed":
            return None
        new_state = {"accept": "accepted", "reject": "rejected", "supersede": "superseded"}[action]
        now = _now()
        updated = record.model_copy(update={"state": new_state, "reviewer": reviewer, "review_note": note, "updated_at": now})
        self._upsert(updated)
        return updated

    def get(self, proposal_id: str) -> Optional[OrgMemoryProposal]:
        with self._connection_factory() as connection:
            row = connection.execute("SELECT * FROM org_memory_proposals WHERE proposal_id = ?", (proposal_id,)).fetchone()
        return _proposal_from_row(row) if row else None

    def list(self, *, state: Optional[str] = None, limit: int = 100) -> Tuple[OrgMemoryProposal, ...]:
        with self._connection_factory() as connection:
            if state:
                rows = connection.execute("SELECT * FROM org_memory_proposals WHERE state = ? ORDER BY created_at DESC LIMIT ?", (state, limit)).fetchall()
            else:
                rows = connection.execute("SELECT * FROM org_memory_proposals ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return tuple(_proposal_from_row(row) for row in rows)

    def pending_count(self) -> int:
        with self._connection_factory() as connection:
            row = connection.execute("SELECT COUNT(*) FROM org_memory_proposals WHERE state = 'proposed'").fetchone()
        return int(row[0] if row else 0)

    def _upsert(self, record: OrgMemoryProposal) -> None:
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO org_memory_proposals (
                    proposal_id, mission_id, kind, title, content, proposer,
                    evidence, state, reviewer, review_note, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.proposal_id, record.mission_id, record.kind, record.title,
                    record.content, record.proposer, "|".join(record.evidence),
                    record.state, record.reviewer, record.review_note,
                    record.created_at, record.updated_at,
                ),
            )


def _proposal_from_row(row) -> OrgMemoryProposal:
    return OrgMemoryProposal(
        proposal_id=row["proposal_id"], mission_id=row["mission_id"], kind=row["kind"],
        title=row["title"], content=row["content"], proposer=row["proposer"],
        evidence=tuple(x for x in row["evidence"].split("|") if x),
        state=row["state"], reviewer=row["reviewer"], review_note=row["review_note"],
        created_at=row["created_at"], updated_at=row["updated_at"],
    )