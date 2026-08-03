"""Extension Secret Broker for the JoeOS Plugin Platform.

Extensions request *named secret references*, never raw enumerations of the
credential store. Values are protected at rest with AES-256-GCM keyed by the
JoeOS identity master key (the same protection used by device identity), and
are never written to logs or returned through the renderer in plain display.
One plugin can never read another plugin's secret.
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

from .permissions import PermissionManager

SECRET_BROKER_DOMAIN = b"JOEOS-PLUGIN-SECRET-AT-REST-V1\0"
NONCE_BYTES = 12
PREFIX = b"JPSK1"
PREFIX_BYTES = len(PREFIX)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SecretBrokerError(RuntimeError):
    pass


class ExtensionSecretBroker:
    """Stores, rotates, revokes, and releases plugin secret references."""

    def __init__(
        self,
        connection_factory: Callable[[], sqlite3.Connection],
        master_key: bytes,
        permissions: PermissionManager,
        lifecycle_probe=None,
        nonce_source=None,
    ) -> None:
        if type(master_key) is not bytes or len(master_key) != 32:
            raise SecretBrokerError("The plugin secret master key must be 32 bytes.")
        self._connection_factory = connection_factory
        self._permissions = permissions
        self._lifecycle_probe = lifecycle_probe or (lambda plugin_id: "active")
        self._cipher = AESGCM(master_key)
        self._nonce_source = nonce_source or builtin_secrets.token_bytes
        self._lock = threading.RLock()

    def set(
        self,
        *,
        plugin_id: str,
        name: str,
        value: str,
        scope: str = "global",
    ) -> dict:
        self._check_access(plugin_id)
        if not name or len(name) > 80:
            raise SecretBrokerError("invalid secret name.")
        ref_id = str(uuid.uuid4())
        nonce = self._nonce_source(NONCE_BYTES)
        payload = json.dumps({"plugin_id": plugin_id, "value": value}).encode("utf-8")
        ciphertext = PREFIX + nonce + self._cipher.encrypt(
            nonce, payload, self._associated_data(plugin_id, name)
        )
        now = _now()
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "DELETE FROM plugin_secret_refs WHERE plugin_id = ? AND name = ? AND scope = ?",
                (plugin_id, name, scope),
            )
            connection.execute(
                """
                INSERT INTO plugin_secret_refs (
                    ref_id, plugin_id, name, scope, encrypted_value, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (ref_id, plugin_id, name, scope, ciphertext.hex(), now, now),
            )
        return {"reference": ref_id, "name": name, "scope": scope}

    def retrieve(
        self,
        *,
        plugin_id: str,
        ref_id: str,
    ) -> str:
        self._check_access(plugin_id)
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM plugin_secret_refs WHERE ref_id = ?",
                (ref_id,),
            ).fetchone()
            peers = connection.execute(
                "SELECT plugin_id FROM plugin_secret_refs WHERE ref_id = ?", (ref_id,)
            ).fetchall()
        if not row or not peers:
            raise SecretBrokerError("secret reference not found.")
        owner = str(peers[0]["plugin_id"])
        if owner != plugin_id:
            raise SecretBrokerError("secret reference belongs to another plugin.")
        try:
            raw = bytes.fromhex(str(row["encrypted_value"]))
            if raw[:PREFIX_BYTES] != PREFIX:
                raise InvalidTag
            offset = PREFIX_BYTES
            nonce = raw[offset : offset + NONCE_BYTES]
            ciphertext = raw[offset + NONCE_BYTES :]
            payload = self._cipher.decrypt(
                nonce, ciphertext, self._associated_data(owner, str(row["name"]))
            )
            decoded = json.loads(payload.decode("utf-8"))
            return str(decoded["value"])
        except (InvalidTag, ValueError, TypeError, KeyError, json.JSONDecodeError):
            raise SecretBrokerError("secret could not be authenticated.") from None

    def revoke(self, *, plugin_id: str, name: str, scope: str = "global") -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "DELETE FROM plugin_secret_refs WHERE plugin_id = ? AND name = ? AND scope = ?",
                (plugin_id, name, scope),
            )

    def references_for(self, *, plugin_id: str) -> tuple:
        """List non-secret reference metadata (never values)."""
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT ref_id, name, scope, created_at FROM plugin_secret_refs WHERE plugin_id = ?",
                (plugin_id,),
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

    def _check_access(self, plugin_id: str) -> None:
        if self._lifecycle_probe(plugin_id) != "active":
            raise PermissionError("plugin is not active.")
        if not self._permissions.granted(
            plugin_id=plugin_id, permission="secrets.request_named_extension_secret"
        ):
            raise PermissionError("plugin lacks the secret permission.")

    @staticmethod
    def _associated_data(plugin_id: str, name: str) -> bytes:
        return (
            SECRET_BROKER_DOMAIN
            + len(plugin_id.encode("utf-8")).to_bytes(2, "big")
            + plugin_id.encode("utf-8")
            + len(name.encode("utf-8")).to_bytes(2, "big")
            + name.encode("utf-8")
        )


class PermissionError(RuntimeError):
    pass


class SecretProviderError(RuntimeError):
    pass