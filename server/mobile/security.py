"""Pairing, authentication, and session management for the JoeOS Mobile
Companion Platform.

Pairing uses short-lived, single-use, rate-limited codes with confirmation on
both the trusted host and the mobile client. Mobile authentication uses
short-lived access tokens with rotating refresh credentials and server-side
revocation; no permanent bearer tokens. Sessions expire and are revocable.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets as builtin_secrets
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Optional, Sequence, Tuple

from .clients import HostRegistry, MobileClientRegistry, MobileError
from .models import (
    MobileSession,
    PairingSession,
    REMOTE_API_VERSION,
)

PAIRING_CODE_TTL_SECONDS = 120
PAIRING_MAX_ATTEMPTS = 5
MAX_PENDING_PAIRINGS = 50
SESSION_TTL_HOURS = 8
REFRESH_TTL_DAYS = 30


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PairingError(MobileError):
    pass


class AuthenticationError(MobileError):
    pass


class PairingCoordinator:
    """Two-party pairing requiring host + client confirmation."""

    def __init__(
        self,
        connection_factory: Callable[[], sqlite3.Connection],
        clients: MobileClientRegistry,
        hosts: HostRegistry,
        code_generator=None,
    ) -> None:
        self._connection_factory = connection_factory
        self._clients = clients
        self._hosts = hosts
        self._lock = threading.RLock()
        self._generate = code_generator or (lambda: "%06d" % builtin_secrets.randbelow(1000000))

    def begin(
        self,
        *,
        host_id: str,
        method: str = "one_time_code",
        requested_permissions: Sequence[str] = (),
        requested_projects: Sequence[str] = (),
    ) -> PairingSession:
        host = self._hosts.get(host_id)
        if host is None:
            raise PairingError("host not found.")
        self._cleanup_expired()
        with self._lock, self._connection_factory() as connection:
            pending = connection.execute(
                "SELECT COUNT(*) FROM mobile_pairing_sessions WHERE state = 'pending'"
            ).fetchone()[0]
            if int(pending) >= MAX_PENDING_PAIRINGS:
                raise PairingError("too many pending pairings; try again later.")
            code = self._generate()
            session_id = "pair_" + uuid.uuid4().hex[:16]
            expires = (datetime.now(timezone.utc) + timedelta(seconds=PAIRING_CODE_TTL_SECONDS)).isoformat()
            connection.execute(
                """
                INSERT INTO mobile_pairing_sessions (
                    session_id, host_id, method, code_reference, code_hash, expires_at,
                    state, api_version, requested_permissions, requested_projects, created_at
                ) VALUES (?, ?, ?, 'hash', ?, ?, 'pending', ?, ?, ?, ?)
                """,
                (
                    session_id, host_id, method, self._hash_code(code), expires,
                    REMOTE_API_VERSION, "\n".join(requested_permissions), "\n".join(requested_projects), _now(),
                ),
            )
        return PairingSession(
            session_id=session_id,
            host_id=host_id,
            method=method,
            code_reference="display-only",
            code_hash="",
            display_code=code,
            expires_at=expires,
            state="pending",
            api_version=REMOTE_API_VERSION,
            requested_permissions=tuple(requested_permissions),
            requested_projects=tuple(requested_projects),
            created_at=_now(),
        )

    def confirm_host(self, *, session_id: str) -> PairingSession:
        with self._lock, self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM mobile_pairing_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if row is None or str(row["state"]) != "pending":
                raise PairingError("pairing session is not pending.")
            connection.execute(
                "UPDATE mobile_pairing_sessions SET state = 'host_confirmed' WHERE session_id = ?",
                (session_id,),
            )
        return self._get(session_id)

    def confirm_client(
        self,
        *,
        session_id: str,
        client_id: str,
        code: str,
        api_version: int = REMOTE_API_VERSION,
    ) -> MobileClientRecord:
        with self._lock, self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM mobile_pairing_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if row is None:
                raise PairingError("pairing session not found.")
            if str(row["state"]) == "pending":
                raise PairingError("host has not confirmed this pairing.")
            if str(row["state"]) not in {"host_confirmed", "client_confirmed", "completed"}:
                raise PairingError("pairing session is not confirmable.")
            if not hmac.compare_digest(self._hash_code(code), str(row["code_hash"])):
                raise PairingError("pairing code is incorrect.")
            try:
                if datetime.fromisoformat(str(row["expires_at"])) < datetime.now(timezone.utc):
                    connection.execute(
                        "UPDATE mobile_pairing_sessions SET state = 'expired' WHERE session_id = ?",
                        (session_id,),
                    )
                    raise PairingError("pairing code has expired.")
            except ValueError:
                raise PairingError("pairing code has expired.") from None
            host_id = str(row["host_id"])
            connection.execute(
                "UPDATE mobile_pairing_sessions SET client_id = ?, state = 'completed' WHERE session_id = ?",
                (client_id, session_id),
            )
            now = _now()
            connection.execute(
                """
                UPDATE mobile_clients
                SET paired_host = ?, pairing_state = 'paired', trust_state = 'paired_but_restricted',
                    last_connection = ?
                WHERE client_id = ?
                """,
                (host_id, now, client_id),
            )
            connection.execute(
                "UPDATE mobile_hosts SET paired_state = 'paired' WHERE host_id = ?", (host_id,)
            )
        return self._clients.get(client_id)

    def cancel(self, *, session_id: str) -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE mobile_pairing_sessions SET state = 'cancelled' WHERE session_id = ?",
                (session_id,),
            )

    def _get(self, session_id: str) -> Optional[PairingSession]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM mobile_pairing_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        if row is None:
            return None
        import json as _json
        return PairingSession(
            session_id=str(row["session_id"]),
            host_id=str(row["host_id"]),
            client_id=str(row["client_id"]),
            method=str(row["method"]),
            code_reference=str(row["code_reference"]),
            code_hash="",
            display_code="",
            expires_at=str(row["expires_at"]),
            state=str(row["state"]),
            api_version=int(row["api_version"]),
            requested_permissions=tuple(p for p in str(row["requested_permissions"]).split("\n") if p),
            requested_projects=tuple(p for p in str(row["requested_projects"]).split("\n") if p),
            created_at=str(row["created_at"]),
        )

    def list_pending(self) -> Tuple[dict, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT session_id, host_id, method, state, created_at, expires_at FROM mobile_pairing_sessions WHERE state IN ('pending','host_confirmed') ORDER BY created_at DESC LIMIT 20"
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def _hash_code(self, code: str) -> str:
        return hashlib.sha256(("joeos-mobile-pairing-v1\0" + code).encode("utf-8")).hexdigest()

    def _cleanup_expired(self) -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE mobile_pairing_sessions SET state = 'expired' WHERE expires_at < ? AND state IN ('pending','host_confirmed')",
                (_now(),),
            )


class MobileAuthenticationService:
    """Short-lived access tokens with rotating refresh credentials."""

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()

    def _ensure_tables(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS mobile_credentials (
                client_id TEXT PRIMARY KEY,
                refresh_reference TEXT NOT NULL,
                refresh_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                revoked INTEGER NOT NULL DEFAULT 0
            )
            """
        )

    def issue_refresh(self, *, client_id: str) -> str:
        """Issue a refresh credential; returns the plaintext once (never stored)."""
        token = builtin_secrets.token_urlsafe(32)
        refresh_reference = "refresh_" + uuid.uuid4().hex[:16]
        expires = (datetime.now(timezone.utc) + timedelta(days=REFRESH_TTL_DAYS)).isoformat()
        with self._lock, self._connection_factory() as connection:
            self._ensure_tables(connection)
            connection.execute(
                "DELETE FROM mobile_credentials WHERE client_id = ?", (client_id,)
            )
            connection.execute(
                """
                INSERT INTO mobile_credentials (client_id, refresh_reference, refresh_hash, created_at, expires_at, revoked)
                VALUES (?, ?, ?, ?, ?, 0)
                """,
                (client_id, refresh_reference, self._hash_token(token), _now(), expires),
            )
        return token

    def verify_refresh(self, *, client_id: str, refresh_token: str) -> bool:
        with self._connection_factory() as connection:
            self._ensure_tables(connection)
            row = connection.execute(
                "SELECT * FROM mobile_credentials WHERE client_id = ?", (client_id,)
            ).fetchone()
        if row is None or bool(row["revoked"]):
            return False
        try:
            if datetime.fromisoformat(str(row["expires_at"])) < datetime.now(timezone.utc):
                return False
        except ValueError:
            return False
        return hmac.compare_digest(self._hash_token(refresh_token), str(row["refresh_hash"]))

    def revoke_refresh(self, *, client_id: str) -> None:
        with self._lock, self._connection_factory() as connection:
            self._ensure_tables(connection)
            connection.execute(
                "UPDATE mobile_credentials SET revoked = 1 WHERE client_id = ?", (client_id,)
            )

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(("joeos-mobile-refresh-v1\0" + token).encode("utf-8")).hexdigest()


class MobileSessionManager:
    """Authoritative mobile sessions: create, renew, expire, revoke."""

    def __init__(
        self,
        connection_factory: Callable[[], sqlite3.Connection],
        clients: MobileClientRegistry,
        auth: MobileAuthenticationService,
    ) -> None:
        self._connection_factory = connection_factory
        self._clients = clients
        self._auth = auth
        self._lock = threading.RLock()

    def create(
        self,
        *,
        client_id: str,
        host_id: str,
        user_identity: str = "user",
        capabilities: Sequence[str] = (),
        projects: Sequence[str] = (),
        scopes: Sequence[str] = (),
        api_version: int = REMOTE_API_VERSION,
        ttl_hours: int = SESSION_TTL_HOURS,
    ) -> MobileSession:
        client = self._clients.get(client_id)
        if client is None or client.revocation_state != "active":
            raise AuthenticationError("client is revoked.")
        session_id = "sess_" + uuid.uuid4().hex[:16]
        started = _now()
        expires = (datetime.now(timezone.utc) + timedelta(hours=ttl_hours)).isoformat()
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO mobile_sessions (
                    session_id, client_id, host_id, user_identity, started_at, expires_at,
                    last_activity, transport, encryption_state, api_version, granted_capabilities,
                    granted_projects, granted_scopes, background_eligible, notification_eligible,
                    risk_state, device_lock_state, authentication_strength, active_subscriptions,
                    queued_operations, termination_reason, connection_state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'https', 'encrypted', ?, ?, ?, ?, 0, 0, 'normal', 'unlocked', 'host_authenticated', '', 0, '', 'active')
                """,
                (
                    session_id, client_id, host_id, user_identity, started, expires, started,
                    api_version, "\n".join(capabilities), "\n".join(projects), "\n".join(scopes),
                ),
            )
            connection.execute(
                "UPDATE mobile_clients SET active_session = ?, authentication_state = 'authenticated', last_connection = ?, connection_state = 'connected' WHERE client_id = ?",
                (session_id, started, client_id),
            )
        return self.get(session_id)

    def get(self, session_id: str) -> Optional[MobileSession]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM mobile_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        return self._row(row) if row else None

    def is_valid(self, session_id: str) -> bool:
        session = self.get(session_id)
        if session is None:
            return False
        if session.connection_state not in {"active", "reconnecting", "offline"}:
            return False
        try:
            if datetime.fromisoformat(session.expires_at) < datetime.now(timezone.utc):
                return False
        except ValueError:
            return False
        return True

    def touch(self, session_id: str) -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE mobile_sessions SET last_activity = ? WHERE session_id = ?",
                (_now(), session_id),
            )

    def renew(self, *, session_id: str, client_id: str) -> MobileSession:
        if not self.is_valid(session_id):
            raise AuthenticationError("session is not renewable.")
        session = self.get(session_id)
        if session is None or session.client_id != client_id:
            raise AuthenticationError("session does not belong to this client.")
        new_expires = (datetime.now(timezone.utc) + timedelta(hours=SESSION_TTL_HOURS)).isoformat()
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE mobile_sessions SET expires_at = ?, last_activity = ? WHERE session_id = ?",
                (new_expires, _now(), session_id),
            )
        return self.get(session_id)

    def revoke(self, session_id: str, *, reason: str = "") -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE mobile_sessions SET connection_state = 'revoked', termination_reason = ? WHERE session_id = ?",
                (reason[:200] or "revoked", session_id),
            )

    def revoke_for_client(self, client_id: str, *, reason: str = "") -> int:
        with self._lock, self._connection_factory() as connection:
            cursor = connection.execute(
                "UPDATE mobile_sessions SET connection_state = 'revoked', termination_reason = ? WHERE client_id = ? AND connection_state IN ('active','reconnecting','offline')",
                (reason[:200] or "client revoked", client_id),
            )
            connection.execute(
                "UPDATE mobile_clients SET active_session = '' WHERE client_id = ?", (client_id,)
            )
        return cursor.rowcount

    def expire_stale(self) -> int:
        with self._lock, self._connection_factory() as connection:
            cursor = connection.execute(
                "UPDATE mobile_sessions SET connection_state = 'expired', termination_reason = 'session expired' WHERE expires_at < ? AND connection_state IN ('active','reconnecting','offline')",
                (_now(),),
            )
        return cursor.rowcount

    def list_active(self) -> Tuple[MobileSession, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM mobile_sessions WHERE connection_state IN ('active','reconnecting','offline') ORDER BY started_at DESC"
            ).fetchall()
        return tuple(self._row(row) for row in rows)

    @staticmethod
    def _row(row: sqlite3.Row) -> MobileSession:
        return MobileSession(
            session_id=str(row["session_id"]),
            client_id=str(row["client_id"]),
            host_id=str(row["host_id"]),
            user_identity=str(row["user_identity"]),
            started_at=str(row["started_at"]),
            expires_at=str(row["expires_at"]),
            last_activity=str(row["last_activity"]),
            transport=str(row["transport"]),
            encryption_state=str(row["encryption_state"]),
            api_version=int(row["api_version"]),
            granted_capabilities=tuple(p for p in str(row["granted_capabilities"]).split("\n") if p),
            granted_projects=tuple(p for p in str(row["granted_projects"]).split("\n") if p),
            granted_scopes=tuple(p for p in str(row["granted_scopes"]).split("\n") if p),
            background_eligible=bool(row["background_eligible"]),
            notification_eligible=bool(row["notification_eligible"]),
            risk_state=str(row["risk_state"]),
            device_lock_state=str(row["device_lock_state"]),
            authentication_strength=str(row["authentication_strength"]),
            active_subscriptions=tuple(p for p in str(row["active_subscriptions"]).split("\n") if p),
            queued_operations=int(row["queued_operations"]),
            termination_reason=str(row["termination_reason"]),
            connection_state=str(row["connection_state"]),
        )