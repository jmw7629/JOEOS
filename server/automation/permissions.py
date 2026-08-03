"""Permission guard for the JoeOS Automation Platform.

Workflows receive only the permissions declared in their approved definition.
A workflow can never grant itself new permissions or bypass Tool Broker-style
authority. Grant state is persisted so revocations take effect immediately.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from typing import Callable, Optional

WORKFLOW_PERMISSION_CATALOG = frozenset(
    {
        "notification.publish",
        "memory.propose_memory",
        "mission.create_mission",
        "mission.create_task",
        "agent.request_task",
        "command.validate",
        "git.read",
        "filesystem.read_project_files",
        "model.call",
        "tool.call",
    }
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkflowPermissionGuard:
    """Persists per-workflow permission grants and evaluates access."""

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()

    def _ensure_table(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_permission_grants (
                workflow_id TEXT NOT NULL,
                permission TEXT NOT NULL,
                scope TEXT NOT NULL DEFAULT 'global',
                scope_target TEXT NOT NULL DEFAULT '',
                granted_at TEXT NOT NULL,
                PRIMARY KEY (workflow_id, permission, scope_target)
            )
            """
        )

    def grant(self, *, workflow_id: str, permission: str, scope: str = "global", scope_target: str = "") -> None:
        if permission not in WORKFLOW_PERMISSION_CATALOG:
            raise ValueError("unknown workflow permission: %r" % permission)
        with self._lock, self._connection_factory() as connection:
            self._ensure_table(connection)
            connection.execute(
                """
                INSERT INTO workflow_permission_grants (workflow_id, permission, scope, scope_target, granted_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(workflow_id, permission, scope_target) DO UPDATE SET scope = excluded.scope
                """,
                (workflow_id, permission, scope, scope_target, _now()),
            )

    def revoke(self, *, workflow_id: str, permission: str, scope_target: str = "") -> None:
        with self._lock, self._connection_factory() as connection:
            self._ensure_table(connection)
            connection.execute(
                "DELETE FROM workflow_permission_grants WHERE workflow_id = ? AND permission = ? AND scope_target = ?",
                (workflow_id, permission, scope_target),
            )

    def granted(self, *, workflow_id: str, permission: str, project: str = "") -> bool:
        with self._connection_factory() as connection:
            self._ensure_table(connection)
            row = connection.execute(
                "SELECT scope, scope_target FROM workflow_permission_grants WHERE workflow_id = ? AND permission = ?",
                (workflow_id, permission),
            ).fetchone()
        if row is None:
            return False
        if str(row["scope"]) == "global":
            return True
        if project and str(row["scope_target"]) == project:
            return True
        return False

    def grants_for(self, *, workflow_id: str) -> tuple:
        with self._connection_factory() as connection:
            self._ensure_table(connection)
            rows = connection.execute(
                "SELECT * FROM workflow_permission_grants WHERE workflow_id = ? ORDER BY permission",
                (workflow_id,),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def verify_declared(self, *, workflow_id: str, definition_required: tuple) -> None:
        """Ensure a workflow only uses permissions it declared and were granted."""
        declared = set(definition_required)
        for permission in sorted(set(declared)):
            if not self.granted(workflow_id=workflow_id, permission=permission):
                raise ValueError(
                    "workflow %s requires permission %s which is not granted." % (workflow_id, permission)
                )