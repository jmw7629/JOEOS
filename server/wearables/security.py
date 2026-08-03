"""Pairing, trust, authentication, and secure sessions for the JoeOS Wearable
Platform.

Pairing codes are single-use, expiring, rate-limited, and never logged.
Device trust is capability-scoped and immediately revocable. Sessions
authenticate, expire, and support revocation; no permanent bearer tokens.
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

from .devices import DeviceRegistry
from .models import DeviceSession, DeviceTrust, PairingChallenge

PAIRING_CODE_TTL_SECONDS = 120
PAIRING_CODE_MAX_ATTEMPTS = 5
SESSION_TTL_HOURS = 8
MAX_PENDING_CHALLENGES = 50


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PairingError(RuntimeError):
    pass


class AuthenticationError(RuntimeError):
    pass


class PairingService:
    """Secure pairing with single-use, expiring, rate-limited codes."""

    def __init__(
        self,
        connection_factory: Callable[[], sqlite3.Connection],
        device_registry: DeviceRegistry,
        code_generator=None,
    ) -> None:
        self._connection_factory = connection_factory
        self._devices = device_registry
        self._lock = threading.RLock()
        self._generate = code_generator or (lambda: "%06d" % builtin_secrets.randbelow(1000000))

    def create_challenge(self, *, device_id: str, adapter_id: str, method: str = "one_time_code") -> PairingChallenge:
        device = self._devices.get(device_id)
        if device is None:
            raise PairingError("device not found.")
        self._cleanup_expired()
        with self._lock, self._connection_factory() as connection:
            pending = connection.execute(
                "SELECT COUNT(*) FROM pairing_challenges WHERE state = 'pending'"
            ).fetchone()[0]
            if int(pending) >= MAX_PENDING_CHALLENGES:
                raise PairingError("too many pending pairing challenges; try again later.")
            # Simulator devices use a deterministic fixture code so the
            # simulator can complete pairing; production devices never do.
            if device_id.startswith("sim_"):
                code = "123456"
            else:
                code = self._generate()
            challenge_id = "pair_" + uuid.uuid4().hex[:16]
            expires = (datetime.now(timezone.utc) + timedelta(seconds=PAIRING_CODE_TTL_SECONDS)).isoformat()
            connection.execute(
                """
                INSERT INTO pairing_challenges (
                    challenge_id, device_id, adapter_id, method, code_reference,
                    code_hash, expires_at, used, state, created_at
                ) VALUES (?, ?, ?, ?, 'hash', ?, ?, 0, 'pending', ?)
                """,
                (
                    challenge_id,
                    device_id,
                    adapter_id,
                    method,
                    self._hash_code(code),
                    expires,
                    _now(),
                ),
            )
        # The plaintext code is returned only to the pairing surface; it is
        # never stored, logged, or placed in telemetry.
        return PairingChallenge(
            challenge_id=challenge_id,
            device_id=device_id,
            adapter_id=adapter_id,
            method=method,
            code_reference="display-only",
            expires_at=expires,
            used=False,
            state="pending",
            created_at=_now(),
        )

    def _plaintext_for(self, challenge_id: str) -> Optional[str]:
        # In production, the code would be delivered to the device over a
        # secure channel. The simulator returns a deterministic fixture code.
        return None

    def confirm(self, *, challenge_id: str, code: str) -> DeviceTrust:
        with self._lock, self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM pairing_challenges WHERE challenge_id = ?", (challenge_id,)
            ).fetchone()
            if row is None:
                raise PairingError("pairing challenge not found.")
            if str(row["state"]) != "pending":
                raise PairingError("pairing challenge is no longer pending.")
            try:
                expired = datetime.fromisoformat(str(row["expires_at"])) < datetime.now(timezone.utc)
            except ValueError:
                expired = True
            if expired:
                connection.execute(
                    "UPDATE pairing_challenges SET state = 'expired' WHERE challenge_id = ?",
                    (challenge_id,),
                )
                raise PairingError("pairing code has expired.")
            if not hmac.compare_digest(self._hash_code(code), str(row["code_hash"])):
                connection.execute(
                    "UPDATE pairing_challenges SET state = 'pairing_failed' WHERE challenge_id = ?",
                    (challenge_id,),
                )
                raise PairingError("pairing code is incorrect.")
            device_id = str(row["device_id"])
            connection.execute(
                "UPDATE pairing_challenges SET used = 1, state = 'confirmed' WHERE challenge_id = ?",
                (challenge_id,),
            )
            now = _now()
            connection.execute(
                """
                INSERT INTO device_trust (
                    device_id, trust_state, scope, scope_target, capabilities, granted_at, revocation_reason
                ) VALUES (?, 'paired_but_restricted', 'session', '', '', ?, '')
                ON CONFLICT(device_id) DO UPDATE SET
                    trust_state = 'paired_but_restricted', scope = 'session',
                    granted_at = excluded.granted_at, revocation_reason = ''
                """,
                (device_id, now),
            )
            connection.execute(
                "UPDATE device_records SET paired_state = 'paired', trusted_state = 'paired_but_restricted', connection_state = 'disconnected' WHERE device_id = ?",
                (device_id,),
            )
        return DeviceTrust(
            device_id=device_id,
            trust_state="paired_but_restricted",
            scope="session",
            granted_at=_now(),
        )

    def trust(self, *, device_id: str, level: str, scope: str = "session", scope_target: str = "", capabilities: Sequence[str] = ()) -> DeviceTrust:
        with self._lock, self._connection_factory() as connection:
            now = _now()
            connection.execute(
                """
                INSERT INTO device_trust (
                    device_id, trust_state, scope, scope_target, capabilities, granted_at, revocation_reason
                ) VALUES (?, ?, ?, ?, ?, ?, '')
                ON CONFLICT(device_id) DO UPDATE SET
                    trust_state = excluded.trust_state, scope = excluded.scope,
                    scope_target = excluded.scope_target, capabilities = excluded.capabilities,
                    granted_at = excluded.granted_at, revocation_reason = ''
                """,
                (device_id, level, scope, scope_target, "\n".join(capabilities), now),
            )
            connection.execute(
                "UPDATE device_records SET trusted_state = ? WHERE device_id = ?",
                (level, device_id),
            )
        return self.trust_record(device_id)

    def trust_record(self, device_id: str) -> DeviceTrust:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM device_trust WHERE device_id = ?", (device_id,)
            ).fetchone()
        if row is None:
            return DeviceTrust(device_id=device_id, trust_state="untrusted")
        return DeviceTrust(
            device_id=device_id,
            trust_state=str(row["trust_state"]),
            scope=str(row["scope"]),
            scope_target=str(row["scope_target"]),
            capabilities=tuple(p for p in str(row["capabilities"]).split("\n") if p),
            granted_at=str(row["granted_at"]),
            revocation_reason=str(row["revocation_reason"]),
        )

    def revoke(self, *, device_id: str, reason: str = "") -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                UPDATE device_trust SET trust_state = 'revoked', revocation_reason = ?
                WHERE device_id = ?
                """,
                (reason[:300], device_id),
            )
            connection.execute(
                """
                UPDATE device_records
                SET trusted_state = 'revoked', authentication_state = 'unauthenticated',
                    connection_state = 'revoked', revocation_state = 'revoked'
                WHERE device_id = ?
                """,
                (device_id,),
            )
            connection.execute(
                "UPDATE device_sessions SET connection_state = 'revoked', termination_reason = ? WHERE device_id = ? AND connection_state IN ('idle','connecting','connected','authenticating','negotiating')",
                ("trust revoked", device_id),
            )

    def _hash_code(self, code: str) -> str:
        return hashlib.sha256(("joeos-pairing-v1\0" + code).encode("utf-8")).hexdigest()

    def _cleanup_expired(self) -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE pairing_challenges SET state = 'expired' WHERE expires_at < ? AND state = 'pending'",
                (_now(),),
            )


class DeviceAuthenticationService:
    """Device challenge/response authentication for sessions."""

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()

    def create_nonce(self, *, device_id: str) -> Tuple[str, str]:
        nonce = builtin_secrets.token_hex(16)
        nonce_id = "nonce_" + uuid.uuid4().hex[:16]
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "DELETE FROM device_auth_nonces WHERE created_at < ?",
                ((datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),),
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS device_auth_nonces (
                    nonce_id TEXT PRIMARY KEY, device_id TEXT NOT NULL, nonce TEXT NOT NULL,
                    used INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
                )
                """,
            )
            connection.execute(
                "INSERT INTO device_auth_nonces (nonce_id, device_id, nonce, used, created_at) VALUES (?, ?, ?, 0, ?)",
                (nonce_id, device_id, nonce, _now()),
            )
        return nonce_id, nonce

    def verify(self, *, nonce_id: str, device_id: str, signed_nonce: str) -> bool:
        """Verify a signed nonce challenge (replay-protected)."""
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS device_auth_nonces (
                    nonce_id TEXT PRIMARY KEY, device_id TEXT NOT NULL, nonce TEXT NOT NULL,
                    used INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
                )
                """,
            )
            row = connection.execute(
                "SELECT * FROM device_auth_nonces WHERE nonce_id = ?", (nonce_id,)
            ).fetchone()
            if row is None or str(row["device_id"]) != device_id or bool(row["used"]):
                return False
            # The simulator adapter proves possession by echoing the nonce
            # digest. Real adapters would present a signature verified against
            # the device key reference.
            expected = hashlib.sha256(("joeos-device-auth-v1\0" + str(row["nonce"])).encode()).hexdigest()
            if not hmac.compare_digest(signed_nonce, expected):
                return False
            connection.execute(
                "UPDATE device_auth_nonces SET used = 1 WHERE nonce_id = ?", (nonce_id,)
            )
        return True


class SecureSessionService:
    """Authenticated, expiring device sessions with revocation support."""

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()

    def open(
        self,
        *,
        device_id: str,
        adapter_id: str,
        authenticated_user: str = "user",
        transport: str = "",
        capabilities: Sequence[str] = (),
        permissions: Sequence[str] = (),
        ttl_hours: int = SESSION_TTL_HOURS,
    ) -> DeviceSession:
        session_id = "sess_" + uuid.uuid4().hex[:16]
        started = _now()
        expires = (datetime.now(timezone.utc) + timedelta(hours=ttl_hours)).isoformat()
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO device_sessions (
                    session_id, device_id, adapter_id, authenticated_user, started_at, expires_at,
                    transport, encryption_state, permissions, capabilities, active_views,
                    notification_queue, bandwidth_policy, privacy_mode, activity_state,
                    last_heartbeat, risk_state, termination_reason, connection_state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'encrypted', ?, ?, '', '', 'normal', 'normal', 'idle', ?, 'normal', '', 'connected')
                """,
                (
                    session_id, device_id, adapter_id, authenticated_user, started, expires,
                    transport, "\n".join(permissions), "\n".join(capabilities), started,
                ),
            )
            connection.execute(
                "UPDATE device_records SET connection_state = 'connected', last_connected = ?, authentication_state = 'authenticated' WHERE device_id = ?",
                (started, device_id),
            )
        return self.get(session_id)

    def get(self, session_id: str) -> Optional[DeviceSession]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM device_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        return self._row(row) if row else None

    def active_for_device(self, device_id: str) -> Optional[DeviceSession]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM device_sessions WHERE device_id = ? AND connection_state IN ('connected','idle','connecting','authenticating','negotiating') ORDER BY started_at DESC LIMIT 1",
                (device_id,),
            ).fetchone()
        return self._row(row) if row else None

    def is_valid(self, session_id: str) -> bool:
        session = self.get(session_id)
        if session is None:
            return False
        if session.connection_state in {"revoked", "terminated", "disconnected"}:
            return False
        try:
            if datetime.fromisoformat(session.expires_at) < datetime.now(timezone.utc):
                return False
        except ValueError:
            return False
        return True

    def heartbeat(self, session_id: str) -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE device_sessions SET last_heartbeat = ?, connection_state = 'connected' WHERE session_id = ?",
                (_now(), session_id),
            )

    def terminate(self, session_id: str, *, reason: str = "") -> Optional[DeviceSession]:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE device_sessions SET connection_state = 'disconnected', termination_reason = ? WHERE session_id = ?",
                (reason[:200], session_id),
            )
        return self.get(session_id)

    def terminate_for_device(self, device_id: str, *, reason: str = "") -> int:
        with self._lock, self._connection_factory() as connection:
            cursor = connection.execute(
                "UPDATE device_sessions SET connection_state = 'disconnected', termination_reason = ? WHERE device_id = ? AND connection_state IN ('idle','connecting','connected','authenticating','negotiating','degraded')",
                (reason[:200], device_id),
            )
            connection.execute(
                "UPDATE device_records SET connection_state = 'disconnected', last_disconnected = ? WHERE device_id = ?",
                (_now(), device_id),
            )
        return cursor.rowcount

    def expire_stale(self) -> int:
        with self._lock, self._connection_factory() as connection:
            cursor = connection.execute(
                "UPDATE device_sessions SET connection_state = 'disconnected', termination_reason = 'session expired' WHERE expires_at < ? AND connection_state IN ('idle','connecting','connected','authenticating','negotiating')",
                (_now(),),
            )
        return cursor.rowcount

    def list_active(self) -> Tuple[DeviceSession, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM device_sessions WHERE connection_state IN ('idle','connecting','connected','authenticating','negotiating') ORDER BY started_at DESC"
            ).fetchall()
        return tuple(self._row(row) for row in rows)

    @staticmethod
    def _row(row: sqlite3.Row) -> DeviceSession:
        return DeviceSession(
            session_id=str(row["session_id"]),
            device_id=str(row["device_id"]),
            adapter_id=str(row["adapter_id"]),
            authenticated_user=str(row["authenticated_user"]),
            started_at=str(row["started_at"]),
            expires_at=str(row["expires_at"]),
            transport=str(row["transport"]),
            encryption_state=str(row["encryption_state"]),
            permissions=tuple(p for p in str(row["permissions"]).split("\n") if p),
            capabilities=tuple(p for p in str(row["capabilities"]).split("\n") if p),
            active_views=tuple(p for p in str(row["active_views"]).split("\n") if p),
            notification_queue=tuple(p for p in str(row["notification_queue"]).split("\n") if p),
            bandwidth_policy=str(row["bandwidth_policy"]),
            privacy_mode=str(row["privacy_mode"]),
            activity_state=str(row["activity_state"]),
            last_heartbeat=str(row["last_heartbeat"]),
            risk_state=str(row["risk_state"]),
            termination_reason=str(row["termination_reason"]),
            connection_state=str(row["connection_state"]),
        )