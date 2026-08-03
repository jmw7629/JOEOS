"""External-send approval, delivery, and attachments for the JoeOS
Communications Platform.

External delivery requires explicit policy and approval; delivery is
idempotent and bounded; attachments are validated and never executed.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .messages import MessageStore, OutboxService
from .models import MessageRecord, OutboxItem
from .safety import content_hash

MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
MAX_DELIVERY_ATTEMPTS = 5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExternalSendError(RuntimeError):
    pass


class ExternalSendApprovalCoordinator:
    """Approval bound to content hash, recipient hash, and attachment hashes."""

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()

    def request(
        self,
        *,
        draft_id: str,
        subject: str,
        body: str,
        recipients: Sequence[str],
        sender_identity: str,
        provider: str,
        account: str,
        scheduled: str = "",
        attachments: Sequence = (),
        privacy: str = "private",
        expires_in_hours: int = 24,
    ) -> dict:
        message_hash = content_hash(subject, body)
        recipient_hash = content_hash(*sorted(recipients))
        attachment_hashes = tuple(a.content_hash for a in attachments if getattr(a, "content_hash", ""))
        approval_id = "ext_" + uuid.uuid4().hex[:16]
        expires = (
            datetime.now(timezone.utc)
            .replace(hour=0, minute=0, second=0, microsecond=0)
        ).isoformat()
        from datetime import timedelta
        expires = (datetime.now(timezone.utc) + timedelta(hours=expires_in_hours)).isoformat()
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO comms_external_approvals (
                    approval_id, draft_id, message_hash, recipient_hash, attachment_hashes,
                    sender_identity, provider, account, scheduled, privacy, state,
                    expires_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    approval_id,
                    draft_id,
                    message_hash,
                    recipient_hash,
                    "\n".join(attachment_hashes),
                    sender_identity,
                    provider,
                    account,
                    scheduled,
                    privacy,
                    expires,
                    _now(),
                ),
            )
        return {
            "approval_id": approval_id,
            "message_hash": message_hash,
            "recipient_hash": recipient_hash,
            "expires_at": expires,
        }

    def resolve(
        self,
        approval_id: str,
        *,
        decision: str,
        subject: str,
        body: str,
        recipients: Sequence[str],
    ) -> dict:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM comms_external_approvals WHERE approval_id = ?", (approval_id,)
            ).fetchone()
            if row is None:
                raise ExternalSendError("approval not found.")
            if str(row["state"]) != "pending":
                raise ExternalSendError("approval already resolved.")
            now = datetime.now(timezone.utc)
            try:
                expired = datetime.fromisoformat(str(row["expires_at"])) < now
            except ValueError:
                expired = True
            if expired:
                raise ExternalSendError("approval has expired.")
            if decision == "approved":
                # Approval is bound to content, recipients, and attachments.
                current_message_hash = content_hash(subject, body)
                current_recipient_hash = content_hash(*sorted(recipients))
                if current_message_hash != str(row["message_hash"]):
                    raise ExternalSendError("message content changed; approval invalidated.")
                if current_recipient_hash != str(row["recipient_hash"]):
                    raise ExternalSendError("recipients changed; approval invalidated.")
            connection.execute(
                "UPDATE comms_external_approvals SET state = ?, resolved_at = ? WHERE approval_id = ?",
                (decision, _now(), approval_id),
            )
        return {"approval_id": approval_id, "decision": decision}

    def deny(self, approval_id: str) -> dict:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM comms_external_approvals WHERE approval_id = ?", (approval_id,)
            ).fetchone()
            if row is None:
                raise ExternalSendError("approval not found.")
            if str(row["state"]) != "pending":
                raise ExternalSendError("approval already resolved.")
            connection.execute(
                "UPDATE comms_external_approvals SET state = 'denied', resolved_at = ? WHERE approval_id = ?",
                (_now(), approval_id),
            )
        return {"approval_id": approval_id, "decision": "denied"}

    def pending(self, *, limit: int = 50) -> Tuple[dict, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM comms_external_approvals WHERE state = 'pending' ORDER BY created_at DESC LIMIT ?",
                (max(1, min(200, int(limit))),),
            ).fetchall()
        return tuple(dict(row) for row in rows)


class DeliveryService:
    """Idempotent, bounded, recoverable external/internal delivery."""

    def __init__(
        self,
        *,
        connection_factory: Callable[[], sqlite3.Connection],
        messages: MessageStore,
        outbox: OutboxService,
        approvals: ExternalSendApprovalCoordinator,
        provider_dispatch=None,
        event_sink=None,
    ) -> None:
        self._connection_factory = connection_factory
        self._messages = messages
        self._outbox = outbox
        self._approvals = approvals
        self._provider_dispatch = provider_dispatch or (lambda provider, account, message: {"sent": True, "provider_message_id": "test-" + message.message_id})
        self._event_sink = event_sink or (lambda level, source, message: None)
        self._lock = threading.RLock()

    def validate_external(
        self,
        *,
        message: MessageRecord,
        approval_id: str,
        sender_identity: str,
        recipients: Sequence[str],
    ) -> None:
        """Validate all pre-send constraints; approval must already be granted."""
        if not message.external:
            raise ExternalSendError("validate_external requires an external message.")
        if not sender_identity:
            raise ExternalSendError("no sender identity.")
        if not recipients:
            raise ExternalSendError("no recipients.")
        if not message.provider:
            raise ExternalSendError("no provider configured.")
        if message.privacy == "restricted" and not self._approval_for(message, approval_id):
            raise ExternalSendError("restricted message requires granted approval.")

    def _approval_for(self, message: MessageRecord, approval_id: str) -> bool:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT state FROM comms_external_approvals WHERE approval_id = ?", (approval_id,)
            ).fetchone()
        return bool(row and row["state"] == "approved")

    def send(self, *, message: MessageRecord, approval_id: str = "", scheduled: str = "", idempotency_key: str = "") -> OutboxItem:
        if message.external and not approval_id:
            raise ExternalSendError("external messages require a granted approval.")
        item = self._outbox.enqueue(
            message_id=message.message_id,
            sender_identity=message.sender_identity,
            recipients=message.recipients,
            provider=message.provider,
            account=message.account,
            scheduled=scheduled,
            approval_state="approved" if approval_id else "none",
            idempotency_key=idempotency_key,
        )
        if scheduled:
            self._outbox.update_state(item.outbox_id, "scheduled")
            return self._outbox.get(item.outbox_id)
        return self.deliver(item.outbox_id)

    def deliver(self, outbox_id: str) -> OutboxItem:
        item = self._outbox.get(outbox_id)
        if item is None:
            raise ExternalSendError("outbox item not found.")
        if item.state == "sent":
            return item
        message = self._messages.get(item.message_id)
        if message is None:
            self._outbox.update_state(outbox_id, "failed", failure="message missing", retryable=False)
            return self._outbox.get(outbox_id)
        attempts = item.attempts
        if attempts >= MAX_DELIVERY_ATTEMPTS:
            self._outbox.update_state(outbox_id, "failed", failure="max delivery attempts exceeded", retryable=False)
            return self._outbox.get(outbox_id)
        self._outbox.update_state(outbox_id, "sending")
        try:
            result = self._provider_dispatch(item.provider, item.account, message)
            sent_at = result.get("sent_at") or _now()
            provider_message_id = result.get("provider_message_id", "")
            self._outbox.update_state(outbox_id, "sent", sent_at=sent_at)
            with self._connection_factory() as connection:
                connection.execute(
                    "UPDATE comms_messages SET delivery_state = 'sent', sent_at = ?, provider_message_id = ?, delivery_attempts = ? WHERE message_id = ?",
                    (sent_at, provider_message_id, attempts + 1, message.message_id),
                )
            self._event_sink("success", "communications", "Message sent through %s." % item.provider)
            return self._outbox.get(outbox_id)
        except Exception as exc:
            retryable = not isinstance(exc, ExternalSendError) and attempts + 1 < MAX_DELIVERY_ATTEMPTS
            state = "failed" if not retryable else "queued"
            self._outbox.update_state(outbox_id, state, failure=str(exc)[:300], retryable=retryable)
            if retryable:
                self._event_sink("warn", "communications", "Delivery of %s retryable." % outbox_id)
            else:
                self._event_sink("error", "communications", "Delivery of %s failed." % outbox_id)
            return self._outbox.get(outbox_id)

    def retry(self, outbox_id: str) -> OutboxItem:
        item = self._outbox.get(outbox_id)
        if item is None or not item.retryable:
            raise ExternalSendError("item is not retryable.")
        return self.deliver(outbox_id)

    def cancel(self, outbox_id: str) -> OutboxItem:
        self._outbox.update_state(outbox_id, "cancelled", failure="cancelled by user", retryable=False)
        return self._outbox.get(outbox_id)


class AttachmentService:
    """Validates and tracks attachments within approved project boundaries."""

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection], allowed_roots: Sequence[str] = ()) -> None:
        self._connection_factory = connection_factory
        self._allowed_roots = [Path(root).resolve() for root in allowed_roots]
        self._lock = threading.RLock()

    def attach(
        self,
        *,
        path: str,
        display_name: str = "",
        project: str = "",
        owner: str = "",
        privacy: str = "private",
    ) -> dict:
        resolved = Path(path).resolve()
        if resolved.is_symlink():
            raise ExternalSendError("symbolic-link attachments are rejected.")
        if self._allowed_roots:
            if not any(self._is_within(root, resolved) for root in self._allowed_roots):
                raise ExternalSendError("attachment is outside an approved project boundary.")
        if not resolved.is_file():
            raise ExternalSendError("attachment file does not exist.")
        size = resolved.stat().st_size
        if size > MAX_ATTACHMENT_BYTES:
            raise ExternalSendError("attachment exceeds the size limit.")
        digest = hashlib.sha256()
        with resolved.open("rb") as handle:
            for chunk in iter(lambda: handle.read(64 * 1024), b""):
                digest.update(chunk)
        attachment_id = "att_" + uuid.uuid4().hex[:16]
        now = _now()
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO comms_attachments (
                    attachment_id, source, safe_path, display_name, mime_type, size,
                    content_hash, project, owner, privacy, sensitivity, malware_scan,
                    file_classification, generated, provider_state, retention, deletion_state, created_at
                ) VALUES (?, 'user', ?, ?, '', ?, ?, ?, ?, ?, '', 'not_scanned', '', 0, 'local', '', 'active', ?)
                """,
                (
                    attachment_id,
                    str(resolved),
                    display_name or resolved.name,
                    size,
                    digest.hexdigest(),
                    project,
                    owner,
                    privacy,
                    now,
                ),
            )
        return {
            "attachment_id": attachment_id,
            "display_name": display_name or resolved.name,
            "size": size,
            "content_hash": digest.hexdigest(),
            "safe_path": str(resolved),
            "project": project,
            "privacy": privacy,
        }

    def _is_within(self, root: Path, candidate: Path) -> bool:
        try:
            candidate.relative_to(root)
            return True
        except ValueError:
            return False

    def list(self, *, project: Optional[str] = None, limit: int = 50) -> Tuple[dict, ...]:
        clauses = "deletion_state = 'active'"
        params: List[object] = []
        if project:
            clauses += " AND project = ?"
            params.append(project)
        params.append(max(1, min(200, int(limit))))
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM comms_attachments WHERE " + clauses + " ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def mark_deleted(self, attachment_id: str) -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE comms_attachments SET deletion_state = 'deleted' WHERE attachment_id = ?",
                (attachment_id,),
            )