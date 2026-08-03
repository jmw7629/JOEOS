"""Message Store, Draft Store, and Outbox for the JoeOS Communications Platform.

Messages are normalized; drafts persist safely; the outbox is authoritative.
Delivery goes through the Delivery Service only — never directly from UI or
provider adapters.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .models import (
    DraftRecord,
    MessageRecord,
    OutboxItem,
    Recipient,
    AttachmentRef,
)

MAX_BODY_BYTES = 100 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CommunicationsError(RuntimeError):
    pass


class MessageStore:
    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()

    def save(self, message: MessageRecord) -> MessageRecord:
        if len(message.body.encode("utf-8")) > MAX_BODY_BYTES:
            raise CommunicationsError("message body exceeds the size limit.")
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO comms_messages (
                    message_id, communication_type, provider, provider_message_id, account,
                    origin_type, origin_label, source_service, source_plugin, source_workflow,
                    source_mission, source_task, source_agent, author, sender_identity,
                    recipients, conversation_id, thread_id, parent_message, subject, body,
                    rich_body, attachments, links, mentions, priority, severity, privacy,
                    draft_state, approval_state, delivery_state, read_state, archive_state,
                    mute_state, snooze_until, scheduled_send, sent_at, received_at,
                    delivery_attempts, content_hash, provenance, verification_state,
                    phishing_indicators, deletion_state, external, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(message_id) DO UPDATE SET
                    body = excluded.body, rich_body = excluded.rich_body,
                    delivery_state = excluded.delivery_state, read_state = excluded.read_state,
                    archive_state = excluded.archive_state, delivery_attempts = excluded.delivery_attempts
                """,
                (
                    message.message_id,
                    message.communication_type,
                    message.provider,
                    message.provider_message_id,
                    message.account,
                    message.origin.origin_type,
                    message.origin.label,
                    message.origin.source_service,
                    message.origin.source_plugin,
                    message.origin.source_workflow,
                    message.origin.source_mission,
                    message.origin.source_task,
                    message.origin.source_agent,
                    message.author,
                    message.sender_identity,
                    "\n".join(message.recipients),
                    message.conversation_id,
                    message.thread_id,
                    message.parent_message,
                    message.subject,
                    message.body,
                    message.rich_body,
                    json.dumps([a.model_dump() for a in message.attachments]),
                    "\n".join(message.links),
                    "\n".join(message.mentions),
                    message.priority,
                    message.severity,
                    message.privacy,
                    1 if message.draft_state else 0,
                    message.approval_state,
                    message.delivery_state,
                    message.read_state,
                    1 if message.archive_state else 0,
                    1 if message.mute_state else 0,
                    message.snooze_until,
                    message.scheduled_send,
                    message.sent_at,
                    message.received_at,
                    message.delivery_attempts,
                    message.content_hash,
                    json.dumps(message.provenance),
                    message.verification_state,
                    "\n".join(message.phishing_indicators),
                    message.deletion_state,
                    1 if message.external else 0,
                    message.created_at or _now(),
                ),
            )
        return self.get(message.message_id)

    def get(self, message_id: str) -> Optional[MessageRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM comms_messages WHERE message_id = ?", (message_id,)
            ).fetchone()
        return self._row(row) if row else None

    def list(
        self,
        *,
        conversation_id: Optional[str] = None,
        communication_type: Optional[str] = None,
        external: Optional[bool] = None,
        limit: int = 50,
        before: Optional[str] = None,
    ) -> Tuple[MessageRecord, ...]:
        count = max(1, min(200, int(limit)))
        clauses: List[str] = ["deletion_state = 'active'"]
        params: List[object] = []
        if conversation_id:
            clauses.append("conversation_id = ?")
            params.append(conversation_id)
        if communication_type:
            clauses.append("communication_type = ?")
            params.append(communication_type)
        if external is not None:
            clauses.append("external = ?")
            params.append(1 if external else 0)
        if before:
            clauses.append("message_id < ?")
            params.append(before)
        where = " AND ".join(clauses)
        params.append(count)
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM comms_messages WHERE " + where + " ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return tuple(self._row(row) for row in rows)

    def mark_read(self, message_id: str, state: str = "read") -> MessageRecord:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE comms_messages SET read_state = ? WHERE message_id = ?",
                (state, message_id),
            )
        return self.get(message_id)

    def set_archive(self, message_id: str, archived: bool) -> MessageRecord:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE comms_messages SET archive_state = ? WHERE message_id = ?",
                (1 if archived else 0, message_id),
            )
        return self.get(message_id)

    def set_snooze(self, message_id: str, until: str) -> MessageRecord:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE comms_messages SET snooze_until = ? WHERE message_id = ?",
                (until, message_id),
            )
        return self.get(message_id)

    def mark_deleted(self, message_id: str) -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE comms_messages SET deletion_state = 'deleted' WHERE message_id = ?",
                (message_id,),
            )

    def search(self, query: str, *, limit: int = 50) -> Tuple[MessageRecord, ...]:
        count = max(1, min(200, int(limit)))
        with self._connection_factory() as connection:
            rows = connection.execute(
                """
                SELECT * FROM comms_messages
                WHERE deletion_state = 'active' AND (body LIKE ? OR subject LIKE ? OR author LIKE ? OR sender_identity LIKE ?)
                ORDER BY created_at DESC LIMIT ?
                """,
                ("%" + query + "%", "%" + query + "%", "%" + query + "%", "%" + query + "%", count),
            ).fetchall()
        return tuple(self._row(row) for row in rows)

    def unread_count(self, *, conversation_id: Optional[str] = None) -> int:
        clauses = "deletion_state = 'active' AND read_state = 'delivered'"
        params: List[object] = []
        if conversation_id:
            clauses += " AND conversation_id = ?"
            params.append(conversation_id)
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM comms_messages WHERE " + clauses, params
            ).fetchone()
        return int(row[0])

    @staticmethod
    def _row(row: sqlite3.Row) -> MessageRecord:
        from .models import Origin
        attachments = tuple(AttachmentRef.model_validate(a) for a in json.loads(str(row["attachments"])))
        return MessageRecord(
            message_id=str(row["message_id"]),
            communication_type=str(row["communication_type"]),
            provider=str(row["provider"]),
            provider_message_id=str(row["provider_message_id"]),
            account=str(row["account"]),
            origin=Origin(
                origin_type=str(row["origin_type"]),
                label=str(row["origin_label"]),
                source_service=str(row["source_service"]),
                source_plugin=str(row["source_plugin"]),
                source_workflow=str(row["source_workflow"]),
                source_mission=str(row["source_mission"]),
                source_task=str(row["source_task"]),
                source_agent=str(row["source_agent"]),
            ),
            author=str(row["author"]),
            sender_identity=str(row["sender_identity"]),
            recipients=tuple(p for p in str(row["recipients"]).split("\n") if p),
            conversation_id=str(row["conversation_id"]),
            thread_id=str(row["thread_id"]),
            parent_message=str(row["parent_message"]),
            subject=str(row["subject"]),
            body=str(row["body"]),
            rich_body=str(row["rich_body"]),
            attachments=attachments,
            links=tuple(p for p in str(row["links"]).split("\n") if p),
            mentions=tuple(p for p in str(row["mentions"]).split("\n") if p),
            priority=str(row["priority"]),
            severity=str(row["severity"]),
            privacy=str(row["privacy"]),
            draft_state=bool(row["draft_state"]),
            approval_state=str(row["approval_state"]),
            delivery_state=str(row["delivery_state"]),
            read_state=str(row["read_state"]),
            archive_state=bool(row["archive_state"]),
            mute_state=bool(row["mute_state"]),
            snooze_until=str(row["snooze_until"]),
            scheduled_send=str(row["scheduled_send"]),
            sent_at=str(row["sent_at"]),
            received_at=str(row["received_at"]),
            delivery_attempts=int(row["delivery_attempts"]),
            content_hash=str(row["content_hash"]),
            provenance=json.loads(str(row["provenance"])),
            verification_state=str(row["verification_state"]),
            phishing_indicators=tuple(p for p in str(row["phishing_indicators"]).split("\n") if p),
            deletion_state=str(row["deletion_state"]),
            external=bool(row["external"]),
            created_at=str(row["created_at"]),
        )


class DraftStore:
    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()

    def save(self, draft: DraftRecord) -> DraftRecord:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO comms_drafts (
                    draft_id, author, proposed_sender, recipients, provider, account,
                    conversation_id, thread_id, subject, body, attachments, privacy, source,
                    source_agent, source_workflow, source_task, approval_required,
                    approval_state, scheduled_send, conflict_state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(draft_id) DO UPDATE SET
                    subject = excluded.subject, body = excluded.body, recipients = excluded.recipients,
                    attachments = excluded.attachments, updated_at = excluded.updated_at
                """,
                (
                    draft.draft_id,
                    draft.author,
                    draft.proposed_sender,
                    "\n".join(draft.recipients),
                    draft.provider,
                    draft.account,
                    draft.conversation_id,
                    draft.thread_id,
                    draft.subject,
                    draft.body,
                    json.dumps([a.model_dump() for a in draft.attachments]),
                    draft.privacy,
                    draft.source,
                    draft.source_agent,
                    draft.source_workflow,
                    draft.source_task,
                    1 if draft.approval_required else 0,
                    draft.approval_state,
                    draft.scheduled_send,
                    draft.conflict_state,
                    draft.created_at or _now(),
                    _now(),
                ),
            )
        return self.get(draft.draft_id)

    def get(self, draft_id: str) -> Optional[DraftRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM comms_drafts WHERE draft_id = ?", (draft_id,)
            ).fetchone()
        return self._row(row) if row else None

    def list(self, *, limit: int = 50) -> Tuple[DraftRecord, ...]:
        count = max(1, min(200, int(limit)))
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM comms_drafts ORDER BY updated_at DESC LIMIT ?", (count,)
            ).fetchall()
        return tuple(self._row(row) for row in rows)

    def delete(self, draft_id: str) -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute("DELETE FROM comms_drafts WHERE draft_id = ?", (draft_id,))

    @staticmethod
    def _row(row: sqlite3.Row) -> DraftRecord:
        attachments = tuple(AttachmentRef.model_validate(a) for a in json.loads(str(row["attachments"])))
        return DraftRecord(
            draft_id=str(row["draft_id"]),
            author=str(row["author"]),
            proposed_sender=str(row["proposed_sender"]),
            recipients=tuple(p for p in str(row["recipients"]).split("\n") if p),
            provider=str(row["provider"]),
            account=str(row["account"]),
            conversation_id=str(row["conversation_id"]),
            thread_id=str(row["thread_id"]),
            subject=str(row["subject"]),
            body=str(row["body"]),
            attachments=attachments,
            privacy=str(row["privacy"]),
            source=str(row["source"]),
            source_agent=str(row["source_agent"]),
            source_workflow=str(row["source_workflow"]),
            source_task=str(row["source_task"]),
            approval_required=bool(row["approval_required"]),
            approval_state=str(row["approval_state"]),
            scheduled_send=str(row["scheduled_send"]),
            conflict_state=str(row["conflict_state"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )


class OutboxService:
    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()

    def enqueue(
        self,
        *,
        message_id: str,
        sender_identity: str,
        recipients: Sequence[str],
        provider: str,
        account: str,
        scheduled: str = "",
        approval_state: str = "none",
        idempotency_key: str = "",
    ) -> OutboxItem:
        outbox_id = "out_" + uuid.uuid4().hex[:16]
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO comms_outbox (
                    outbox_id, message_id, sender_identity, recipients, provider, account,
                    scheduled, approval_state, attempts, idempotency_key, state, failure,
                    retryable, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'queued', '', 0, ?)
                """,
                (
                    outbox_id,
                    message_id,
                    sender_identity,
                    "\n".join(recipients),
                    provider,
                    account,
                    scheduled,
                    approval_state,
                    idempotency_key,
                    _now(),
                ),
            )
        return self.get(outbox_id)

    def get(self, outbox_id: str) -> Optional[OutboxItem]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM comms_outbox WHERE outbox_id = ?", (outbox_id,)
            ).fetchone()
        return self._row(row) if row else None

    def update_state(self, outbox_id: str, state: str, *, failure: str = "", retryable: bool = False, sent_at: str = "") -> Optional[OutboxItem]:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                UPDATE comms_outbox
                SET state = ?, failure = ?, retryable = ?, sent_at = ?, attempts = attempts + 1
                WHERE outbox_id = ?
                """,
                (state, failure[:300], 1 if retryable else 0, sent_at, outbox_id),
            )
        return self.get(outbox_id)

    def list(self, *, state: Optional[str] = None, limit: int = 50) -> Tuple[OutboxItem, ...]:
        count = max(1, min(200, int(limit)))
        clauses = ""
        params: List[object] = []
        if state:
            clauses = " WHERE state = ?"
            params.append(state)
        params.append(count)
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM comms_outbox" + clauses + " ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return tuple(self._row(row) for row in rows)

    @staticmethod
    def _row(row: sqlite3.Row) -> OutboxItem:
        return OutboxItem(
            outbox_id=str(row["outbox_id"]),
            message_id=str(row["message_id"]),
            sender_identity=str(row["sender_identity"]),
            recipients=tuple(p for p in str(row["recipients"]).split("\n") if p),
            provider=str(row["provider"]),
            account=str(row["account"]),
            scheduled=str(row["scheduled"]),
            approval_state=str(row["approval_state"]),
            attempts=int(row["attempts"]),
            idempotency_key=str(row["idempotency_key"]),
            state=str(row["state"]),
            failure=str(row["failure"]),
            retryable=bool(row["retryable"]),
            created_at=str(row["created_at"]),
            sent_at=str(row["sent_at"]),
        )