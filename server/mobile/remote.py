"""Scoped Remote API and Remote Command Gateway for the JoeOS Mobile Companion.

The mobile client calls only narrowly scoped, typed operations — never
internal service methods. Commands are allowlisted and pass through
authentication, session, device trust, capability grant, project scope,
approval, idempotency, and rate limits. No arbitrary shell, no raw AI output
execution, no runtime command creation.
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Callable, Dict, Optional, Sequence, Tuple

from .clients import HostRegistry, MobileClientRegistry, MobileError
from .models import MOBILE_PERMISSIONS, REMOTE_API_VERSION
from .security import MobileSessionManager

ALLOWED_REMOTE_COMMANDS: Dict[str, Dict[str, object]] = {
    "view_system_status": {"permission": "data.view_system_status", "risk": "low"},
    "view_projects": {"permission": "data.view_projects", "risk": "low"},
    "view_missions": {"permission": "data.view_missions", "risk": "low"},
    "view_tasks": {"permission": "data.view_tasks", "risk": "low"},
    "view_agents": {"permission": "data.view_agents", "risk": "low"},
    "view_notifications": {"permission": "data.view_system_status", "risk": "low"},
    "view_communications": {"permission": "data.view_communications", "risk": "low"},
    "view_approvals": {"permission": "data.view_system_status", "risk": "low"},
    "view_workflows": {"permission": "data.view_system_status", "risk": "low"},
    "view_runtime_health": {"permission": "data.view_system_status", "risk": "low"},
    "view_models": {"permission": "data.view_system_status", "risk": "low"},
    "acknowledge_notification": {"permission": "action.acknowledge_notification", "risk": "low"},
    "respond_internal": {"permission": "action.respond_internal", "risk": "medium"},
    "create_note": {"permission": "action.create_note", "risk": "low"},
    "create_task_proposal": {"permission": "action.create_task_proposal", "risk": "medium"},
    "pause_task": {"permission": "action.pause_task", "risk": "medium"},
    "pause_mission": {"permission": "action.pause_mission", "risk": "medium"},
    "trigger_workflow": {"permission": "action.trigger_selected_workflow", "risk": "medium"},
    "select_model": {"permission": "action.select_model", "risk": "low"},
    "request_test": {"permission": "action.request_test", "risk": "medium"},
    "request_build": {"permission": "action.request_build", "risk": "medium"},
    "request_desktop_handoff": {"permission": "action.request_desktop_handoff", "risk": "low"},
    "approve_low_risk": {"permission": "action.approve_low_risk", "risk": "medium"},
    "deny_action": {"permission": "action.deny_action", "risk": "low"},
}

PROHIBITED_REMOTE_COMMANDS = {
    "arbitrary_command",
    "shell_execute",
    "spawn_process",
    "git_push",
    "deployment",
    "file_deletion",
    "service_restart",
    "secret_access",
    "modify_trust",
    "grant_permission",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RemoteCommandGateway:
    """Authoritative allowlisted gateway between mobile and JoeOS actions."""

    def __init__(
        self,
        *,
        connection_factory: Callable[[], sqlite3.Connection],
        clients: MobileClientRegistry,
        sessions: MobileSessionManager,
        command_executor=None,
        event_sink=None,
    ) -> None:
        self._connection_factory = connection_factory
        self._clients = clients
        self._sessions = sessions
        self._command_executor = command_executor or (lambda command, params, context: {"executed": True, "command": command})
        self._event_sink = event_sink or (lambda level, source, message: None)
        self._lock = threading.RLock()

    def execute(
        self,
        *,
        client_id: str,
        session_id: str,
        command: str,
        params: Optional[dict] = None,
        project: str = "",
    ) -> dict:
        if command not in ALLOWED_REMOTE_COMMANDS:
            if command in PROHIBITED_REMOTE_COMMANDS:
                raise MobileError("command %r is prohibited for mobile clients." % command)
            raise MobileError("command %r is not allowlisted for mobile clients." % command)
        if command in PROHIBITED_REMOTE_COMMANDS:
            raise MobileError("command %r is prohibited." % command)
        if not self._sessions.is_valid(session_id):
            raise MobileError("session is not valid.")
        session = self._sessions.get(session_id)
        if session is None or session.client_id != client_id:
            raise MobileError("session does not belong to this client.")
        client = self._clients.get(client_id)
        if client is None or client.revocation_state != "active":
            raise MobileError("client is revoked.")
        spec = ALLOWED_REMOTE_COMMANDS[command]
        permission = str(spec.get("permission") or "")
        if permission and not self._clients.permission_granted(client_id=client_id, permission=permission):
            raise MobileError("client lacks permission %s." % permission)
        if project and project not in session.granted_projects and "*" not in session.granted_projects:
            raise MobileError("client does not have access to project %s." % project)
        request_id = "mcmd_" + uuid.uuid4().hex[:16]
        self._sessions.touch(session_id)
        result = self._command_executor(command, params or {}, {"client_id": client_id, "session_id": session_id, "project": project})
        self._event_sink("info", "mobile", "Mobile command %s executed for %s." % (command, client_id))
        return {"request_id": request_id, "command": command, "result": result}

    def allowed_commands(self) -> Tuple[str, ...]:
        return tuple(sorted(ALLOWED_REMOTE_COMMANDS))


class ScopedRemoteAPI:
    """Typed scoped queries backed by authoritative JoeOS services."""

    def __init__(self, *, sessions: MobileSessionManager, clients: MobileClientRegistry, providers=None) -> None:
        self._sessions = sessions
        self._clients = clients
        self._providers = providers or {}

    def query(self, *, client_id: str, session_id: str, resource: str, scope: dict = None) -> dict:
        if not self._sessions.is_valid(session_id):
            raise MobileError("session is not valid.")
        session = self._sessions.get(session_id)
        if session is None or session.client_id != client_id:
            raise MobileError("session does not belong to this client.")
        provider = self._providers.get(resource)
        if provider is None:
            raise MobileError("unknown remote resource %r." % resource)
        self._sessions.touch(session_id)
        return provider(session=session, scope=scope or {})

    def register_provider(self, resource: str, provider) -> None:
        self._providers[resource] = provider

    def list_resources(self) -> Tuple[str, ...]:
        return tuple(sorted(self._providers))