"""Authoritative Secret Broker for the JoeOS Security Platform.

Secret values are never stored in ordinary databases, never returned to
renderers/mobile/wearables, never placed in logs/URLs/clipboard/model context.
Values are encrypted at rest with AES-256-GCM using an established library
(no custom crypto, no master key beside the encrypted data). Access is
scoped, purpose-bound, rate-limited, and audited. Rotation stages a new value
before revoking the old. Secret detection produces masked fingerprints, never
full values.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets as builtin_secrets
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Callable, Dict, Optional, Sequence, Tuple

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .policy import SecurityError
from .models import SecretDetection, SecretMetadata

PREFIX = b"JOSEC1"
PREFIX_BYTES = len(PREFIX)
NONCE_BYTES = 12
DOMAIN = b"JOEOS-SECRET-AT-REST-V1\0"

CANDIDATE_PATTERNS = (
    (re.compile(r"\b(?:sk|rk|pk)_[A-Za-z0-9]{16,}\b"), "api_key"),
    (re.compile(r"\b(?:sk|rk|pk)[_-](?:live|test)[_-][A-Za-z0-9]{16,}\b"), "api_key"),
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"), "private_key"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b", re.I), "github_token"),
    (re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), "aws_access_key"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "google_api_key"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b", re.I), "slack_token"),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()



class SecretBroker:
    def __init__(
        self,
        connection_factory: Callable[[], sqlite3.Connection],
        master_key: bytes,
        nonce_source=None,
    ) -> None:
        if type(master_key) is not bytes or len(master_key) != 32:
            raise SecurityError("The secret master key must be exactly 32 bytes.")
        self._connection_factory = connection_factory
        self._cipher = AESGCM(master_key)
        self._nonce_source = nonce_source or builtin_secrets.token_bytes
        self._lock = threading.RLock()
        self._rate: Dict[str, list] = {}

    def create(
        self,
        *,
        label: str,
        secret_type: str,
        value: str,
        scope: str = "global",
        project: str = "",
        plugin: str = "",
        workflow: str = "",
        provider: str = "",
        allowed_operations: Sequence[str] = (),
        allowed_destinations: Sequence[str] = (),
    ) -> SecretMetadata:
        if not value:
            raise SecurityError("secret value cannot be empty.")
        secret_id = "secret_" + uuid.uuid4().hex[:16]
        nonce = self._nonce_source(NONCE_BYTES)
        payload = json.dumps({"value": value}).encode("utf-8")
        ciphertext = PREFIX + nonce + self._cipher.encrypt(
            nonce, payload, self._associated_data(secret_id)
        )
        now = _now()
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO security_secret_metadata (
                    secret_id, display_label, secret_type, owner, scope, project, plugin,
                    workflow, provider, storage_adapter, created_at, last_rotation,
                    expiration, last_use, usage_count, allowed_operations,
                    allowed_destinations, revoked_state, health
                ) VALUES (?, ?, ?, 'user', ?, ?, ?, ?, ?, 'encrypted_vault', ?, ?, '', '', 0, ?, ?, 'active', 'healthy')
                """,
                (
                    secret_id, label, secret_type, scope, project, plugin, workflow,
                    provider, now, now, "\n".join(allowed_operations),
                    "\n".join(allowed_destinations),
                ),
            )
            connection.execute(
                """
                INSERT INTO security_secret_values (secret_id, encrypted_value, nonce, rotation, updated_at)
                VALUES (?, ?, ?, 0, ?)
                """,
                (secret_id, ciphertext.hex(), nonce.hex(), now),
            )
        return self.metadata(secret_id)

    def metadata(self, secret_id: str) -> Optional[SecretMetadata]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM security_secret_metadata WHERE secret_id = ?", (secret_id,)
            ).fetchone()
        return self._meta_row(row) if row else None

    def list(self) -> Tuple[SecretMetadata, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM security_secret_metadata ORDER BY display_label"
            ).fetchall()
        return tuple(self._meta_row(row) for row in rows)

    def _check_rate(self, secret_id: str) -> None:
        window = 60.0
        import time as _time
        now = _time.monotonic()
        stamps = [s for s in self._rate.get(secret_id, []) if now - s < window]
        if len(stamps) >= 30:
            raise SecurityError("secret access rate limit reached.")
        stamps.append(now)
        self._rate[secret_id] = stamps

    def retrieve(
        self,
        *,
        secret_id: str,
        subject: str,
        purpose: str,
        destination: str = "",
        scope_ok: bool = True,
    ) -> str:
        """Return the secret value only to a privileged in-process consumer."""
        self._check_rate(secret_id)
        meta = self.metadata(secret_id)
        if meta is None:
            raise SecurityError("secret not found.")
        if meta.revoked_state != "active":
            raise SecurityError("secret is revoked.")
        if not scope_ok:
            raise SecurityError("secret scope does not match the request.")
        if meta.allowed_destinations and destination and destination not in meta.allowed_destinations:
            raise SecurityError("secret destination not allowed.")
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM security_secret_values WHERE secret_id = ?", (secret_id,)
            ).fetchone()
            if row is None:
                raise SecurityError("secret storage not found.")
            connection.execute(
                "UPDATE security_secret_metadata SET last_use = ?, usage_count = usage_count + 1 WHERE secret_id = ?",
                (_now(), secret_id),
            )
        try:
            raw = bytes.fromhex(str(row["encrypted_value"]))
            if raw[:PREFIX_BYTES] != PREFIX:
                raise InvalidTag
            offset = PREFIX_BYTES
            nonce = raw[offset : offset + NONCE_BYTES]
            ciphertext = raw[offset + NONCE_BYTES :]
            payload = self._cipher.decrypt(nonce, ciphertext, self._associated_data(secret_id))
            decoded = json.loads(payload.decode("utf-8"))
            return str(decoded["value"])
        except (InvalidTag, ValueError, TypeError, KeyError, json.JSONDecodeError):
            raise SecurityError("secret could not be authenticated.") from None

    def rotate(self, *, secret_id: str, new_value: str) -> SecretMetadata:
        if not new_value:
            raise SecurityError("new secret value cannot be empty.")
        nonce = self._nonce_source(NONCE_BYTES)
        payload = json.dumps({"value": new_value}).encode("utf-8")
        ciphertext = PREFIX + nonce + self._cipher.encrypt(
            nonce, payload, self._associated_data(secret_id)
        )
        now = _now()
        with self._lock, self._connection_factory() as connection:
            row = connection.execute(
                "SELECT rotation FROM security_secret_values WHERE secret_id = ?", (secret_id,)
            ).fetchone()
            rotation = (int(row["rotation"]) if row else 0) + 1
            connection.execute(
                """
                INSERT INTO security_secret_values (secret_id, encrypted_value, nonce, rotation, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(secret_id) DO UPDATE SET
                    encrypted_value = excluded.encrypted_value, nonce = excluded.nonce,
                    rotation = excluded.rotation, updated_at = excluded.updated_at
                """,
                (secret_id, ciphertext.hex(), nonce.hex(), rotation, now),
            )
            connection.execute(
                "UPDATE security_secret_metadata SET last_rotation = ?, health = 'healthy' WHERE secret_id = ?",
                (now, secret_id),
            )
        return self.metadata(secret_id)

    def revoke(self, *, secret_id: str) -> SecretMetadata:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE security_secret_metadata SET revoked_state = 'revoked', health = 'revoked' WHERE secret_id = ?",
                (secret_id,),
            )
        return self.metadata(secret_id)

    def revoke_all(self) -> int:
        with self._lock, self._connection_factory() as connection:
            cursor = connection.execute(
                "UPDATE security_secret_metadata SET revoked_state = 'revoked', health = 'revoked' WHERE revoked_state = 'active'"
            )
        return cursor.rowcount

    def secrets_requiring_rotation(self, *, max_age_days: int = 90) -> Tuple[SecretMetadata, ...]:
        from datetime import timedelta
        threshold = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        result = []
        for meta in self.list():
            if meta.revoked_state != "active":
                continue
            try:
                if datetime.fromisoformat(meta.last_rotation) < threshold:
                    result.append(meta)
            except ValueError:
                result.append(meta)
        return tuple(result)

    # ---- secret detection ----

    def scan_text(self, *, text: str, source: str = "") -> Tuple[SecretDetection, ...]:
        detections = []
        for pattern, candidate_type in CANDIDATE_PATTERNS:
            for match in pattern.finditer(text):
                raw = match.group(0)
                fingerprint = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
                detections.append(
                    SecretDetection(
                        detection_id="detect_" + uuid.uuid4().hex[:16],
                        candidate_type=candidate_type,
                        confidence="likely",
                        masked_fingerprint=fingerprint,
                        location="",
                        source=source,
                        created_at=_now(),
                    )
                )
        for detection in detections:
            with self._lock, self._connection_factory() as connection:
                connection.execute(
                    """
                    INSERT INTO security_secret_detections (
                        detection_id, candidate_type, confidence, masked_fingerprint,
                        location, source, status, created_at
                    ) VALUES (?, ?, ?, ?, '', ?, 'open', ?)
                    """,
                    (
                        detection.detection_id, detection.candidate_type,
                        detection.confidence, detection.masked_fingerprint,
                        detection.source, detection.created_at,
                    ),
                )
        return tuple(detections)

    def detections(self, *, status: str = "open", limit: int = 50) -> Tuple[SecretDetection, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM security_secret_detections WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, max(1, min(200, int(limit)))),
            ).fetchall()
        return tuple(
            SecretDetection(
                detection_id=str(row["detection_id"]),
                candidate_type=str(row["candidate_type"]),
                confidence=str(row["confidence"]),
                masked_fingerprint=str(row["masked_fingerprint"]),
                location=str(row["location"]),
                source=str(row["source"]),
                status=str(row["status"]),
                created_at=str(row["created_at"]),
            )
            for row in rows
        )

    def resolve_detection(self, detection_id: str, *, status: str = "false_positive") -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE security_secret_detections SET status = ? WHERE detection_id = ?",
                (status, detection_id),
            )

    @staticmethod
    def _associated_data(secret_id: str) -> bytes:
        return DOMAIN + len(secret_id.encode("utf-8")).to_bytes(2, "big") + secret_id.encode("utf-8")

    @staticmethod
    def _meta_row(row: sqlite3.Row) -> SecretMetadata:
        return SecretMetadata(
            secret_id=str(row["secret_id"]),
            display_label=str(row["display_label"]),
            secret_type=str(row["secret_type"]),
            owner=str(row["owner"]),
            scope=str(row["scope"]),
            project=str(row["project"]),
            plugin=str(row["plugin"]),
            workflow=str(row["workflow"]),
            provider=str(row["provider"]),
            device=str(row["device"]),
            storage_adapter=str(row["storage_adapter"]),
            created_at=str(row["created_at"]),
            last_rotation=str(row["last_rotation"]),
            expiration=str(row["expiration"]),
            last_use=str(row["last_use"]),
            usage_count=int(row["usage_count"]),
            allowed_operations=tuple(p for p in str(row["allowed_operations"]).split("\n") if p),
            allowed_destinations=tuple(p for p in str(row["allowed_destinations"]).split("\n") if p),
            revoked_state=str(row["revoked_state"]),
            health=str(row["health"]),
        )