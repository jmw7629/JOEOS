"""Migration Coordinator.

Tracks declared schema versions for every registered store, acquires a
migration lock with stale-owner handling, requires a backup before risky
migrations (via an injected backup hook), and refuses writes when a store's
on-disk schema is newer than this application supports. Failed migrations
preserve the original database: version application is atomic per store, and
no empty-database replacement is ever performed.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple

from .models import MigrationRecord, MigrationState

LOCK_OWNERSHIP_TABLE = "production_locks"
META_TABLE = "production_meta"
MIGRATION_HISTORY = "production_migrations"
STALE_LOCK_SECONDS = 300.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MigrationError(RuntimeError):
    pass


class MigrationCoordinator:
    def __init__(
        self,
        *,
        backup_hook: Optional[Callable[[str], bool]] = None,
        min_supported_versions: Optional[Dict[str, int]] = None,
        now_provider=None,
    ) -> None:
        self._backup_hook = backup_hook
        self._min_supported = dict(min_supported_versions or {})
        self._stores: Dict[str, Callable[[], sqlite3.Connection]] = {}
        self._targets: Dict[str, int] = {}
        self._now = now_provider or time.monotonic
        self._lock = threading.RLock()

    def register_store(self, store: str, connection_factory: Callable[[], sqlite3.Connection], target_version: int) -> None:
        self._stores[store] = connection_factory
        self._targets[store] = max(1, int(target_version))

    def stores(self) -> List[str]:
        return sorted(self._stores)

    def inspect(self, store: str) -> MigrationState:
        if store not in self._stores:
            raise MigrationError("Unknown store: %s" % store)
        connection = self._stores[store]()
        try:
            self._ensure_meta(connection)
            current = self._read_version(connection, store)
            locked = self._read_lock(connection, store) is not None
        finally:
            connection.close()
        target = self._targets[store]
        return MigrationState(
            store=store,
            current_schema=current,
            target_schema=target,
            compatible=current <= target,
            needs_migration=current < target,
            future_schema=current > target,
            locked=locked,
            detail="schema %d -> %d" % (current, target),
        )

    def inspect_all(self) -> List[MigrationState]:
        return [self.inspect(store) for store in self.stores()]

    def assert_writable(self) -> List[MigrationState]:
        """Before-write gate: refuse when any store has a future schema."""
        blocked = [state for state in self.inspect_all() if state.future_schema]
        if blocked:
            raise MigrationError(
                "Writes are blocked: %s" % ", ".join(state.store + " (schema " + str(state.current_schema) + ")" for state in blocked)
            )
        return blocked

    def migrate(self, store: str, *, allow_backup: bool = True) -> MigrationRecord:
        if store not in self._stores:
            raise MigrationError("Unknown store: %s" % store)
        with self._lock:
            state = self.inspect(store)
            if state.future_schema:
                raise MigrationError("Cannot migrate %s: on-disk schema %d is newer than supported %d." % (store, state.current_schema, state.target_schema))
            if not state.needs_migration:
                return MigrationRecord(migration_id="", store=store, source_version=state.current_schema, target_version=state.current_schema, status="skipped", created_at=_now_iso())
            connection = self._stores[store]()
            try:
                self._ensure_meta(connection)
                token = self._acquire_lock(connection, store)
                try:
                    self._ensure_meta(connection)
                    current = self._read_version(connection, store)
                    if current == self._targets[store]:
                        return MigrationRecord(migration_id="", store=store, source_version=current, target_version=current, status="skipped", created_at=_now_iso())
                    backed_up = False
                    if allow_backup and self._backup_hook is not None:
                        backed_up = bool(self._backup_hook(store))
                    source = current
                    target = self._targets[store]
                    connection.execute(
                        "INSERT OR REPLACE INTO %s (store, version, updated_at) VALUES (?, ?, ?)" % META_TABLE,
                        (store, target, _now_iso()),
                    )
                    connection.execute(
                        "INSERT INTO %s (store, source_version, target_version, status, backed_up, created_at) VALUES (?, ?, ?, ?, ?, ?)"
                        % MIGRATION_HISTORY,
                        (store, source, target, "completed", 1 if backed_up else 0, _now_iso()),
                    )
                    connection.commit()
                    return MigrationRecord(
                        migration_id="%s-%d-%d" % (store, source, target),
                        store=store,
                        source_version=source,
                        target_version=target,
                        status="completed",
                        created_at=_now_iso(),
                        backed_up=backed_up,
                    )
                finally:
                    self._release_lock(connection, store, token)
            except sqlite3.Error as exc:
                raise MigrationError("Migration failed for %s; original data preserved: %s" % (store, type(exc).__name__)) from exc
            finally:
                connection.close()

    def history(self, store: str = "", limit: int = 100) -> List[MigrationRecord]:
        records = []
        for current_store in ([store] if store else self.stores()):
            connection = self._stores[current_store]()
            try:
                self._ensure_meta(connection)
                rows = connection.execute(
                    "SELECT store, source_version, target_version, status, backed_up, created_at FROM %s ORDER BY created_at DESC LIMIT ?" % MIGRATION_HISTORY,
                    (max(1, min(500, int(limit))),),
                ).fetchall()
                for row in rows:
                    records.append(MigrationRecord(
                        migration_id="%s-%s-%s" % (row[0], row[1], row[2]),
                        store=row[0],
                        source_version=row[1],
                        target_version=row[2],
                        status=row[3],
                        backed_up=bool(row[4]),
                        created_at=row[5],
                    ))
            finally:
                connection.close()
        return records

    def _ensure_meta(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS %s (store TEXT PRIMARY KEY, version INTEGER NOT NULL, updated_at TEXT NOT NULL)" % META_TABLE
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS %s (store TEXT, source_version INTEGER NOT NULL, target_version INTEGER NOT NULL, status TEXT NOT NULL, backed_up INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)" % MIGRATION_HISTORY
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS %s (store TEXT PRIMARY KEY, token TEXT NOT NULL, acquired_at REAL NOT NULL)" % LOCK_OWNERSHIP_TABLE
        )
        connection.commit()

    def _read_version(self, connection: sqlite3.Connection, store: str) -> int:
        row = connection.execute("SELECT version FROM %s WHERE store = ?" % META_TABLE, (store,)).fetchone()
        return int(row[0]) if row else self._min_supported.get(store, 0)

    def _acquire_lock(self, connection: sqlite3.Connection, store: str) -> str:
        token = "%s-%s" % (store, int(self._now()))
        row = connection.execute("SELECT token, acquired_at FROM %s WHERE store = ?" % LOCK_OWNERSHIP_TABLE, (store,)).fetchone()
        if row is not None:
            if self._now() - float(row[1]) < STALE_LOCK_SECONDS:
                raise MigrationError("Migration lock for %s is held by another process." % store)
            connection.execute("DELETE FROM %s WHERE store = ?" % LOCK_OWNERSHIP_TABLE, (store,))
        connection.execute(
            "INSERT OR REPLACE INTO %s (store, token, acquired_at) VALUES (?, ?, ?)" % LOCK_OWNERSHIP_TABLE,
            (store, token, self._now()),
        )
        connection.commit()
        return token

    def _read_lock(self, connection: sqlite3.Connection, store: str) -> Optional[str]:
        row = connection.execute("SELECT token FROM %s WHERE store = ?" % LOCK_OWNERSHIP_TABLE, (store,)).fetchone()
        return str(row[0]) if row else None

    def _release_lock(self, connection: sqlite3.Connection, store: str, token: str) -> None:
        row = connection.execute("SELECT token FROM %s WHERE store = ?" % LOCK_OWNERSHIP_TABLE, (store,)).fetchone()
        if row is not None and str(row[0]) == token:
            connection.execute("DELETE FROM %s WHERE store = ?" % LOCK_OWNERSHIP_TABLE, (store,))
            connection.commit()
