"""Versioned SQLite storage for the JoeOS Communications Platform.

Stores messages, notifications, drafts, outbox items, conversations, threads,
identities, providers, accounts, contacts, attachments, and preferences.
Secret values (provider credentials) are never stored here; the Secret Broker
holds encrypted references.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
from pathlib import Path
from typing import Optional

STORAGE_VERSION = 1
SCHEMA_VERSION_ROW = ("communications_schema_version", "1")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS comms_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS comms_providers (
    provider_id TEXT PRIMARY KEY,
    provider_type TEXT NOT NULL DEFAULT 'generic',
    display_name TEXT NOT NULL,
    capabilities TEXT NOT NULL DEFAULT '{}',
    authentication TEXT NOT NULL DEFAULT 'none',
    plugin_source TEXT NOT NULL DEFAULT '',
    health_state TEXT NOT NULL DEFAULT 'unknown',
    privacy TEXT NOT NULL DEFAULT 'private',
    is_isolated_test INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS comms_accounts (
    account_id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL,
    display_label TEXT NOT NULL,
    identity_id TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 0,
    connection_state TEXT NOT NULL DEFAULT 'unknown',
    capabilities TEXT NOT NULL DEFAULT '{}',
    sending_permission INTEGER NOT NULL DEFAULT 0,
    last_sync TEXT NOT NULL DEFAULT '',
    last_failure TEXT NOT NULL DEFAULT '',
    health TEXT NOT NULL DEFAULT 'unknown',
    plugin_source TEXT NOT NULL DEFAULT '',
    removed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS comms_identities (
    identity_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    identity_type TEXT NOT NULL,
    user_owned INTEGER NOT NULL DEFAULT 0,
    provider TEXT NOT NULL DEFAULT '',
    account TEXT NOT NULL DEFAULT '',
    verified_addresses TEXT NOT NULL DEFAULT '',
    verified_handles TEXT NOT NULL DEFAULT '',
    verification_state TEXT NOT NULL DEFAULT 'unverified',
    sending_permission INTEGER NOT NULL DEFAULT 0,
    default_state INTEGER NOT NULL DEFAULT 0,
    privacy TEXT NOT NULL DEFAULT 'private',
    created_at TEXT NOT NULL DEFAULT '',
    disabled INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS comms_contacts (
    contact_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    organization TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT '',
    addresses TEXT NOT NULL DEFAULT '',
    handles TEXT NOT NULL DEFAULT '',
    aliases TEXT NOT NULL DEFAULT '',
    preferred_channel TEXT NOT NULL DEFAULT '',
    timezone TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT '',
    trust_state TEXT NOT NULL DEFAULT 'unknown',
    verification_state TEXT NOT NULL DEFAULT 'unverified',
    source TEXT NOT NULL DEFAULT '',
    privacy TEXT NOT NULL DEFAULT 'private',
    last_interaction TEXT NOT NULL DEFAULT '',
    deleted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS comms_conversations (
    conversation_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL DEFAULT '',
    account TEXT NOT NULL DEFAULT '',
    participants TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    project TEXT NOT NULL DEFAULT '',
    mission TEXT NOT NULL DEFAULT '',
    task TEXT NOT NULL DEFAULT '',
    agent TEXT NOT NULL DEFAULT '',
    privacy TEXT NOT NULL DEFAULT 'private',
    latest_message TEXT NOT NULL DEFAULT '',
    unread_count INTEGER NOT NULL DEFAULT 0,
    mention_count INTEGER NOT NULL DEFAULT 0,
    mute_state INTEGER NOT NULL DEFAULT 0,
    archive_state INTEGER NOT NULL DEFAULT 0,
    health TEXT NOT NULL DEFAULT 'healthy',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS comms_threads (
    thread_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL DEFAULT '',
    parent_message TEXT NOT NULL DEFAULT '',
    participants TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',
    latest_reply TEXT NOT NULL DEFAULT '',
    unresolved_questions INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS comms_messages (
    message_id TEXT PRIMARY KEY,
    communication_type TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT '',
    provider_message_id TEXT NOT NULL DEFAULT '',
    account TEXT NOT NULL DEFAULT '',
    origin_type TEXT NOT NULL,
    origin_label TEXT NOT NULL,
    source_service TEXT NOT NULL DEFAULT '',
    source_plugin TEXT NOT NULL DEFAULT '',
    source_workflow TEXT NOT NULL DEFAULT '',
    source_mission TEXT NOT NULL DEFAULT '',
    source_task TEXT NOT NULL DEFAULT '',
    source_agent TEXT NOT NULL DEFAULT '',
    author TEXT NOT NULL DEFAULT '',
    sender_identity TEXT NOT NULL DEFAULT '',
    recipients TEXT NOT NULL DEFAULT '',
    conversation_id TEXT NOT NULL DEFAULT '',
    thread_id TEXT NOT NULL DEFAULT '',
    parent_message TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    rich_body TEXT NOT NULL DEFAULT '',
    attachments TEXT NOT NULL DEFAULT '[]',
    links TEXT NOT NULL DEFAULT '',
    mentions TEXT NOT NULL DEFAULT '',
    priority TEXT NOT NULL DEFAULT 'normal',
    severity TEXT NOT NULL DEFAULT 'informational',
    privacy TEXT NOT NULL DEFAULT 'private',
    draft_state INTEGER NOT NULL DEFAULT 0,
    approval_state TEXT NOT NULL DEFAULT 'none',
    delivery_state TEXT NOT NULL DEFAULT 'pending_validation',
    read_state TEXT NOT NULL DEFAULT 'delivered',
    archive_state INTEGER NOT NULL DEFAULT 0,
    mute_state INTEGER NOT NULL DEFAULT 0,
    snooze_until TEXT NOT NULL DEFAULT '',
    scheduled_send TEXT NOT NULL DEFAULT '',
    sent_at TEXT NOT NULL DEFAULT '',
    received_at TEXT NOT NULL DEFAULT '',
    delivery_attempts INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT NOT NULL DEFAULT '',
    provenance TEXT NOT NULL DEFAULT '{}',
    verification_state TEXT NOT NULL DEFAULT 'unverified',
    phishing_indicators TEXT NOT NULL DEFAULT '',
    deletion_state TEXT NOT NULL DEFAULT 'active',
    external INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_comms_messages_conv ON comms_messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_comms_messages_type ON comms_messages(communication_type);
CREATE INDEX IF NOT EXISTS idx_comms_messages_created ON comms_messages(created_at);

CREATE TABLE IF NOT EXISTS comms_drafts (
    draft_id TEXT PRIMARY KEY,
    author TEXT NOT NULL DEFAULT '',
    proposed_sender TEXT NOT NULL DEFAULT '',
    recipients TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL DEFAULT '',
    account TEXT NOT NULL DEFAULT '',
    conversation_id TEXT NOT NULL DEFAULT '',
    thread_id TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    attachments TEXT NOT NULL DEFAULT '[]',
    privacy TEXT NOT NULL DEFAULT 'private',
    source TEXT NOT NULL DEFAULT '',
    source_agent TEXT NOT NULL DEFAULT '',
    source_workflow TEXT NOT NULL DEFAULT '',
    source_task TEXT NOT NULL DEFAULT '',
    approval_required INTEGER NOT NULL DEFAULT 0,
    approval_state TEXT NOT NULL DEFAULT 'none',
    scheduled_send TEXT NOT NULL DEFAULT '',
    conflict_state TEXT NOT NULL DEFAULT 'clean',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS comms_outbox (
    outbox_id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL DEFAULT '',
    sender_identity TEXT NOT NULL DEFAULT '',
    recipients TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL DEFAULT '',
    account TEXT NOT NULL DEFAULT '',
    scheduled TEXT NOT NULL DEFAULT '',
    approval_state TEXT NOT NULL DEFAULT 'none',
    attempts INTEGER NOT NULL DEFAULT 0,
    idempotency_key TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'queued',
    failure TEXT NOT NULL DEFAULT '',
    retryable INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT '',
    sent_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS comms_attachments (
    attachment_id TEXT PRIMARY KEY,
    source TEXT NOT NULL DEFAULT '',
    safe_path TEXT NOT NULL DEFAULT '',
    display_name TEXT NOT NULL DEFAULT '',
    mime_type TEXT NOT NULL DEFAULT '',
    size INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT NOT NULL DEFAULT '',
    project TEXT NOT NULL DEFAULT '',
    owner TEXT NOT NULL DEFAULT '',
    privacy TEXT NOT NULL DEFAULT 'private',
    sensitivity TEXT NOT NULL DEFAULT '',
    malware_scan TEXT NOT NULL DEFAULT 'not_scanned',
    file_classification TEXT NOT NULL DEFAULT '',
    generated INTEGER NOT NULL DEFAULT 0,
    provider_state TEXT NOT NULL DEFAULT 'local',
    retention TEXT NOT NULL DEFAULT '',
    deletion_state TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS comms_notifications (
    notification_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL DEFAULT 'informational',
    priority TEXT NOT NULL DEFAULT 'normal',
    urgency TEXT NOT NULL DEFAULT 'routine',
    privacy TEXT NOT NULL DEFAULT 'private',
    project TEXT NOT NULL DEFAULT '',
    mission TEXT NOT NULL DEFAULT '',
    task TEXT NOT NULL DEFAULT '',
    workflow TEXT NOT NULL DEFAULT '',
    plugin TEXT NOT NULL DEFAULT '',
    service TEXT NOT NULL DEFAULT '',
    related_entity TEXT NOT NULL DEFAULT '',
    action_links TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    expiration TEXT NOT NULL DEFAULT '',
    delivery_channels TEXT NOT NULL DEFAULT '',
    delivery_state TEXT NOT NULL DEFAULT 'created',
    read_state TEXT NOT NULL DEFAULT 'delivered',
    archive_state INTEGER NOT NULL DEFAULT 0,
    mute_state INTEGER NOT NULL DEFAULT 0,
    snooze_until TEXT NOT NULL DEFAULT '',
    deduplication_key TEXT NOT NULL DEFAULT '',
    grouping_key TEXT NOT NULL DEFAULT '',
    escalation_policy TEXT NOT NULL DEFAULT '',
    trace_id TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_comms_notif_source ON comms_notifications(source);
CREATE INDEX IF NOT EXISTS idx_comms_notif_created ON comms_notifications(created_at);

CREATE TABLE IF NOT EXISTS comms_notification_rules (
    rule_id TEXT PRIMARY KEY,
    source TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL DEFAULT 'deliver',
    channel TEXT NOT NULL DEFAULT '',
    priority INTEGER NOT NULL DEFAULT 50,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS comms_quiet_hours (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    payload TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS comms_digests (
    digest_id TEXT PRIMARY KEY,
    time_window_start TEXT NOT NULL DEFAULT '',
    time_window_end TEXT NOT NULL DEFAULT '',
    source_categories TEXT NOT NULL DEFAULT '',
    important_items TEXT NOT NULL DEFAULT '',
    unresolved_items TEXT NOT NULL DEFAULT '',
    failures TEXT NOT NULL DEFAULT '',
    approvals TEXT NOT NULL DEFAULT '',
    generation_method TEXT NOT NULL DEFAULT 'structured',
    privacy TEXT NOT NULL DEFAULT 'private',
    created_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS comms_external_approvals (
    approval_id TEXT PRIMARY KEY,
    draft_id TEXT NOT NULL DEFAULT '',
    message_hash TEXT NOT NULL DEFAULT '',
    recipient_hash TEXT NOT NULL DEFAULT '',
    attachment_hashes TEXT NOT NULL DEFAULT '',
    sender_identity TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL DEFAULT '',
    account TEXT NOT NULL DEFAULT '',
    scheduled TEXT NOT NULL DEFAULT '',
    privacy TEXT NOT NULL DEFAULT 'private',
    state TEXT NOT NULL DEFAULT 'pending',
    expires_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    resolved_at TEXT NOT NULL DEFAULT ''
);
"""


class CommunicationsStorage:
    """Owning SQLite storage for the Communications Platform."""

    def __init__(self, data_dir: str) -> None:
        self._data_dir = Path(data_dir)
        self._path = self._data_dir / "communications.db"
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
                "INSERT OR REPLACE INTO comms_meta (key, value) VALUES (?, ?)",
                SCHEMA_VERSION_ROW,
            )
            connection.commit()

    def _verify_or_migrate(self, connection: sqlite3.Connection) -> None:
        try:
            version = connection.execute(
                "SELECT value FROM comms_meta WHERE key = 'communications_schema_version'"
            ).fetchone()
        except sqlite3.OperationalError:
            version = None
        if version is not None:
            current = int(version["value"])
            if current > STORAGE_VERSION:
                raise RuntimeError(
                    "communications storage version %d is newer than supported version %d"
                    % (current, STORAGE_VERSION)
                )
            if current < STORAGE_VERSION:
                raise RuntimeError(
                    "communications storage version %d predates supported version %d; migration required"
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
            "communications-%s.db" % hashlib.sha256(os.urandom(4)).hexdigest()[:8]
        )
        with self._lock:
            try:
                connection = self.connect()
                connection.execute("VACUUM INTO ?", (str(target),))
            except sqlite3.OperationalError:
                return None
        return str(target)