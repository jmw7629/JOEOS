from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Callable, Optional
from uuid import UUID, uuid4


class SQLiteServerIdentityRepository:
    """Persists exactly one non-secret UUIDv4 installation identifier."""

    def __init__(
        self,
        connection_factory: Callable[[], sqlite3.Connection],
        uuid_provider: Optional[Callable[[], UUID]] = None,
    ) -> None:
        self._connection_factory = connection_factory
        self._uuid = uuid_provider or uuid4

    def prepare(self) -> None:
        with self._connection_factory() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS server_identity (
                    server_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL
                )
                """
            )

    def get_or_create_server_id(self) -> UUID:
        server_id = self._uuid()
        if not isinstance(server_id, UUID) or server_id.version != 4:
            raise TypeError("Server identity provider must return a UUIDv4.")
        connection = self._connection_factory()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT server_id FROM server_identity ORDER BY rowid ASC LIMIT 1"
            ).fetchone()
            if row is not None:
                try:
                    persisted = UUID(row["server_id"])
                except (TypeError, ValueError) as exc:
                    raise TypeError(
                        "Stored server identity must be a UUIDv4."
                    ) from exc
                if persisted.version != 4:
                    raise TypeError("Stored server identity must be a UUIDv4.")
                connection.commit()
                return persisted
            connection.execute(
                "INSERT INTO server_identity(server_id, created_at) VALUES (?, ?)",
                (str(server_id), datetime.now(timezone.utc).isoformat()),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return server_id
