"""Versioned SQLite storage for the JoeOS Smart Glasses and Wearable Platform.

Stores devices, adapters, sessions, pairing challenges, capabilities, trust,
permissions, glance cards, checklists, handoffs, offline operations, voice and
camera activity metadata, and device health. Pairing codes and session keys
are never stored here in plain text; only secure references are kept.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
from pathlib import Path
from typing import Optional

STORAGE_VERSION = 1
SCHEMA_VERSION_ROW = ("wearables_schema_version", "1")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS wearables_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS device_records (
    device_id TEXT PRIMARY KEY,
    device_type TEXT NOT NULL,
    display_name TEXT NOT NULL,
    manufacturer TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    hardware_revision TEXT NOT NULL DEFAULT '',
    firmware_version TEXT NOT NULL DEFAULT '',
    adapter_id TEXT NOT NULL DEFAULT '',
    plugin_id TEXT NOT NULL DEFAULT '',
    transport TEXT NOT NULL DEFAULT 'local_network',
    connection_address_reference TEXT NOT NULL DEFAULT '',
    paired_state TEXT NOT NULL DEFAULT 'unpaired',
    trusted_state TEXT NOT NULL DEFAULT 'untrusted',
    authentication_state TEXT NOT NULL DEFAULT 'unauthenticated',
    key_reference TEXT NOT NULL DEFAULT '',
    user_owned INTEGER NOT NULL DEFAULT 1,
    verified_capabilities TEXT NOT NULL DEFAULT '',
    disabled_capabilities TEXT NOT NULL DEFAULT '',
    connection_state TEXT NOT NULL DEFAULT 'discovered',
    battery_state TEXT NOT NULL DEFAULT 'unknown',
    charging_state TEXT NOT NULL DEFAULT 'unknown',
    thermal_state TEXT NOT NULL DEFAULT 'unknown',
    network_state TEXT NOT NULL DEFAULT 'unknown',
    latency_ms INTEGER,
    bandwidth_class TEXT NOT NULL DEFAULT 'unknown',
    health TEXT NOT NULL DEFAULT 'unknown',
    privacy_mode TEXT NOT NULL DEFAULT 'normal',
    mic_active INTEGER NOT NULL DEFAULT 0,
    camera_active INTEGER NOT NULL DEFAULT 0,
    last_connected TEXT NOT NULL DEFAULT '',
    last_disconnected TEXT NOT NULL DEFAULT '',
    last_firmware_check TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    revocation_state TEXT NOT NULL DEFAULT 'active',
    deletion_state TEXT NOT NULL DEFAULT 'active'
);
CREATE INDEX IF NOT EXISTS idx_device_conn ON device_records(connection_state);

CREATE TABLE IF NOT EXISTS adapter_records (
    adapter_id TEXT PRIMARY KEY,
    plugin_id TEXT NOT NULL DEFAULT '',
    display_name TEXT NOT NULL,
    supported_manufacturers TEXT NOT NULL DEFAULT '',
    supported_transports TEXT NOT NULL DEFAULT '',
    supports_discovery INTEGER NOT NULL DEFAULT 0,
    supports_pairing INTEGER NOT NULL DEFAULT 1,
    supported_capabilities TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'registered',
    version TEXT NOT NULL DEFAULT '',
    platform TEXT NOT NULL DEFAULT '',
    health TEXT NOT NULL DEFAULT 'unknown',
    is_simulator INTEGER NOT NULL DEFAULT 0,
    known_limitations TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS device_capabilities (
    capability_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    adapter_id TEXT NOT NULL DEFAULT '',
    support_state TEXT NOT NULL DEFAULT 'unknown',
    verification_state TEXT NOT NULL DEFAULT 'unverified',
    permission_requirement TEXT NOT NULL DEFAULT '',
    privacy_classification TEXT NOT NULL DEFAULT 'private',
    resource_cost TEXT NOT NULL DEFAULT 'low',
    limitations TEXT NOT NULL DEFAULT '',
    health TEXT NOT NULL DEFAULT 'unknown'
);
CREATE INDEX IF NOT EXISTS idx_dev_caps_device ON device_capabilities(device_id);

CREATE TABLE IF NOT EXISTS pairing_challenges (
    challenge_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    adapter_id TEXT NOT NULL,
    method TEXT NOT NULL DEFAULT 'one_time_code',
    code_reference TEXT NOT NULL DEFAULT '',
    code_hash TEXT NOT NULL DEFAULT '',
    expires_at TEXT NOT NULL,
    used INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pairing_expiry ON pairing_challenges(expires_at);

CREATE TABLE IF NOT EXISTS device_trust (
    device_id TEXT PRIMARY KEY,
    trust_state TEXT NOT NULL DEFAULT 'untrusted',
    scope TEXT NOT NULL DEFAULT 'session',
    scope_target TEXT NOT NULL DEFAULT '',
    capabilities TEXT NOT NULL DEFAULT '',
    granted_at TEXT NOT NULL DEFAULT '',
    revocation_reason TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS device_permission_grants (
    device_id TEXT NOT NULL,
    permission TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'session',
    scope_target TEXT NOT NULL DEFAULT '',
    granted_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (device_id, permission, scope_target)
);
CREATE INDEX IF NOT EXISTS idx_dev_perm_device ON device_permission_grants(device_id);

CREATE TABLE IF NOT EXISTS device_sessions (
    session_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    adapter_id TEXT NOT NULL DEFAULT '',
    authenticated_user TEXT NOT NULL DEFAULT 'user',
    started_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    transport TEXT NOT NULL DEFAULT '',
    encryption_state TEXT NOT NULL DEFAULT 'encrypted',
    permissions TEXT NOT NULL DEFAULT '',
    capabilities TEXT NOT NULL DEFAULT '',
    active_views TEXT NOT NULL DEFAULT '',
    notification_queue TEXT NOT NULL DEFAULT '',
    bandwidth_policy TEXT NOT NULL DEFAULT 'normal',
    privacy_mode TEXT NOT NULL DEFAULT 'normal',
    activity_state TEXT NOT NULL DEFAULT 'idle',
    last_heartbeat TEXT NOT NULL DEFAULT '',
    risk_state TEXT NOT NULL DEFAULT 'normal',
    termination_reason TEXT NOT NULL DEFAULT '',
    connection_state TEXT NOT NULL DEFAULT 'idle'
);
CREATE INDEX IF NOT EXISTS idx_session_device ON device_sessions(device_id);

CREATE TABLE IF NOT EXISTS wearable_content (
    content_id TEXT PRIMARY KEY,
    content_type TEXT NOT NULL,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    detail_pages TEXT NOT NULL DEFAULT '[]',
    icon TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL DEFAULT 'informational',
    priority TEXT NOT NULL DEFAULT 'normal',
    privacy TEXT NOT NULL DEFAULT 'private',
    actions TEXT NOT NULL DEFAULT '',
    expiration TEXT NOT NULL DEFAULT '',
    requires_acknowledgement INTEGER NOT NULL DEFAULT 0,
    project TEXT NOT NULL DEFAULT '',
    mission TEXT NOT NULL DEFAULT '',
    task TEXT NOT NULL DEFAULT '',
    workflow TEXT NOT NULL DEFAULT '',
    agent TEXT NOT NULL DEFAULT '',
    conversation TEXT NOT NULL DEFAULT '',
    artifact TEXT NOT NULL DEFAULT '',
    deduplication_key TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    delivery_state TEXT NOT NULL DEFAULT 'pending',
    device_id TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_content_device ON wearable_content(device_id);

CREATE TABLE IF NOT EXISTS device_interactions (
    event_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT '',
    input_type TEXT NOT NULL,
    timestamp TEXT NOT NULL DEFAULT '',
    confidence REAL,
    normalized_action TEXT NOT NULL DEFAULT '',
    active_content TEXT NOT NULL DEFAULT '',
    permission_state TEXT NOT NULL DEFAULT 'denied',
    duplicate INTEGER NOT NULL DEFAULT 0,
    expiration TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS device_commands (
    request_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT '',
    command TEXT NOT NULL,
    params TEXT NOT NULL DEFAULT '{}',
    risk TEXT NOT NULL DEFAULT 'low',
    confirmation_level TEXT NOT NULL DEFAULT 'low',
    created_at TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'received'
);

CREATE TABLE IF NOT EXISTS voice_intents (
    intent_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT '',
    transcript TEXT NOT NULL,
    normalized_intent TEXT NOT NULL,
    entities TEXT NOT NULL DEFAULT '{}',
    confidence REAL NOT NULL DEFAULT 0,
    ambiguous INTEGER NOT NULL DEFAULT 0,
    required_permissions TEXT NOT NULL DEFAULT '',
    required_confirmation TEXT NOT NULL DEFAULT 'low',
    source_device TEXT NOT NULL DEFAULT '',
    active_context TEXT NOT NULL DEFAULT '',
    model_source TEXT NOT NULL DEFAULT 'local',
    created_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS camera_captures (
    capture_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL DEFAULT 'still_image',
    permission_state TEXT NOT NULL DEFAULT 'denied',
    recording_indicator INTEGER NOT NULL DEFAULT 1,
    artifact_reference TEXT NOT NULL DEFAULT '',
    privacy_classification TEXT NOT NULL DEFAULT 'private',
    retention_policy TEXT NOT NULL DEFAULT 'process_and_delete',
    local_only INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT '',
    stopped_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_capture_device ON camera_captures(device_id);

CREATE TABLE IF NOT EXISTS wearable_checklists (
    checklist_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    project TEXT NOT NULL DEFAULT '',
    task TEXT NOT NULL DEFAULT '',
    mission TEXT NOT NULL DEFAULT '',
    steps TEXT NOT NULL DEFAULT '[]',
    current_step INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'active',
    owner TEXT NOT NULL DEFAULT 'user',
    source TEXT NOT NULL DEFAULT '',
    version TEXT NOT NULL DEFAULT '1.0.0',
    created_at TEXT NOT NULL DEFAULT '',
    device_id TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS handoffs (
    handoff_id TEXT PRIMARY KEY,
    source_surface TEXT NOT NULL,
    target_surface TEXT NOT NULL,
    active_item TEXT NOT NULL DEFAULT '',
    project TEXT NOT NULL DEFAULT '',
    mission TEXT NOT NULL DEFAULT '',
    task TEXT NOT NULL DEFAULT '',
    content_position TEXT NOT NULL DEFAULT '',
    selected_action TEXT NOT NULL DEFAULT '',
    pending_approval TEXT NOT NULL DEFAULT '',
    checklist_position TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'created',
    created_at TEXT NOT NULL DEFAULT '',
    expires_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS offline_operations (
    operation_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT '',
    expires_at TEXT NOT NULL DEFAULT '',
    idempotency_key TEXT NOT NULL DEFAULT '',
    privacy TEXT NOT NULL DEFAULT 'private',
    approval_state TEXT NOT NULL DEFAULT 'none',
    conflict_policy TEXT NOT NULL DEFAULT 'keep_authoritative',
    retry_state TEXT NOT NULL DEFAULT 'queued'
);
CREATE INDEX IF NOT EXISTS idx_offline_device ON offline_operations(device_id);

CREATE TABLE IF NOT EXISTS wearable_activity (
    event_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    level TEXT NOT NULL DEFAULT 'info',
    recorded_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_wearable_activity_device ON wearable_activity(device_id);
"""


class WearablesStorage:
    """Owning SQLite storage for the Wearable and Ambient Device Platform."""

    def __init__(self, data_dir: str) -> None:
        self._data_dir = Path(data_dir)
        self._path = self._data_dir / "wearables.db"
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
                "INSERT OR REPLACE INTO wearables_meta (key, value) VALUES (?, ?)",
                SCHEMA_VERSION_ROW,
            )
            connection.commit()

    def _verify_or_migrate(self, connection: sqlite3.Connection) -> None:
        try:
            version = connection.execute(
                "SELECT value FROM wearables_meta WHERE key = 'wearables_schema_version'"
            ).fetchone()
        except sqlite3.OperationalError:
            version = None
        if version is not None:
            current = int(version["value"])
            if current > STORAGE_VERSION:
                raise RuntimeError(
                    "wearables storage version %d is newer than supported version %d"
                    % (current, STORAGE_VERSION)
                )
            if current < STORAGE_VERSION:
                raise RuntimeError(
                    "wearables storage version %d predates supported version %d; migration required"
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
            "wearables-%s.db" % hashlib.sha256(os.urandom(4)).hexdigest()[:8]
        )
        with self._lock:
            try:
                connection = self.connect()
                connection.execute("VACUUM INTO ?", (str(target),))
            except sqlite3.OperationalError:
                return None
        return str(target)