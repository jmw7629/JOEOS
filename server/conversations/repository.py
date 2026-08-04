"""Canonical conversation persistence (Phase P3A).

The JoeOS backend is authoritative for conversation history. Conversations are
identified by a stable server-assigned id that never changes across client
restarts. Messages are append-only with an idempotency key so retries cannot
corrupt history.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Callable, List, Optional
from uuid import UUID

MESSAGE_ROLES = ("user", "assistant", "system", "tool")
MESSAGE_STATUSES = ("pending", "completed", "failed", "cancelled")
CONVERSATION_STATUSES = ("active", "archived")


@dataclass(frozen=True)
class ConversationRecord:
    conversation_id: UUID
    user_id: UUID
    device_id: UUID
    organization_id: UUID
    workspace_id: UUID
    title: str
    status: str
    created_at: int
    updated_at: int
    revision: int


@dataclass(frozen=True)
class MessageRecord:
    message_id: UUID
    conversation_id: UUID
    role: str
    content: str
    provider: Optional[str]
    model: Optional[str]
    status: str
    created_at: int
    completed_at: Optional[int]
    idempotency_key: Optional[UUID]
    parent_message_id: Optional[UUID]
    error_detail: str
    tokens_used: Optional[int]


class SQLiteConversationRepository:
    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory

    def prepare(self) -> None:
        with self._connection_factory() as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    organization_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT 'Conversation',
                    status TEXT NOT NULL CHECK(status IN ('active','archived')),
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    revision INTEGER NOT NULL CHECK(revision >= 1)
                );

                CREATE INDEX IF NOT EXISTS idx_conversations_workspace_updated
                ON conversations(workspace_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS conversation_messages (
                    message_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL
                        REFERENCES conversations(conversation_id),
                    role TEXT NOT NULL CHECK(role IN ('user','assistant','system','tool')),
                    content TEXT NOT NULL,
                    provider TEXT,
                    model TEXT,
                    status TEXT NOT NULL
                        CHECK(status IN ('pending','completed','failed','cancelled')),
                    created_at INTEGER NOT NULL,
                    completed_at INTEGER,
                    idempotency_key TEXT UNIQUE,
                    parent_message_id TEXT,
                    error_detail TEXT NOT NULL DEFAULT '',
                    tokens_used INTEGER
                );

                CREATE INDEX IF NOT EXISTS idx_messages_conversation_created
                ON conversation_messages(conversation_id, created_at, message_id);
                """
            )
            connection.commit()

    def create_conversation(
        self,
        *,
        conversation_id: UUID,
        user_id: UUID,
        device_id: UUID,
        organization_id: UUID,
        workspace_id: UUID,
        title: str,
        now: int,
    ) -> ConversationRecord:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO conversations(
                        conversation_id, user_id, device_id, organization_id,
                        workspace_id, title, status, created_at, updated_at, revision
                    ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, 1)
                    """,
                    (
                        str(conversation_id),
                        str(user_id),
                        str(device_id),
                        str(organization_id),
                        str(workspace_id),
                        title,
                        now,
                        now,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.get_conversation(conversation_id)  # type: ignore[return-value]

    def get_conversation(self, conversation_id: UUID) -> Optional[ConversationRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM conversations WHERE conversation_id = ?",
                (str(conversation_id),),
            ).fetchone()
        return self._conversation(row) if row is not None else None

    def list_conversations(
        self, user_id: UUID, workspace_id: UUID, limit: int = 100
    ) -> List[ConversationRecord]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                """
                SELECT * FROM conversations
                WHERE user_id = ? AND workspace_id = ? AND status = 'active'
                ORDER BY updated_at DESC LIMIT ?
                """,
                (str(user_id), str(workspace_id), max(1, min(limit, 500))),
            ).fetchall()
        return [self._conversation(row) for row in rows]

    def append_message(
        self,
        *,
        message_id: UUID,
        conversation_id: UUID,
        role: str,
        content: str,
        status: str,
        now: int,
        idempotency_key: Optional[UUID] = None,
        parent_message_id: Optional[UUID] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
    ) -> bool:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO conversation_messages(
                        message_id, conversation_id, role, content, provider, model,
                        status, created_at, idempotency_key, parent_message_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(message_id),
                        str(conversation_id),
                        role,
                        content,
                        provider,
                        model,
                        status,
                        now,
                        str(idempotency_key) if idempotency_key else None,
                        str(parent_message_id) if parent_message_id else None,
                    ),
                )
                connection.execute(
                    """
                    UPDATE conversations
                    SET updated_at = ?, revision = revision + 1
                    WHERE conversation_id = ?
                    """,
                    (now, str(conversation_id)),
                )
                connection.commit()
                return True
            except sqlite3.IntegrityError:
                connection.rollback()
                return False
            except Exception:
                connection.rollback()
                raise

    def complete_message(
        self,
        message_id: UUID,
        *,
        status: str,
        now: int,
        content: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        tokens_used: Optional[int] = None,
        error_detail: str = "",
    ) -> bool:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    """
                    UPDATE conversation_messages
                    SET status = ?, completed_at = ?,
                        content = COALESCE(?, content),
                        provider = COALESCE(?, provider),
                        model = COALESCE(?, model),
                        tokens_used = COALESCE(?, tokens_used),
                        error_detail = ?
                    WHERE message_id = ? AND status = 'pending'
                    """,
                    (
                        status,
                        now,
                        content,
                        provider,
                        model,
                        tokens_used,
                        error_detail,
                        str(message_id),
                    ),
                )
                connection.commit()
                return cursor.rowcount == 1
            except Exception:
                connection.rollback()
                raise

    def list_messages(self, conversation_id: UUID) -> List[MessageRecord]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                """
                SELECT * FROM conversation_messages
                WHERE conversation_id = ?
                ORDER BY created_at, message_id
                """,
                (str(conversation_id),),
            ).fetchall()
        return [self._message(row) for row in rows]

    def get_message(self, message_id: UUID) -> Optional[MessageRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM conversation_messages WHERE message_id = ?",
                (str(message_id),),
            ).fetchone()
        return self._message(row) if row is not None else None

    def last_user_message(self, conversation_id: UUID) -> Optional[MessageRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                """
                SELECT * FROM conversation_messages
                WHERE conversation_id = ? AND role = 'user' AND status = 'completed'
                ORDER BY created_at DESC, message_id DESC LIMIT 1
                """,
                (str(conversation_id),),
            ).fetchone()
        return self._message(row) if row is not None else None

    @staticmethod
    def _conversation(row: sqlite3.Row) -> ConversationRecord:
        return ConversationRecord(
            conversation_id=UUID(str(row["conversation_id"])),
            user_id=UUID(str(row["user_id"])),
            device_id=UUID(str(row["device_id"])),
            organization_id=UUID(str(row["organization_id"])),
            workspace_id=UUID(str(row["workspace_id"])),
            title=str(row["title"]),
            status=str(row["status"]),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
            revision=int(row["revision"]),
        )

    @staticmethod
    def _message(row: sqlite3.Row) -> MessageRecord:
        return MessageRecord(
            message_id=UUID(str(row["message_id"])),
            conversation_id=UUID(str(row["conversation_id"])),
            role=str(row["role"]),
            content=str(row["content"]),
            provider=str(row["provider"]) if row["provider"] else None,
            model=str(row["model"]) if row["model"] else None,
            status=str(row["status"]),
            created_at=int(row["created_at"]),
            completed_at=int(row["completed_at"]) if row["completed_at"] is not None else None,
            idempotency_key=UUID(str(row["idempotency_key"])) if row["idempotency_key"] else None,
            parent_message_id=(
                UUID(str(row["parent_message_id"])) if row["parent_message_id"] else None
            ),
            error_detail=str(row["error_detail"]),
            tokens_used=int(row["tokens_used"]) if row["tokens_used"] is not None else None,
        )
