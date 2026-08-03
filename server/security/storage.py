"""Versioned SQLite storage for the JoeOS Security Platform.

Stores security policies, threat models, identities, scope grants, approvals,
consent, secret metadata (never values), audit events with integrity hashes,
security events, incidents, lockdown state, circuit breakers, and data
classifications. Secret values live only in the Secret Broker's encrypted
vault, never here.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
from pathlib import Path
from typing import Optional

STORAGE_VERSION = 1
SCHEMA_VERSION_ROW = ("security_schema_version", "1")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS security_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS security_policies (
    policy_id TEXT PRIMARY KEY,
    version INTEGER NOT NULL DEFAULT 1,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL,
    scope_target TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    resource TEXT NOT NULL DEFAULT '',
    effect TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 50,
    conditions TEXT NOT NULL DEFAULT '{}',
    exceptions TEXT NOT NULL DEFAULT '',
    authority TEXT NOT NULL DEFAULT 'user',
    owner TEXT NOT NULL DEFAULT 'user',
    created_at TEXT NOT NULL DEFAULT '',
    review_time TEXT NOT NULL DEFAULT '',
    expiration TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    superseded INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_policies_enabled ON security_policies(enabled);

CREATE TABLE IF NOT EXISTS threat_models (
    threat_model_id TEXT PRIMARY KEY,
    subsystem TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    assets TEXT NOT NULL DEFAULT '',
    actors TEXT NOT NULL DEFAULT '',
    trust_boundaries TEXT NOT NULL DEFAULT '',
    entry_points TEXT NOT NULL DEFAULT '',
    data_flows TEXT NOT NULL DEFAULT '',
    assumptions TEXT NOT NULL DEFAULT '',
    threats TEXT NOT NULL DEFAULT '',
    mitigations TEXT NOT NULL DEFAULT '',
    residual_risk TEXT NOT NULL DEFAULT '',
    owner TEXT NOT NULL DEFAULT '',
    review_date TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS security_identities (
    identity_id TEXT PRIMARY KEY,
    identity_type TEXT NOT NULL,
    display_label TEXT NOT NULL,
    owner TEXT NOT NULL DEFAULT '',
    issuer TEXT NOT NULL DEFAULT '',
    trust_state TEXT NOT NULL DEFAULT 'untrusted',
    status TEXT NOT NULL DEFAULT 'active',
    credentials_reference TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    last_activity TEXT NOT NULL DEFAULT '',
    expiration TEXT NOT NULL DEFAULT '',
    revocation_state TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS security_scope_grants (
    grant_id TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    capability TEXT NOT NULL,
    action TEXT NOT NULL DEFAULT '',
    resource TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL DEFAULT 'session',
    project TEXT NOT NULL DEFAULT '',
    task TEXT NOT NULL DEFAULT '',
    mission TEXT NOT NULL DEFAULT '',
    device TEXT NOT NULL DEFAULT '',
    conditions TEXT NOT NULL DEFAULT '{}',
    duration TEXT NOT NULL DEFAULT '',
    issued_by TEXT NOT NULL DEFAULT 'user',
    authority TEXT NOT NULL DEFAULT 'user',
    approval TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    expiration TEXT NOT NULL DEFAULT '',
    usage_count INTEGER NOT NULL DEFAULT 0,
    last_use TEXT NOT NULL DEFAULT '',
    revocation_state TEXT NOT NULL DEFAULT 'active'
);
CREATE INDEX IF NOT EXISTS idx_scope_grants_subject ON security_scope_grants(subject);

CREATE TABLE IF NOT EXISTS security_approvals (
    approval_id TEXT PRIMARY KEY,
    requester_identity TEXT NOT NULL,
    approver_identity TEXT NOT NULL DEFAULT '',
    host TEXT NOT NULL DEFAULT '',
    device TEXT NOT NULL DEFAULT '',
    session TEXT NOT NULL DEFAULT '',
    action_id TEXT NOT NULL,
    target_id TEXT NOT NULL DEFAULT '',
    target_type TEXT NOT NULL DEFAULT '',
    arguments_hash TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL DEFAULT '',
    attachment_hashes TEXT NOT NULL DEFAULT '',
    workflow_version TEXT NOT NULL DEFAULT '',
    plugin_version TEXT NOT NULL DEFAULT '',
    project TEXT NOT NULL DEFAULT '',
    task TEXT NOT NULL DEFAULT '',
    mission TEXT NOT NULL DEFAULT '',
    data_classification TEXT NOT NULL DEFAULT 'unknown',
    risk TEXT NOT NULL DEFAULT 'low',
    strength_required TEXT NOT NULL DEFAULT 'level1',
    expiration TEXT NOT NULL DEFAULT '',
    policy_version INTEGER NOT NULL DEFAULT 1,
    state TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT '',
    resolved_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_approvals_state ON security_approvals(state);

CREATE TABLE IF NOT EXISTS security_consent (
    consent_id TEXT PRIMARY KEY,
    identity TEXT NOT NULL,
    purpose TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '',
    destination TEXT NOT NULL DEFAULT '',
    duration TEXT NOT NULL DEFAULT '',
    policy_version INTEGER NOT NULL DEFAULT 1,
    state TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS security_secret_metadata (
    secret_id TEXT PRIMARY KEY,
    display_label TEXT NOT NULL,
    secret_type TEXT NOT NULL,
    owner TEXT NOT NULL DEFAULT 'user',
    scope TEXT NOT NULL DEFAULT 'global',
    project TEXT NOT NULL DEFAULT '',
    plugin TEXT NOT NULL DEFAULT '',
    workflow TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL DEFAULT '',
    device TEXT NOT NULL DEFAULT '',
    storage_adapter TEXT NOT NULL DEFAULT 'encrypted_vault',
    created_at TEXT NOT NULL DEFAULT '',
    last_rotation TEXT NOT NULL DEFAULT '',
    expiration TEXT NOT NULL DEFAULT '',
    last_use TEXT NOT NULL DEFAULT '',
    usage_count INTEGER NOT NULL DEFAULT 0,
    allowed_operations TEXT NOT NULL DEFAULT '',
    allowed_destinations TEXT NOT NULL DEFAULT '',
    revoked_state TEXT NOT NULL DEFAULT 'active',
    health TEXT NOT NULL DEFAULT 'healthy'
);

CREATE TABLE IF NOT EXISTS security_secret_values (
    secret_id TEXT PRIMARY KEY,
    encrypted_value TEXT NOT NULL,
    nonce TEXT NOT NULL,
    rotation INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS security_secret_detections (
    detection_id TEXT PRIMARY KEY,
    candidate_type TEXT NOT NULL,
    confidence TEXT NOT NULL DEFAULT 'candidate',
    masked_fingerprint TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS security_audit (
    event_id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    actor TEXT NOT NULL,
    actor_type TEXT NOT NULL DEFAULT 'identity',
    session TEXT NOT NULL DEFAULT '',
    device TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    target TEXT NOT NULL DEFAULT '',
    project TEXT NOT NULL DEFAULT '',
    task TEXT NOT NULL DEFAULT '',
    mission TEXT NOT NULL DEFAULT '',
    plugin TEXT NOT NULL DEFAULT '',
    workflow TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL DEFAULT '',
    permission_decision TEXT NOT NULL DEFAULT '',
    approval TEXT NOT NULL DEFAULT '',
    policy_version INTEGER NOT NULL DEFAULT 1,
    result TEXT NOT NULL DEFAULT 'allowed',
    risk TEXT NOT NULL DEFAULT 'low',
    source TEXT NOT NULL DEFAULT '',
    trace_id TEXT NOT NULL DEFAULT '',
    integrity_hash TEXT NOT NULL DEFAULT '',
    previous_hash TEXT NOT NULL DEFAULT '',
    sequence INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON security_audit(actor);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON security_audit(timestamp);

CREATE TABLE IF NOT EXISTS security_events (
    event_id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'warning',
    confidence TEXT NOT NULL DEFAULT 'candidate',
    evidence TEXT NOT NULL DEFAULT '',
    affected_identity TEXT NOT NULL DEFAULT '',
    affected_project TEXT NOT NULL DEFAULT '',
    affected_service TEXT NOT NULL DEFAULT '',
    timestamp TEXT NOT NULL DEFAULT '',
    recommended_action TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',
    trace_id TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_events_status ON security_events(status);

CREATE TABLE IF NOT EXISTS security_incidents (
    incident_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'medium',
    status TEXT NOT NULL DEFAULT 'new',
    detection_source TEXT NOT NULL DEFAULT '',
    affected_assets TEXT NOT NULL DEFAULT '',
    affected_identities TEXT NOT NULL DEFAULT '',
    affected_secrets TEXT NOT NULL DEFAULT '',
    timeline TEXT NOT NULL DEFAULT '[]',
    evidence TEXT NOT NULL DEFAULT '',
    containment TEXT NOT NULL DEFAULT '',
    eradication TEXT NOT NULL DEFAULT '',
    recovery TEXT NOT NULL DEFAULT '',
    residual_risk TEXT NOT NULL DEFAULT '',
    owner TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    resolved_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS security_lockdown (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    payload TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS security_circuit_breakers (
    breaker_id TEXT PRIMARY KEY,
    target TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'closed',
    failures INTEGER NOT NULL DEFAULT 0,
    opened_at TEXT NOT NULL DEFAULT '',
    retry_after TEXT NOT NULL DEFAULT '',
    last_error TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS security_activity (
    event_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    level TEXT NOT NULL DEFAULT 'info',
    recorded_at TEXT NOT NULL DEFAULT ''
);
"""


class SecurityStorage:
    """Owning SQLite storage for the Security Platform."""

    def __init__(self, data_dir: str) -> None:
        self._data_dir = Path(data_dir)
        self._path = self._data_dir / "security.db"
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
                "INSERT OR REPLACE INTO security_meta (key, value) VALUES (?, ?)",
                SCHEMA_VERSION_ROW,
            )
            connection.commit()

    def _verify_or_migrate(self, connection: sqlite3.Connection) -> None:
        try:
            version = connection.execute(
                "SELECT value FROM security_meta WHERE key = 'security_schema_version'"
            ).fetchone()
        except sqlite3.OperationalError:
            version = None
        if version is not None:
            current = int(version["value"])
            if current > STORAGE_VERSION:
                raise RuntimeError(
                    "security storage version %d is newer than supported version %d"
                    % (current, STORAGE_VERSION)
                )
            if current < STORAGE_VERSION:
                raise RuntimeError(
                    "security storage version %d predates supported version %d; migration required"
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
            "security-%s.db" % hashlib.sha256(os.urandom(4)).hexdigest()[:8]
        )
        with self._lock:
            try:
                connection = self.connect()
                connection.execute("VACUUM INTO ?", (str(target),))
            except sqlite3.OperationalError:
                return None
        return str(target)