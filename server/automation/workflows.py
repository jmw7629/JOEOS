"""Workflow Registry for the JoeOS Automation and Workflow Platform.

Authoritative storage and versioning of workflow definitions. Every edit
creates a new version; running executions are pinned to the version that
started them; permissions/secrets/trigger expansions require renewed review.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Callable, Dict, Optional, Sequence, Tuple

from pydantic import ValidationError

from .models import (
    WorkflowDefinition,
    WorkflowRecord,
    WorkflowVersion,
)

MAX_WORKFLOW_BYTES = 256 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkflowError(RuntimeError):
    pass


def definition_hash(definition: WorkflowDefinition) -> str:
    payload = json.dumps(definition.model_dump(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class WorkflowRegistry:
    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()

    # ---- definitions ----

    def create(
        self,
        definition: WorkflowDefinition,
        *,
        creator: str = "user",
        change_summary: str = "",
        source: str = "user",
    ) -> WorkflowRecord:
        if len(json.dumps(definition.model_dump())) > MAX_WORKFLOW_BYTES:
            raise WorkflowError("workflow definition is too large.")
        now = _now()
        version_id = str(uuid.uuid4())
        digest = definition_hash(definition)
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO workflow_definitions (
                    workflow_id, name, current_version, definition, enabled, status,
                    health_state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 0, 'draft', 'inactive', ?, ?)
                """,
                (
                    definition.workflow_id,
                    definition.name,
                    definition.version,
                    json.dumps(definition.model_dump()),
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO workflow_versions (
                    version_id, workflow_id, version, definition_hash, definition,
                    created_at, creator, change_summary, published, superseded
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0)
                """,
                (
                    version_id,
                    definition.workflow_id,
                    definition.version,
                    digest,
                    json.dumps(definition.model_dump()),
                    now,
                    creator,
                    change_summary,
                ),
            )
            connection.execute(
                """
                INSERT INTO workflow_activity (event_id, workflow_id, kind, message, level, recorded_at)
                VALUES (?, ?, 'workflow_created', ?, 'success', ?)
                """,
                ("wfa_" + uuid.uuid4().hex[:20], definition.workflow_id, "Workflow created (%s)." % source, now),
            )
        return self.get_record(definition.workflow_id)

    def update(
        self,
        definition: WorkflowDefinition,
        *,
        creator: str = "user",
        change_summary: str = "",
        require_review_for: Optional[Sequence[str]] = None,
    ) -> WorkflowRecord:
        existing = self.get_record(definition.workflow_id)
        if existing is None:
            raise WorkflowError("workflow does not exist.")
        old = existing.definition
        permission_diff = self._permission_difference(old, definition)
        trigger_diff = self._trigger_difference(old, definition)
        secret_diff = self._secret_difference(old, definition)
        now = _now()
        version_id = str(uuid.uuid4())
        digest = definition_hash(definition)
        needs_review = bool(permission_diff or trigger_diff or secret_diff)
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                UPDATE workflow_versions SET superseded = 1
                WHERE workflow_id = ?
                """,
                (definition.workflow_id,),
            )
            connection.execute(
                """
                INSERT INTO workflow_versions (
                    version_id, workflow_id, version, definition_hash, definition,
                    created_at, creator, change_summary, published, superseded
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0)
                """,
                (
                    version_id,
                    definition.workflow_id,
                    definition.version,
                    digest,
                    json.dumps(definition.model_dump()),
                    now,
                    creator,
                    change_summary,
                ),
            )
            status = "ready" if not needs_review else "draft"
            connection.execute(
                """
                UPDATE workflow_definitions
                SET definition = ?, current_version = ?, updated_at = ?, status = ?
                WHERE workflow_id = ?
                """,
                (
                    json.dumps(definition.model_dump()),
                    definition.version,
                    now,
                    status,
                    definition.workflow_id,
                ),
            )
            if needs_review:
                connection.execute(
                    """
                    INSERT INTO workflow_activity (event_id, workflow_id, kind, message, level, recorded_at)
                    VALUES (?, ?, 'workflow_updated', ?, 'warn', ?)
                    """,
                    (
                        "wfa_" + uuid.uuid4().hex[:20],
                        definition.workflow_id,
                        "Update expands permissions, triggers, or secrets; renewed review required.",
                        now,
                    ),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO workflow_activity (event_id, workflow_id, kind, message, level, recorded_at)
                    VALUES (?, ?, 'workflow_updated', ?, 'info', ?)
                    """,
                    (
                        "wfa_" + uuid.uuid4().hex[:20],
                        definition.workflow_id,
                        "Workflow updated to version %s." % definition.version,
                        now,
                    ),
                )
        return self.get_record(definition.workflow_id)

    def get_record(self, workflow_id: str) -> Optional[WorkflowRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM workflow_definitions WHERE workflow_id = ?", (workflow_id,)
            ).fetchone()
        if row is None:
            return None
        definition = parse_definition(json.loads(str(row["definition"])))
        return WorkflowRecord(
            workflow_id=str(row["workflow_id"]),
            name=str(row["name"]),
            current_version=str(row["current_version"]),
            definition=definition,
            enabled=bool(row["enabled"]),
            status=str(row["status"]),
            health_state=str(row["health_state"]),
            next_scheduled_run=self._next_scheduled_run(workflow_id),
            last_successful_run=self._last_run(workflow_id, "succeeded"),
            last_failed_run=self._last_run(workflow_id, "failed"),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def list_records(self) -> Tuple[WorkflowRecord, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM workflow_definitions ORDER BY name"
            ).fetchall()
        return tuple(
            WorkflowRecord(
                workflow_id=str(row["workflow_id"]),
                name=str(row["name"]),
                current_version=str(row["current_version"]),
                definition=parse_definition(json.loads(str(row["definition"]))),
                enabled=bool(row["enabled"]),
                status=str(row["status"]),
                health_state=str(row["health_state"]),
                next_scheduled_run=None,
                last_successful_run=self._last_run(str(row["workflow_id"]), "succeeded"),
                last_failed_run=self._last_run(str(row["workflow_id"]), "failed"),
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
            )
            for row in rows
        )

    def set_state(self, workflow_id: str, *, status: str, enabled: Optional[bool] = None, health: Optional[str] = None) -> WorkflowRecord:
        with self._lock, self._connection_factory() as connection:
            if enabled is not None:
                connection.execute(
                    "UPDATE workflow_definitions SET enabled = ?, status = ?, updated_at = ? WHERE workflow_id = ?",
                    (1 if enabled else 0, status, _now(), workflow_id),
                )
            else:
                connection.execute(
                    "UPDATE workflow_definitions SET status = ?, updated_at = ? WHERE workflow_id = ?",
                    (status, _now(), workflow_id),
                )
            if health:
                connection.execute(
                    "UPDATE workflow_definitions SET health_state = ? WHERE workflow_id = ?",
                    (health, workflow_id),
                )
        return self.get_record(workflow_id)

    # ---- versions ----

    def versions(self, workflow_id: str) -> Tuple[WorkflowVersion, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM workflow_versions WHERE workflow_id = ? ORDER BY created_at DESC",
                (workflow_id,),
            ).fetchall()
        return tuple(self._row_version(row) for row in rows)

    def version(self, workflow_id: str, version: str) -> Optional[WorkflowVersion]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM workflow_versions WHERE workflow_id = ? AND version = ? ORDER BY created_at DESC LIMIT 1",
                (workflow_id, version),
            ).fetchone()
        return self._row_version(row) if row else None

    def publish(self, workflow_id: str, version: str) -> WorkflowRecord:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE workflow_versions SET published = 1 WHERE workflow_id = ? AND version = ?",
                (workflow_id, version),
            )
            connection.execute(
                "UPDATE workflow_definitions SET current_version = ?, status = 'ready', updated_at = ? WHERE workflow_id = ?",
                (version, _now(), workflow_id),
            )
        return self.get_record(workflow_id)

    def _permission_difference(self, old: WorkflowDefinition, new: WorkflowDefinition) -> Tuple[str, ...]:
        return tuple(sorted(set(new.required_permissions) - set(old.required_permissions)))

    def _trigger_difference(self, old: WorkflowDefinition, new: WorkflowDefinition) -> Tuple[str, ...]:
        return tuple(sorted(set(t.trigger_id for t in new.triggers) - set(t.trigger_id for t in old.triggers)))

    def _secret_difference(self, old: WorkflowDefinition, new: WorkflowDefinition) -> Tuple[str, ...]:
        return tuple(sorted(set(s.name for s in new.secrets) - set(s.name for s in old.secrets)))

    def _next_scheduled_run(self, workflow_id: str) -> Optional[str]:
        try:
            with self._connection_factory() as connection:
                row = connection.execute(
                    "SELECT next_run FROM workflow_schedules WHERE workflow_id = ? AND enabled = 1 ORDER BY next_run LIMIT 1",
                    (workflow_id,),
                ).fetchone()
            return str(row["next_run"]) if row and row["next_run"] else None
        except Exception:
            return None

    def _last_run(self, workflow_id: str, state: str) -> Optional[str]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT started_at FROM workflow_runs WHERE workflow_id = ? AND state = ? ORDER BY created_at DESC LIMIT 1",
                (workflow_id, state),
            ).fetchone()
        return str(row["started_at"]) if row and row["started_at"] else None

    @staticmethod
    def _row_version(row: sqlite3.Row) -> WorkflowVersion:
        return WorkflowVersion(
            version_id=str(row["version_id"]),
            workflow_id=str(row["workflow_id"]),
            version=str(row["version"]),
            definition_hash=str(row["definition_hash"]),
            created_at=str(row["created_at"]),
            creator=str(row["creator"]),
            change_summary=str(row["change_summary"]),
            definition=parse_definition(json.loads(str(row["definition"]))),
            published=bool(row["published"]),
            superseded=bool(row["superseded"]),
        )


def _normalize_json(value):
    """Recursively convert JSON arrays into tuples for strict tuple fields."""
    if isinstance(value, list):
        return tuple(_normalize_json(item) for item in value)
    if isinstance(value, dict):
        return {key: _normalize_json(item) for key, item in value.items()}
    return value


def parse_definition(payload: dict) -> WorkflowDefinition:
    """Strictly validate a workflow definition dictionary."""
    if not isinstance(payload, dict):
        raise WorkflowError("workflow definition must be an object.")
    payload = _normalize_json(payload)
    try:
        return WorkflowDefinition.model_validate(payload)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise WorkflowError(
            "workflow definition invalid: %s (%s)"
            % (first.get("msg"), ".".join(map(str, first.get("loc", []))))
        ) from exc