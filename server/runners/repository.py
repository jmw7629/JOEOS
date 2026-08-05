"""CRUD for the runner execution plane."""

from __future__ import annotations

import sqlite3
from typing import Callable, List, Optional
from uuid import UUID

from .storage import (
    ArtifactRecord,
    EnrollmentChallengeRecord,
    ExecutionJobRecord,
    ExecutorDefinitionRecord,
    RunnerConnectionRecord,
    RunnerKeyRecord,
    RunnerRecord,
    SecretLeaseRecord,
    SecretReferenceRecord,
    SQLiteRunnerSchema,
    TERMINAL_JOB_STATES,
)


class SQLiteRunnerStore(SQLiteRunnerSchema):
    # ------------------------------------------------------------------
    # Runners
    # ------------------------------------------------------------------

    def create_runner(self, record: RunnerRecord) -> RunnerRecord:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO runner_definitions(
                        id, key, installation_id, organization_id, workspace_id, display_name,
                        machine_identity, machine_fingerprint, operating_system, architecture,
                        runner_version, protocol_version, private_network_identity,
                        allowed_executors, denied_executors, allowed_roots,
                        max_concurrent_jobs, max_job_runtime_ms, artifact_limits,
                        status, health, enrolled_at, last_seen_at, revision
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        str(record.id), record.key, str(record.installation_id),
                        str(record.organization_id), str(record.workspace_id),
                        record.display_name, record.machine_identity,
                        record.machine_fingerprint, record.operating_system,
                        record.architecture, record.runner_version, record.protocol_version,
                        record.private_network_identity, record.allowed_executors,
                        record.denied_executors, record.allowed_roots,
                        record.max_concurrent_jobs, record.max_job_runtime_ms,
                        record.artifact_limits, record.status, record.health,
                        record.enrolled_at, record.last_seen_at,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return record

    def get_runner(self, runner_id: UUID) -> Optional[RunnerRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM runner_definitions WHERE id = ?", (str(runner_id),)
            ).fetchone()
        return _runner(row) if row is not None else None

    def get_runner_by_key(self, key: str) -> Optional[RunnerRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM runner_definitions WHERE key = ?", (key,)
            ).fetchone()
        return _runner(row) if row is not None else None

    def list_runners(self, workspace_id: Optional[UUID] = None) -> List[RunnerRecord]:
        with self._connection_factory() as connection:
            if workspace_id is None:
                rows = connection.execute(
                    "SELECT * FROM runner_definitions ORDER BY key"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM runner_definitions WHERE workspace_id = ? ORDER BY key",
                    (str(workspace_id),),
                ).fetchall()
        return [_runner(row) for row in rows]

    def update_runner_state(self, runner_id: UUID, status: str, health: str, now: int,
                            last_seen_at: Optional[int] = None) -> bool:
        with self._connection_factory() as connection:
            cursor = connection.execute(
                """
                UPDATE runner_definitions
                SET status=?, health=?, last_seen_at=COALESCE(?, last_seen_at),
                    revoked_at=CASE WHEN ? = 'revoked' THEN ? ELSE revoked_at END,
                    revision=revision+1
                WHERE id=?
                """,
                (status, health, last_seen_at, status, now, str(runner_id)),
            )
            connection.commit()
            return cursor.rowcount == 1

    # ------------------------------------------------------------------
    # Runner keys
    # ------------------------------------------------------------------

    def add_runner_key(self, key: RunnerKeyRecord) -> None:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO runner_keys(
                    runner_id, key_identifier, public_key, purpose, created_at,
                    expires_at, rotation_state, revision
                ) VALUES (?, ?, ?, ?, ?, ?, 'active', 1)
                """,
                (
                    str(key.runner_id), key.key_identifier, key.public_key, key.purpose,
                    key.created_at, key.expires_at,
                ),
            )
            connection.commit()

    def get_active_key(self, runner_id: UUID) -> Optional[RunnerKeyRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                """
                SELECT * FROM runner_keys
                WHERE runner_id = ? AND rotation_state = 'active' AND revoked_at IS NULL
                ORDER BY created_at DESC LIMIT 1
                """,
                (str(runner_id),),
            ).fetchone()
        return _runner_key(row) if row is not None else None

    def rotate_key(self, runner_id: UUID, now: int) -> bool:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE runner_keys SET rotation_state='rotated', revoked_at=?
                WHERE runner_id=? AND rotation_state='active' AND revoked_at IS NULL
                """,
                (now, str(runner_id)),
            )
            connection.commit()
            return cursor.rowcount >= 1

    # ------------------------------------------------------------------
    # Enrollment challenges
    # ------------------------------------------------------------------

    def create_enrollment_challenge(self, challenge: EnrollmentChallengeRecord) -> EnrollmentChallengeRecord:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO runner_enrollment_challenges(
                    id, installation_id, organization_id, workspace_id, purpose,
                    nonce, expected_fingerprint, issued_at, expires_at, state, revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', 1)
                """,
                (
                    str(challenge.id), str(challenge.installation_id),
                    str(challenge.organization_id), str(challenge.workspace_id),
                    challenge.purpose, challenge.nonce, challenge.expected_fingerprint,
                    challenge.issued_at, challenge.expires_at,
                ),
            )
            connection.commit()
        return challenge

    def get_enrollment_challenge(self, challenge_id: UUID) -> Optional[EnrollmentChallengeRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM runner_enrollment_challenges WHERE id = ?", (str(challenge_id),)
            ).fetchone()
        return _enrollment_challenge(row) if row is not None else None

    def update_enrollment_challenge(self, challenge_id: UUID, state: str) -> bool:
        with self._connection_factory() as connection:
            cursor = connection.execute(
                "UPDATE runner_enrollment_challenges SET state=? WHERE id=? AND state='open'",
                (state, str(challenge_id)),
            )
            connection.commit()
            return cursor.rowcount == 1

    def list_pending_enrollments(self) -> List[EnrollmentChallengeRecord]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM runner_enrollment_challenges WHERE state='open' ORDER BY issued_at DESC"
            ).fetchall()
        return [_enrollment_challenge(row) for row in rows]

    # ------------------------------------------------------------------
    # Connections
    # ------------------------------------------------------------------

    def create_connection(self, record: RunnerConnectionRecord) -> RunnerConnectionRecord:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO runner_connections(
                    id, runner_id, generation, connected_at, last_heartbeat_at,
                    protocol_version, runner_version, catalog_digest, source_identity,
                    status, revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 1)
                """,
                (
                    str(record.id), str(record.runner_id), record.generation,
                    record.connected_at, record.last_heartbeat_at, record.protocol_version,
                    record.runner_version, record.catalog_digest, record.source_identity,
                ),
            )
            connection.commit()
        return record

    def close_connections(self, runner_id: UUID, now: int) -> int:
        with self._connection_factory() as connection:
            cursor = connection.execute(
                """
                UPDATE runner_connections SET status='revoked', disconnected_at=COALESCE(disconnected_at, ?)
                WHERE runner_id=? AND status='active'
                """,
                (now, str(runner_id)),
            )
            connection.commit()
            return max(0, cursor.rowcount)

    def has_active_connection(self, runner_id: UUID) -> bool:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT 1 FROM runner_connections WHERE runner_id=? AND status='active' LIMIT 1",
                (str(runner_id),),
            ).fetchone()
        return row is not None

    def update_heartbeat(self, connection_id: UUID, now: int) -> bool:
        with self._connection_factory() as connection:
            cursor = connection.execute(
                "UPDATE runner_connections SET last_heartbeat_at=? WHERE id=? AND status='active'",
                (now, str(connection_id)),
            )
            connection.commit()
            return cursor.rowcount == 1

    def get_active_connection(self, runner_id: UUID) -> Optional[RunnerConnectionRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM runner_connections WHERE runner_id=? AND status='active' ORDER BY generation DESC LIMIT 1",
                (str(runner_id),),
            ).fetchone()
        return _connection(row) if row is not None else None

    # ------------------------------------------------------------------
    # Executors
    # ------------------------------------------------------------------

    def register_executor(self, record: ExecutorDefinitionRecord) -> ExecutorDefinitionRecord:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT revision FROM runner_executor_definitions WHERE key=?", (record.key,)
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO runner_executor_definitions(
                        id, key, display_name, version, supported_os, supported_arch,
                        accepted_tools, input_schema, target_schema, output_schema,
                        risk_floor, timeout_min_ms, timeout_max_ms, artifact_policy,
                        environment_policy, network_policy, filesystem_policy,
                        secret_policy, cancellation, idempotency, status,
                        implementation_digest, revision
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, 1)
                    """,
                    (
                        str(record.id), record.key, record.display_name, record.version,
                        record.supported_os, record.supported_arch, record.accepted_tools,
                        record.input_schema, record.target_schema, record.output_schema,
                        record.risk_floor, record.timeout_min_ms, record.timeout_max_ms,
                        record.artifact_policy, record.environment_policy,
                        record.network_policy, record.filesystem_policy,
                        record.secret_policy, 1 if record.cancellation else 0,
                        1 if record.idempotency else 0, record.implementation_digest,
                    ),
                )
            else:
                revision = int(row["revision"]) + 1
                connection.execute(
                    """
                    UPDATE runner_executor_definitions
                    SET display_name=?, version=?, accepted_tools=?, input_schema=?,
                        target_schema=?, output_schema=?, risk_floor=?, timeout_min_ms=?,
                        timeout_max_ms=?, artifact_policy=?, environment_policy=?,
                        network_policy=?, filesystem_policy=?, secret_policy=?,
                        cancellation=?, idempotency=?, implementation_digest=?,
                        revision=?
                    WHERE key=?
                    """,
                    (
                        record.display_name, record.version, record.accepted_tools,
                        record.input_schema, record.target_schema, record.output_schema,
                        record.risk_floor, record.timeout_min_ms, record.timeout_max_ms,
                        record.artifact_policy, record.environment_policy,
                        record.network_policy, record.filesystem_policy,
                        record.secret_policy, 1 if record.cancellation else 0,
                        1 if record.idempotency else 0, record.implementation_digest,
                        revision, record.key,
                    ),
                )
            connection.commit()
        return record

    def get_executor(self, executor_id: UUID) -> Optional[ExecutorDefinitionRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM runner_executor_definitions WHERE id=?", (str(executor_id),)
            ).fetchone()
        return _executor(row) if row is not None else None

    def get_executor_by_key(self, key: str) -> Optional[ExecutorDefinitionRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM runner_executor_definitions WHERE key=?", (key,)
            ).fetchone()
        return _executor(row) if row is not None else None

    def list_executors(self) -> List[ExecutorDefinitionRecord]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM runner_executor_definitions ORDER BY key"
            ).fetchall()
        return [_executor(row) for row in rows]

    def set_executor_state(self, executor_id: UUID, status: str, now: int) -> bool:
        with self._connection_factory() as connection:
            cursor = connection.execute(
                "UPDATE runner_executor_definitions SET status=?, revision=revision+1 WHERE id=?",
                (status, str(executor_id)),
            )
            connection.commit()
            return cursor.rowcount == 1

    # ------------------------------------------------------------------
    # Execution jobs
    # ------------------------------------------------------------------

    def create_job(self, record: ExecutionJobRecord) -> ExecutionJobRecord:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO runner_execution_jobs(
                        id, organization_id, workspace_id, proposal_id, proposal_digest,
                        policy_decision_id, policy_digest, approval_ids, approval_digests,
                        tool_id, tool_version, executor_id, executor_version, runner_id,
                        parameters, target, payload, payload_digest, requested_by,
                        trace_id, idempotency_key, priority, state, requested_at, revision
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_revalidation', ?, 1)
                    """,
                    (
                        str(record.id), str(record.organization_id), str(record.workspace_id),
                        str(record.proposal_id), record.proposal_digest,
                        str(record.policy_decision_id), record.policy_digest,
                        record.approval_ids, record.approval_digests,
                        str(record.tool_id), record.tool_version,
                        str(record.executor_id), record.executor_version,
                        str(record.runner_id), record.parameters, record.target,
                        record.payload, record.payload_digest, str(record.requested_by),
                        record.trace_id, record.idempotency_key, record.priority,
                        record.requested_at,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return record

    def get_job(self, job_id: UUID) -> Optional[ExecutionJobRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM runner_execution_jobs WHERE id=?", (str(job_id),)
            ).fetchone()
        return _job(row) if row is not None else None

    def get_job_by_idempotency(self, key: str) -> Optional[ExecutionJobRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM runner_execution_jobs WHERE idempotency_key=?", (key,)
            ).fetchone()
        return _job(row) if row is not None else None

    def list_jobs(self, workspace_id: UUID, state: Optional[str] = None, limit: int = 200) -> List[ExecutionJobRecord]:
        with self._connection_factory() as connection:
            if state is None:
                rows = connection.execute(
                    "SELECT * FROM runner_execution_jobs WHERE workspace_id=? ORDER BY requested_at DESC LIMIT ?",
                    (str(workspace_id), max(1, min(limit, 500))),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM runner_execution_jobs WHERE workspace_id=? AND state=? ORDER BY requested_at DESC LIMIT ?",
                    (str(workspace_id), state, max(1, min(limit, 500))),
                ).fetchall()
        return [_job(row) for row in rows]

    def transition_job(self, job_id: UUID, state: str, *, now: int, **fields) -> bool:
        """Centralized terminal-safe job transition."""
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT state FROM runner_execution_jobs WHERE id=?", (str(job_id),)
            ).fetchone()
            if row is None or str(row["state"]) in TERMINAL_JOB_STATES:
                return False
            sets = []
            values = []
            for key, value in fields.items():
                sets.append("%s=?" % key)
                values.append(value)
            sets.append("completed_at=CASE WHEN ? IN ('succeeded','failed','cancelled','timed_out','interrupted','lease_expired','runner_revoked','result_validation_failed','quarantined') THEN ? ELSE completed_at END")
            values += [state, now]
            values.append(str(job_id))
            cursor = connection.execute(
                "UPDATE runner_execution_jobs SET state=?, %s, revision=revision+1 WHERE id=? AND state NOT IN (%s)"
                % (", ".join(sets), ", ".join("'%s'" % s for s in TERMINAL_JOB_STATES)),
                [state] + values,
            )
            connection.commit()
            return cursor.rowcount == 1

    def lease_job(self, job_id: UUID, runner_id: UUID, connection_id: UUID, generation: int,
                  now: int, lease_ms: int) -> bool:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT state, lease_generation FROM runner_execution_jobs WHERE id=?",
                (str(job_id),),
            ).fetchone()
            if row is None or str(row["state"]) in TERMINAL_JOB_STATES:
                return False
            new_generation = generation if str(row["lease_generation"]) == "0" else int(row["lease_generation"]) + 1
            cursor = connection.execute(
                """
                UPDATE runner_execution_jobs
                SET state='leased', lease_generation=?, lease_owner=?,
                    lease_issued_at=?, lease_expires_at=?, dispatched_at=COALESCE(dispatched_at, ?),
                    revision=revision+1
                WHERE id=? AND state='queued'
                """,
                (new_generation, str(connection_id), now, now + lease_ms, now, str(job_id)),
            )
            connection.commit()
            return cursor.rowcount == 1

    def expire_stale_leases(self, now: int) -> int:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT id FROM runner_execution_jobs WHERE state IN ('leased','acknowledged','running') AND lease_expires_at > 0 AND lease_expires_at < ?",
                (now,),
            ).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE runner_execution_jobs SET state='lease_expired', completed_at=?, revision=revision+1 WHERE id=? AND state NOT IN (%s)"
                    % ", ".join("'%s'" % s for s in TERMINAL_JOB_STATES),
                    (now, str(row["id"])),
                )
            connection.commit()
            return len(rows)

    def recover_stale_jobs(self, now: int) -> int:
        with self._connection_factory() as connection:
            cursor = connection.execute(
                """
                UPDATE runner_execution_jobs SET state='interrupted',
                    completed_at=COALESCE(completed_at, ?), terminal_classification='interrupted_by_restart',
                    revision=revision+1
                WHERE state IN ('pending_revalidation','queued','leased','acknowledged','running','cancellation_requested')
                """,
                (now,),
            )
            connection.commit()
            return max(0, cursor.rowcount)

    def jobs_needing_cancel(self, now: int) -> List[ExecutionJobRecord]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM runner_execution_jobs WHERE state='cancellation_requested' AND completed_at IS NULL"
            ).fetchall()
        return [_job(row) for row in rows]

    # ------------------------------------------------------------------
    # Secrets
    # ------------------------------------------------------------------

    def create_secret_reference(self, record: SecretReferenceRecord) -> SecretReferenceRecord:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO secret_references(
                    id, organization_id, workspace_id, key, provider_type, purpose,
                    allowed_tools, allowed_executors, allowed_runners, allowed_targets,
                    status, created_at, updated_at, revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, 1)
                """,
                (
                    str(record.id), str(record.organization_id), str(record.workspace_id),
                    record.key, record.provider_type, record.purpose, record.allowed_tools,
                    record.allowed_executors, record.allowed_runners, record.allowed_targets,
                    record.created_at, record.created_at,
                ),
            )
            connection.commit()
        return record

    def get_secret_reference(self, reference_id: UUID) -> Optional[SecretReferenceRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM secret_references WHERE id=?", (str(reference_id),)
            ).fetchone()
        return _secret_reference(row) if row is not None else None

    def list_secret_references(self, workspace_id: UUID) -> List[SecretReferenceRecord]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM secret_references WHERE workspace_id=? ORDER BY key",
                (str(workspace_id),),
            ).fetchall()
        return [_secret_reference(row) for row in rows]

    def create_secret_lease(self, lease: SecretLeaseRecord) -> SecretLeaseRecord:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO secret_leases(
                    id, reference_id, job_id, runner_id, executor_id, purpose,
                    issued_at, expires_at, revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    str(lease.id), str(lease.reference_id), str(lease.job_id),
                    str(lease.runner_id), str(lease.executor_id), lease.purpose,
                    lease.issued_at, lease.expires_at,
                ),
            )
            connection.commit()
        return lease

    def revoke_leases_for_job(self, job_id: UUID, now: int) -> int:
        with self._connection_factory() as connection:
            cursor = connection.execute(
                "UPDATE secret_leases SET revoked_at=COALESCE(revoked_at, ?) WHERE job_id=? AND revoked_at IS NULL",
                (now, str(job_id)),
            )
            connection.commit()
            return max(0, cursor.rowcount)

    def revoke_leases_for_runner(self, runner_id: UUID, now: int) -> int:
        with self._connection_factory() as connection:
            cursor = connection.execute(
                "UPDATE secret_leases SET revoked_at=COALESCE(revoked_at, ?) WHERE runner_id=? AND revoked_at IS NULL",
                (now, str(runner_id)),
            )
            connection.commit()
            return max(0, cursor.rowcount)

    def get_active_lease(self, reference_id: UUID, job_id: UUID) -> Optional[SecretLeaseRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM secret_leases WHERE reference_id=? AND job_id=? AND revoked_at IS NULL LIMIT 1",
                (str(reference_id), str(job_id)),
            ).fetchone()
        return _secret_lease(row) if row is not None else None

    # ------------------------------------------------------------------
    # Artifacts
    # ------------------------------------------------------------------

    def create_artifact(self, record: ArtifactRecord) -> ArtifactRecord:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO runner_artifacts(
                    id, job_id, runner_id, artifact_type, media_type, filename, description,
                    byte_size, sha256, storage_reference, sensitivity, created_at, expires_at, revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    str(record.id), str(record.job_id), str(record.runner_id),
                    record.artifact_type, record.media_type, record.filename,
                    record.description, record.byte_size, record.sha256,
                    record.storage_reference, record.sensitivity,
                    record.created_at, record.expires_at,
                ),
            )
            connection.commit()
        return record

    def get_artifact(self, artifact_id: UUID) -> Optional[ArtifactRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM runner_artifacts WHERE id=?", (str(artifact_id),)
            ).fetchone()
        return _artifact(row) if row is not None else None

    def list_artifacts(self, job_id: UUID) -> List[ArtifactRecord]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM runner_artifacts WHERE job_id=? ORDER BY created_at",
                (str(job_id),),
            ).fetchall()
        return [_artifact(row) for row in rows]

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def append_event(self, event_type: str, *, runner_id, job_id, organization_id, workspace_id, now, message=""):
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO runner_events(
                    event_type, runner_id, job_id, organization_id, workspace_id, occurred_at, message
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (event_type, str(runner_id) if runner_id else None,
                 str(job_id) if job_id else None, str(organization_id), str(workspace_id),
                 now, message[:480]),
            )

    def fetch_events_after(self, cursor: int, workspace_id: UUID, limit: int = 100):
        with self._connection_factory() as connection:
            rows = connection.execute(
                """
                SELECT * FROM runner_events
                WHERE event_id > ? AND workspace_id = ?
                ORDER BY event_id ASC LIMIT ?
                """,
                (max(0, int(cursor)), str(workspace_id), max(1, min(limit, 200))),
            ).fetchall()
        return [dict(row) for row in rows]


def _runner(row: sqlite3.Row) -> RunnerRecord:
    return RunnerRecord(
        id=UUID(str(row["id"])), key=str(row["key"]),
        installation_id=UUID(str(row["installation_id"])),
        organization_id=UUID(str(row["organization_id"])),
        workspace_id=UUID(str(row["workspace_id"])),
        display_name=str(row["display_name"]), machine_identity=str(row["machine_identity"]),
        machine_fingerprint=str(row["machine_fingerprint"]),
        operating_system=str(row["operating_system"]), architecture=str(row["architecture"]),
        runner_version=str(row["runner_version"]), protocol_version=int(row["protocol_version"]),
        private_network_identity=str(row["private_network_identity"]),
        allowed_executors=str(row["allowed_executors"]), denied_executors=str(row["denied_executors"]),
        allowed_roots=str(row["allowed_roots"]),
        max_concurrent_jobs=int(row["max_concurrent_jobs"]),
        max_job_runtime_ms=int(row["max_job_runtime_ms"]),
        artifact_limits=str(row["artifact_limits"]), status=str(row["status"]),
        health=str(row["health"]), enrolled_at=int(row["enrolled_at"]),
        last_seen_at=int(row["last_seen_at"]),
        revoked_at=int(row["revoked_at"]) if row["revoked_at"] is not None else None,
        revision=int(row["revision"]),
    )


def _runner_key(row: sqlite3.Row) -> RunnerKeyRecord:
    return RunnerKeyRecord(
        runner_id=UUID(str(row["runner_id"])), key_identifier=str(row["key_identifier"]),
        public_key=str(row["public_key"]), purpose=str(row["purpose"]),
        created_at=int(row["created_at"]),
        expires_at=int(row["expires_at"]) if row["expires_at"] is not None else None,
        rotation_state=str(row["rotation_state"]),
        revoked_at=int(row["revoked_at"]) if row["revoked_at"] is not None else None,
        revision=int(row["revision"]),
    )


def _enrollment_challenge(row: sqlite3.Row) -> EnrollmentChallengeRecord:
    return EnrollmentChallengeRecord(
        id=UUID(str(row["id"])), installation_id=UUID(str(row["installation_id"])),
        organization_id=UUID(str(row["organization_id"])),
        workspace_id=UUID(str(row["workspace_id"])), purpose=str(row["purpose"]),
        nonce=str(row["nonce"]), expected_fingerprint=str(row["expected_fingerprint"]),
        issued_at=int(row["issued_at"]), expires_at=int(row["expires_at"]),
        state=str(row["state"]), revision=int(row["revision"]),
    )


def _connection(row: sqlite3.Row) -> RunnerConnectionRecord:
    return RunnerConnectionRecord(
        id=UUID(str(row["id"])), runner_id=UUID(str(row["runner_id"])),
        generation=int(row["generation"]), connected_at=int(row["connected_at"]),
        last_heartbeat_at=int(row["last_heartbeat_at"]),
        disconnected_at=int(row["disconnected_at"]) if row["disconnected_at"] is not None else None,
        protocol_version=int(row["protocol_version"]), runner_version=str(row["runner_version"]),
        catalog_digest=str(row["catalog_digest"]), source_identity=str(row["source_identity"]),
        status=str(row["status"]), revision=int(row["revision"]),
    )


def _executor(row: sqlite3.Row) -> ExecutorDefinitionRecord:
    return ExecutorDefinitionRecord(
        id=UUID(str(row["id"])), key=str(row["key"]), display_name=str(row["display_name"]),
        version=str(row["version"]), supported_os=str(row["supported_os"]),
        supported_arch=str(row["supported_arch"]), accepted_tools=str(row["accepted_tools"]),
        input_schema=str(row["input_schema"]), target_schema=str(row["target_schema"]),
        output_schema=str(row["output_schema"]), risk_floor=str(row["risk_floor"]),
        timeout_min_ms=int(row["timeout_min_ms"]), timeout_max_ms=int(row["timeout_max_ms"]),
        artifact_policy=str(row["artifact_policy"]), environment_policy=str(row["environment_policy"]),
        network_policy=str(row["network_policy"]), filesystem_policy=str(row["filesystem_policy"]),
        secret_policy=str(row["secret_policy"]), cancellation=bool(row["cancellation"]),
        idempotency=bool(row["idempotency"]), status=str(row["status"]),
        implementation_digest=str(row["implementation_digest"]), revision=int(row["revision"]),
    )


def _job(row: sqlite3.Row) -> ExecutionJobRecord:
    return ExecutionJobRecord(
        id=UUID(str(row["id"])), organization_id=UUID(str(row["organization_id"])),
        workspace_id=UUID(str(row["workspace_id"])),
        proposal_id=UUID(str(row["proposal_id"])), proposal_digest=str(row["proposal_digest"]),
        policy_decision_id=UUID(str(row["policy_decision_id"])), policy_digest=str(row["policy_digest"]),
        approval_ids=str(row["approval_ids"]), approval_digests=str(row["approval_digests"]),
        tool_id=UUID(str(row["tool_id"])), tool_version=str(row["tool_version"]),
        executor_id=UUID(str(row["executor_id"])), executor_version=str(row["executor_version"]),
        runner_id=UUID(str(row["runner_id"])), parameters=str(row["parameters"]),
        target=str(row["target"]), payload=str(row["payload"]), payload_digest=str(row["payload_digest"]),
        requested_by=UUID(str(row["requested_by"])),
        dispatched_by=UUID(str(row["dispatched_by"])) if row["dispatched_by"] else None,
        trace_id=str(row["trace_id"]), idempotency_key=str(row["idempotency_key"]),
        priority=int(row["priority"]), state=str(row["state"]),
        lease_generation=int(row["lease_generation"]), lease_owner=str(row["lease_owner"]),
        lease_issued_at=int(row["lease_issued_at"]), lease_expires_at=int(row["lease_expires_at"]),
        requested_at=int(row["requested_at"]),
        dispatched_at=int(row["dispatched_at"]) if row["dispatched_at"] is not None else None,
        acknowledged_at=int(row["acknowledged_at"]) if row["acknowledged_at"] is not None else None,
        started_at=int(row["started_at"]) if row["started_at"] is not None else None,
        cancellation_requested_at=int(row["cancellation_requested_at"]) if row["cancellation_requested_at"] is not None else None,
        completed_at=int(row["completed_at"]) if row["completed_at"] is not None else None,
        terminal_classification=str(row["terminal_classification"]),
        exit_classification=str(row["exit_classification"]),
        result_summary=str(row["result_summary"]), artifact_refs=str(row["artifact_refs"]),
        revision=int(row["revision"]),
    )


def _secret_reference(row: sqlite3.Row) -> SecretReferenceRecord:
    return SecretReferenceRecord(
        id=UUID(str(row["id"])), organization_id=UUID(str(row["organization_id"])),
        workspace_id=UUID(str(row["workspace_id"])), key=str(row["key"]),
        provider_type=str(row["provider_type"]), purpose=str(row["purpose"]),
        allowed_tools=str(row["allowed_tools"]), allowed_executors=str(row["allowed_executors"]),
        allowed_runners=str(row["allowed_runners"]), allowed_targets=str(row["allowed_targets"]),
        status=str(row["status"]), created_at=int(row["created_at"]),
        updated_at=int(row["updated_at"]), revision=int(row["revision"]),
    )


def _secret_lease(row: sqlite3.Row) -> SecretLeaseRecord:
    return SecretLeaseRecord(
        id=UUID(str(row["id"])), reference_id=UUID(str(row["reference_id"])),
        job_id=UUID(str(row["job_id"])), runner_id=UUID(str(row["runner_id"])),
        executor_id=UUID(str(row["executor_id"])), purpose=str(row["purpose"]),
        issued_at=int(row["issued_at"]), expires_at=int(row["expires_at"]),
        consumed_at=int(row["consumed_at"]) if row["consumed_at"] is not None else None,
        revoked_at=int(row["revoked_at"]) if row["revoked_at"] is not None else None,
        revision=int(row["revision"]),
    )


def _artifact(row: sqlite3.Row) -> ArtifactRecord:
    return ArtifactRecord(
        id=UUID(str(row["id"])), job_id=UUID(str(row["job_id"])),
        runner_id=UUID(str(row["runner_id"])), artifact_type=str(row["artifact_type"]),
        media_type=str(row["media_type"]), filename=str(row["filename"]),
        description=str(row["description"]), byte_size=int(row["byte_size"]),
        sha256=str(row["sha256"]), storage_reference=str(row["storage_reference"]),
        sensitivity=str(row["sensitivity"]), created_at=int(row["created_at"]),
        expires_at=int(row["expires_at"]), revision=int(row["revision"]),
    )
