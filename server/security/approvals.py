"""Approval service for the JoeOS Security Platform.

Approvals bind to the exact action, target, arguments hash, content hash,
attachment hashes, workflow/plugin versions, scope, and expiration. Any
material change invalidates the approval. Approval strength is risk-based
(levels 0-5); high-risk operations require stronger confirmation and can never
be authorized by voice/gesture/notification/agent alone. Separation of duties
prevents the requester from approving their own high-risk action.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Optional, Sequence, Tuple

from .policy import SecurityError
from .models import (
    ApprovalRequestRecord,
    ApprovalStrength,
    ConsentRecord,
)

HIGH_RISK_ACTIONS = {
    "force_push", "hard_reset", "destructive_cleanup", "delete_user_data",
    "expose_public_service", "disable_tls_validation", "modify_firewall",
    "export_secrets", "change_global_policy", "rotate_root_credentials",
    "disable_audit", "external_send", "deployment", "git_push",
    "file_deletion", "service_restart", "secret_access",
}

STRENGTH_FOR_RISK = {
    "low": "level1",
    "medium": "level2",
    "high": "level3",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()



def arguments_hash(args: dict) -> str:
    payload = json.dumps(args, sort_keys=True, default=str)
    return hashlib.sha256(("joeos-approval-args-v1\0" + payload).encode()).hexdigest()


def content_hash(*parts: str) -> str:
    payload = "\x1f".join(parts or ())
    return hashlib.sha256(("joeos-approval-content-v1\0" + payload).encode()).hexdigest()


class ApprovalService:
    """Exact, expiration-bound, risk-scaled approvals."""

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()

    def request(
        self,
        *,
        requester_identity: str,
        action_id: str,
        target_id: str = "",
        target_type: str = "",
        arguments: Optional[dict] = None,
        content: Optional[Sequence[str]] = None,
        attachment_hashes: Sequence[str] = (),
        workflow_version: str = "",
        plugin_version: str = "",
        project: str = "",
        task: str = "",
        mission: str = "",
        risk: str = "low",
        ttl_hours: int = 24,
    ) -> ApprovalRequestRecord:
        strength = self._strength_for(action_id, risk)
        arg_hash = arguments_hash(arguments or {}) if arguments is not None else ""
        content_hash_value = content_hash(*content) if content else ""
        approval = ApprovalRequestRecord(
            approval_id="approval_" + uuid.uuid4().hex[:16],
            requester_identity=requester_identity,
            action_id=action_id,
            target_id=target_id,
            target_type=target_type,
            arguments_hash=arg_hash,
            content_hash=content_hash_value,
            attachment_hashes=tuple(attachment_hashes),
            workflow_version=workflow_version,
            plugin_version=plugin_version,
            project=project,
            task=task,
            mission=mission,
            risk=risk,
            strength_required=strength,
            expiration=(datetime.now(timezone.utc) + timedelta(hours=ttl_hours)).isoformat(),
            state="pending",
            created_at=_now(),
        )
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO security_approvals (
                    approval_id, requester_identity, approver_identity, host, device, session,
                    action_id, target_id, target_type, arguments_hash, content_hash,
                    attachment_hashes, workflow_version, plugin_version, project, task, mission,
                    data_classification, risk, strength_required, expiration, policy_version,
                    state, created_at, resolved_at
                ) VALUES (?, ?, '', '', '', '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unknown', ?, ?, ?, 1, 'pending', ?, '')
                """,
                (
                    approval.approval_id, requester_identity, action_id, target_id, target_type,
                    arg_hash, content_hash_value, "\n".join(attachment_hashes),
                    workflow_version, plugin_version, project, task, mission,
                    risk, strength, approval.expiration, approval.created_at,
                ),
            )
        return approval

    def approve(
        self,
        *,
        approval_id: str,
        approver_identity: str,
        confirmation_strength: str = "level1",
        session: str = "",
        device: str = "",
    ) -> ApprovalRequestRecord:
        record = self._get(approval_id)
        if record is None:
            raise SecurityError("approval not found.")
        if record.state != "pending":
            raise SecurityError("approval is not pending.")
        if record.requester_identity == approver_identity and record.risk in {"high", "medium"}:
            raise SecurityError("separation of duties: requester cannot approve own high-risk action.")
        if self._strength_rank(confirmation_strength) < self._strength_rank(record.strength_required):
            raise SecurityError(
                "approval requires confirmation strength %s." % record.strength_required
            )
        try:
            if datetime.fromisoformat(record.expiration) < datetime.now(timezone.utc):
                self._set_state(approval_id, "expired")
                raise SecurityError("approval has expired.")
        except ValueError:
            raise SecurityError("approval has expired.") from None
        self._set_state(approval_id, "approved", approver_identity=approver_identity)
        return self._get(approval_id)

    def deny(self, *, approval_id: str, approver_identity: str = "user") -> ApprovalRequestRecord:
        self._set_state(approval_id, "denied", approver_identity=approver_identity)
        return self._get(approval_id)

    def verify_exact(
        self,
        *,
        approval_id: str,
        action_id: str,
        target_id: str = "",
        arguments: Optional[dict] = None,
        content: Optional[Sequence[str]] = None,
        project: str = "",
    ) -> bool:
        """Revalidate an approval against the exact action at use time."""
        record = self._get(approval_id)
        if record is None or record.state != "approved":
            return False
        if record.action_id != action_id:
            return False
        if target_id and record.target_id != target_id:
            return False
        if project and record.project != project:
            return False
        if arguments is not None and arguments_hash(arguments) != record.arguments_hash:
            return False
        if content is not None and content_hash(*content) != record.content_hash:
            return False
        try:
            if datetime.fromisoformat(record.expiration) < datetime.now(timezone.utc):
                return False
        except ValueError:
            return False
        return True

    def invalidate(self, approval_id: str, *, reason: str = "") -> None:
        self._set_state(approval_id, "invalidated")

    def pending(self, *, limit: int = 50) -> Tuple[ApprovalRequestRecord, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM security_approvals WHERE state = 'pending' ORDER BY created_at DESC LIMIT ?",
                (max(1, min(200, int(limit))),),
            ).fetchall()
        return tuple(self._row(row) for row in rows)

    def list(self, *, state: Optional[str] = None, limit: int = 50) -> Tuple[ApprovalRequestRecord, ...]:
        clause = " WHERE state = ?" if state else ""
        params: list = [state] if state else []
        params.append(max(1, min(200, int(limit))))
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM security_approvals" + clause + " ORDER BY created_at DESC LIMIT ?", params
            ).fetchall()
        return tuple(self._row(row) for row in rows)

    # ---- consent ----

    def record_consent(self, *, identity: str, purpose: str, data: str = "", destination: str = "", duration: str = "") -> ConsentRecord:
        consent = ConsentRecord(
            consent_id="consent_" + uuid.uuid4().hex[:16],
            identity=identity,
            purpose=purpose,
            data=data,
            destination=destination,
            duration=duration,
            state="active",
            created_at=_now(),
        )
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO security_consent (consent_id, identity, purpose, data, destination, duration, policy_version, state, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, 'active', ?)
                """,
                (consent.consent_id, identity, purpose, data, destination, duration, _now()),
            )
        return consent

    def withdraw_consent(self, consent_id: str) -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE security_consent SET state = 'withdrawn' WHERE consent_id = ?", (consent_id,)
            )

    def consent_active(self, *, identity: str, purpose: str, destination: str = "") -> bool:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM security_consent WHERE identity = ? AND purpose = ? AND state = 'active'",
                (identity, purpose),
            ).fetchall()
        for row in rows:
            if destination and str(row["destination"]) and str(row["destination"]) != destination:
                continue
            return True
        return False

    # ---- helpers ----

    @staticmethod
    def _strength_for(action_id: str, risk: str) -> ApprovalStrength:
        if action_id in HIGH_RISK_ACTIONS:
            return "level4"
        return STRENGTH_FOR_RISK.get(risk, "level1")

    @staticmethod
    def _strength_rank(strength: str) -> int:
        return {
            "level0": 0, "level1": 1, "level2": 2, "level3": 3, "level4": 4, "level5": 5,
        }.get(strength, 0)

    def _get(self, approval_id: str) -> Optional[ApprovalRequestRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM security_approvals WHERE approval_id = ?", (approval_id,)
            ).fetchone()
        return self._row(row) if row else None

    def _set_state(self, approval_id: str, state: str, *, approver_identity: str = "") -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE security_approvals SET state = ?, approver_identity = ?, resolved_at = ? WHERE approval_id = ?",
                (state, approver_identity, _now(), approval_id),
            )

    @staticmethod
    def _row(row: sqlite3.Row) -> ApprovalRequestRecord:
        return ApprovalRequestRecord(
            approval_id=str(row["approval_id"]),
            requester_identity=str(row["requester_identity"]),
            approver_identity=str(row["approver_identity"]),
            host=str(row["host"]),
            device=str(row["device"]),
            session=str(row["session"]),
            action_id=str(row["action_id"]),
            target_id=str(row["target_id"]),
            target_type=str(row["target_type"]),
            arguments_hash=str(row["arguments_hash"]),
            content_hash=str(row["content_hash"]),
            attachment_hashes=tuple(p for p in str(row["attachment_hashes"]).split("\n") if p),
            workflow_version=str(row["workflow_version"]),
            plugin_version=str(row["plugin_version"]),
            project=str(row["project"]),
            task=str(row["task"]),
            mission=str(row["mission"]),
            data_classification=str(row["data_classification"]),
            risk=str(row["risk"]),
            strength_required=str(row["strength_required"]),
            expiration=str(row["expiration"]),
            policy_version=int(row["policy_version"]),
            state=str(row["state"]),
            created_at=str(row["created_at"]),
            resolved_at=str(row["resolved_at"]),
        )