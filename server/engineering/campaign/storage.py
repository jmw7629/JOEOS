"""SQLite storage for the engineering campaign platform.

Versioned tables mirroring the authoritative campaign records. Every mutation is
transactional; checkpoints store a digest of the recorded state so a checkpoint
can be verified against later corruption.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .models import (
    CampaignRecord,
    CampaignState,
    EngineeringAttemptRecord,
    EngineeringBlockerRecord,
    EngineeringCheckpointRecord,
    RoadmapEntry,
    RoadmapEnvelope,
    StageName,
    WatchdogHeartbeatRecord,
    WorkPackageDefinition,
    WorkPackageRecord,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CampaignStore:
    schema_version = 1

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory

    def prepare(self) -> None:
        with self._connection_factory() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS engineering_campaigns (
                    campaign_id TEXT PRIMARY KEY,
                    key TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    repository_path TEXT NOT NULL,
                    base_branch TEXT NOT NULL,
                    integration_branch TEXT NOT NULL,
                    autonomy_policy_key TEXT NOT NULL,
                    state TEXT NOT NULL,
                    current_stage TEXT NOT NULL,
                    worktree_root TEXT,
                    max_parallel_packages INTEGER NOT NULL,
                    max_attempts_per_package INTEGER NOT NULL,
                    heartbeat_timeout_ms INTEGER NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 0,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_heartbeat_at TEXT,
                    completion_summary TEXT,
                    failure_reason TEXT,
                    schema_version INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS engineering_work_packages (
                    package_id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    acceptance_criteria TEXT NOT NULL,
                    owner_agent_key TEXT NOT NULL,
                    verifier_agent_key TEXT,
                    review_agent_key TEXT,
                    dependencies TEXT NOT NULL,
                    stage_order TEXT NOT NULL,
                    state TEXT NOT NULL,
                    current_stage TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    roadmap_order INTEGER NOT NULL DEFAULT 0,
                    priority INTEGER NOT NULL DEFAULT 100,
                    risk TEXT NOT NULL,
                    error_detail TEXT,
                    last_gate TEXT,
                    checkpoint_revision INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    schema_version INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(campaign_id, key)
                );

                CREATE TABLE IF NOT EXISTS engineering_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    package_id TEXT NOT NULL,
                    campaign_id TEXT NOT NULL,
                    attempt_number INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    started_by TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    summary TEXT,
                    evidence TEXT NOT NULL,
                    schema_version INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS engineering_checkpoints (
                    checkpoint_id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL,
                    package_id TEXT,
                    kind TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    state_snapshot_digest TEXT NOT NULL,
                    note TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    schema_version INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS engineering_blockers (
                    blocker_id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL,
                    package_id TEXT,
                    reason TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT,
                    resolved_by TEXT,
                    resolution TEXT,
                    schema_version INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS engineering_heartbeats (
                    heartbeat_id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    worker TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    schema_version INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS engineering_roadmap (
                    campaign_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    owner_agent_key TEXT NOT NULL,
                    verifier_agent_key TEXT,
                    review_agent_key TEXT,
                    dependencies TEXT NOT NULL,
                    acceptance_criteria TEXT NOT NULL,
                    roadmap_order INTEGER NOT NULL,
                    priority INTEGER NOT NULL,
                    risk TEXT NOT NULL,
                    stage_order TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    source TEXT NOT NULL,
                    schema_version INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY(campaign_id, key)
                );

                CREATE INDEX IF NOT EXISTS idx_work_packages_campaign ON engineering_work_packages(campaign_id);
                CREATE INDEX IF NOT EXISTS idx_attempts_package ON engineering_attempts(package_id);
                CREATE INDEX IF NOT EXISTS idx_attempts_campaign ON engineering_attempts(campaign_id);
                CREATE INDEX IF NOT EXISTS idx_checkpoints_campaign ON engineering_checkpoints(campaign_id);
                CREATE INDEX IF NOT EXISTS idx_blockers_campaign ON engineering_blockers(campaign_id);
                CREATE INDEX IF NOT EXISTS idx_heartbeats_campaign ON engineering_heartbeats(campaign_id);
                """
            )

    # ------------------------------------------------------------------
    # Campaigns
    # ------------------------------------------------------------------

    def create_campaign(self, record: CampaignRecord) -> CampaignRecord:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO engineering_campaigns(
                        campaign_id, key, title, description, repository_path, base_branch,
                        integration_branch, autonomy_policy_key, state, current_stage, worktree_root,
                        max_parallel_packages, max_attempts_per_package, heartbeat_timeout_ms,
                        revision, created_by, created_at, updated_at, last_heartbeat_at,
                        completion_summary, failure_reason, schema_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.campaign_id, record.key, record.title, record.description,
                        record.repository_path, record.base_branch, record.integration_branch,
                        record.autonomy_policy_key, record.state, record.current_stage,
                        record.worktree_root, record.max_parallel_packages,
                        record.max_attempts_per_package, record.heartbeat_timeout_ms,
                        record.revision, record.created_by, record.created_at, record.updated_at,
                        record.last_heartbeat_at, record.completion_summary, record.failure_reason,
                        self.schema_version,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return record

    def get_campaign(self, campaign_id: str) -> Optional[CampaignRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM engineering_campaigns WHERE campaign_id = ?", (campaign_id,)
            ).fetchone()
        return _campaign_from_row(row)

    def get_campaign_by_key(self, key: str) -> Optional[CampaignRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM engineering_campaigns WHERE key = ?", (key,)
            ).fetchone()
        return _campaign_from_row(row)

    def list_campaigns(self, limit: int = 100) -> Tuple[CampaignRecord, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM engineering_campaigns ORDER BY created_at DESC LIMIT ?",
                (max(1, min(500, limit)),),
            ).fetchall()
        return tuple(_campaign_from_row(row) for row in rows)

    def update_campaign_state(
        self,
        campaign_id: str,
        *,
        state: CampaignState,
        current_stage: Optional[StageName] = None,
        revision: Optional[int] = None,
        completion_summary: Optional[str] = None,
        failure_reason: Optional[str] = None,
        last_heartbeat_at: Optional[str] = None,
    ) -> Optional[CampaignRecord]:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    UPDATE engineering_campaigns SET state = ?, current_stage = ?,
                        revision = revision + ?, updated_at = ?,
                        completion_summary = COALESCE(?, completion_summary),
                        failure_reason = COALESCE(?, failure_reason),
                        last_heartbeat_at = COALESCE(?, last_heartbeat_at)
                    WHERE campaign_id = ?
                    """,
                    (
                        state,
                        current_stage or "queued",
                        revision or 0,
                        _now_iso(),
                        completion_summary,
                        failure_reason,
                        last_heartbeat_at,
                        campaign_id,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.get_campaign(campaign_id)

    def record_heartbeat(self, record: WatchdogHeartbeatRecord) -> None:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO engineering_heartbeats(
                        heartbeat_id, campaign_id, recorded_at, worker, detail, schema_version
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.heartbeat_id, record.campaign_id, record.recorded_at,
                        record.worker, record.detail, self.schema_version,
                    ),
                )
                connection.execute(
                    "UPDATE engineering_campaigns SET last_heartbeat_at = ?, updated_at = ? "
                    "WHERE campaign_id = ?",
                    (record.recorded_at, _now_iso(), record.campaign_id),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def latest_heartbeat(self, campaign_id: str) -> Optional[WatchdogHeartbeatRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM engineering_heartbeats WHERE campaign_id = ? ORDER BY recorded_at DESC LIMIT 1",
                (campaign_id,),
            ).fetchone()
        if row is None:
            return None
        return WatchdogHeartbeatRecord(
            heartbeat_id=row["heartbeat_id"], campaign_id=row["campaign_id"],
            recorded_at=row["recorded_at"], worker=row["worker"], detail=row["detail"],
        )

    # ------------------------------------------------------------------
    # Work packages
    # ------------------------------------------------------------------

    def create_work_package(self, campaign_id: str, definition: WorkPackageDefinition) -> WorkPackageRecord:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                package_id = _stable_package_id(campaign_id, definition.key)
                connection.execute(
                    """
                    INSERT INTO engineering_work_packages(
                        package_id, campaign_id, key, title, description, acceptance_criteria,
                        owner_agent_key, verifier_agent_key, review_agent_key, dependencies,
                        stage_order, state, current_stage, attempts, roadmap_order, priority,
                        risk, error_detail, last_gate, checkpoint_revision, created_at, updated_at,
                        schema_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', 'queued', 0, ?, ?, ?, NULL, NULL, 0, ?, ?, ?)
                    """,
                    (
                        package_id, campaign_id, definition.key, definition.title,
                        definition.description, json.dumps(list(definition.acceptance_criteria)),
                        definition.owner_agent_key, definition.verifier_agent_key,
                        definition.review_agent_key, json.dumps(list(definition.dependencies)),
                        json.dumps(list(definition.stage_order)),
                        definition.roadmap_order, definition.priority, definition.risk,
                        _now_iso(), _now_iso(), self.schema_version,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        record = self.get_work_package(package_id)
        assert record is not None
        return record

    def get_work_package(self, package_id: str) -> Optional[WorkPackageRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM engineering_work_packages WHERE package_id = ?", (package_id,)
            ).fetchone()
        return _package_from_row(row)

    def get_work_package_by_key(self, campaign_id: str, key: str) -> Optional[WorkPackageRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM engineering_work_packages WHERE campaign_id = ? AND key = ?",
                (campaign_id, key),
            ).fetchone()
        return _package_from_row(row)

    def list_work_packages(self, campaign_id: str) -> Tuple[WorkPackageRecord, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM engineering_work_packages WHERE campaign_id = ? "
                "ORDER BY roadmap_order ASC, priority ASC",
                (campaign_id,),
            ).fetchall()
        return tuple(_package_from_row(row) for row in rows)

    def update_work_package(
        self,
        package_id: str,
        *,
        state: Optional[str] = None,
        current_stage: Optional[str] = None,
        attempts: Optional[int] = None,
        error_detail: Optional[str] = None,
        last_gate: Optional[str] = None,
        checkpoint_revision: Optional[int] = None,
    ) -> Optional[WorkPackageRecord]:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                sets = []
                values: List = []
                if state is not None:
                    sets.append("state = ?")
                    values.append(state)
                if current_stage is not None:
                    sets.append("current_stage = ?")
                    values.append(current_stage)
                if attempts is not None:
                    sets.append("attempts = ?")
                    values.append(attempts)
                if error_detail is not None:
                    sets.append("error_detail = ?")
                    values.append(error_detail)
                if last_gate is not None:
                    sets.append("last_gate = ?")
                    values.append(last_gate)
                if checkpoint_revision is not None:
                    sets.append("checkpoint_revision = ?")
                    values.append(checkpoint_revision)
                sets.append("updated_at = ?")
                values.append(_now_iso())
                values.append(package_id)
                connection.execute(
                    "UPDATE engineering_work_packages SET %s WHERE package_id = ?" % ", ".join(sets),
                    values,
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.get_work_package(package_id)

    # ------------------------------------------------------------------
    # Attempts
    # ------------------------------------------------------------------

    def create_attempt(self, record: EngineeringAttemptRecord) -> EngineeringAttemptRecord:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO engineering_attempts(
                        attempt_id, package_id, campaign_id, attempt_number, state, started_by,
                        started_at, finished_at, summary, evidence, schema_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)
                    """,
                    (
                        record.attempt_id, record.package_id, record.campaign_id,
                        record.attempt_number, record.state, record.started_by,
                        record.started_at, json.dumps(list(record.evidence)), self.schema_version,
                    ),
                )
                connection.execute(
                    "UPDATE engineering_work_packages SET attempts = ?, updated_at = ? WHERE package_id = ?",
                    (record.attempt_number, _now_iso(), record.package_id),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return record

    def finish_attempt(self, attempt_id: str, *, state: str, summary: Optional[str],
                       evidence: Sequence[str]) -> Optional[EngineeringAttemptRecord]:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    UPDATE engineering_attempts SET state = ?, finished_at = ?,
                        summary = ?, evidence = ? WHERE attempt_id = ?
                    """,
                    (state, _now_iso(), summary, json.dumps(list(evidence)), attempt_id),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.get_attempt(attempt_id)

    def get_attempt(self, attempt_id: str) -> Optional[EngineeringAttemptRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM engineering_attempts WHERE attempt_id = ?", (attempt_id,)
            ).fetchone()
        if row is None:
            return None
        return EngineeringAttemptRecord(
            attempt_id=row["attempt_id"], package_id=row["package_id"],
            campaign_id=row["campaign_id"], attempt_number=row["attempt_number"],
            state=row["state"], started_by=row["started_by"], started_at=row["started_at"],
            finished_at=row["finished_at"], summary=row["summary"],
            evidence=tuple(json.loads(row["evidence"] or "[]")),
        )

    def list_attempts(self, package_id: str) -> Tuple[EngineeringAttemptRecord, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM engineering_attempts WHERE package_id = ? ORDER BY attempt_number ASC",
                (package_id,),
            ).fetchall()
        return tuple(
            EngineeringAttemptRecord(
                attempt_id=row["attempt_id"], package_id=row["package_id"],
                campaign_id=row["campaign_id"], attempt_number=row["attempt_number"],
                state=row["state"], started_by=row["started_by"], started_at=row["started_at"],
                finished_at=row["finished_at"], summary=row["summary"],
                evidence=tuple(json.loads(row["evidence"] or "[]")),
            )
            for row in rows
        )

    def attempts_for_campaign(self, campaign_id: str) -> Tuple[EngineeringAttemptRecord, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM engineering_attempts WHERE campaign_id = ? ORDER BY started_at ASC",
                (campaign_id,),
            ).fetchall()
        return tuple(
            EngineeringAttemptRecord(
                attempt_id=row["attempt_id"], package_id=row["package_id"],
                campaign_id=row["campaign_id"], attempt_number=row["attempt_number"],
                state=row["state"], started_by=row["started_by"], started_at=row["started_at"],
                finished_at=row["finished_at"], summary=row["summary"],
                evidence=tuple(json.loads(row["evidence"] or "[]")),
            )
            for row in rows
        )

    # ------------------------------------------------------------------
    # Checkpoints
    # ------------------------------------------------------------------

    def create_checkpoint(self, record: EngineeringCheckpointRecord) -> EngineeringCheckpointRecord:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO engineering_checkpoints(
                        checkpoint_id, campaign_id, package_id, kind, stage, revision,
                        state_snapshot_digest, note, created_at, schema_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.checkpoint_id, record.campaign_id, record.package_id,
                        record.kind, record.stage, record.revision,
                        record.state_snapshot_digest, record.note, record.created_at,
                        self.schema_version,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return record

    def list_checkpoints(self, campaign_id: str, limit: int = 200) -> Tuple[EngineeringCheckpointRecord, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM engineering_checkpoints WHERE campaign_id = ? "
                "ORDER BY created_at ASC LIMIT ?",
                (campaign_id, max(1, min(1000, limit))),
            ).fetchall()
        return tuple(
            EngineeringCheckpointRecord(
                checkpoint_id=row["checkpoint_id"], campaign_id=row["campaign_id"],
                package_id=row["package_id"], kind=row["kind"], stage=row["stage"],
                revision=row["revision"], state_snapshot_digest=row["state_snapshot_digest"],
                note=row["note"], created_at=row["created_at"],
            )
            for row in rows
        )

    # ------------------------------------------------------------------
    # Blockers
    # ------------------------------------------------------------------

    def create_blocker(self, record: EngineeringBlockerRecord) -> EngineeringBlockerRecord:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO engineering_blockers(
                        blocker_id, campaign_id, package_id, reason, detail, state,
                        created_at, resolved_at, resolved_by, resolution, schema_version
                    ) VALUES (?, ?, ?, ?, ?, 'open', ?, NULL, NULL, NULL, ?)
                    """,
                    (
                        record.blocker_id, record.campaign_id, record.package_id,
                        record.reason, record.detail, record.created_at, self.schema_version,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return record

    def resolve_blocker(self, blocker_id: str, *, resolved_by: str, resolution: str) -> Optional[EngineeringBlockerRecord]:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    UPDATE engineering_blockers SET state = 'resolved', resolved_at = ?,
                        resolved_by = ?, resolution = ? WHERE blocker_id = ? AND state = 'open'
                    """,
                    (_now_iso(), resolved_by, resolution, blocker_id),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.get_blocker(blocker_id)

    def get_blocker(self, blocker_id: str) -> Optional[EngineeringBlockerRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM engineering_blockers WHERE blocker_id = ?", (blocker_id,)
            ).fetchone()
        return _blocker_from_row(row)

    def list_blockers(self, campaign_id: str) -> Tuple[EngineeringBlockerRecord, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM engineering_blockers WHERE campaign_id = ? ORDER BY created_at DESC",
                (campaign_id,),
            ).fetchall()
        return tuple(_blocker_from_row(row) for row in rows)

    def open_blockers(self, campaign_id: str) -> Tuple[EngineeringBlockerRecord, ...]:
        return tuple(b for b in self.list_blockers(campaign_id) if b.state == "open")

    # ------------------------------------------------------------------
    # Roadmap
    # ------------------------------------------------------------------

    def upsert_roadmap(self, campaign_id: str, entry: RoadmapEntry) -> None:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO engineering_roadmap(
                        campaign_id, key, title, description, owner_agent_key, verifier_agent_key,
                        review_agent_key, dependencies, acceptance_criteria, roadmap_order,
                        priority, risk, stage_order, enabled, source, schema_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(campaign_id, key) DO UPDATE SET
                        title = excluded.title,
                        description = excluded.description,
                        owner_agent_key = excluded.owner_agent_key,
                        verifier_agent_key = excluded.verifier_agent_key,
                        review_agent_key = excluded.review_agent_key,
                        dependencies = excluded.dependencies,
                        acceptance_criteria = excluded.acceptance_criteria,
                        roadmap_order = excluded.roadmap_order,
                        priority = excluded.priority,
                        risk = excluded.risk,
                        stage_order = excluded.stage_order,
                        enabled = excluded.enabled,
                        source = excluded.source
                    """,
                    (
                        campaign_id, entry.key, entry.title, entry.description,
                        entry.owner_agent_key, entry.verifier_agent_key, entry.review_agent_key,
                        json.dumps(list(entry.dependencies)),
                        json.dumps(list(entry.acceptance_criteria)),
                        entry.roadmap_order, entry.priority, entry.risk,
                        json.dumps(list(entry.stage_order)),
                        1 if entry.enabled else 0, entry.source, self.schema_version,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def roadmap(self, campaign_id: str) -> Tuple[RoadmapEntry, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM engineering_roadmap WHERE campaign_id = ? ORDER BY roadmap_order ASC",
                (campaign_id,),
            ).fetchall()
        return tuple(_roadmap_from_row(row) for row in rows)

    def replace_roadmap(self, campaign_id: str, entries: Sequence[RoadmapEntry]) -> None:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "DELETE FROM engineering_roadmap WHERE campaign_id = ?", (campaign_id,)
                )
                for entry in entries:
                    connection.execute(
                        """
                        INSERT INTO engineering_roadmap(
                            campaign_id, key, title, description, owner_agent_key, verifier_agent_key,
                            review_agent_key, dependencies, acceptance_criteria, roadmap_order,
                            priority, risk, stage_order, enabled, source, schema_version
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            campaign_id, entry.key, entry.title, entry.description,
                            entry.owner_agent_key, entry.verifier_agent_key, entry.review_agent_key,
                            json.dumps(list(entry.dependencies)),
                            json.dumps(list(entry.acceptance_criteria)),
                            entry.roadmap_order, entry.priority, entry.risk,
                            json.dumps(list(entry.stage_order)),
                            1 if entry.enabled else 0, entry.source, self.schema_version,
                        ),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise


def _campaign_from_row(row) -> Optional[CampaignRecord]:
    if row is None:
        return None
    return CampaignRecord(
        campaign_id=row["campaign_id"], key=row["key"], title=row["title"],
        description=row["description"], repository_path=row["repository_path"],
        base_branch=row["base_branch"], integration_branch=row["integration_branch"],
        autonomy_policy_key=row["autonomy_policy_key"], state=row["state"],
        current_stage=row["current_stage"], worktree_root=row["worktree_root"],
        max_parallel_packages=row["max_parallel_packages"],
        max_attempts_per_package=row["max_attempts_per_package"],
        heartbeat_timeout_ms=row["heartbeat_timeout_ms"], revision=row["revision"],
        created_by=row["created_by"], created_at=row["created_at"],
        updated_at=row["updated_at"], last_heartbeat_at=row["last_heartbeat_at"],
        completion_summary=row["completion_summary"], failure_reason=row["failure_reason"],
    )


def _package_from_row(row) -> Optional[WorkPackageRecord]:
    if row is None:
        return None
    return WorkPackageRecord(
        package_id=row["package_id"], campaign_id=row["campaign_id"], key=row["key"],
        title=row["title"], description=row["description"],
        acceptance_criteria=tuple(json.loads(row["acceptance_criteria"] or "[]")),
        owner_agent_key=row["owner_agent_key"], verifier_agent_key=row["verifier_agent_key"],
        review_agent_key=row["review_agent_key"],
        dependencies=tuple(json.loads(row["dependencies"] or "[]")),
        stage_order=tuple(json.loads(row["stage_order"] or "[]")),
        state=row["state"], current_stage=row["current_stage"], attempts=row["attempts"],
        roadmap_order=row["roadmap_order"], priority=row["priority"], risk=row["risk"],
        error_detail=row["error_detail"], last_gate=row["last_gate"],
        checkpoint_revision=row["checkpoint_revision"], created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _blocker_from_row(row) -> Optional[EngineeringBlockerRecord]:
    if row is None:
        return None
    return EngineeringBlockerRecord(
        blocker_id=row["blocker_id"], campaign_id=row["campaign_id"],
        package_id=row["package_id"], reason=row["reason"], detail=row["detail"],
        state=row["state"], created_at=row["created_at"], resolved_at=row["resolved_at"],
        resolved_by=row["resolved_by"], resolution=row["resolution"],
    )


def _roadmap_from_row(row) -> RoadmapEntry:
    return RoadmapEntry(
        key=row["key"], title=row["title"], description=row["description"],
        owner_agent_key=row["owner_agent_key"], verifier_agent_key=row["verifier_agent_key"],
        review_agent_key=row["review_agent_key"],
        dependencies=tuple(json.loads(row["dependencies"] or "[]")),
        acceptance_criteria=tuple(json.loads(row["acceptance_criteria"] or "[]")),
        roadmap_order=row["roadmap_order"], priority=row["priority"], risk=row["risk"],
        stage_order=tuple(json.loads(row["stage_order"] or "[]")),
        enabled=bool(row["enabled"]), source=row["source"],
    )


def _stable_package_id(campaign_id: str, key: str) -> str:
    import hashlib

    digest = hashlib.sha256(("%s:%s" % (campaign_id, key)).encode("utf-8")).hexdigest()[:16]
    return "pkg-%s" % digest
