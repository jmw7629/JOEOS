"""Permission Manager for the JoeOS Plugin Platform.

A typed, granular permission model with explicit scopes. There is no single
"trusted plugin" switch: every capability is granted independently, can be
revoked without reinstall, and no plugin can grant a permission to itself.
Undeclared permissions are rejected at the boundary.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from typing import Callable, FrozenSet, Optional, Sequence, Tuple

from .models import (
    PermissionGrant,
    PermissionScope,
    PermissionSummary,
    is_valid_permission,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PermissionError(RuntimeError):
    pass


class PermissionManager:
    """Persists and evaluates per-plugin permission grants and denials."""

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()

    # ---- grants ----

    def grant(
        self,
        *,
        plugin_id: str,
        permission: str,
        scope: PermissionScope,
        scope_target: str = "",
        reason: str = "",
    ) -> PermissionGrant:
        if not is_valid_permission(permission):
            raise PermissionError("unknown permission: %r" % permission)
        if scope_target and not (
            scope in {"granted_workspace", "granted_project", "granted_global"}
        ):
            raise PermissionError("scope_target is only valid for workspace/project/global scopes.")
        now = _now()
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO plugin_permission_grants (
                    plugin_id, permission, scope, scope_target, reason, granted_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT DO NOTHING
                """,
                (plugin_id, permission, scope, scope_target, reason, now),
            )
            connection.execute(
                """
                UPDATE plugin_permission_grants
                SET scope = ?, scope_target = ?, reason = ?, granted_at = ?
                WHERE plugin_id = ? AND permission = ? AND scope_target = ?
                """,
                (scope, scope_target, reason, now, plugin_id, permission, scope_target),
            )
        return PermissionGrant(
            plugin_id=plugin_id,
            permission=permission,
            scope=scope,
            scope_target=scope_target,
            reason=reason,
            granted_at=now,
        )

    def revoke(self, *, plugin_id: str, permission: str, scope_target: str = "") -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                DELETE FROM plugin_permission_grants
                WHERE plugin_id = ? AND permission = ? AND scope = ?
                """,
                (plugin_id, permission, "blocked_by_policy"),
            )
            connection.execute(
                """
                UPDATE plugin_permission_grants
                SET scope = 'revoked'
                WHERE plugin_id = ? AND permission = ? AND scope_target = ?
                """,
                (plugin_id, permission, scope_target),
            )

    def deny(self, *, plugin_id: str, permission: str, scope_target: str = "") -> None:
        """Record an explicit denial that takes precedence going forward."""
        self._insert_denial(plugin_id, permission, scope_target, "revoked")

    def block(self, *, plugin_id: str, permission: str) -> None:
        """Block a permission by policy; stronger than a user denial."""
        self._insert_denial(plugin_id, permission, "", "blocked_by_policy")

    def _insert_denial(self, plugin_id: str, permission: str, scope_target: str, scope: str) -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                DELETE FROM plugin_permission_grants
                WHERE plugin_id = ? AND permission = ? AND scope_target = ?
                """,
                (plugin_id, permission, scope_target),
            )
            connection.execute(
                """
                INSERT INTO plugin_permission_grants (
                    plugin_id, permission, scope, scope_target, reason, granted_at
                ) VALUES (?, ?, ?, ?, 'denied', ?)
                """,
                (plugin_id, permission, scope, scope_target, _now()),
            )

    # ---- checks ----

    def level(
        self,
        *,
        plugin_id: str,
        permission: str,
        workspace: str = "",
        project: str = "",
    ) -> str:
        """Return the effective scope for a permission ('' if not granted)."""
        with self._connection_factory() as connection:
            rows = connection.execute(
                """
                SELECT scope, scope_target FROM plugin_permission_grants
                WHERE plugin_id = ? AND permission = ?
                ORDER BY id
                """,
                (plugin_id, permission),
            ).fetchall()
        scopes = [str(row["scope"]) for row in rows]
        if "blocked_by_policy" in scopes:
            return "blocked_by_policy"
        if "revoked" in scopes:
            return "revoked"
        global_scope = any(scope == "granted_global" for scope in scopes)
        if global_scope:
            return "granted_global"
        if workspace and any(
            scope == "granted_workspace" and row["scope_target"] == workspace for row in rows
        ):
            return "granted_workspace"
        if project and any(
            scope == "granted_project" and row["scope_target"] == project for row in rows
        ):
            return "granted_project"
        return ""

    def granted(self, *, plugin_id: str, permission: str, workspace: str = "", project: str = "") -> bool:
        return self.level(plugin_id=plugin_id, permission=permission, workspace=workspace, project=project) in {
            "granted_global",
            "granted_workspace",
            "granted_project",
            "granted_session",
            "granted_once",
        }

    def summary(self, *, plugin_id: str, required: Sequence[str], optional: Sequence[str] = ()) -> PermissionSummary:
        granted: list = []
        denied: list = []
        pending: list = []
        for permission in required:
            if self.granted(plugin_id=plugin_id, permission=permission):
                granted.append(permission)
            else:
                pending.append(permission)
        for permission in optional:
            if self.granted(plugin_id=plugin_id, permission=permission):
                granted.append(permission)
            elif self.level(plugin_id=plugin_id, permission=permission) in {
                "revoked",
                "blocked_by_policy",
            }:
                denied.append(permission)
            else:
                pending.append(permission)
        return PermissionSummary(granted=tuple(sorted(set(granted))), denied=tuple(sorted(set(denied))), pending=tuple(sorted(set(pending))))

    def active_grants(self, *, plugin_id: str) -> Tuple[PermissionGrant, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM plugin_permission_grants WHERE plugin_id = ? ORDER BY permission",
                (plugin_id,),
            ).fetchall()
        return tuple(
            PermissionGrant(
                plugin_id=str(row["plugin_id"]),
                permission=str(row["permission"]),
                scope=str(row["scope"]),
                scope_target=str(row["scope_target"]),
                reason=str(row["reason"]),
                granted_at=str(row["granted_at"]),
            )
            for row in rows
        )


class CapabilityBroker:
    """Final gatekeeper between a capability request and the platform.

    Validates plugin identity, declared capability, granted permission,
    workspace/project scopes, lifecycle state, and policy before any operation
    is permitted. Plugins receive a high-level capability, never raw internals.
    """

    def __init__(self, permissions: PermissionManager, lifecycle_probe=None) -> None:
        self._permissions = permissions
        self._lifecycle_probe = lifecycle_probe or (lambda plugin_id: "active")
        self._capability_map = {
            "register_command": "ui.register_status_item",
            "register_panel": "ui.register_panel",
            "register_view": "ui.register_view",
            "register_menu_item": "ui.register_menu",
            "register_editor": "ui.register_editor",
            "register_theme": "ui.register_theme",
            "register_tool": "tool",
            "read_selected_file": "filesystem.read_selected_file",
            "read_project_file": "filesystem.read_project_files",
            "propose_file_edit": "filesystem.propose_project_edit",
            "search_project": "intelligence.search_project",
            "inspect_symbols": "intelligence.inspect_symbols",
            "inspect_git_status": "intelligence.inspect_git_history",
            "request_terminal_command": "terminal.request_registered_command",
            "request_network": "network.access_declared_domains",
            "register_provider": "ai.register_provider",
            "register_parser": "parser",
            "import_document": "document_importer",
            "publish_notification": "notification.publish",
            "read_approved_memory": "memory.read_task_memory",
            "propose_memory": "memory.propose_memory",
            "store_extension_data": "storage.extension_data",
            "retrieve_extension_secret": "secrets.request_named_extension_secret",
            "register_automation_action": "automation.register_action",
            "access_approved_device": "hardware.bluetooth_device",
            "contribute_settings": "settings.contribute",
        }

    def capability_permission(self, capability: str) -> str:
        return self._capability_map.get(capability, "")

    def check(
        self,
        *,
        plugin_id: str,
        capability: str,
        workspace: str = "",
        project: str = "",
    ) -> None:
        if self._lifecycle_probe(plugin_id) != "active":
            raise PermissionError("plugin is not active.")
        permission = self._capability_map.get(capability, "")
        if not permission:
            raise PermissionError("unknown capability requested.")
        if not self._permissions.granted(
            plugin_id=plugin_id, permission=permission, workspace=workspace, project=project
        ):
            raise PermissionError(
                "plugin %s lacks permission '%s' for capability '%s'."
                % (plugin_id, permission, capability)
            )


def known_capabilities() -> FrozenSet[str]:
    return frozenset(
        {
            "register_command",
            "register_panel",
            "register_view",
            "register_menu_item",
            "register_editor",
            "register_theme",
            "register_tool",
            "read_selected_file",
            "read_project_file",
            "propose_file_edit",
            "search_project",
            "inspect_symbols",
            "inspect_git_status",
            "request_terminal_command",
            "request_network",
            "register_provider",
            "register_parser",
            "import_document",
            "publish_notification",
            "read_approved_memory",
            "propose_memory",
            "store_extension_data",
            "retrieve_extension_secret",
            "register_automation_action",
            "access_approved_device",
            "contribute_settings",
        }
    )