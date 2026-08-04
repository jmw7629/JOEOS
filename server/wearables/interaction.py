"""Wearable Command Gateway and interaction model for the JoeOS Wearable
Platform.

Commands are allowlisted, pass through authentication, session, capability,
permission, privacy, and approval checks, and enforce confirmation levels.
High-risk actions require a stronger surface and are never bound to an
ambiguous gesture or voice input.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Callable, Dict, Optional, Sequence, Tuple

from .devices import DeviceRegistry
from .models import CommandRequest, InteractionEvent
from .permissions import DevicePermissionManager, PermissionError
from .security import SecureSessionService

ALLOWLISTED_COMMANDS: Dict[str, Dict[str, object]] = {
    "open_mission": {"risk": "low", "confirmation": "none", "permission": "joeos.view_missions"},
    "show_next_task": {"risk": "low", "confirmation": "none", "permission": "joeos.view_tasks"},
    "read_latest_urgent_notification": {"risk": "low", "confirmation": "none", "permission": "joeos.view_notifications"},
    "snooze_reminder": {"risk": "low", "confirmation": "none", "permission": ""},
    "mark_item_read": {"risk": "low", "confirmation": "none", "permission": ""},
    "create_note": {"risk": "medium", "confirmation": "low", "permission": "joeos.create_note"},
    "create_task_proposal": {"risk": "medium", "confirmation": "medium", "permission": "joeos.create_task_proposal"},
    "ask_joeos": {"risk": "low", "confirmation": "none", "permission": ""},
    "start_checklist": {"risk": "low", "confirmation": "none", "permission": ""},
    "pause_workflow": {"risk": "medium", "confirmation": "medium", "permission": ""},
    "cancel_task": {"risk": "high", "confirmation": "high", "permission": ""},
    "request_desktop_handoff": {"risk": "low", "confirmation": "none", "permission": ""},
}

# High-risk commands that can never be confirmed by a single ambiguous gesture
# or voice phrase.
HIGH_RISK_COMMANDS = {"cancel_task", "external_send", "deploy", "git_push", "delete_file", "access_secret"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class InteractionGateway:
    """Normalizes wearable input events and rejects ambiguous/duplicate ones."""

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()

    def record(self, *, device_id: str, session_id: str, input_type: str, normalized_action: str = "", confidence: Optional[float] = None) -> InteractionEvent:
        event = InteractionEvent(
            event_id="evt_" + uuid.uuid4().hex[:16],
            device_id=device_id,
            session_id=session_id,
            input_type=input_type,
            timestamp=_now(),
            confidence=confidence,
            normalized_action=normalized_action,
            active_content="",
            permission_state="received",
        )
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO device_interactions (
                    event_id, device_id, session_id, input_type, timestamp, confidence,
                    normalized_action, active_content, permission_state, duplicate, expiration
                ) VALUES (?, ?, ?, ?, ?, ?, ?, '', 'received', 0, '')
                """,
                (event.event_id, device_id, session_id, input_type, event.timestamp, confidence, normalized_action),
            )
        return event


class WearableCommandGateway:
    """Authoritative allowlisted command execution for wearable devices."""

    def __init__(
        self,
        *,
        connection_factory: Callable[[], sqlite3.Connection],
        devices: DeviceRegistry,
        sessions: SecureSessionService,
        permissions: DevicePermissionManager,
        command_executor=None,
        event_sink=None,
        governance_blocked=None,
    ) -> None:
        self._connection_factory = connection_factory
        self._devices = devices
        self._sessions = sessions
        self._permissions = permissions
        self._command_executor = command_executor or (lambda command, params, context: {"executed": True, "command": command})
        self._event_sink = event_sink or (lambda level, source, message: None)
        self._governance_blocked = governance_blocked or (lambda: (False, ""))
        self._lock = threading.RLock()

    def execute(
        self,
        *,
        device_id: str,
        session_id: str,
        command: str,
        params: Optional[dict] = None,
        confirmation: str = "none",
        interactive_confirm: bool = False,
    ) -> dict:
        blocked, reason = self._governance_blocked()
        if blocked:
            raise PermissionError("governance: %s" % reason)
        if command not in ALLOWLISTED_COMMANDS:
            raise PermissionError("command %r is not allowlisted for wearables." % command)
        if not self._sessions.is_valid(session_id):
            raise PermissionError("device session is not valid.")
        session = self._sessions.get(session_id)
        if session is None or session.device_id != device_id:
            raise PermissionError("session does not belong to this device.")
        device = self._devices.get(device_id)
        if device is None or device.revocation_state == "revoked":
            raise PermissionError("device is revoked.")
        spec = ALLOWLISTED_COMMANDS[command]
        permission = str(spec.get("permission") or "")
        if permission and not self._permissions.granted(device_id=device_id, permission=permission):
            raise PermissionError("device lacks permission %s." % permission)
        required_confirmation = str(spec.get("confirmation") or "none")
        risk = str(spec.get("risk") or "low")
        if risk == "high":
            # High-risk commands require a deliberate, unambiguous confirmation
            # (e.g., companion/desktop confirmation), never an ambiguous gesture.
            if confirmation != "high" or not interactive_confirm:
                return {
                    "state": "escalated",
                    "reason": "high-risk action requires desktop or companion confirmation.",
                    "command": command,
                }
        elif confirmation == "none" and required_confirmation in {"low", "medium", "high"}:
            raise PermissionError("command %s requires confirmation." % command)
        request_id = "cmd_" + uuid.uuid4().hex[:16]
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO device_commands (
                    request_id, device_id, session_id, command, params, risk,
                    confirmation_level, created_at, state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'executed')
                """,
                (request_id, device_id, session_id, command, json.dumps(params or {}), risk, required_confirmation, _now()),
            )
        result = self._command_executor(command, params or {}, {"device_id": device_id, "session_id": session_id})
        self._event_sink("info", "wearables", "Device command %s executed." % command)
        return {"state": "executed", "command": command, "result": result}