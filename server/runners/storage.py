"""Durable SQLite schema for the signed private runner execution plane (P3C).

Runner definitions, runner signing keys, one-time enrollment challenges,
authenticated connections, health snapshots, the executor catalog, immutable
execution jobs, secret references and short-lived leases, and artifact metadata.
Secret values are never stored here; only opaque references.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Callable, List, Optional
from uuid import UUID

RUNNER_STATES = ("pending_enrollment", "active", "degraded", "incompatible", "disabled", "offline", "revoked", "quarantined")
HEALTH_STATES = ("unknown", "connecting", "healthy", "degraded", "unavailable", "incompatible", "unauthorized", "revoked")
JOB_STATES = (
    "pending_revalidation", "rejected", "queued", "leased", "acknowledged", "running",
    "cancellation_requested", "cancelled", "succeeded", "failed", "timed_out",
    "interrupted", "lease_expired", "runner_offline", "runner_revoked", "quarantined",
    "result_validation_failed",
)
TERMINAL_JOB_STATES = frozenset({
    "cancelled", "succeeded", "failed", "timed_out", "interrupted", "lease_expired",
    "runner_offline", "runner_revoked", "quarantined", "result_validation_failed", "rejected",
})
LEASE_GRACE_MS = 60_000


@dataclass(frozen=True)
class RunnerRecord:
    id: UUID
    key: str
    installation_id: UUID
    organization_id: UUID
    workspace_id: UUID
    display_name: str
    machine_identity: str
    machine_fingerprint: str
    operating_system: str
    architecture: str
    runner_version: str
    protocol_version: int
    private_network_identity: str
    allowed_executors: str
    denied_executors: str
    allowed_roots: str
    max_concurrent_jobs: int
    max_job_runtime_ms: int
    artifact_limits: str
    status: str
    health: str
    enrolled_at: int
    last_seen_at: int
    revoked_at: Optional[int]
    revision: int


@dataclass(frozen=True)
class RunnerKeyRecord:
    runner_id: UUID
    key_identifier: str
    public_key: str
    purpose: str
    created_at: int
    expires_at: Optional[int]
    rotation_state: str
    revoked_at: Optional[int]
    revision: int


@dataclass(frozen=True)
class EnrollmentChallengeRecord:
    id: UUID
    installation_id: UUID
    organization_id: UUID
    workspace_id: UUID
    purpose: str
    nonce: str
    expected_fingerprint: str
    issued_at: int
    expires_at: int
    state: str
    revision: int


@dataclass(frozen=True)
class RunnerConnectionRecord:
    id: UUID
    runner_id: UUID
    generation: int
    connected_at: int
    last_heartbeat_at: int
    disconnected_at: Optional[int]
    protocol_version: int
    runner_version: str
    catalog_digest: str
    source_identity: str
    status: str
    revision: int


@dataclass(frozen=True)
class ExecutorDefinitionRecord:
    id: UUID
    key: str
    display_name: str
    version: str
    supported_os: str
    supported_arch: str
    accepted_tools: str
    input_schema: str
    target_schema: str
    output_schema: str
    risk_floor: str
    timeout_min_ms: int
    timeout_max_ms: int
    artifact_policy: str
    environment_policy: str
    network_policy: str
    filesystem_policy: str
    secret_policy: str
    cancellation: bool
    idempotency: bool
    status: str
    implementation_digest: str
    revision: int


@dataclass(frozen=True)
class ExecutionJobRecord:
    id: UUID
    organization_id: UUID
    workspace_id: UUID
    proposal_id: UUID
    proposal_digest: str
    policy_decision_id: UUID
    policy_digest: str
    approval_ids: str
    approval_digests: str
    tool_id: UUID
    tool_version: str
    executor_id: UUID
    executor_version: str
    runner_id: UUID
    parameters: str
    target: str
    payload: str
    payload_digest: str
    requested_by: UUID
    dispatched_by: Optional[UUID]
    trace_id: str
    idempotency_key: str
    priority: int
    state: str
    lease_generation: int
    lease_owner: str
    lease_issued_at: int
    lease_expires_at: int
    requested_at: int
    dispatched_at: Optional[int]
    acknowledged_at: Optional[int]
    started_at: Optional[int]
    cancellation_requested_at: Optional[int]
    completed_at: Optional[int]
    terminal_classification: str
    exit_classification: str
    result_summary: str
    artifact_refs: str
    revision: int


@dataclass(frozen=True)
class SecretReferenceRecord:
    id: UUID
    organization_id: UUID
    workspace_id: UUID
    key: str
    provider_type: str
    purpose: str
    allowed_tools: str
    allowed_executors: str
    allowed_runners: str
    allowed_targets: str
    status: str
    created_at: int
    updated_at: int
    revision: int


@dataclass(frozen=True)
class SecretLeaseRecord:
    id: UUID
    reference_id: UUID
    job_id: UUID
    runner_id: UUID
    executor_id: UUID
    purpose: str
    issued_at: int
    expires_at: int
    consumed_at: Optional[int]
    revoked_at: Optional[int]
    revision: int


@dataclass(frozen=True)
class ArtifactRecord:
    id: UUID
    job_id: UUID
    runner_id: UUID
    artifact_type: str
    media_type: str
    filename: str
    description: str
    byte_size: int
    sha256: str
    storage_reference: str
    sensitivity: str
    created_at: int
    expires_at: int
    revision: int


class SQLiteRunnerSchema:
    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory

    def prepare(self) -> None:
        with self._connection_factory() as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runner_definitions (
                    id TEXT PRIMARY KEY,
                    key TEXT NOT NULL UNIQUE,
                    installation_id TEXT NOT NULL,
                    organization_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    machine_identity TEXT NOT NULL,
                    machine_fingerprint TEXT NOT NULL,
                    operating_system TEXT NOT NULL DEFAULT '',
                    architecture TEXT NOT NULL DEFAULT '',
                    runner_version TEXT NOT NULL DEFAULT '',
                    protocol_version INTEGER NOT NULL DEFAULT 1,
                    private_network_identity TEXT NOT NULL DEFAULT '',
                    allowed_executors TEXT NOT NULL DEFAULT '',
                    denied_executors TEXT NOT NULL DEFAULT '',
                    allowed_roots TEXT NOT NULL DEFAULT '',
                    max_concurrent_jobs INTEGER NOT NULL DEFAULT 1,
                    max_job_runtime_ms INTEGER NOT NULL DEFAULT 600000,
                    artifact_limits TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL CHECK(status IN (
                        'pending_enrollment','active','degraded','incompatible',
                        'disabled','offline','revoked','quarantined'
                    )),
                    health TEXT NOT NULL CHECK(health IN (
                        'unknown','connecting','healthy','degraded','unavailable',
                        'incompatible','unauthorized','revoked'
                    )),
                    enrolled_at INTEGER NOT NULL,
                    last_seen_at INTEGER NOT NULL,
                    revoked_at INTEGER,
                    revision INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS runner_keys (
                    runner_id TEXT NOT NULL REFERENCES runner_definitions(id),
                    key_identifier TEXT NOT NULL,
                    public_key TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER,
                    rotation_state TEXT NOT NULL DEFAULT 'active',
                    revoked_at INTEGER,
                    revision INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY(runner_id, key_identifier)
                );

                CREATE TABLE IF NOT EXISTS runner_enrollment_challenges (
                    id TEXT PRIMARY KEY,
                    installation_id TEXT NOT NULL,
                    organization_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    nonce TEXT NOT NULL UNIQUE,
                    expected_fingerprint TEXT NOT NULL,
                    issued_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('open','solved','expired','revoked')),
                    revision INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS runner_connections (
                    id TEXT PRIMARY KEY,
                    runner_id TEXT NOT NULL REFERENCES runner_definitions(id),
                    generation INTEGER NOT NULL DEFAULT 1,
                    connected_at INTEGER NOT NULL,
                    last_heartbeat_at INTEGER NOT NULL,
                    disconnected_at INTEGER,
                    protocol_version INTEGER NOT NULL DEFAULT 1,
                    runner_version TEXT NOT NULL DEFAULT '',
                    catalog_digest TEXT NOT NULL DEFAULT '',
                    source_identity TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL CHECK(status IN ('active','closed','revoked')),
                    revision INTEGER NOT NULL DEFAULT 1
                );

                CREATE INDEX IF NOT EXISTS idx_runner_connections_runner
                ON runner_connections(runner_id, status);

                CREATE TABLE IF NOT EXISTS runner_health (
                    runner_id TEXT NOT NULL REFERENCES runner_definitions(id),
                    timestamp INTEGER NOT NULL,
                    cpu_summary TEXT NOT NULL DEFAULT '',
                    memory_summary TEXT NOT NULL DEFAULT '',
                    disk_summary TEXT NOT NULL DEFAULT '',
                    load_summary TEXT NOT NULL DEFAULT '',
                    supported_executors TEXT NOT NULL DEFAULT '',
                    executor_health TEXT NOT NULL DEFAULT '',
                    running_jobs INTEGER NOT NULL DEFAULT 0,
                    version_compatible INTEGER NOT NULL DEFAULT 1,
                    diagnostics TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(runner_id, timestamp)
                );

                CREATE TABLE IF NOT EXISTS runner_executor_definitions (
                    id TEXT PRIMARY KEY,
                    key TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    version TEXT NOT NULL,
                    supported_os TEXT NOT NULL DEFAULT '',
                    supported_arch TEXT NOT NULL DEFAULT '',
                    accepted_tools TEXT NOT NULL DEFAULT '',
                    input_schema TEXT NOT NULL DEFAULT '{}',
                    target_schema TEXT NOT NULL DEFAULT '{}',
                    output_schema TEXT NOT NULL DEFAULT '{}',
                    risk_floor TEXT NOT NULL DEFAULT 'medium',
                    timeout_min_ms INTEGER NOT NULL DEFAULT 1000,
                    timeout_max_ms INTEGER NOT NULL DEFAULT 600000,
                    artifact_policy TEXT NOT NULL DEFAULT 'bounded',
                    environment_policy TEXT NOT NULL DEFAULT 'allowlisted',
                    network_policy TEXT NOT NULL DEFAULT 'blocked',
                    filesystem_policy TEXT NOT NULL DEFAULT 'restricted',
                    secret_policy TEXT NOT NULL DEFAULT 'no_injection',
                    cancellation INTEGER NOT NULL DEFAULT 1,
                    idempotency INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL CHECK(status IN ('active','disabled')),
                    implementation_digest TEXT NOT NULL DEFAULT '',
                    revision INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS runner_execution_jobs (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    proposal_id TEXT NOT NULL,
                    proposal_digest TEXT NOT NULL,
                    policy_decision_id TEXT NOT NULL,
                    policy_digest TEXT NOT NULL,
                    approval_ids TEXT NOT NULL DEFAULT '',
                    approval_digests TEXT NOT NULL DEFAULT '',
                    tool_id TEXT NOT NULL,
                    tool_version TEXT NOT NULL,
                    executor_id TEXT NOT NULL,
                    executor_version TEXT NOT NULL,
                    runner_id TEXT NOT NULL,
                    parameters TEXT NOT NULL,
                    target TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    payload_digest TEXT NOT NULL,
                    requested_by TEXT NOT NULL,
                    dispatched_by TEXT,
                    trace_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    priority INTEGER NOT NULL DEFAULT 0,
                    state TEXT NOT NULL CHECK(state IN (
                        'pending_revalidation','rejected','queued','leased','acknowledged','running',
                        'cancellation_requested','cancelled','succeeded','failed','timed_out',
                        'interrupted','lease_expired','runner_offline','runner_revoked','quarantined',
                        'result_validation_failed'
                    )),
                    lease_generation INTEGER NOT NULL DEFAULT 0,
                    lease_owner TEXT NOT NULL DEFAULT '',
                    lease_issued_at INTEGER NOT NULL DEFAULT 0,
                    lease_expires_at INTEGER NOT NULL DEFAULT 0,
                    requested_at INTEGER NOT NULL,
                    dispatched_at INTEGER,
                    acknowledged_at INTEGER,
                    started_at INTEGER,
                    cancellation_requested_at INTEGER,
                    completed_at INTEGER,
                    terminal_classification TEXT NOT NULL DEFAULT '',
                    exit_classification TEXT NOT NULL DEFAULT '',
                    result_summary TEXT NOT NULL DEFAULT '',
                    artifact_refs TEXT NOT NULL DEFAULT '',
                    revision INTEGER NOT NULL DEFAULT 1
                );

                CREATE INDEX IF NOT EXISTS idx_execution_jobs_workspace
                ON runner_execution_jobs(workspace_id, state);

                CREATE INDEX IF NOT EXISTS idx_execution_jobs_state
                ON runner_execution_jobs(state);

                CREATE TABLE IF NOT EXISTS secret_references (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    provider_type TEXT NOT NULL DEFAULT 'development',
                    purpose TEXT NOT NULL DEFAULT '',
                    allowed_tools TEXT NOT NULL DEFAULT '',
                    allowed_executors TEXT NOT NULL DEFAULT '',
                    allowed_runners TEXT NOT NULL DEFAULT '',
                    allowed_targets TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL CHECK(status IN ('active','disabled','revoked')),
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(organization_id, workspace_id, key)
                );

                CREATE TABLE IF NOT EXISTS secret_leases (
                    id TEXT PRIMARY KEY,
                    reference_id TEXT NOT NULL REFERENCES secret_references(id),
                    job_id TEXT NOT NULL,
                    runner_id TEXT NOT NULL,
                    executor_id TEXT NOT NULL,
                    purpose TEXT NOT NULL DEFAULT '',
                    issued_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    consumed_at INTEGER,
                    revoked_at INTEGER,
                    revision INTEGER NOT NULL DEFAULT 1
                );

                CREATE INDEX IF NOT EXISTS idx_secret_leases_job
                ON secret_leases(job_id);

                CREATE TABLE IF NOT EXISTS runner_artifacts (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    runner_id TEXT NOT NULL,
                    artifact_type TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    byte_size INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    storage_reference TEXT NOT NULL,
                    sensitivity TEXT NOT NULL DEFAULT 'restricted',
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1
                );

                CREATE INDEX IF NOT EXISTS idx_runner_artifacts_job
                ON runner_artifacts(job_id);

                CREATE TABLE IF NOT EXISTS runner_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    runner_id TEXT,
                    job_id TEXT,
                    organization_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    occurred_at INTEGER NOT NULL,
                    message TEXT NOT NULL DEFAULT ''
                );

                CREATE TRIGGER IF NOT EXISTS trg_runner_events_no_update
                BEFORE UPDATE ON runner_events
                BEGIN SELECT RAISE(ABORT, 'runner events are append-only'); END;

                CREATE TRIGGER IF NOT EXISTS trg_runner_events_no_delete
                BEFORE DELETE ON runner_events
                BEGIN SELECT RAISE(ABORT, 'runner events are append-only'); END;
                """
            )
            connection.commit()
