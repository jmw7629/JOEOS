"""Governance: escalations, interventions, approvals, and scope changes.

Approvals never self-authorize: a requester can never approve their own action
when self_approval_blocked is set (the default). Escalations and interventions
route to the organization's escalation path (the user by default). Nothing
here grants permission on its own; the authoritative security and Tool Broker
systems remain the sole source of authority.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from typing import Callable, Optional, Tuple

from .models import ApprovalRecord, EscalationRecord, InterventionRecord


_PROCESS_APPROVAL_ACTIONS = (
    "delete",
    "remove",
    "destroy",
    "grant",
    "revoke",
    "permission",
    "deploy",
    "release",
    "external",
    "outbound",
    "budget increase",
    "scope expansion",
    "go live",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(*parts: str) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:24]


def requires_approval(action: str) -> bool:
    """Heuristic helper: whether an action string looks approval-worthy."""
    lowered = action.strip().lower()
    for marker in _PROCESS_APPROVAL_ACTIONS:
        if marker in lowered:
            return True
    return False


class GovernanceService:
    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory

    # ---- escalations ----

    def open_escalation(self, record: EscalationRecord) -> EscalationRecord:
        now = _now()
        stored = record.model_copy(update={"state": "open", "created_at": now, "updated_at": now})
        self._upsert_escalation(stored)
        return stored

    def resolve_escalation(self, escalation_id: str, *, response: str, state: str = "resolved") -> Optional[EscalationRecord]:
        record = self.escalation(escalation_id)
        if record is None:
            return None
        now = _now()
        updated = record.model_copy(update={"state": state, "response": response, "updated_at": now})
        self._upsert_escalation(updated)
        return updated

    def escalation(self, escalation_id: str) -> Optional[EscalationRecord]:
        with self._connection_factory() as connection:
            row = connection.execute("SELECT * FROM org_escalations WHERE escalation_id = ?", (escalation_id,)).fetchone()
        return _escalation_from_row(row) if row else None

    def escalations(self, *, state: Optional[str] = None, mission_id: Optional[str] = None, limit: int = 100) -> Tuple[EscalationRecord, ...]:
        clauses, params = [], []
        if state:
            clauses.append("state = ?")
            params.append(state)
        if mission_id:
            clauses.append("mission_id = ?")
            params.append(mission_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM org_escalations%s ORDER BY created_at DESC LIMIT ?" % where,
                params + [max(1, min(500, limit))],
            ).fetchall()
        return tuple(_escalation_from_row(row) for row in rows)

    def _upsert_escalation(self, record: EscalationRecord) -> None:
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO org_escalations (
                    escalation_id, source, mission_id, task_id, reason, severity,
                    evidence, attempted_resolutions, required_decision, options,
                    consequence_of_delay, privacy_classification, responsible_recipient,
                    state, response, expires_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.escalation_id, record.source, record.mission_id, record.task_id,
                    record.reason, record.severity, "|".join(record.evidence),
                    "|".join(record.attempted_resolutions), record.required_decision,
                    "|".join(record.options), record.consequence_of_delay,
                    record.privacy_classification, record.responsible_recipient,
                    record.state, record.response, record.expires_at,
                    record.created_at, record.updated_at,
                ),
            )

    # ---- interventions ----

    def open_intervention(self, record: InterventionRecord) -> InterventionRecord:
        now = _now()
        stored = record.model_copy(update={"state": "pending", "created_at": now, "updated_at": now})
        self._upsert_intervention(stored)
        return stored

    def respond_intervention(self, intervention_id: str, *, response: str, approved: bool, work_can_continue: bool) -> Optional[InterventionRecord]:
        record = self.intervention(intervention_id)
        if record is None:
            return None
        now = _now()
        updated = record.model_copy(
            update={
                "response": response,
                "state": "approved" if approved else "denied",
                "work_can_continue": work_can_continue,
                "updated_at": now,
            }
        )
        self._upsert_intervention(updated)
        return updated

    def intervention(self, intervention_id: str) -> Optional[InterventionRecord]:
        with self._connection_factory() as connection:
            row = connection.execute("SELECT * FROM org_interventions WHERE intervention_id = ?", (intervention_id,)).fetchone()
        return _intervention_from_row(row) if row else None

    def interventions(self, *, state: Optional[str] = None, limit: int = 100) -> Tuple[InterventionRecord, ...]:
        with self._connection_factory() as connection:
            if state:
                rows = connection.execute("SELECT * FROM org_interventions WHERE state = ? ORDER BY created_at DESC LIMIT ?", (state, limit)).fetchall()
            else:
                rows = connection.execute("SELECT * FROM org_interventions ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return tuple(_intervention_from_row(row) for row in rows)

    def _upsert_intervention(self, record: InterventionRecord) -> None:
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO org_interventions (
                    intervention_id, need, rationale, mission_id, task_id, options,
                    recommended_option, evidence, risk, consequence, deadline,
                    work_can_continue, state, response, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.intervention_id, record.need, record.rationale, record.mission_id,
                    record.task_id, "|".join(record.options), record.recommended_option,
                    "|".join(record.evidence), record.risk, record.consequence,
                    record.deadline, int(record.work_can_continue), record.state,
                    record.response, record.created_at, record.updated_at,
                ),
            )

    # ---- approvals ----

    def request_approval(self, record: ApprovalRecord) -> ApprovalRecord:
        now = _now()
        stored = record.model_copy(update={"state": "pending", "created_at": now, "updated_at": now})
        self._upsert_approval(stored)
        return stored

    def approve(self, approval_id: str, *, approver: str) -> Optional[ApprovalRecord]:
        """Approve an action. Self-approval is blocked when flag is set."""
        record = self.approval(approval_id)
        if record is None or record.state != "pending":
            return None
        if record.self_approval_blocked and approver == record.requester:
            return None
        now = _now()
        updated = record.model_copy(update={"state": "approved", "approver": approver, "updated_at": now})
        self._upsert_approval(updated)
        return updated

    def deny(self, approval_id: str, *, approver: str) -> Optional[ApprovalRecord]:
        record = self.approval(approval_id)
        if record is None or record.state != "pending":
            return None
        now = _now()
        updated = record.model_copy(update={"state": "denied", "approver": approver, "updated_at": now})
        self._upsert_approval(updated)
        return updated

    def approval(self, approval_id: str) -> Optional[ApprovalRecord]:
        with self._connection_factory() as connection:
            row = connection.execute("SELECT * FROM org_approvals WHERE approval_id = ?", (approval_id,)).fetchone()
        return _approval_from_row(row) if row else None

    def approvals(self, *, state: Optional[str] = None, mission_id: Optional[str] = None, limit: int = 100) -> Tuple[ApprovalRecord, ...]:
        clauses, params = [], []
        if state:
            clauses.append("state = ?")
            params.append(state)
        if mission_id:
            clauses.append("mission_id = ?")
            params.append(mission_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM org_approvals%s ORDER BY created_at DESC LIMIT ?" % where,
                params + [max(1, min(500, limit))],
            ).fetchall()
        return tuple(_approval_from_row(row) for row in rows)

    def pending_count(self) -> int:
        with self._connection_factory() as connection:
            row = connection.execute("SELECT COUNT(*) FROM org_approvals WHERE state = 'pending'").fetchone()
        return int(row[0] if row else 0)

    def open_escalation_count(self) -> int:
        with self._connection_factory() as connection:
            row = connection.execute("SELECT COUNT(*) FROM org_escalations WHERE state = 'open'").fetchone()
        return int(row[0] if row else 0)

    def _upsert_approval(self, record: ApprovalRecord) -> None:
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO org_approvals (
                    approval_id, requester, mission_id, task_id, action, rationale,
                    evidence, risk, self_approval_blocked, state, approver, expires_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.approval_id, record.requester, record.mission_id, record.task_id,
                    record.action, record.rationale, "|".join(record.evidence),
                    record.risk, int(record.self_approval_blocked), record.state,
                    record.approver, record.expires_at, record.created_at, record.updated_at,
                ),
            )


def _escalation_from_row(row) -> EscalationRecord:
    return EscalationRecord(
        escalation_id=row["escalation_id"], source=row["source"], mission_id=row["mission_id"],
        task_id=row["task_id"], reason=row["reason"], severity=row["severity"],
        evidence=tuple(x for x in row["evidence"].split("|") if x),
        attempted_resolutions=tuple(x for x in row["attempted_resolutions"].split("|") if x),
        required_decision=row["required_decision"],
        options=tuple(x for x in row["options"].split("|") if x),
        consequence_of_delay=row["consequence_of_delay"],
        privacy_classification=row["privacy_classification"],
        responsible_recipient=row["responsible_recipient"], state=row["state"],
        response=row["response"], expires_at=row["expires_at"],
        created_at=row["created_at"], updated_at=row["updated_at"],
    )


def _intervention_from_row(row) -> InterventionRecord:
    return InterventionRecord(
        intervention_id=row["intervention_id"], need=row["need"], rationale=row["rationale"],
        mission_id=row["mission_id"], task_id=row["task_id"],
        options=tuple(x for x in row["options"].split("|") if x),
        recommended_option=row["recommended_option"],
        evidence=tuple(x for x in row["evidence"].split("|") if x),
        risk=row["risk"], consequence=row["consequence"], deadline=row["deadline"],
        work_can_continue=bool(row["work_can_continue"]), state=row["state"],
        response=row["response"], created_at=row["created_at"], updated_at=row["updated_at"],
    )


def _approval_from_row(row) -> ApprovalRecord:
    return ApprovalRecord(
        approval_id=row["approval_id"], requester=row["requester"],
        mission_id=row["mission_id"], task_id=row["task_id"], action=row["action"],
        rationale=row["rationale"], evidence=tuple(x for x in row["evidence"].split("|") if x),
        risk=row["risk"], self_approval_blocked=bool(row["self_approval_blocked"]),
        state=row["state"], approver=row["approver"], expires_at=row["expires_at"],
        created_at=row["created_at"], updated_at=row["updated_at"],
    )