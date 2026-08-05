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
RUN_STATUSES = (
    "queued",
    "running",
    "cancellation_requested",
    "completed",
    "failed",
    "cancelled",
    "interrupted",
)
TERMINAL_RUN_STATUSES = ("completed", "failed", "cancelled", "interrupted")


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


@dataclass(frozen=True)
class RunRecord:
    run_id: UUID
    conversation_id: UUID
    message_id: UUID
    status: str
    provider: Optional[str]
    model: Optional[str]
    parent_run_id: Optional[UUID]
    created_at: int
    started_at: Optional[int]
    terminal_at: Optional[int]
    error_detail: str
    schema_version: int


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

                CREATE TABLE IF NOT EXISTS conversation_runs (
                    run_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL
                        REFERENCES conversations(conversation_id),
                    message_id TEXT NOT NULL
                        REFERENCES conversation_messages(message_id),
                    status TEXT NOT NULL CHECK(status IN (
                        'queued','running','cancellation_requested',
                        'completed','failed','cancelled','interrupted'
                    )),
                    provider TEXT,
                    model TEXT,
                    parent_run_id TEXT,
                    created_at INTEGER NOT NULL,
                    started_at INTEGER,
                    terminal_at INTEGER,
                    error_detail TEXT NOT NULL DEFAULT '',
                    schema_version INTEGER NOT NULL DEFAULT 1
                );

                CREATE INDEX IF NOT EXISTS idx_conversation_runs_conversation
                ON conversation_runs(conversation_id, created_at);

                CREATE INDEX IF NOT EXISTS idx_conversation_runs_status
                ON conversation_runs(status);
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

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------

    def create_run(
        self,
        *,
        run_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        status: str,
        now: int,
        parent_run_id: Optional[UUID] = None,
    ) -> RunRecord:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO conversation_runs(
                        run_id, conversation_id, message_id, status,
                        parent_run_id, created_at, started_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(run_id),
                        str(conversation_id),
                        str(message_id),
                        status,
                        str(parent_run_id) if parent_run_id else None,
                        now,
                        now if status == "running" else None,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.get_run(run_id)  # type: ignore[return-value]

    def get_run(self, run_id: UUID) -> Optional[RunRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM conversation_runs WHERE run_id = ?", (str(run_id),)
            ).fetchone()
        return self._run(row) if row is not None else None

    def list_runs(self, conversation_id: UUID) -> List[RunRecord]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                """
                SELECT * FROM conversation_runs
                WHERE conversation_id = ?
                ORDER BY created_at, run_id
                """,
                (str(conversation_id),),
            ).fetchall()
        return [self._run(row) for row in rows]

    def list_runs_all(self) -> List[RunRecord]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM conversation_runs ORDER BY created_at"
            ).fetchall()
        return [self._run(row) for row in rows]

    def message_by_idempotency_key(self, key: UUID) -> Optional[MessageRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                """
                SELECT * FROM conversation_messages WHERE idempotency_key = ?
                """,
                (str(key),),
            ).fetchone()
        return self._message(row) if row is not None else None

    def fetch_conversation_events_after(
        self,
        cursor: int,
        workspace_id: UUID,
        conversation_id: Optional[UUID] = None,
        limit: int = 100,
    ) -> List[sqlite3.Row]:
        """Cursor-resumable conversation events scoped to a workspace. Uses the
        same shared `events` table as the existing realtime infrastructure."""
        start = max(0, int(cursor))
        count = max(1, min(200, int(limit)))
        with self._connection_factory() as connection:
            if conversation_id is None:
                rows = connection.execute(
                    """
                    SELECT id, recorded_at, level, source, message
                    FROM events
                    WHERE id > ? AND source = 'conversations'
                      AND json_extract(message, '$.ws') = ?
                    ORDER BY id ASC LIMIT ?
                    """,
                    (start, str(workspace_id), count),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT id, recorded_at, level, source, message
                    FROM events
                    WHERE id > ? AND source = 'conversations'
                      AND json_extract(message, '$.ws') = ?
                      AND json_extract(message, '$.conversation') = ?
                    ORDER BY id ASC LIMIT ?
                    """,
                    (start, str(workspace_id), str(conversation_id), count),
                ).fetchall()
        return rows

    def update_run(
        self,
        run_id: UUID,
        *,
        status: str,
        now: int,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        error_detail: str = "",
    ) -> bool:
        """Transitions a run. Once a run is terminal, later writes cannot
        overwrite it (late provider output after cancellation is discarded)."""
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT status FROM conversation_runs WHERE run_id = ?",
                    (str(run_id),),
                ).fetchone()
                if row is None:
                    connection.rollback()
                    return False
                current = str(row["status"])
                if current in TERMINAL_RUN_STATUSES:
                    connection.rollback()
                    return False
                terminal = status in TERMINAL_RUN_STATUSES
                connection.execute(
                    """
                    UPDATE conversation_runs
                    SET status = ?, provider = COALESCE(?, provider),
                        model = COALESCE(?, model),
                        error_detail = CASE WHEN ? = '' THEN error_detail ELSE ? END,
                        started_at = COALESCE(started_at, ?),
                        terminal_at = CASE WHEN ? THEN ? ELSE terminal_at END
                    WHERE run_id = ? AND status NOT IN (
                        'completed','failed','cancelled','interrupted'
                    )
                    """,
                    (
                        status,
                        provider,
                        model,
                        error_detail,
                        error_detail,
                        now,
                        1 if terminal else 0,
                        now if terminal else None,
                        str(run_id),
                    ),
                )
                connection.commit()
                return True
            except Exception:
                connection.rollback()
                raise

    def interrupt_stale_runs(self, now: int) -> int:
        """Recovery: runs left in queued/running/cancellation_requested after a
        restart are interrupted; their pending assistant messages are cancelled
        with an explicit interruption note. User messages are preserved."""
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                rows = connection.execute(
                    """
                    SELECT run_id, message_id FROM conversation_runs
                    WHERE status IN ('queued','running','cancellation_requested')
                    """
                ).fetchall()
                if rows:
                    connection.executemany(
                        """
                        UPDATE conversation_runs
                        SET status = 'interrupted', terminal_at = ?,
                            error_detail = 'interrupted by restart'
                        WHERE run_id = ? AND status IN (
                            'queued','running','cancellation_requested'
                        )
                        """,
                        [(now, str(row["run_id"])) for row in rows],
                    )
                    for row in rows:
                        connection.execute(
                            """
                            UPDATE conversation_messages
                            SET status = 'cancelled', completed_at = ?,
                                error_detail = 'interrupted by restart'
                            WHERE message_id = ? AND status = 'pending'
                            """,
                            (now, str(row["message_id"])),
                        )
                connection.commit()
                return len(rows)
            except Exception:
                connection.rollback()
                raise

    # ------------------------------------------------------------------
    # Conversation title / status
    # ------------------------------------------------------------------

    def set_conversation_title(self, conversation_id: UUID, title: str, now: int) -> bool:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    """
                    UPDATE conversations
                    SET title = ?, updated_at = ?, revision = revision + 1
                    WHERE conversation_id = ? AND status = 'active'
                    """,
                    (title, now, str(conversation_id)),
                )
                connection.commit()
                return cursor.rowcount == 1
            except Exception:
                connection.rollback()
                raise

    def set_conversation_status(
        self, conversation_id: UUID, status: str, now: int
    ) -> bool:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    """
                    UPDATE conversations
                    SET status = ?, updated_at = ?, revision = revision + 1
                    WHERE conversation_id = ? AND status = 'active'
                    """,
                    (status, now, str(conversation_id)),
                )
                connection.commit()
                return cursor.rowcount == 1
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _run(row: sqlite3.Row) -> RunRecord:
        return RunRecord(
            run_id=UUID(str(row["run_id"])),
            conversation_id=UUID(str(row["conversation_id"])),
            message_id=UUID(str(row["message_id"])),
            status=str(row["status"]),
            provider=str(row["provider"]) if row["provider"] else None,
            model=str(row["model"]) if row["model"] else None,
            parent_run_id=UUID(str(row["parent_run_id"])) if row["parent_run_id"] else None,
            created_at=int(row["created_at"]),
            started_at=int(row["started_at"]) if row["started_at"] is not None else None,
            terminal_at=int(row["terminal_at"]) if row["terminal_at"] is not None else None,
            error_detail=str(row["error_detail"]),
            schema_version=int(row["schema_version"]),
        )

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
