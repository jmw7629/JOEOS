"""Device Permission Manager for the JoeOS Wearable Platform.

Permissions are granular and capability-scoped. Camera, microphone, location,
and private-content permissions are never granted by default. Permissions can
be revoked immediately without reinstalling or re-pairing.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from typing import Callable, Dict, Tuple

# Canonical wearable permission catalog.
WEARABLE_PERMISSIONS: Dict[str, str] = {
    "display.routine_cards": "Receive routine glance cards.",
    "display.urgent_cards": "Receive urgent alert cards.",
    "display.private_content": "Show private content (never default).",
    "display.project_names": "Show project names.",
    "display.contact_names": "Show contact names.",
    "display.message_previews": "Show message previews.",
    "input.button": "Send button input.",
    "input.touch": "Send touch input.",
    "input.voice": "Send voice input.",
    "input.gesture": "Send gesture input.",
    "input.gaze": "Send gaze input.",
    "audio.output_tone": "Play notification tones.",
    "audio.output_speech": "Speak text aloud.",
    "audio.input_microphone": "Use the microphone.",
    "audio.input_wake_word": "Use a wake word.",
    "audio.continuous_listening": "Continuous listening (never default).",
    "camera.capture_still": "Capture a still image.",
    "camera.capture_video": "Capture video (disabled by default).",
    "camera.local_vision": "Analyze an image locally.",
    "camera.send_to_model": "Send an image to an approved model.",
    "sensor.location": "Access location.",
    "sensor.head_orientation": "Access head orientation.",
    "joeos.view_notifications": "View notifications.",
    "joeos.view_missions": "View missions.",
    "joeos.view_tasks": "View tasks.",
    "joeos.view_communications": "View communications.",
    "joeos.view_approvals": "View approvals.",
    "joeos.approve_low_risk": "Approve low-risk actions.",
    "joeos.deny_action": "Deny an action.",
    "joeos.create_note": "Create a note.",
    "joeos.create_task_proposal": "Create a task proposal.",
    "joeos.invoke_commands": "Invoke selected commands.",
    "joeos.access_projects": "Access selected projects.",
    "joeos.access_memories": "Access selected memories.",
}

NON_DEFAULT_PRIVILEGED = {
    "display.private_content",
    "camera.capture_still",
    "camera.capture_video",
    "camera.local_vision",
    "camera.send_to_model",
    "audio.input_microphone",
    "audio.continuous_listening",
    "sensor.location",
    "joeos.approve_low_risk",
    "joeos.access_memories",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PermissionError(RuntimeError):
    pass


class DevicePermissionManager:
    """Granular, capability-scoped device permissions."""

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()

    def grant(
        self,
        *,
        device_id: str,
        permission: str,
        scope: str = "session",
        scope_target: str = "",
    ) -> None:
        if permission not in WEARABLE_PERMISSIONS:
            raise PermissionError("unknown wearable permission %r." % permission)
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO device_permission_grants (device_id, permission, scope, scope_target, granted_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(device_id, permission, scope_target) DO UPDATE SET scope = excluded.scope, granted_at = excluded.granted_at
                """,
                (device_id, permission, scope, scope_target, _now()),
            )

    def revoke(self, *, device_id: str, permission: str, scope_target: str = "") -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "DELETE FROM device_permission_grants WHERE device_id = ? AND permission = ? AND scope_target = ?",
                (device_id, permission, scope_target),
            )

    def granted(self, *, device_id: str, permission: str, scope_target: str = "") -> bool:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT scope FROM device_permission_grants WHERE device_id = ? AND permission = ? AND scope_target = ?",
                (device_id, permission, scope_target),
            ).fetchone()
        if row is None:
            return False
        scope = str(row["scope"])
        if scope in {"session", "device", "persistent"}:
            return True
        if scope == "project" and scope_target:
            return True
        return False

    def grants_for(self, device_id: str) -> Tuple[dict, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM device_permission_grants WHERE device_id = ? ORDER BY permission",
                (device_id,),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def revoke_all(self, device_id: str) -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "DELETE FROM device_permission_grants WHERE device_id = ?", (device_id,)
            )