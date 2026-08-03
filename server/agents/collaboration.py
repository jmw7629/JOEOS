"""Collaboration, review, disagreement, and consultation services.

Structured messages and handoffs carry only concise, evidence-based content.
Message content never alters permissions. Reviewers inspect real evidence and
artifacts; no gate passes because a reviewer stayed silent. Dissent is always
preserved in consensus records. No hidden reasoning is stored.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from typing import Callable, Optional, Tuple

from .models import (
    ArtifactRecord,
    CollaborationMessage,
    ConsensusResult,
    ConsultationRecord,
    DebateRecord,
    DisagreementRecord,
    HandoffRecord,
    QualityGate,
    ReviewFinding,
    ReviewRecord,
)

SECRET_HINT = ("api_key", "password", "secret", "token", "private_key", "credential", "bearer", "BEGIN PRIVATE KEY")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(*parts: str) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:24]


def _redact(content: str) -> Tuple[str, bool]:
    lowered = content.lower()
    redacted = False
    for hint in SECRET_HINT:
        if hint in lowered:
            content = content.replace(hint, "***")
            redacted = True
    return content, redacted


class CollaborationService:
    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory

    # ---- messaging ----

    def send_message(self, record: CollaborationMessage) -> CollaborationMessage:
        content, redacted = _redact(record.content)
        stored = record.model_copy(
            update={
                "content": content,
                "redacted": redacted or record.redacted,
                "status": "sent",
                "created_at": record.created_at or _now(),
            }
        )
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO org_messages (
                    message_id, sender, recipient, mission_id, task_id, thread_kind,
                    message_type, content, payload, related_evidence, related_artifacts,
                    priority, privacy_classification, requires_acknowledgement,
                    acknowledged, response_deadline, trace_id, status, redacted,
                    created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stored.message_id, stored.sender, stored.recipient, stored.mission_id,
                    stored.task_id, stored.thread_kind, stored.message_type, stored.content,
                    stored.payload, "|".join(stored.related_evidence),
                    "|".join(stored.related_artifacts), stored.priority,
                    stored.privacy_classification, int(stored.requires_acknowledgement),
                    int(stored.acknowledged), stored.response_deadline, stored.trace_id,
                    stored.status, int(stored.redacted), stored.created_at, stored.expires_at,
                ),
            )
        return stored

    def acknowledge(self, message_id: str) -> bool:
        with self._connection_factory() as connection:
            cursor = connection.execute(
                "UPDATE org_messages SET acknowledged = 1, status = 'acknowledged' WHERE message_id = ?",
                (message_id,),
            )
        return cursor.rowcount > 0

    def messages(self, *, mission_id: Optional[str] = None, task_id: Optional[str] = None, recipient: Optional[str] = None, limit: int = 50) -> Tuple[CollaborationMessage, ...]:
        clauses = []
        params = []
        if mission_id:
            clauses.append("mission_id = ?")
            params.append(mission_id)
        if task_id:
            clauses.append("task_id = ?")
            params.append(task_id)
        if recipient:
            clauses.append("(recipient = ? OR sender = ?)")
            params.extend([recipient, recipient])
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM org_messages%s ORDER BY created_at DESC LIMIT ?" % where,
                params + [max(1, min(200, limit))],
            ).fetchall()
        return tuple(_message_from_row(row) for row in rows)

    # ---- handoffs ----

    def send_handoff(self, record: HandoffRecord) -> HandoffRecord:
        now = _now()
        stored = record.model_copy(update={"state": "sent", "created_at": now, "updated_at": now})
        self._upsert_handoff(stored)
        return stored

    def respond_handoff(self, handoff_id: str, action: str, *, note: str = "") -> Optional[HandoffRecord]:
        state_map = {
            "accept": "accepted",
            "clarification_requested": "clarification_requested",
            "reject": "rejected",
            "escalate": "escalated",
        }
        if action not in state_map:
            return None
        record = self.handoff(handoff_id)
        if record is None or record.state != "sent":
            return None
        now = _now()
        updated = record.model_copy(update={"state": state_map[action], "response_note": note, "updated_at": now})
        self._upsert_handoff(updated)
        return updated

    def handoff(self, handoff_id: str) -> Optional[HandoffRecord]:
        with self._connection_factory() as connection:
            row = connection.execute("SELECT * FROM org_handoffs WHERE handoff_id = ?", (handoff_id,)).fetchone()
        return _handoff_from_row(row) if row else None

    def handoffs(self, *, mission_id: Optional[str] = None, limit: int = 50) -> Tuple[HandoffRecord, ...]:
        with self._connection_factory() as connection:
            if mission_id:
                rows = connection.execute("SELECT * FROM org_handoffs WHERE mission_id = ? ORDER BY created_at DESC LIMIT ?", (mission_id, limit)).fetchall()
            else:
                rows = connection.execute("SELECT * FROM org_handoffs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return tuple(_handoff_from_row(row) for row in rows)

    def _upsert_handoff(self, record: HandoffRecord) -> None:
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO org_handoffs (
                    handoff_id, mission_id, source_task_id, destination_task_id,
                    sending_agent, receiving_agent, objective, completed_work,
                    incomplete_work, artifacts, evidence, decisions, assumptions,
                    risks, open_questions, recommended_next_action, required_validation,
                    scope_limitations, privacy_classification, state, response_note,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.handoff_id, record.mission_id, record.source_task_id,
                    record.destination_task_id, record.sending_agent, record.receiving_agent,
                    record.objective, record.completed_work, record.incomplete_work,
                    "|".join(record.artifacts), "|".join(record.evidence),
                    "|".join(record.decisions), "|".join(record.assumptions),
                    "|".join(record.risks), "|".join(record.open_questions),
                    record.recommended_next_action, "|".join(record.required_validation),
                    "|".join(record.scope_limitations), record.privacy_classification,
                    record.state, record.response_note, record.created_at, record.updated_at,
                ),
            )

    # ---- artifacts ----

    def register_artifact(self, record: ArtifactRecord) -> ArtifactRecord:
        now = _now()
        stored = record.model_copy(update={"created_at": now, "updated_at": now, "review_state": "unreviewed", "validation_state": "none"})
        self._upsert_artifact(stored)
        return stored

    def artifact(self, artifact_id: str) -> Optional[ArtifactRecord]:
        with self._connection_factory() as connection:
            row = connection.execute("SELECT * FROM org_artifacts WHERE artifact_id = ?", (artifact_id,)).fetchone()
        return _artifact_from_row(row) if row else None

    def artifacts(self, *, mission_id: Optional[str] = None, limit: int = 100) -> Tuple[ArtifactRecord, ...]:
        with self._connection_factory() as connection:
            if mission_id:
                rows = connection.execute("SELECT * FROM org_artifacts WHERE mission_id = ? AND deletion_state = 'active' ORDER BY created_at DESC LIMIT ?", (mission_id, limit)).fetchall()
            else:
                rows = connection.execute("SELECT * FROM org_artifacts WHERE deletion_state = 'active' ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return tuple(_artifact_from_row(row) for row in rows)

    def validate_artifact(self, artifact_id: str, state: str) -> Optional[ArtifactRecord]:
        if state not in {"pending", "passed", "failed", "unavailable"}:
            return None
        record = self.artifact(artifact_id)
        if record is None:
            return None
        updated = record.model_copy(update={"validation_state": state, "updated_at": _now()})
        self._upsert_artifact(updated)
        return updated

    def _upsert_artifact(self, record: ArtifactRecord) -> None:
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO org_artifacts (
                    artifact_id, artifact_type, title, producer, mission_id, task_id,
                    project, version, storage_reference, content_hash,
                    privacy_classification, authority, review_state, validation_state,
                    source_inputs, evidence, superseded_state, deletion_state,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.artifact_id, record.artifact_type, record.title, record.producer,
                    record.mission_id, record.task_id, record.project, record.version,
                    record.storage_reference, record.content_hash,
                    record.privacy_classification, record.authority, record.review_state,
                    record.validation_state, "|".join(record.source_inputs),
                    "|".join(record.evidence), record.superseded_state,
                    record.deletion_state, record.created_at, record.updated_at,
                ),
            )

    # ---- reviews and gates ----

    def create_gate(self, record: QualityGate) -> QualityGate:
        now = _now()
        stored = record.model_copy(update={"created_at": now, "updated_at": now, "state": "not_ready"})
        self._upsert_gate(stored)
        return stored

    def gate(self, gate_id: str) -> Optional[QualityGate]:
        with self._connection_factory() as connection:
            row = connection.execute("SELECT * FROM org_gates WHERE gate_id = ?", (gate_id,)).fetchone()
        return _gate_from_row(row) if row else None

    def gates(self, *, mission_id: Optional[str] = None, state: Optional[str] = None, limit: int = 100) -> Tuple[QualityGate, ...]:
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
                "SELECT * FROM org_gates%s ORDER BY created_at DESC LIMIT ?" % where,
                params + [max(1, min(500, limit))],
            ).fetchall()
        return tuple(_gate_from_row(row) for row in rows)

    def request_review(self, record: ReviewRecord) -> ReviewRecord:
        now = _now()
        stored = record.model_copy(update={"status": "requested", "created_at": now, "updated_at": now})
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO org_reviews (
                    review_id, mission_id, task_id, gate_id, reviewer, implementer,
                    artifacts, evidence, model, runtime, findings, independence,
                    disclosure, conclusion, confidence, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stored.review_id, stored.mission_id, stored.task_id, stored.gate_id,
                    stored.reviewer, stored.implementer, "|".join(stored.artifacts),
                    "|".join(stored.evidence), stored.model, stored.runtime,
                    _serialize_findings(stored.findings), int(stored.independence),
                    stored.disclosure, stored.conclusion, stored.confidence,
                    stored.status, stored.created_at, stored.updated_at,
                ),
            )
        return stored

    def complete_review(self, review_id: str, *, conclusion: str, findings: Tuple[ReviewFinding, ...] = (), confidence: str = "medium", disclosure: str = "") -> Optional[ReviewRecord]:
        record = self.review(review_id)
        if record is None:
            return None
        now = _now()
        updated = record.model_copy(
            update={
                "conclusion": conclusion,
                "findings": findings,
                "confidence": confidence,
                "disclosure": disclosure,
                "status": "completed",
                "updated_at": now,
            }
        )
        with self._connection_factory() as connection:
            connection.execute(
                "UPDATE org_reviews SET conclusion = ?, findings = ?, confidence = ?, disclosure = ?, status = 'completed', updated_at = ? WHERE review_id = ?",
                (conclusion, _serialize_findings(findings), confidence, disclosure, now, review_id),
            )
        if updated.gate_id:
            self._apply_gate_result(updated.gate_id, conclusion)
        return updated

    def review(self, review_id: str) -> Optional[ReviewRecord]:
        with self._connection_factory() as connection:
            row = connection.execute("SELECT * FROM org_reviews WHERE review_id = ?", (review_id,)).fetchone()
        return _review_from_row(row) if row else None

    def reviews(self, *, mission_id: Optional[str] = None, limit: int = 100) -> Tuple[ReviewRecord, ...]:
        with self._connection_factory() as connection:
            if mission_id:
                rows = connection.execute("SELECT * FROM org_reviews WHERE mission_id = ? ORDER BY created_at DESC LIMIT ?", (mission_id, limit)).fetchall()
            else:
                rows = connection.execute("SELECT * FROM org_reviews ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return tuple(_review_from_row(row) for row in rows)

    def _apply_gate_result(self, gate_id: str, conclusion: str) -> None:
        state = "passed" if conclusion == "pass" else ("passed_with_conditions" if conclusion == "pass_with_conditions" else "failed")
        with self._connection_factory() as connection:
            connection.execute("UPDATE org_gates SET state = ?, updated_at = ? WHERE gate_id = ?", (state, _now(), gate_id))

    def _upsert_gate(self, record: QualityGate) -> None:
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO org_gates (
                    gate_id, mission_id, task_id, gate_type, required_reviewer_role,
                    independence_required, required_evidence, required_validation,
                    pass_criteria, failure_criteria, waiver_policy, state, reviewer,
                    findings, resolution, approval, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.gate_id, record.mission_id, record.task_id, record.gate_type,
                    record.required_reviewer_role, int(record.independence_required),
                    "|".join(record.required_evidence), "|".join(record.required_validation),
                    "|".join(record.pass_criteria), "|".join(record.failure_criteria),
                    record.waiver_policy, record.state, record.reviewer,
                    _serialize_findings(record.findings), record.resolution,
                    record.approval, record.created_at, record.updated_at,
                ),
            )

    # ---- disagreements ----

    def open_disagreement(self, record: DisagreementRecord) -> DisagreementRecord:
        now = _now()
        stored = record.model_copy(update={"state": "open", "created_at": now, "updated_at": now})
        self._upsert_disagreement(stored)
        return stored

    def resolve_disagreement(self, disagreement_id: str, *, method: str, notes: str = "", escalated: bool = False) -> Optional[DisagreementRecord]:
        record = self.disagreement(disagreement_id)
        if record is None:
            return None
        now = _now()
        updated = record.model_copy(update={"state": "resolved", "resolution_method": method, "resolution_notes": notes, "escalated": escalated, "updated_at": now})
        self._upsert_disagreement(updated)
        return updated

    def disagreement(self, disagreement_id: str) -> Optional[DisagreementRecord]:
        with self._connection_factory() as connection:
            row = connection.execute("SELECT * FROM org_disagreements WHERE disagreement_id = ?", (disagreement_id,)).fetchone()
        return _disagreement_from_row(row) if row else None

    def disagreements(self, *, mission_id: Optional[str] = None, state: Optional[str] = None, limit: int = 100) -> Tuple[DisagreementRecord, ...]:
        clauses, params = [], []
        if mission_id:
            clauses.append("mission_id = ?")
            params.append(mission_id)
        if state:
            clauses.append("state = ?")
            params.append(state)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._connection_factory() as connection:
            rows = connection.execute("SELECT * FROM org_disagreements%s ORDER BY created_at DESC LIMIT ?" % where, params + [max(1, min(500, limit))]).fetchall()
        return tuple(_disagreement_from_row(row) for row in rows)

    def _upsert_disagreement(self, record: DisagreementRecord) -> None:
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO org_disagreements (
                    disagreement_id, mission_id, task_id, participants, subject,
                    positions, evidence, assumptions, affected_decision, urgency,
                    state, resolution_method, resolution_notes, escalated,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.disagreement_id, record.mission_id, record.task_id,
                    "|".join(record.participants), record.subject,
                    "|".join(record.positions), "|".join(record.evidence),
                    "|".join(record.assumptions), record.affected_decision,
                    record.urgency, record.state, record.resolution_method,
                    record.resolution_notes, int(record.escalated),
                    record.created_at, record.updated_at,
                ),
            )

    # ---- consensus ----

    def record_consensus(self, record: ConsensusResult) -> ConsensusResult:
        now = _now()
        stored = record.model_copy(update={"created_at": now})
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO org_consensus (
                    consensus_id, subject, participants, method, positions, evidence,
                    conclusion, authority, dissent, abstentions, unresolved_concerns,
                    final_decision_source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stored.consensus_id, stored.subject, "|".join(stored.participants),
                    stored.method, "|".join(stored.positions), "|".join(stored.evidence),
                    stored.conclusion, stored.authority, "|".join(stored.dissent),
                    "|".join(stored.abstentions), "|".join(stored.unresolved_concerns),
                    stored.final_decision_source, stored.created_at,
                ),
            )
        return stored

    def consensus(self, *, limit: int = 50) -> Tuple[ConsensusResult, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute("SELECT * FROM org_consensus ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return tuple(
            ConsensusResult(
                consensus_id=r["consensus_id"], subject=r["subject"],
                participants=tuple(x for x in r["participants"].split("|") if x),
                method=r["method"], positions=tuple(x for x in r["positions"].split("|") if x),
                evidence=tuple(x for x in r["evidence"].split("|") if x),
                conclusion=r["conclusion"], authority=r["authority"],
                dissent=tuple(x for x in r["dissent"].split("|") if x),
                abstentions=tuple(x for x in r["abstentions"].split("|") if x),
                unresolved_concerns=tuple(x for x in r["unresolved_concerns"].split("|") if x),
                final_decision_source=r["final_decision_source"], created_at=r["created_at"],
            )
            for r in rows
        )

    # ---- debates ----

    def create_debate(self, record: DebateRecord) -> DebateRecord:
        now = _now()
        stored = record.model_copy(update={"state": "open", "created_at": now, "updated_at": now})
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO org_debates (
                    debate_id, question, participants, max_rounds, max_tokens,
                    time_limit_minutes, round_count, state, synthesis, escalation_path,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stored.debate_id, stored.question, "|".join(stored.participants),
                    stored.max_rounds, stored.max_tokens, stored.time_limit_minutes,
                    stored.round_count, stored.state, stored.synthesis,
                    stored.escalation_path, stored.created_at, stored.updated_at,
                ),
            )
        return stored

    def advance_debate(self, debate_id: str, *, rounds: int = 1) -> Optional[DebateRecord]:
        with self._connection_factory() as connection:
            row = connection.execute("SELECT * FROM org_debates WHERE debate_id = ?", (debate_id,)).fetchone()
        if row is None:
            return None
        if row["round_count"] + rounds > row["max_rounds"]:
            return None
        with self._connection_factory() as connection:
            connection.execute(
                "UPDATE org_debates SET round_count = round_count + ?, state = 'in_progress', updated_at = ? WHERE debate_id = ?",
                (rounds, _now(), debate_id),
            )
        return self._debate_from_connection(debate_id)

    def conclude_debate(self, debate_id: str, *, synthesis: str) -> Optional[DebateRecord]:
        with self._connection_factory() as connection:
            cursor = connection.execute(
                "UPDATE org_debates SET state = 'concluded', synthesis = ?, updated_at = ? WHERE debate_id = ?",
                (synthesis, _now(), debate_id),
            )
            if cursor.rowcount == 0:
                return None
        return self._debate_from_connection(debate_id)

    def debate(self, debate_id: str) -> Optional[DebateRecord]:
        return self._debate_from_connection(debate_id)

    def _debate_from_connection(self, debate_id: str) -> Optional[DebateRecord]:
        with self._connection_factory() as connection:
            row = connection.execute("SELECT * FROM org_debates WHERE debate_id = ?", (debate_id,)).fetchone()
        if row is None:
            return None
        return DebateRecord(
            debate_id=row["debate_id"], question=row["question"],
            participants=tuple(x for x in row["participants"].split("|") if x),
            max_rounds=row["max_rounds"], max_tokens=row["max_tokens"],
            time_limit_minutes=row["time_limit_minutes"], round_count=row["round_count"],
            state=row["state"], synthesis=row["synthesis"], escalation_path=row["escalation_path"],
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    # ---- consultations ----

    def request_consultation(self, record: ConsultationRecord) -> ConsultationRecord:
        now = _now()
        stored = record.model_copy(update={"state": "requested", "created_at": now, "updated_at": now})
        self._upsert_consultation(stored)
        return stored

    def respond_consultation(self, consultation_id: str, *, response: str, conclusion: str, confidence: str = "medium", limitations: Tuple[str, ...] = ()) -> Optional[ConsultationRecord]:
        record = self.consultation(consultation_id)
        if record is None:
            return None
        now = _now()
        updated = record.model_copy(
            update={"response": response, "conclusion": conclusion, "confidence": confidence, "limitations": limitations, "state": "responded", "updated_at": now}
        )
        self._upsert_consultation(updated)
        return updated

    def consultation(self, consultation_id: str) -> Optional[ConsultationRecord]:
        with self._connection_factory() as connection:
            row = connection.execute("SELECT * FROM org_consultations WHERE consultation_id = ?", (consultation_id,)).fetchone()
        return _consultation_from_row(row) if row else None

    def consultations(self, *, limit: int = 50) -> Tuple[ConsultationRecord, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute("SELECT * FROM org_consultations ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return tuple(_consultation_from_row(row) for row in rows)

    def _upsert_consultation(self, record: ConsultationRecord) -> None:
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO org_consultations (
                    consultation_id, question, requester, specialist, context, evidence,
                    constraints, required_expertise, tool_use_allowed, deadline,
                    privacy_classification, budget, response, conclusion, assumptions,
                    confidence, limitations, recommended_action, affected_risk, state,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.consultation_id, record.question, record.requester,
                    record.specialist, record.context, "|".join(record.evidence),
                    "|".join(record.constraints), "|".join(record.required_expertise),
                    int(record.tool_use_allowed), record.deadline,
                    record.privacy_classification, record.budget.model_dump_json(),
                    record.response, record.conclusion, "|".join(record.assumptions),
                    record.confidence, "|".join(record.limitations),
                    record.recommended_action, record.affected_risk, record.state,
                    record.created_at, record.updated_at,
                ),
            )


# ---- serialization ----

def _serialize_findings(findings: Tuple[ReviewFinding, ...]) -> str:
    return "¬".join(
        "|".join((f.finding_id, f.review_id, f.severity, f.summary, "|".join(f.evidence), f.required_action, f.resolution, f.resolution_rationale))
        for f in findings
    )


def _deserialize_findings(raw: str) -> Tuple[ReviewFinding, ...]:
    result = []
    for part in raw.split("¬"):
        if not part:
            continue
        fields = part.split("|")
        if len(fields) < 4:
            continue
        result.append(
            ReviewFinding(
                finding_id=fields[0], review_id=fields[1], severity=fields[2],
                summary=fields[3], evidence=tuple(x for x in fields[4].split("|") if x),
                required_action=fields[5] if len(fields) > 5 else "",
                resolution=fields[6] if len(fields) > 6 else "open",
                resolution_rationale=fields[7] if len(fields) > 7 else "",
            )
        )
    return tuple(result)


def _message_from_row(row) -> CollaborationMessage:
    return CollaborationMessage(
        message_id=row["message_id"], sender=row["sender"], recipient=row["recipient"],
        mission_id=row["mission_id"], task_id=row["task_id"], thread_kind=row["thread_kind"],
        message_type=row["message_type"], content=row["content"], payload=row["payload"],
        related_evidence=tuple(x for x in row["related_evidence"].split("|") if x),
        related_artifacts=tuple(x for x in row["related_artifacts"].split("|") if x),
        priority=row["priority"], privacy_classification=row["privacy_classification"],
        requires_acknowledgement=bool(row["requires_acknowledgement"]),
        acknowledged=bool(row["acknowledged"]), response_deadline=row["response_deadline"],
        trace_id=row["trace_id"], status=row["status"], redacted=bool(row["redacted"]),
        created_at=row["created_at"], expires_at=row["expires_at"],
    )


def _handoff_from_row(row) -> HandoffRecord:
    return HandoffRecord(
        handoff_id=row["handoff_id"], mission_id=row["mission_id"],
        source_task_id=row["source_task_id"], destination_task_id=row["destination_task_id"],
        sending_agent=row["sending_agent"], receiving_agent=row["receiving_agent"],
        objective=row["objective"], completed_work=row["completed_work"],
        incomplete_work=row["incomplete_work"],
        artifacts=tuple(x for x in row["artifacts"].split("|") if x),
        evidence=tuple(x for x in row["evidence"].split("|") if x),
        decisions=tuple(x for x in row["decisions"].split("|") if x),
        assumptions=tuple(x for x in row["assumptions"].split("|") if x),
        risks=tuple(x for x in row["risks"].split("|") if x),
        open_questions=tuple(x for x in row["open_questions"].split("|") if x),
        recommended_next_action=row["recommended_next_action"],
        required_validation=tuple(x for x in row["required_validation"].split("|") if x),
        scope_limitations=tuple(x for x in row["scope_limitations"].split("|") if x),
        privacy_classification=row["privacy_classification"], state=row["state"],
        response_note=row["response_note"], created_at=row["created_at"], updated_at=row["updated_at"],
    )


def _artifact_from_row(row) -> ArtifactRecord:
    return ArtifactRecord(
        artifact_id=row["artifact_id"], artifact_type=row["artifact_type"], title=row["title"],
        producer=row["producer"], mission_id=row["mission_id"], task_id=row["task_id"],
        project=row["project"], version=row["version"], storage_reference=row["storage_reference"],
        content_hash=row["content_hash"], privacy_classification=row["privacy_classification"],
        authority=row["authority"], review_state=row["review_state"],
        validation_state=row["validation_state"],
        source_inputs=tuple(x for x in row["source_inputs"].split("|") if x),
        evidence=tuple(x for x in row["evidence"].split("|") if x),
        superseded_state=row["superseded_state"], deletion_state=row["deletion_state"],
        created_at=row["created_at"], updated_at=row["updated_at"],
    )


def _gate_from_row(row) -> QualityGate:
    return QualityGate(
        gate_id=row["gate_id"], mission_id=row["mission_id"], task_id=row["task_id"],
        gate_type=row["gate_type"], required_reviewer_role=row["required_reviewer_role"],
        independence_required=bool(row["independence_required"]),
        required_evidence=tuple(x for x in row["required_evidence"].split("|") if x),
        required_validation=tuple(x for x in row["required_validation"].split("|") if x),
        pass_criteria=tuple(x for x in row["pass_criteria"].split("|") if x),
        failure_criteria=tuple(x for x in row["failure_criteria"].split("|") if x),
        waiver_policy=row["waiver_policy"], state=row["state"], reviewer=row["reviewer"],
        findings=_deserialize_findings(row["findings"]), resolution=row["resolution"],
        approval=row["approval"], created_at=row["created_at"], updated_at=row["updated_at"],
    )


def _review_from_row(row) -> ReviewRecord:
    return ReviewRecord(
        review_id=row["review_id"], mission_id=row["mission_id"], task_id=row["task_id"],
        gate_id=row["gate_id"], reviewer=row["reviewer"], implementer=row["implementer"],
        artifacts=tuple(x for x in row["artifacts"].split("|") if x),
        evidence=tuple(x for x in row["evidence"].split("|") if x),
        model=row["model"], runtime=row["runtime"], findings=_deserialize_findings(row["findings"]),
        independence=bool(row["independence"]), disclosure=row["disclosure"],
        conclusion=row["conclusion"], confidence=row["confidence"], status=row["status"],
        created_at=row["created_at"], updated_at=row["updated_at"],
    )


def _disagreement_from_row(row) -> DisagreementRecord:
    return DisagreementRecord(
        disagreement_id=row["disagreement_id"], mission_id=row["mission_id"], task_id=row["task_id"],
        participants=tuple(x for x in row["participants"].split("|") if x),
        subject=row["subject"], positions=tuple(x for x in row["positions"].split("|") if x),
        evidence=tuple(x for x in row["evidence"].split("|") if x),
        assumptions=tuple(x for x in row["assumptions"].split("|") if x),
        affected_decision=row["affected_decision"], urgency=row["urgency"],
        state=row["state"], resolution_method=row["resolution_method"],
        resolution_notes=row["resolution_notes"], escalated=bool(row["escalated"]),
        created_at=row["created_at"], updated_at=row["updated_at"],
    )


def _consultation_from_row(row) -> ConsultationRecord:
    return ConsultationRecord(
        consultation_id=row["consultation_id"], question=row["question"],
        requester=row["requester"], specialist=row["specialist"], context=row["context"],
        evidence=tuple(x for x in row["evidence"].split("|") if x),
        constraints=tuple(x for x in row["constraints"].split("|") if x),
        required_expertise=tuple(x for x in row["required_expertise"].split("|") if x),
        tool_use_allowed=bool(row["tool_use_allowed"]), deadline=row["deadline"],
        privacy_classification=row["privacy_classification"],
        budget=_consultation_budget(row["budget"]),
        response=row["response"], conclusion=row["conclusion"],
        assumptions=tuple(x for x in row["assumptions"].split("|") if x),
        confidence=row["confidence"], limitations=tuple(x for x in row["limitations"].split("|") if x),
        recommended_action=row["recommended_action"], affected_risk=row["affected_risk"],
        state=row["state"], created_at=row["created_at"], updated_at=row["updated_at"],
    )


def _consultation_budget(raw: str):
    from .models import ResourceBudget

    try:
        return ResourceBudget.model_validate_json(raw or "{}")
    except Exception:
        return ResourceBudget()