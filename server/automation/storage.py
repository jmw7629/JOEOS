"""Versioned SQLite storage for the JoeOS Automation and Workflow Platform.

Stores workflow definitions, versions, runs, triggers, schedules, actions,
approvals, user-input requests, traces, idempotency records, locks, rate-limit
counters, and history. Secret values are never stored here in plain text; the
Workflow Secret Broker keeps encrypted references elsewhere.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
from pathlib import Path
from typing import Optional

STORAGE_VERSION = 1
SCHEMA_VERSION_ROW = ("automation_schema_version", "1")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS automation_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS workflow_definitions (
    workflow_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    current_version TEXT NOT NULL,
    definition TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'draft',
    health_state TEXT NOT NULL DEFAULT 'unknown',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workflow_versions (
    version_id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    version TEXT NOT NULL,
    definition_hash TEXT NOT NULL,
    definition TEXT NOT NULL,
    created_at TEXT NOT NULL,
    creator TEXT NOT NULL DEFAULT '',
    change_summary TEXT NOT NULL DEFAULT '',
    published INTEGER NOT NULL DEFAULT 0,
    superseded INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_workflow_versions_workflow ON workflow_versions(workflow_id);

CREATE TABLE IF NOT EXISTS workflow_triggers (
    trigger_id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    config TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    health_state TEXT NOT NULL DEFAULT 'healthy',
    last_event TEXT NOT NULL DEFAULT '',
    last_ignored_reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_workflow_triggers_workflow ON workflow_triggers(workflow_id);

CREATE TABLE IF NOT EXISTS workflow_schedules (
    schedule_id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    recurrence TEXT NOT NULL,
    next_run TEXT,
    last_run TEXT,
    missed_run_policy TEXT NOT NULL DEFAULT 'skip',
    overlap_policy TEXT NOT NULL DEFAULT 'skip',
    enabled INTEGER NOT NULL DEFAULT 1,
    health_state TEXT NOT NULL DEFAULT 'healthy',
    validation_state TEXT NOT NULL DEFAULT 'valid',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_workflow_schedules_next ON workflow_schedules(next_run);

CREATE TABLE IF NOT EXISTS workflow_runs (
    run_id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    workflow_version TEXT NOT NULL,
    trigger_id TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'created',
    current_node TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL DEFAULT '',
    ended_at TEXT NOT NULL DEFAULT '',
    duration_seconds REAL NOT NULL DEFAULT 0,
    trigger_context TEXT NOT NULL DEFAULT '{}',
    inputs TEXT NOT NULL DEFAULT '{}',
    outputs TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT '',
    error_code TEXT NOT NULL DEFAULT '',
    retry_count INTEGER NOT NULL DEFAULT 0,
    cancellation_state TEXT NOT NULL DEFAULT 'none',
    trace_id TEXT NOT NULL DEFAULT '',
    node_states TEXT NOT NULL DEFAULT '{}',
    variables TEXT NOT NULL DEFAULT '{}',
    idempotency_key TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_workflow ON workflow_runs(workflow_id);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_state ON workflow_runs(state);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_created ON workflow_runs(created_at);

CREATE TABLE IF NOT EXISTS workflow_action_executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    started_at TEXT NOT NULL DEFAULT '',
    ended_at TEXT NOT NULL DEFAULT '',
    result TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT '',
    retry_count INTEGER NOT NULL DEFAULT 0,
    idempotency_key TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_action_runs ON workflow_action_executions(run_id);

CREATE TABLE IF NOT EXISTS workflow_traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    node_id TEXT NOT NULL DEFAULT '',
    action_id TEXT NOT NULL DEFAULT '',
    event_type TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    duration_ms REAL NOT NULL DEFAULT 0,
    state_transition TEXT NOT NULL DEFAULT '',
    error_code TEXT NOT NULL DEFAULT '',
    retry_state TEXT NOT NULL DEFAULT '',
    safe_summary TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_traces_run ON workflow_traces(run_id);
CREATE INDEX IF NOT EXISTS idx_traces_trace ON workflow_traces(trace_id);

CREATE TABLE IF NOT EXISTS workflow_approvals (
    approval_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    action TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    risk TEXT NOT NULL DEFAULT 'low',
    scope TEXT NOT NULL DEFAULT '',
    project TEXT NOT NULL DEFAULT '',
    side_effects TEXT NOT NULL DEFAULT '',
    arguments_hash TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'pending',
    expires_at TEXT NOT NULL DEFAULT '',
    requested_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    resolved_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_approvals_run ON workflow_approvals(run_id);

CREATE TABLE IF NOT EXISTS workflow_user_inputs (
    input_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    prompt TEXT NOT NULL,
    schema TEXT NOT NULL DEFAULT '{}',
    state TEXT NOT NULL DEFAULT 'pending',
    response TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_inputs_run ON workflow_user_inputs(run_id);

CREATE TABLE IF NOT EXISTS workflow_idempotency (
    idempotency_key TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    action TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'global',
    state TEXT NOT NULL DEFAULT 'completed',
    result TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_idem_run ON workflow_idempotency(run_id);

CREATE TABLE IF NOT EXISTS workflow_locks (
    lock_id TEXT PRIMARY KEY,
    resource TEXT NOT NULL,
    lock_type TEXT NOT NULL DEFAULT 'exclusive',
    owner TEXT NOT NULL,
    run_id TEXT NOT NULL DEFAULT '',
    lease_until TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_locks_resource ON workflow_locks(resource);

CREATE TABLE IF NOT EXISTS workflow_activity (
    event_id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    level TEXT NOT NULL DEFAULT 'info',
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wf_activity_workflow ON workflow_activity(workflow_id);
"""


class AutomationStorage:
    """Owning SQLite storage for the Automation and Workflow Platform."""

    def __init__(self, data_dir: str) -> None:
        self._data_dir = Path(data_dir)
        self._path = self._data_dir / "automation.db"
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
                "INSERT OR REPLACE INTO automation_meta (key, value) VALUES (?, ?)",
                SCHEMA_VERSION_ROW,
            )
            connection.commit()

    def _verify_or_migrate(self, connection: sqlite3.Connection) -> None:
        try:
            version = connection.execute(
                "SELECT value FROM automation_meta WHERE key = 'automation_schema_version'"
            ).fetchone()
        except sqlite3.OperationalError:
            version = None
        if version is not None:
            current = int(version["value"])
            if current > STORAGE_VERSION:
                raise RuntimeError(
                    "automation storage version %d is newer than supported version %d"
                    % (current, STORAGE_VERSION)
                )
            if current < STORAGE_VERSION:
                raise RuntimeError(
                    "automation storage version %d predates supported version %d; migration required"
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
            "automation-%s.db" % hashlib.sha256(os.urandom(4)).hexdigest()[:8]
        )
        with self._lock:
            try:
                connection = self.connect()
                connection.execute("VACUUM INTO ?", (str(target),))
            except sqlite3.OperationalError:
                return None
        return str(target)