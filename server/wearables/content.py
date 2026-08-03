"""Glance Card system, Wearable Notification Router, and privacy modes for the
JoeOS Wearable Platform.

Cards are device-adaptive and glanceable; delivery prioritizes security and
approvals; privacy modes minimize disclosure; routine notifications are never
mirrored wholesale to glasses.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .connections import DevicePermissionManager
from .devices import DeviceRegistry
from .models import CardAck, WearableContent

MAX_CARDS_PER_DEVICE = 200
PRIVACY_MODES = ("normal", "minimal_preview", "titles_only", "public_environment", "emergency_only")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WearableError(RuntimeError):
    pass


class PrivacyModeService:
    """Privacy modes that reduce wearable disclosure."""

    def __init__(self, devices: DeviceRegistry) -> None:
        self._devices = devices

    def set_mode(self, *, device_id: str, mode: str) -> str:
        if mode not in PRIVACY_MODES:
            raise WearableError("unknown privacy mode %r." % mode)
        self._devices.update_state(device_id, privacy_mode=mode)
        return mode

    def mode(self, device_id: str) -> str:
        device = self._devices.get(device_id)
        return device.privacy_mode if device else "normal"

    def render(self, content: WearableContent, mode: str, *, permitted_private: bool = False) -> Dict[str, object]:
        """Return the minimum-disclosure representation for a privacy mode."""
        if mode == "titles_only":
            return {
                "title": content.title,
                "body": "",
                "private_hidden": True,
                "severity": content.severity,
                "actions": content.actions if content.severity in {"critical", "security_critical"} else (),
            }
        if mode == "minimal_preview":
            if content.privacy != "public_safe" and not permitted_private:
                return {
                    "title": content.title,
                    "body": "",
                    "private_hidden": True,
                    "severity": content.severity,
                }
            return {"title": content.title, "body": content.body[:60], "severity": content.severity, "actions": content.actions}
        if mode == "public_environment":
            if content.severity in {"critical", "security_critical"}:
                return {"title": "Attention required", "body": "", "severity": content.severity, "private_hidden": True, "actions": ("acknowledge",)}
            return {"title": "New item", "body": "", "private_hidden": True, "severity": content.severity}
        if mode == "emergency_only":
            if content.severity in {"critical", "security_critical"}:
                return {"title": content.title, "body": content.body[:40], "severity": content.severity}
            return {"title": "Suppressed", "body": "", "suppressed": True, "severity": "informational"}
        return {"title": content.title, "body": content.body, "severity": content.severity, "actions": content.actions}


class GlanceCardSystem:
    """Delivers device-adaptive, glanceable cards."""

    def __init__(
        self,
        connection_factory: Callable[[], sqlite3.Connection],
        devices: DeviceRegistry,
        permissions: DevicePermissionManager,
    ) -> None:
        self._connection_factory = connection_factory
        self._devices = devices
        self._permissions = permissions
        self._lock = threading.RLock()

    def deliver(
        self,
        *,
        device_id: str,
        content: WearableContent,
        session_permissions: Sequence[str] = (),
        privacy_mode: str = "normal",
    ) -> WearableContent:
        device = self._devices.get(device_id)
        if device is None:
            raise WearableError("device not found.")
        if content.deduplication_key and self._has_dedup(device_id, content.deduplication_key):
            return WearableContent(**{**content.model_dump(), "delivery_state": "suppressed"})
        private = content.privacy != "public_safe"
        if private and not (self._permissions.granted(device_id=device_id, permission="display.private_content") or "display.private_content" in session_permissions):
            # Deliver a privacy-safe representation instead of dropping it.
            content = WearableContent(
                **{**content.model_dump(), "title": "Private item", "body": "", "privacy": "private", "actions": ("open_on_desktop",)}
            )
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO wearable_content (
                    content_id, content_type, source, title, body, detail_pages, icon, severity,
                    priority, privacy, actions, expiration, requires_acknowledgement, project, mission,
                    task, workflow, agent, conversation, artifact, deduplication_key, created_at,
                    delivery_state, device_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'delivered', ?)
                """,
                (
                    content.content_id,
                    content.content_type,
                    content.source,
                    content.title,
                    content.body,
                    json.dumps([dict(p) for p in content.detail_pages]),
                    content.icon,
                    content.severity,
                    content.priority,
                    content.privacy,
                    "\n".join(content.actions),
                    content.expiration,
                    1 if content.requires_acknowledgement else 0,
                    content.project,
                    content.mission,
                    content.task,
                    content.workflow,
                    content.agent,
                    content.conversation,
                    content.artifact,
                    content.deduplication_key,
                    content.created_at or _now(),
                    device_id,
                ),
            )
            connection.execute(
                """
                DELETE FROM wearable_content WHERE device_id = ? AND content_id NOT IN (
                    SELECT content_id FROM wearable_content WHERE device_id = ? ORDER BY created_at DESC LIMIT ?
                )
                """,
                (device_id, device_id, MAX_CARDS_PER_DEVICE),
            )
        return self.get(content.content_id)

    def get(self, content_id: str) -> Optional[WearableContent]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM wearable_content WHERE content_id = ?", (content_id,)
            ).fetchone()
        return self._row(row) if row else None

    def list_for_device(self, device_id: str, *, limit: int = 50) -> Tuple[WearableContent, ...]:
        count = max(1, min(200, int(limit)))
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM wearable_content WHERE device_id = ? ORDER BY created_at DESC LIMIT ?",
                (device_id, count),
            ).fetchall()
        return tuple(self._row(row) for row in rows)

    def acknowledge(self, *, device_id: str, content_id: str) -> WearableContent:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE wearable_content SET delivery_state = 'acknowledged' WHERE content_id = ? AND device_id = ?",
                (content_id, device_id),
            )
        return self.get(content_id)

    def _has_dedup(self, device_id: str, key: str) -> bool:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT content_id FROM wearable_content WHERE device_id = ? AND deduplication_key = ? LIMIT 1",
                (device_id, key),
            ).fetchone()
        return row is not None

    @staticmethod
    def _row(row: sqlite3.Row) -> WearableContent:
        return WearableContent(
            content_id=str(row["content_id"]),
            content_type=str(row["content_type"]),
            source=str(row["source"]),
            title=str(row["title"]),
            body=str(row["body"]),
            detail_pages=tuple(dict(item) for item in json.loads(str(row["detail_pages"]))),
            icon=str(row["icon"]),
            severity=str(row["severity"]),
            priority=str(row["priority"]),
            privacy=str(row["privacy"]),
            actions=tuple(p for p in str(row["actions"]).split("\n") if p),
            expiration=str(row["expiration"]),
            requires_acknowledgement=bool(row["requires_acknowledgement"]),
            project=str(row["project"]),
            mission=str(row["mission"]),
            task=str(row["task"]),
            workflow=str(row["workflow"]),
            agent=str(row["agent"]),
            conversation=str(row["conversation"]),
            artifact=str(row["artifact"]),
            deduplication_key=str(row["deduplication_key"]),
            created_at=str(row["created_at"]),
            delivery_state=str(row["delivery_state"]),
        )


class WearableNotificationRouter:
    """Routes notifications to wearables with eligibility and explanation."""

    def __init__(self, cards: GlanceCardSystem, devices: DeviceRegistry) -> None:
        self._cards = cards
        self._devices = devices

    def eligible(
        self,
        *,
        device_id: str,
        severity: str,
        urgency: str,
        category: str = "",
        privacy: str = "private",
        battery_state: str = "unknown",
        thermal_state: str = "unknown",
        dnd_active: bool = False,
        quiet_hours_active: bool = False,
    ) -> Tuple[bool, str]:
        """Return (eligible, explanation). Routine items are not mirrored."""
        device = self._devices.get(device_id)
        if device is None:
            return False, "device not found"
        if device.connection_state != "connected":
            return False, "device not connected"
        if device.revocation_state == "revoked":
            return False, "device trust revoked"
        if privacy == "sensitive":
            return False, "sensitive content not eligible for wearables"
        if battery_state in {"low", "critical"} and urgency != "immediate":
            return False, "low battery; routine delivery reduced"
        if thermal_state in {"warning", "critical"} and urgency != "immediate":
            return False, "thermal pressure; routine delivery reduced"
        if (dnd_active or quiet_hours_active) and severity not in {"critical", "security_critical"}:
            return False, "suppressed by quiet hours or do-not-disturb"
        if severity in {"critical", "security_critical"}:
            return True, "security-critical alert"
        if urgency == "immediate":
            return True, "immediate urgency"
        if category in {"approval_request", "escalation", "mission_escalation", "task_blocker"}:
            return True, "requires attention"
        return False, "routine; not mirrored to wearable"

    def route(
        self,
        *,
        device_id: str,
        severity: str,
        priority: str,
        urgency: str,
        category: str,
        source: str,
        title: str,
        body: str,
        session_permissions: Sequence[str] = (),
        privacy_mode: str = "normal",
        battery_state: str = "unknown",
        thermal_state: str = "unknown",
        dnd_active: bool = False,
        quiet_hours_active: bool = False,
    ) -> Dict[str, object]:
        eligible, reason = self.eligible(
            device_id=device_id,
            severity=severity,
            urgency=urgency,
            category=category,
            battery_state=battery_state,
            thermal_state=thermal_state,
            dnd_active=dnd_active,
            quiet_hours_active=quiet_hours_active,
        )
        if not eligible:
            return {"eligible": False, "reason": reason}
        content = self._cards.deliver(
            device_id=device_id,
            content=WearableContent(
                content_id="card_" + uuid.uuid4().hex[:16],
                content_type="notification_card",
                source=source,
                title=title,
                body=body[:120],
                severity=severity,
                priority=priority,
                privacy="public_safe" if severity in {"critical", "security_critical"} else "private",
                requires_acknowledgement=severity in {"critical", "security_critical"},
                created_at=_now(),
            ),
            session_permissions=session_permissions,
            privacy_mode=privacy_mode,
        )
        return {"eligible": True, "reason": reason, "content_id": content.content_id}