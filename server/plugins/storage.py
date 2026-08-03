"""Versioned SQLite storage for the JoeOS Plugin and Extension Platform.

The registry database stores manifest metadata, publishers, contributors,
permissions, extension storage, extension settings, secret references,
contributions, activity and health. Secret values are never stored in plain
text here; only AES-encrypted references are kept by the Secret Broker.

Storage is versioned and refuses to load a newer schema rather than silently
corrupting data (same convention as the rest of JoeOS).
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
from pathlib import Path
from typing import Optional

STORAGE_VERSION = 1
SCHEMA_VERSION_ROW = ("plugins_schema_version", "1")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS plugins_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS plugin_publishers (
    publisher_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    verification_state TEXT NOT NULL DEFAULT 'unknown',
    trusted INTEGER NOT NULL DEFAULT 0,
    first_party INTEGER NOT NULL DEFAULT 0,
    signing_fingerprints TEXT NOT NULL DEFAULT '',
    official_website TEXT NOT NULL DEFAULT '',
    support TEXT NOT NULL DEFAULT '',
    revoked INTEGER NOT NULL DEFAULT 0,
    blocked INTEGER NOT NULL DEFAULT 0,
    known_plugin_ids TEXT NOT NULL DEFAULT '',
    last_verified_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plugin_records (
    plugin_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    version TEXT NOT NULL,
    publisher_id TEXT NOT NULL,
    manifest TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    integrity_state TEXT NOT NULL DEFAULT 'not_verified',
    signature_state TEXT NOT NULL DEFAULT 'unavailable',
    package_hash TEXT NOT NULL DEFAULT '',
    signer_fingerprint TEXT NOT NULL DEFAULT '',
    lifecycle_state TEXT NOT NULL DEFAULT 'discovered',
    health_state TEXT NOT NULL DEFAULT 'unknown',
    enabled_state TEXT NOT NULL DEFAULT 'disabled',
    enabled_scope TEXT NOT NULL DEFAULT 'global',
    quarantine_reason TEXT NOT NULL DEFAULT '',
    crash_count INTEGER NOT NULL DEFAULT 0,
    install_path TEXT NOT NULL DEFAULT '',
    package_path TEXT NOT NULL DEFAULT '',
    installed_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plugins_publisher ON plugin_records(publisher_id);
CREATE INDEX IF NOT EXISTS idx_plugins_enabled ON plugin_records(enabled_state);

CREATE TABLE IF NOT EXISTS plugin_permission_grants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plugin_id TEXT NOT NULL,
    permission TEXT NOT NULL,
    scope TEXT NOT NULL,
    scope_target TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    granted_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plugin_perm_plugin ON plugin_permission_grants(plugin_id);

CREATE TABLE IF NOT EXISTS plugin_contributions (
    contribution_id TEXT PRIMARY KEY,
    plugin_id TEXT NOT NULL,
    type TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    commands TEXT NOT NULL DEFAULT '',
    requires_permissions TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'registered',
    registered_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_contrib_plugin ON plugin_contributions(plugin_id);
CREATE INDEX IF NOT EXISTS idx_contrib_type ON plugin_contributions(type);

CREATE TABLE IF NOT EXISTS plugin_settings (
    setting_id TEXT PRIMARY KEY,
    plugin_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL DEFAULT 'global',
    sensitive INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plugin_settings_plugin ON plugin_settings(plugin_id);

CREATE TABLE IF NOT EXISTS plugin_storage (
    storage_id TEXT PRIMARY KEY,
    plugin_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL DEFAULT 'global',
    schema_version INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plugin_storage_plugin ON plugin_storage(plugin_id);
CREATE INDEX IF NOT EXISTS idx_plugin_storage_key ON plugin_storage(plugin_id, key, scope);

CREATE TABLE IF NOT EXISTS plugin_secret_refs (
    ref_id TEXT PRIMARY KEY,
    plugin_id TEXT NOT NULL,
    name TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'global',
    encrypted_value TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_secret_refs_plugin ON plugin_secret_refs(plugin_id);

CREATE TABLE IF NOT EXISTS plugin_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plugin_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plugin_events_plugin ON plugin_events(plugin_id);

CREATE TABLE IF NOT EXISTS plugin_activity (
    event_id TEXT PRIMARY KEY,
    plugin_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    level TEXT NOT NULL DEFAULT 'info',
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plugin_activity_plugin ON plugin_activity(plugin_id);

CREATE TABLE IF NOT EXISTS plugin_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plugin_id TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'info',
    category TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL DEFAULT '',
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plugin_logs_plugin ON plugin_logs(plugin_id);

CREATE TABLE IF NOT EXISTS plugin_health (
    plugin_id TEXT PRIMARY KEY,
    state TEXT NOT NULL DEFAULT 'unknown',
    last_activation TEXT NOT NULL DEFAULT '',
    last_success TEXT NOT NULL DEFAULT '',
    last_crash TEXT NOT NULL DEFAULT '',
    crash_count INTEGER NOT NULL DEFAULT 0,
    recent_errors TEXT NOT NULL DEFAULT '',
    contribution_count INTEGER NOT NULL DEFAULT 0,
    active_jobs INTEGER NOT NULL DEFAULT 0,
    host_state TEXT NOT NULL DEFAULT 'not_running',
    update_state TEXT NOT NULL DEFAULT 'none',
    message TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plugin_update_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plugin_id TEXT NOT NULL,
    previous_version TEXT NOT NULL,
    target_version TEXT NOT NULL,
    outcome TEXT NOT NULL DEFAULT 'succeeded',
    reason TEXT NOT NULL DEFAULT '',
    recorded_at TEXT NOT NULL
);
"""


class PluginRegistryStorage:
    """Owning SQLite registry for installed plugins and related metadata."""

    def __init__(self, data_dir: str) -> None:
        self._data_dir = Path(data_dir)
        self._path = self._data_dir / "plugins.db"
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
                "INSERT OR REPLACE INTO plugins_meta (key, value) VALUES (?, ?)",
                SCHEMA_VERSION_ROW,
            )
            connection.commit()

    def _verify_or_migrate(self, connection: sqlite3.Connection) -> None:
        try:
            version = connection.execute(
                "SELECT value FROM plugins_meta WHERE key = 'plugins_schema_version'"
            ).fetchone()
        except sqlite3.OperationalError:
            version = None
        if version is not None:
            current = int(version["value"])
            if current > STORAGE_VERSION:
                raise RuntimeError(
                    "plugins storage version %d is newer than supported version %d"
                    % (current, STORAGE_VERSION)
                )
            if current < STORAGE_VERSION:
                raise RuntimeError(
                    "plugins storage version %d predates supported version %d; migration required"
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
            "plugins-%s.db" % hashlib.sha256(os.urandom(4)).hexdigest()[:8]
        )
        with self._lock:
            try:
                connection = self.connect()
                connection.execute("VACUUM INTO ?", (str(target),))
            except sqlite3.OperationalError:
                return None
        return str(target)