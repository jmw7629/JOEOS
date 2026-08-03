"""Execution safety primitives for the JoeOS Automation Platform.

Idempotency, deduplication, concurrency limits, resource locks, and rate
limits. Together these prevent duplicate side effects, runaway parallelism,
retry/notification storms, and stale-lock deadlocks.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Optional, Tuple


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConcurrencyLimit(RuntimeError):
    pass


class LockError(RuntimeError):
    pass


class RateLimitError(RuntimeError):
    pass


class IdempotencyService:
    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()

    def has_completed(self, key: str) -> bool:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT idempotency_key FROM workflow_idempotency WHERE idempotency_key = ? AND state = 'completed'",
                (key,),
            ).fetchone()
        return row is not None

    def mark_completed(self, key: str, *, run_id: str, node_id: str, action: str, result: dict) -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO workflow_idempotency (idempotency_key, run_id, node_id, action, scope, state, result, created_at, expires_at)
                VALUES (?, ?, ?, ?, 'global', 'completed', ?, ?, ?)
                ON CONFLICT(idempotency_key) DO NOTHING
                """,
                (key, run_id, node_id, action, __import__("json").dumps(result)[:2000], _now(), ""),
            )


class ConcurrencyGovernor:
    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()

    def _ensure_table(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_concurrency (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT NOT NULL,
                owner TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )

    def acquire(self, *, key: str, owner: str, max_count: int) -> None:
        with self._lock, self._connection_factory() as connection:
            self._ensure_table(connection)
            current = connection.execute(
                "SELECT COUNT(*) FROM workflow_concurrency WHERE key = ?", (key,)
            ).fetchone()[0]
            if int(current) >= max_count:
                raise ConcurrencyLimit("concurrency limit reached for %s." % key)
            connection.execute(
                "INSERT INTO workflow_concurrency (key, owner, created_at) VALUES (?, ?, ?)",
                (key, owner, _now()),
            )

    def release(self, *, key: str, owner: str) -> None:
        with self._lock, self._connection_factory() as connection:
            self._ensure_table(connection)
            connection.execute(
                "DELETE FROM workflow_concurrency WHERE key = ? AND owner = ?", (key, owner)
            )

    def active(self, *, key: str) -> int:
        with self._connection_factory() as connection:
            self._ensure_table(connection)
            row = connection.execute(
                "SELECT COUNT(*) FROM workflow_concurrency WHERE key = ?", (key,)
            ).fetchone()
        return int(row[0])


class ResourceLockManager:
    def __init__(self, connection_factory: Callable[[], sqlite3.Connection], lease_seconds: int = 300) -> None:
        self._connection_factory = connection_factory
        self._lease_seconds = lease_seconds
        self._lock = threading.RLock()

    def acquire(self, *, resource: str, owner: str, lock_type: str = "exclusive", timeout: int = 10) -> str:
        deadline = time.monotonic() + timeout
        while True:
            with self._lock, self._connection_factory() as connection:
                existing = connection.execute(
                    "SELECT * FROM workflow_locks WHERE resource = ?", (resource,)
                ).fetchone()
                if existing is None or existing["lock_type"] == "shared" and lock_type == "shared":
                    lock_id = "lock_" + str(int(time.time() * 1000))
                    lease_until = (datetime.now(timezone.utc) + timedelta(seconds=self._lease_seconds)).isoformat()
                    connection.execute(
                        """
                        INSERT INTO workflow_locks (lock_id, resource, lock_type, owner, run_id, lease_until, created_at)
                        VALUES (?, ?, ?, ?, '', ?, ?)
                        """,
                        (lock_id, resource, lock_type, owner, lease_until, _now()),
                    )
                    return lock_id
                lease = existing["lease_until"]
                try:
                    expired = datetime.fromisoformat(str(lease)) < datetime.now(timezone.utc)
                except ValueError:
                    expired = True
                if expired:
                    connection.execute("DELETE FROM workflow_locks WHERE resource = ?", (resource,))
                    continue
            if time.monotonic() >= deadline:
                raise LockError("could not acquire lock on %s within %ds." % (resource, timeout))
            time.sleep(0.1)

    def release(self, *, resource: str, owner: str) -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "DELETE FROM workflow_locks WHERE resource = ? AND owner = ?", (resource, owner)
            )

    def release_stale(self) -> int:
        now = datetime.now(timezone.utc)
        with self._lock, self._connection_factory() as connection:
            cursor = connection.execute(
                "DELETE FROM workflow_locks WHERE lease_until < ?", (now.isoformat(),)
            )
        return cursor.rowcount


class RateLimiter:
    def __init__(self, now_provider=None) -> None:
        self._now = now_provider or time.monotonic
        self._lock = threading.RLock()
        self._counts: Dict[str, list] = {}

    def check(self, *, key: str, limit_per_minute: int, burst: int = 0) -> None:
        window = 60.0
        now = self._now()
        with self._lock:
            counts = [stamp for stamp in self._counts.get(key, []) if now - stamp < window]
            limit = max(0, int(limit_per_minute))
            if len(counts) >= limit:
                raise RateLimitError("rate limit reached for %s." % key)
            counts.append(now)
            self._counts[key] = counts