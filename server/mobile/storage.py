"""Versioned SQLite storage for the JoeOS Mobile Companion and Secure Remote
Operations Platform.

Stores mobile clients, hosts, pairing sessions, mobile sessions, permissions,
offline actions, handoffs, deep-link references, and push registrations.
Credentials, private keys, and push tokens are never stored here in plain
text; only secure references are kept.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
from pathlib import Path
from typing import Optional

STORAGE_VERSION = 1
SCHEMA_VERSION_ROW = ("mobile_schema_version", "1")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS mobile_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS mobile_clients (
    client_id TEXT PRIMARY KEY,
    platform TEXT NOT NULL DEFAULT 'ios',
    os_version TEXT NOT NULL DEFAULT '',
    app_version TEXT NOT NULL DEFAULT '',
    build_number TEXT NOT NULL DEFAULT '',
    device_model_category TEXT NOT NULL DEFAULT '',
    installation_identity TEXT NOT NULL DEFAULT '',
    paired_host TEXT NOT NULL DEFAULT '',
    paired_user TEXT NOT NULL DEFAULT 'user',
    crypto_identity_reference TEXT NOT NULL DEFAULT '',
    pairing_state TEXT NOT NULL DEFAULT 'unconfigured',
    trust_state TEXT NOT NULL DEFAULT 'untrusted',
    authentication_state TEXT NOT NULL DEFAULT 'unauthenticated',
    permission_grants TEXT NOT NULL DEFAULT '',
    project_grants TEXT NOT NULL DEFAULT '',
    privacy_policy TEXT NOT NULL DEFAULT 'normal',
    notification_policy TEXT NOT NULL DEFAULT 'normal',
    last_connection TEXT NOT NULL DEFAULT '',
    last_sync TEXT NOT NULL DEFAULT '',
    active_session TEXT NOT NULL DEFAULT '',
    connection_state TEXT NOT NULL DEFAULT 'disconnected',
    push_registration_state TEXT NOT NULL DEFAULT 'unregistered',
    background_capability_state TEXT NOT NULL DEFAULT 'unknown',
    health TEXT NOT NULL DEFAULT 'unknown',
    revocation_state TEXT NOT NULL DEFAULT 'active',
    removal_state TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mobile_clients_revoked ON mobile_clients(revocation_state);

CREATE TABLE IF NOT EXISTS mobile_hosts (
    host_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    instance_identity TEXT NOT NULL DEFAULT '',
    installation_identity TEXT NOT NULL DEFAULT '',
    connection_methods TEXT NOT NULL DEFAULT 'local_network',
    local_endpoint TEXT NOT NULL DEFAULT '',
    secure_overlay_endpoint TEXT NOT NULL DEFAULT '',
    relay_endpoint TEXT NOT NULL DEFAULT '',
    tls_identity TEXT NOT NULL DEFAULT '',
    certificate_fingerprint TEXT NOT NULL DEFAULT '',
    api_version INTEGER NOT NULL DEFAULT 1,
    supported_capabilities TEXT NOT NULL DEFAULT '',
    paired_state TEXT NOT NULL DEFAULT 'unpaired',
    trusted_state TEXT NOT NULL DEFAULT 'untrusted',
    last_connection TEXT NOT NULL DEFAULT '',
    last_authentication TEXT NOT NULL DEFAULT '',
    reachability TEXT NOT NULL DEFAULT 'unknown',
    latency_ms INTEGER,
    health TEXT NOT NULL DEFAULT 'unknown',
    current_user TEXT NOT NULL DEFAULT 'user',
    compatibility_state TEXT NOT NULL DEFAULT 'unknown',
    revocation_state TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS mobile_pairing_sessions (
    session_id TEXT PRIMARY KEY,
    host_id TEXT NOT NULL,
    client_id TEXT NOT NULL DEFAULT '',
    method TEXT NOT NULL DEFAULT 'one_time_code',
    code_reference TEXT NOT NULL DEFAULT '',
    code_hash TEXT NOT NULL DEFAULT '',
    expires_at TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    api_version INTEGER NOT NULL DEFAULT 1,
    requested_permissions TEXT NOT NULL DEFAULT '',
    requested_projects TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mobile_pairing_expiry ON mobile_pairing_sessions(expires_at);

CREATE TABLE IF NOT EXISTS mobile_sessions (
    session_id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    host_id TEXT NOT NULL,
    user_identity TEXT NOT NULL DEFAULT 'user',
    started_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_activity TEXT NOT NULL,
    transport TEXT NOT NULL DEFAULT 'https',
    encryption_state TEXT NOT NULL DEFAULT 'encrypted',
    api_version INTEGER NOT NULL DEFAULT 1,
    granted_capabilities TEXT NOT NULL DEFAULT '',
    granted_projects TEXT NOT NULL DEFAULT '',
    granted_scopes TEXT NOT NULL DEFAULT '',
    background_eligible INTEGER NOT NULL DEFAULT 0,
    notification_eligible INTEGER NOT NULL DEFAULT 0,
    risk_state TEXT NOT NULL DEFAULT 'normal',
    device_lock_state TEXT NOT NULL DEFAULT 'unlocked',
    authentication_strength TEXT NOT NULL DEFAULT 'host_authenticated',
    active_subscriptions TEXT NOT NULL DEFAULT '',
    queued_operations INTEGER NOT NULL DEFAULT 0,
    termination_reason TEXT NOT NULL DEFAULT '',
    connection_state TEXT NOT NULL DEFAULT 'active'
);
CREATE INDEX IF NOT EXISTS idx_mobile_sessions_client ON mobile_sessions(client_id);

CREATE TABLE IF NOT EXISTS mobile_permission_grants (
    client_id TEXT NOT NULL,
    permission TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'session',
    scope_target TEXT NOT NULL DEFAULT '',
    granted_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (client_id, permission, scope_target)
);
CREATE INDEX IF NOT EXISTS idx_mobile_perm_client ON mobile_permission_grants(client_id);

CREATE TABLE IF NOT EXISTS mobile_offline_actions (
    action_id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    host_id TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT '',
    user_identity TEXT NOT NULL DEFAULT 'user',
    action TEXT NOT NULL,
    target TEXT NOT NULL DEFAULT '',
    base_version TEXT NOT NULL DEFAULT '',
    arguments_hash TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    expires_at TEXT NOT NULL DEFAULT '',
    idempotency_key TEXT NOT NULL DEFAULT '',
    privacy TEXT NOT NULL DEFAULT 'private',
    project TEXT NOT NULL DEFAULT '',
    conflict_policy TEXT NOT NULL DEFAULT 'keep_authoritative',
    permission_state TEXT NOT NULL DEFAULT 'pending',
    approval_state TEXT NOT NULL DEFAULT 'none',
    retry_state TEXT NOT NULL DEFAULT 'queued'
);
CREATE INDEX IF NOT EXISTS idx_mobile_offline_client ON mobile_offline_actions(client_id);

CREATE TABLE IF NOT EXISTS mobile_handoffs (
    handoff_id TEXT PRIMARY KEY,
    source_surface TEXT NOT NULL,
    destination_surface TEXT NOT NULL,
    user_identity TEXT NOT NULL DEFAULT 'user',
    host_id TEXT NOT NULL DEFAULT '',
    item_type TEXT NOT NULL DEFAULT '',
    item_id TEXT NOT NULL DEFAULT '',
    content_position TEXT NOT NULL DEFAULT '',
    selected_tab TEXT NOT NULL DEFAULT '',
    unsent_draft TEXT NOT NULL DEFAULT '',
    pending_action TEXT NOT NULL DEFAULT '',
    privacy TEXT NOT NULL DEFAULT 'private',
    expiration TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'created',
    idempotency_key TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS mobile_deep_links (
    link_id TEXT PRIMARY KEY,
    host_id TEXT NOT NULL DEFAULT '',
    user_identity TEXT NOT NULL DEFAULT 'user',
    target_type TEXT NOT NULL DEFAULT '',
    target_id TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL DEFAULT '',
    expires_at TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS mobile_push_registrations (
    registration_id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    platform TEXT NOT NULL DEFAULT 'ios',
    provider TEXT NOT NULL DEFAULT 'apns',
    push_token_reference TEXT NOT NULL DEFAULT '',
    environment TEXT NOT NULL DEFAULT 'sandbox',
    registered_at TEXT NOT NULL DEFAULT '',
    last_validation TEXT NOT NULL DEFAULT '',
    enabled_categories TEXT NOT NULL DEFAULT '',
    privacy_mode TEXT NOT NULL DEFAULT 'normal',
    quiet_hours INTEGER NOT NULL DEFAULT 0,
    health TEXT NOT NULL DEFAULT 'unknown',
    revocation_state TEXT NOT NULL DEFAULT 'active'
);
CREATE INDEX IF NOT EXISTS idx_mobile_push_client ON mobile_push_registrations(client_id);

CREATE TABLE IF NOT EXISTS mobile_activity (
    event_id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    level TEXT NOT NULL DEFAULT 'info',
    recorded_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_mobile_activity_client ON mobile_activity(client_id);
"""


class MobileStorage:
    """Owning SQLite storage for the Mobile Companion Platform."""

    def __init__(self, data_dir: str) -> None:
        self._data_dir = Path(data_dir)
        self._path = self._data_dir / "mobile.db"
        self._lock = threading.RLock()
        self._local = threading.local()
        self._data_dir.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = sqlite3.connect(str(self._path))
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 10000")
            self._local.connection = connection
        return connection

    def prepare(self) -> None:
        with self._lock:
            connection = self.connect()
            self._verify_or_migrate(connection)
            connection.executescript(_SCHEMA)
            connection.execute(
                "INSERT OR REPLACE INTO mobile_meta (key, value) VALUES (?, ?)",
                SCHEMA_VERSION_ROW,
            )
            connection.commit()

    def _verify_or_migrate(self, connection: sqlite3.Connection) -> None:
        try:
            version = connection.execute(
                "SELECT value FROM mobile_meta WHERE key = 'mobile_schema_version'"
            ).fetchone()
        except sqlite3.OperationalError:
            version = None
        if version is not None:
            current = int(version["value"])
            if current > STORAGE_VERSION:
                raise RuntimeError(
                    "mobile storage version %d is newer than supported version %d"
                    % (current, STORAGE_VERSION)
                )
            if current < STORAGE_VERSION:
                raise RuntimeError(
                    "mobile storage version %d predates supported version %d; migration required"
                    % (current, STORAGE_VERSION)
                )

    def path(self) -> str:
        return str(self._path)

    def size_bytes(self) -> int:
        try:
            return self._path.stat().st_size
        except OSError:
            return 0

    def backup_to(self, target_dir: str) -> Optional[str]:
        target = Path(target_dir) / (
            "mobile-%s.db" % hashlib.sha256(os.urandom(4)).hexdigest()[:8]
        )
        with self._lock:
            try:
                connection = self.connect()
                connection.execute("VACUUM INTO ?", (str(target),))
            except sqlite3.OperationalError:
                return None
        return str(target)