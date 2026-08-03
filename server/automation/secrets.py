"""Workflow Secret Broker for the JoeOS Automation Platform.

Workflows reference named secret identifiers; values are never stored in
workflow definitions, run history, traces, or logs. Secrets are protected at
rest with AES-256-GCM and delivered only to authorized privileged operations.
"""

from __future__ import annotations

import json
import secrets as builtin_secrets
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Callable, Dict, Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

DOMAIN = b"JOEOS-WORKFLOW-SECRET-AT-REST-V1\0"
NONCE_BYTES = 12
PREFIX = b"JWSK1"
PREFIX_BYTES = len(PREFIX)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SecretBrokerError(RuntimeError):
    pass


class WorkflowSecretBroker:
    def __init__(
        self,
        connection_factory: Callable[[], sqlite3.Connection],
        master_key: bytes,
        nonce_source=None,
    ) -> None:
        if type(master_key) is not bytes or len(master_key) != 32:
            raise SecretBrokerError("The workflow secret master key must be 32 bytes.")
        self._connection_factory = connection_factory
        self._cipher = AESGCM(master_key)
        self._nonce_source = nonce_source or builtin_secrets.token_bytes
        self._lock = threading.RLock()

    def _ensure_table(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_secrets (
                ref_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                scope TEXT NOT NULL DEFAULT 'global',
                encrypted_value TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

    def set(self, *, name: str, value: str, scope: str = "global") -> dict:
        if not name or len(name) > 80:
            raise SecretBrokerError("invalid secret name.")
        nonce = self._nonce_source(NONCE_BYTES)
        payload = json.dumps({"name": name, "value": value}).encode("utf-8")
        ciphertext = PREFIX + nonce + self._cipher.encrypt(
            nonce, payload, self._associated_data(name, scope)
        )
        ref_id = str(uuid.uuid4())
        now = _now()
        with self._lock, self._connection_factory() as connection:
            self._ensure_table(connection)
            connection.execute(
                "DELETE FROM workflow_secrets WHERE name = ? AND scope = ?",
                (name, scope),
            )
            connection.execute(
                """
                INSERT INTO workflow_secrets (ref_id, name, scope, encrypted_value, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (ref_id, name, scope, ciphertext.hex(), now, now),
            )
        return {"reference_id": ref_id, "name": name, "scope": scope}

    def retrieve(self, *, ref_id: str) -> str:
        with self._connection_factory() as connection:
            self._ensure_table(connection)
            row = connection.execute(
                "SELECT * FROM workflow_secrets WHERE ref_id = ?", (ref_id,)
            ).fetchone()
        if not row:
            raise SecretBrokerError("secret reference not found.")
        try:
            raw = bytes.fromhex(str(row["encrypted_value"]))
            if raw[:PREFIX_BYTES] != PREFIX:
                raise InvalidTag
            offset = PREFIX_BYTES
            nonce = raw[offset : offset + NONCE_BYTES]
            ciphertext = raw[offset + NONCE_BYTES :]
            payload = self._cipher.decrypt(
                nonce, ciphertext, self._associated_data(str(row["name"]), str(row["scope"]))
            )
            decoded = json.loads(payload.decode("utf-8"))
            return str(decoded["value"])
        except (InvalidTag, ValueError, TypeError, KeyError, json.JSONDecodeError):
            raise SecretBrokerError("secret could not be authenticated.") from None

    def revoke(self, *, name: str, scope: str = "global") -> None:
        with self._lock, self._connection_factory() as connection:
            self._ensure_table(connection)
            connection.execute(
                "DELETE FROM workflow_secrets WHERE name = ? AND scope = ?", (name, scope)
            )

    def references(self) -> tuple:
        with self._connection_factory() as connection:
            self._ensure_table(connection)
            rows = connection.execute(
                "SELECT ref_id, name, scope, created_at FROM workflow_secrets ORDER BY name"
            ).fetchall()
        return tuple(
            {
                "reference_id": str(row["ref_id"]),
                "name": str(row["name"]),
                "scope": str(row["scope"]),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        )

    def availability(self, *, workflow_id: str, required: tuple) -> Dict[str, bool]:
        """Report whether each required secret reference exists (no values)."""
        with self._connection_factory() as connection:
            self._ensure_table(connection)
            rows = connection.execute(
                "SELECT name, scope FROM workflow_secrets"
            ).fetchall()
        present = {(str(row["name"]), str(row["scope"])) for row in rows}
        return {
            secret.name: (secret.name, secret.scope) in present for secret in required
        }

    @staticmethod
    def _associated_data(name: str, scope: str) -> bytes:
        return (
            DOMAIN
            + len(name.encode("utf-8")).to_bytes(2, "big")
            + name.encode("utf-8")
            + len(scope.encode("utf-8")).to_bytes(2, "big")
            + scope.encode("utf-8")
        )