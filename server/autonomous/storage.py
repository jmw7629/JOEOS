"""Durable storage for the autonomous operations domain.

SQLite storage following JoeOS conventions. Automations are stored with
immutable configuration snapshots per run so historical runs can identify the
exact definition revision that executed them. Occurrences are deduplicated by
a deterministic occurrence_key enforced with a unique index.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .models import (
    AutomationDefinition,
    AutomationRun,
    TriggerSpec,
)

SCHEMA_VERSION = 1


class AutonomousStore:
    def __init__(self, data_dir: str) -> None:
        self._data_dir = Path(data_dir)
        self._db_path = self._data_dir / "autonomous.db"
        self._lock = threading.RLock()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self._db_path), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def prepare(self) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        with self._lock, self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS autonomous_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS automation_definitions (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    owner_principal_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    objective TEXT NOT NULL,
                    agent_ref TEXT NOT NULL DEFAULT 'auto',
                    trigger_json TEXT NOT NULL,
                    timezone TEXT NOT NULL DEFAULT 'UTC',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    state TEXT NOT NULL DEFAULT 'draft',
                    next_run_at TEXT NOT NULL DEFAULT '',
                    last_run_at TEXT NOT NULL DEFAULT '',
                    concurrency_policy TEXT NOT NULL DEFAULT 'skip_if_running',
                    missed_run_policy TEXT NOT NULL DEFAULT 'skip',
                    retry_policy_json TEXT NOT NULL,
                    notification_policy_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1
                );
                CREATE INDEX IF NOT EXISTS idx_automation_defs_ws
                ON automation_definitions(workspace_id, state, next_run_at);

                CREATE TABLE IF NOT EXISTS automation_runs (
                    id TEXT PRIMARY KEY,
                    automation_id TEXT NOT NULL,
                    occurrence_key TEXT NOT NULL,
                    trigger_kind TEXT NOT NULL DEFAULT '',
                    scheduled_for TEXT NOT NULL DEFAULT '',
                    triggered_at TEXT NOT NULL DEFAULT '',
                    started_at TEXT NOT NULL DEFAULT '',
                    completed_at TEXT NOT NULL DEFAULT '',
                    attempt INTEGER NOT NULL DEFAULT 1,
                    state TEXT NOT NULL DEFAULT 'queued',
                    agent_run_id TEXT NOT NULL DEFAULT '',
                    task_graph_id TEXT NOT NULL DEFAULT '',
                    approval_id TEXT NOT NULL DEFAULT '',
                    execution_id TEXT NOT NULL DEFAULT '',
                    result_summary TEXT NOT NULL DEFAULT '',
                    error_category TEXT NOT NULL DEFAULT '',
                    next_retry_at TEXT NOT NULL DEFAULT '',
                    worker_claimed_by TEXT NOT NULL DEFAULT '',
                    worker_claimed_at TEXT NOT NULL DEFAULT '',
                    lease_expires_at TEXT NOT NULL DEFAULT '',
                    provider_key TEXT NOT NULL DEFAULT '',
                    model_key TEXT NOT NULL DEFAULT '',
                    definition_revision INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1
                );
                CREATE UNIQUE INDEX IF NOT EXISTS uq_automation_occurrence
                ON automation_runs(automation_id, occurrence_key);

                CREATE TABLE IF NOT EXISTS automation_definition_snapshots (
                    run_id TEXT PRIMARY KEY,
                    automation_id TEXT NOT NULL,
                    definition_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_automation_snapshot_auto
                ON automation_definition_snapshots(automation_id);
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO autonomous_meta(key, value) VALUES ('schema_version', '1')"
            )
            connection.commit()

    # ------------------------------------------------------------------
    # Definitions
    # ------------------------------------------------------------------

    def insert_definition(self, definition: AutomationDefinition) -> AutomationDefinition:
        with self._lock, self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO automation_definitions(
                        id, organization_id, workspace_id, owner_principal_id, name,
                        description, objective, agent_ref, trigger_json, timezone, enabled,
                        state, next_run_at, last_run_at, concurrency_policy, missed_run_policy,
                        retry_policy_json, notification_policy_json, created_at, updated_at, revision
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        definition.id, definition.organization_id, definition.workspace_id,
                        definition.owner_principal_id, definition.name, definition.description,
                        definition.objective, definition.agent_ref,
                        definition.trigger.model_dump_json(), definition.trigger.timezone,
                        1 if definition.enabled else 0, definition.state,
                        definition.next_run_at, definition.last_run_at,
                        definition.concurrency_policy, definition.missed_run_policy,
                        definition.retry_policy.model_dump_json(),
                        definition.notification_policy.model_dump_json(),
                        definition.created_at, definition.updated_at, definition.revision,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return definition

    def update_definition(self, definition: AutomationDefinition) -> AutomationDefinition:
        with self._lock, self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    UPDATE automation_definitions
                    SET name=?, description=?, objective=?, agent_ref=?, trigger_json=?,
                        timezone=?, enabled=?, state=?, next_run_at=?, last_run_at=?,
                        concurrency_policy=?, missed_run_policy=?, retry_policy_json=?,
                        notification_policy_json=?, updated_at=?, revision=?
                    WHERE id=?
                    """,
                    (
                        definition.name, definition.description, definition.objective,
                        definition.agent_ref, definition.trigger.model_dump_json(),
                        definition.trigger.timezone, 1 if definition.enabled else 0,
                        definition.state, definition.next_run_at, definition.last_run_at,
                        definition.concurrency_policy, definition.missed_run_policy,
                        definition.retry_policy.model_dump_json(),
                        definition.notification_policy.model_dump_json(),
                        definition.updated_at, definition.revision, definition.id,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return definition

    def get_definition(self, automation_id: str) -> Optional[AutomationDefinition]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM automation_definitions WHERE id = ?", (automation_id,)
            ).fetchone()
        return self._definition_from_row(row) if row is not None else None

    def list_definitions(self, workspace_id: Optional[str] = None,
                         state: Optional[str] = None) -> List[AutomationDefinition]:
        query = "SELECT * FROM automation_definitions"
        clauses = []
        params: List[str] = []
        if workspace_id:
            clauses.append("workspace_id = ?")
            params.append(workspace_id)
        if state:
            clauses.append("state = ?")
            params.append(state)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC"
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._definition_from_row(row) for row in rows]

    def list_due_definitions(self, now_iso: str) -> List[AutomationDefinition]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM automation_definitions
                WHERE state = 'active'
                  AND enabled = 1
                  AND next_run_at <> '' AND next_run_at <= ?
                ORDER BY next_run_at
                """,
                (now_iso,),
            ).fetchall()
        return [self._definition_from_row(row) for row in rows]

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------

    def insert_run(self, run: AutomationRun) -> AutomationRun:
        with self._lock, self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO automation_runs(
                        id, automation_id, occurrence_key, trigger_kind, scheduled_for,
                        triggered_at, started_at, completed_at, attempt, state, agent_run_id,
                        task_graph_id, approval_id, execution_id, result_summary, error_category,
                        next_retry_at, worker_claimed_by, worker_claimed_at, lease_expires_at,
                        provider_key, model_key, definition_revision, created_at, revision
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run.id, run.automation_id, run.occurrence_key, run.trigger_kind,
                        run.scheduled_for, run.triggered_at, run.started_at, run.completed_at,
                        run.attempt, run.state, run.agent_run_id, run.task_graph_id,
                        run.approval_id, run.execution_id, run.result_summary, run.error_category,
                        run.next_retry_at, run.worker_claimed_by, run.worker_claimed_at,
                        run.lease_expires_at, run.provider_key, run.model_key,
                        run.definition_revision, run.created_at, run.revision,
                    ),
                )
                connection.commit()
            except sqlite3.IntegrityError:
                connection.rollback()
                raise DuplicateOccurrenceError(
                    "occurrence already exists for automation %s key %s"
                    % (run.automation_id, run.occurrence_key)
                )
            except Exception:
                connection.rollback()
                raise
        return run

    def get_run(self, run_id: str) -> Optional[AutomationRun]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM automation_runs WHERE id = ?", (run_id,)
            ).fetchone()
        return self._run_from_row(row) if row is not None else None

    def get_run_by_occurrence(self, automation_id: str, occurrence_key: str) -> Optional[AutomationRun]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM automation_runs WHERE automation_id=? AND occurrence_key=?",
                (automation_id, occurrence_key),
            ).fetchone()
        return self._run_from_row(row) if row is not None else None

    def list_runs(self, automation_id: str, limit: int = 50) -> List[AutomationRun]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM automation_runs WHERE automation_id=? ORDER BY created_at DESC LIMIT ?",
                (automation_id, int(limit)),
            ).fetchall()
        return [self._run_from_row(row) for row in rows]

    def list_runs_by_state(self, state: str, workspace_ids: Sequence[str],
                           limit: int = 100) -> List[AutomationRun]:
        placeholders = ",".join("?" for _ in workspace_ids)
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT r.* FROM automation_runs r "
                "JOIN automation_definitions d ON d.id = r.automation_id "
                "WHERE r.state = ? AND d.workspace_id IN (%s) "
                "ORDER BY r.created_at DESC LIMIT ?" % placeholders,
                list(workspace_ids) + [state, int(limit)],
            ).fetchall()
        return [self._run_from_row(row) for row in rows]

    def update_run(self, run: AutomationRun) -> AutomationRun:
        with self._lock, self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    UPDATE automation_runs
                    SET triggered_at=?, started_at=?, completed_at=?, attempt=?, state=?,
                        agent_run_id=?, task_graph_id=?, approval_id=?, execution_id=?,
                        result_summary=?, error_category=?, next_retry_at=?, worker_claimed_by=?,
                        worker_claimed_at=?, lease_expires_at=?, provider_key=?, model_key=?,
                        revision=?
                    WHERE id=?
                    """,
                    (
                        run.triggered_at, run.started_at, run.completed_at, run.attempt,
                        run.state, run.agent_run_id, run.task_graph_id, run.approval_id,
                        run.execution_id, run.result_summary, run.error_category,
                        run.next_retry_at, run.worker_claimed_by, run.worker_claimed_at,
                        run.lease_expires_at, run.provider_key, run.model_key,
                        run.revision + 1, run.id,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return run

    def claim_run(self, run_id: str, *, worker: str, now_iso: str,
                  lease_expires_iso: str) -> bool:
        with self._lock, self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    """
                    UPDATE automation_runs
                    SET worker_claimed_by=?, worker_claimed_at=?, lease_expires_at=?,
                        state=CASE WHEN state='retry_wait' THEN 'queued' ELSE state END,
                        revision=revision+1
                    WHERE id=? AND (worker_claimed_by='' OR lease_expires_at=''
                        OR lease_expires_at < ?) AND state IN ('queued','retry_wait')
                    """,
                    (worker, now_iso, lease_expires_iso, run_id, now_iso),
                )
                connection.commit()
                return cursor.rowcount == 1
            except Exception:
                connection.rollback()
                raise

    def release_run_claim(self, run_id: str) -> bool:
        with self._lock, self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE automation_runs SET worker_claimed_by='', worker_claimed_at='',
                    lease_expires_at='', revision=revision+1
                WHERE id=?
                """,
                (run_id,),
            )
            connection.commit()
            return cursor.rowcount == 1

    def recover_expired_leases(self, now_iso: str, lease_expires_iso: str) -> int:
        """Recover runs whose lease expired while a worker was still 'running'.
        Terminal runs are never reset; only non-terminal claimed runs are."""
        with self._lock, self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    """
                    UPDATE automation_runs
                    SET state='queued', worker_claimed_by='', worker_claimed_at='',
                        lease_expires_at='', revision=revision+1
                    WHERE worker_claimed_by <> '' AND lease_expires_at <> ''
                      AND lease_expires_at < ? AND state NOT IN ('succeeded','failed','cancelled')
                    """,
                    (lease_expires_iso,),
                )
                connection.commit()
                return cursor.rowcount
            except Exception:
                connection.rollback()
                raise

    def advance_definition_next(self, automation_id: str, trigger, revision: int, now_iso: str) -> None:
        """Recompute next_run_at after an occurrence. Called under the store
        lock so concurrent scheduler passes advance deterministically."""
        from .scheduling import next_occurrence
        nxt = next_occurrence(trigger, now_iso)
        with self._lock, self.connect() as connection:
            connection.execute(
                """
                UPDATE automation_definitions
                SET next_run_at=?, last_run_at=?, updated_at=?
                WHERE id=?
                """,
                (nxt or "", now_iso, now_iso, automation_id),
            )
            connection.commit()

    # ------------------------------------------------------------------
    # Definition snapshots (immutable config per run)
    # ------------------------------------------------------------------

    def save_definition_snapshot(self, run_id: str, automation_id: str,
                                 definition: AutomationDefinition, now_iso: str) -> None:
        with self._lock, self.connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO automation_definition_snapshots(run_id, automation_id, definition_json, created_at) VALUES (?, ?, ?, ?)",
                (run_id, automation_id, definition.model_dump_json(), now_iso),
            )
            connection.commit()

    def get_definition_snapshot(self, run_id: str) -> Optional[Dict]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT definition_json FROM automation_definition_snapshots WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        import json
        return json.loads(row["definition_json"])

    # ------------------------------------------------------------------
    # Row mappers
    # ------------------------------------------------------------------

    @staticmethod
    def _definition_from_row(row: sqlite3.Row) -> AutomationDefinition:
        import json
        from .models import NotificationPolicySpec, RetryPolicySpec
        trigger = TriggerSpec.model_validate_json(row["trigger_json"])
        retry = RetryPolicySpec.model_validate_json(row["retry_policy_json"] or "{}")
        notif = NotificationPolicySpec.model_validate_json(row["notification_policy_json"] or "{}")
        return AutomationDefinition(
            id=row["id"], organization_id=row["organization_id"],
            workspace_id=row["workspace_id"], owner_principal_id=row["owner_principal_id"],
            name=row["name"], description=row["description"], objective=row["objective"],
            agent_ref=row["agent_ref"], trigger=trigger, enabled=bool(row["enabled"]),
            state=row["state"], next_run_at=row["next_run_at"], last_run_at=row["last_run_at"],
            concurrency_policy=row["concurrency_policy"], missed_run_policy=row["missed_run_policy"],
            retry_policy=retry, notification_policy=notif,
            created_at=row["created_at"], updated_at=row["updated_at"], revision=row["revision"],
        )

    @staticmethod
    def _run_from_row(row: sqlite3.Row) -> AutomationRun:
        return AutomationRun(
            id=row["id"], automation_id=row["automation_id"],
            occurrence_key=row["occurrence_key"], trigger_kind=row["trigger_kind"],
            scheduled_for=row["scheduled_for"], triggered_at=row["triggered_at"],
            started_at=row["started_at"], completed_at=row["completed_at"],
            attempt=row["attempt"], state=row["state"], agent_run_id=row["agent_run_id"],
            task_graph_id=row["task_graph_id"], approval_id=row["approval_id"],
            execution_id=row["execution_id"], result_summary=row["result_summary"],
            error_category=row["error_category"], next_retry_at=row["next_retry_at"],
            worker_claimed_by=row["worker_claimed_by"], worker_claimed_at=row["worker_claimed_at"],
            lease_expires_at=row["lease_expires_at"], provider_key=row["provider_key"],
            model_key=row["model_key"], definition_revision=row["definition_revision"],
            created_at=row["created_at"], revision=row["revision"],
        )


class DuplicateOccurrenceError(RuntimeError):
    pass
