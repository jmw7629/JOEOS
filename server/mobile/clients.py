"""Mobile Client Registry, Host Registry, and discovery for the JoeOS Mobile
Companion Platform.

Mobile clients have stable installation identity (never user-editable names).
Hosts are multiple and never hard-coded; host identity is verified, and
discovery is explicit and treats names/addresses/metadata as untrusted.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .models import (
    DiscoveryResult,
    HostRecord,
    MobileClientRecord,
    REMOTE_API_VERSION,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MobileError(RuntimeError):
    pass


class MobileClientRegistry:
    """Authoritative registry of paired mobile clients."""

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()

    def register(
        self,
        *,
        client_id: str,
        platform: str = "ios",
        os_version: str = "",
        app_version: str = "",
        build_number: str = "",
        device_model_category: str = "",
        installation_identity: str = "",
        crypto_identity_reference: str = "",
    ) -> MobileClientRecord:
        now = _now()
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO mobile_clients (
                    client_id, platform, os_version, app_version, build_number,
                    device_model_category, installation_identity, crypto_identity_reference,
                    pairing_state, trust_state, authentication_state, privacy_policy,
                    notification_policy, push_registration_state, background_capability_state,
                    health, revocation_state, removal_state, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'unpaired', 'untrusted', 'unauthenticated',
                          'normal', 'normal', 'unregistered', 'unknown', 'unknown', 'active', 'active', ?)
                ON CONFLICT(client_id) DO UPDATE SET
                    os_version = excluded.os_version, app_version = excluded.app_version,
                    build_number = excluded.build_number, installation_identity = excluded.installation_identity
                """,
                (
                    client_id, platform, os_version, app_version, build_number,
                    device_model_category, installation_identity, crypto_identity_reference, now,
                ),
            )
        return self.get(client_id)

    def get(self, client_id: str) -> Optional[MobileClientRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM mobile_clients WHERE client_id = ?", (client_id,)
            ).fetchone()
        return self._row(row) if row else None

    def list(self, *, include_removed: bool = False) -> Tuple[MobileClientRecord, ...]:
        clause = "" if include_removed else " WHERE removal_state = 'active'"
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM mobile_clients" + clause + " ORDER BY created_at DESC"
            ).fetchall()
        return tuple(self._row(row) for row in rows)

    def update(self, client_id: str, **fields) -> MobileClientRecord:
        allowed = {
            "paired_host", "paired_user", "pairing_state", "trust_state",
            "authentication_state", "privacy_policy", "notification_policy",
            "last_connection", "last_sync", "active_session", "connection_state",
            "push_registration_state", "background_capability_state", "health",
            "revocation_state", "removal_state", "permission_grants", "project_grants",
        }
        setters = [field for field in fields if field in allowed]
        if not setters:
            return self.get(client_id)
        assignments = ", ".join("%s = ?" % field for field in setters)
        values: List[object] = []
        for field in setters:
            value = fields[field]
            if isinstance(value, (list, tuple)):
                value = "\n".join(value)
            elif isinstance(value, bool):
                value = 1 if value else 0
            values.append(value)
        values.append(client_id)
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE mobile_clients SET %s WHERE client_id = ?" % assignments, values
            )
        return self.get(client_id)

    def grant_permission(self, *, client_id: str, permission: str, scope: str = "session", scope_target: str = "") -> None:
        from .models import MOBILE_PERMISSIONS
        if permission not in MOBILE_PERMISSIONS:
            raise MobileError("unknown mobile permission %r." % permission)
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO mobile_permission_grants (client_id, permission, scope, scope_target, granted_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(client_id, permission, scope_target) DO UPDATE SET scope = excluded.scope, granted_at = excluded.granted_at
                """,
                (client_id, permission, scope, scope_target, _now()),
            )

    def revoke_permission(self, *, client_id: str, permission: str, scope_target: str = "") -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "DELETE FROM mobile_permission_grants WHERE client_id = ? AND permission = ? AND scope_target = ?",
                (client_id, permission, scope_target),
            )

    def permission_granted(self, *, client_id: str, permission: str, scope_target: str = "") -> bool:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT scope FROM mobile_permission_grants WHERE client_id = ? AND permission = ? AND scope_target = ?",
                (client_id, permission, scope_target),
            ).fetchone()
        if row is None:
            return False
        return str(row["scope"]) in {"session", "device", "persistent", "project"}

    def grants_for(self, client_id: str) -> Tuple[dict, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM mobile_permission_grants WHERE client_id = ? ORDER BY permission",
                (client_id,),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def mark_revoked(self, client_id: str, *, reason: str = "") -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                UPDATE mobile_clients
                SET revocation_state = 'revoked', trust_state = 'revoked',
                    authentication_state = 'unauthenticated', pairing_state = 'paired',
                    push_registration_state = 'disabled'
                WHERE client_id = ?
                """,
                (client_id,),
            )
            connection.execute(
                "UPDATE mobile_sessions SET connection_state = 'revoked', termination_reason = ? WHERE client_id = ? AND connection_state IN ('active','reconnecting','offline')",
                (reason[:200] or "client revoked", client_id),
            )
            connection.execute(
                "UPDATE mobile_push_registrations SET revocation_state = 'revoked' WHERE client_id = ?",
                (client_id,),
            )

    def mark_lost(self, client_id: str) -> None:
        self.mark_revoked(client_id, reason="device marked lost")

    @staticmethod
    def _row(row: sqlite3.Row) -> MobileClientRecord:
        return MobileClientRecord(
            client_id=str(row["client_id"]),
            platform=str(row["platform"]),
            os_version=str(row["os_version"]),
            app_version=str(row["app_version"]),
            build_number=str(row["build_number"]),
            device_model_category=str(row["device_model_category"]),
            installation_identity=str(row["installation_identity"]),
            paired_host=str(row["paired_host"]),
            paired_user=str(row["paired_user"]),
            crypto_identity_reference=str(row["crypto_identity_reference"]),
            pairing_state=str(row["pairing_state"]),
            trust_state=str(row["trust_state"]),
            authentication_state=str(row["authentication_state"]),
            permission_grants=tuple(p for p in str(row["permission_grants"]).split("\n") if p),
            project_grants=tuple(p for p in str(row["project_grants"]).split("\n") if p),
            privacy_policy=str(row["privacy_policy"]),
            notification_policy=str(row["notification_policy"]),
            last_connection=str(row["last_connection"]),
            last_sync=str(row["last_sync"]),
            active_session=str(row["active_session"]),
            push_registration_state=str(row["push_registration_state"]),
            background_capability_state=str(row["background_capability_state"]),
            health=str(row["health"]),
            revocation_state=str(row["revocation_state"]),
            removal_state=str(row["removal_state"]),
            created_at=str(row["created_at"]),
        )


class HostRegistry:
    """Authoritative registry of JoeOS hosts a mobile client can connect to."""

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection], *, server_version: str = "2.0.0", instance_identity: str = "") -> None:
        self._connection_factory = connection_factory
        self._server_version = server_version
        self._instance_identity = instance_identity or ("joeos-" + uuid.uuid4().hex[:10])
        self._lock = threading.RLock()

    def ensure_self(self, *, display_name: str = "This JoeOS", endpoint: str = "") -> HostRecord:
        """Register the running JoeOS instance as the primary trusted host."""
        host_id = "host_" + self._instance_identity[:10]
        with self._lock, self._connection_factory() as connection:
            existing = connection.execute(
                "SELECT * FROM mobile_hosts WHERE host_id = ?", (host_id,)
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO mobile_hosts (
                        host_id, display_name, instance_identity, installation_identity,
                        connection_methods, local_endpoint, api_version, supported_capabilities,
                        paired_state, trusted_state, reachability, health, current_user,
                        compatibility_state, revocation_state
                    ) VALUES (?, ?, ?, ?, 'local_network,secure_overlay', ?, ?, 'command_center,mobile',
                              'paired', 'trusted', 'unknown', 'healthy', 'user', 'fully_compatible', 'active')
                    """,
                    (host_id, display_name, self._instance_identity, self._instance_identity, endpoint, REMOTE_API_VERSION),
                )
            else:
                connection.execute(
                    "UPDATE mobile_hosts SET display_name = ?, local_endpoint = ?, compatibility_state = 'fully_compatible' WHERE host_id = ?",
                    (display_name, endpoint, host_id),
                )
        return self.get(host_id)

    def register(
        self,
        *,
        display_name: str,
        instance_identity: str,
        connection_methods: Sequence[str] = ("local_network",),
        local_endpoint: str = "",
        secure_overlay_endpoint: str = "",
        api_version: int = REMOTE_API_VERSION,
    ) -> HostRecord:
        host_id = "host_" + uuid.uuid4().hex[:10]
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO mobile_hosts (
                    host_id, display_name, instance_identity, installation_identity,
                    connection_methods, local_endpoint, secure_overlay_endpoint, api_version,
                    supported_capabilities, paired_state, trusted_state, reachability, health,
                    current_user, compatibility_state, revocation_state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', 'unpaired', 'untrusted', 'unknown', 'unknown', 'user', 'unknown', 'active')
                """,
                (
                    host_id, display_name, instance_identity, instance_identity,
                    "\n".join(connection_methods), local_endpoint, secure_overlay_endpoint, api_version,
                ),
            )
        return self.get(host_id)

    def get(self, host_id: str) -> Optional[HostRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM mobile_hosts WHERE host_id = ?", (host_id,)
            ).fetchone()
        return self._row(row) if row else None

    def list(self) -> Tuple[HostRecord, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM mobile_hosts WHERE revocation_state = 'active' ORDER BY display_name"
            ).fetchall()
        return tuple(self._row(row) for row in rows)

    def update(self, host_id: str, **fields) -> HostRecord:
        allowed = {
            "paired_state", "trusted_state", "last_connection", "last_authentication",
            "reachability", "latency_ms", "health", "compatibility_state", "revocation_state",
            "certificate_fingerprint",
        }
        setters = [field for field in fields if field in allowed]
        if not setters:
            return self.get(host_id)
        assignments = ", ".join("%s = ?" % field for field in setters)
        values: List[object] = [fields[field] for field in setters]
        values.append(host_id)
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE mobile_hosts SET %s WHERE host_id = ?" % assignments, values
            )
        return self.get(host_id)

    def discover(self, *, entries: Sequence[dict] = ()) -> Tuple[DiscoveryResult, ...]:
        """Explicit discovery; names/addresses are untrusted until pairing."""
        results = []
        for entry in entries:
            results.append(
                DiscoveryResult(
                    host_id=str(entry.get("host_id") or ("host_" + uuid.uuid4().hex[:10])),
                    display_name=str(entry.get("display_name") or "Unknown JoeOS host"),
                    instance_identity=str(entry.get("instance_identity") or ""),
                    connection_path=str(entry.get("connection_path") or ""),
                    tls_state=str(entry.get("tls_state") or "unknown"),
                    compatibility=str(entry.get("compatibility") or "unknown"),
                    pairing_required=True,
                    network_classification=str(entry.get("network_classification") or "local"),
                )
            )
        return tuple(results)

    @staticmethod
    def _row(row: sqlite3.Row) -> HostRecord:
        return HostRecord(
            host_id=str(row["host_id"]),
            display_name=str(row["display_name"]),
            instance_identity=str(row["instance_identity"]),
            installation_identity=str(row["installation_identity"]),
            connection_methods=tuple(p for p in str(row["connection_methods"]).split("\n") if p),
            local_endpoint=str(row["local_endpoint"]),
            secure_overlay_endpoint=str(row["secure_overlay_endpoint"]),
            relay_endpoint=str(row["relay_endpoint"]),
            tls_identity=str(row["tls_identity"]),
            certificate_fingerprint=str(row["certificate_fingerprint"]),
            api_version=int(row["api_version"]),
            supported_capabilities=tuple(p for p in str(row["supported_capabilities"]).split("\n") if p),
            paired_state=str(row["paired_state"]),
            trusted_state=str(row["trusted_state"]),
            last_connection=str(row["last_connection"]),
            last_authentication=str(row["last_authentication"]),
            reachability=str(row["reachability"]),
            latency_ms=row["latency_ms"],
            health=str(row["health"]),
            current_user=str(row["current_user"]),
            compatibility_state=str(row["compatibility_state"]),
            revocation_state=str(row["revocation_state"]),
        )