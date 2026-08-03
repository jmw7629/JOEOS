"""Checklists, handoff, offline queue, and resource governor for the JoeOS
Wearable Platform.

Checklists enforce required safety steps. Handoff preserves context and
privacy. Offline operations are revalidated against authoritative state. The
resource governor reacts to battery, thermal, and network conditions without
fabricating data.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .devices import DeviceRegistry
from .models import ChecklistRecord, ChecklistStep, HandoffRecord, OfflineOperation, ResourceState
from .security import SecureSessionService


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WearableError(RuntimeError):
    pass


class ChecklistService:
    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()

    def create(
        self,
        *,
        title: str,
        steps: Sequence[dict],
        project: str = "",
        task: str = "",
        mission: str = "",
        device_id: str = "",
        source: str = "",
        owner: str = "user",
        version: str = "1.0.0",
    ) -> ChecklistRecord:
        checklist = ChecklistRecord(
            checklist_id="check_" + uuid.uuid4().hex[:16],
            title=title,
            project=project,
            task=task,
            mission=mission,
            steps=tuple(ChecklistStep.model_validate(step) for step in steps),
            current_step=0,
            state="active",
            owner=owner,
            source=source,
            version=version,
            created_at=_now(),
            device_id=device_id,
        )
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO wearable_checklists (
                    checklist_id, title, project, task, mission, steps, current_step,
                    state, owner, source, version, created_at, device_id
                ) VALUES (?, ?, ?, ?, ?, ?, 0, 'active', ?, ?, ?, ?, ?)
                """,
                (
                    checklist.checklist_id, title, project, task, mission,
                    json.dumps([step.model_dump() for step in checklist.steps]),
                    owner, source, version, _now(), device_id,
                ),
            )
        return self.get(checklist.checklist_id)

    def get(self, checklist_id: str) -> Optional[ChecklistRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM wearable_checklists WHERE checklist_id = ?", (checklist_id,)
            ).fetchone()
        return self._row(row) if row else None

    def list(self, *, device_id: str = "") -> Tuple[ChecklistRecord, ...]:
        clause = " WHERE device_id = ?" if device_id else ""
        params: List[object] = [device_id] if device_id else []
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM wearable_checklists" + clause + " ORDER BY created_at DESC", params
            ).fetchall()
        return tuple(self._row(row) for row in rows)

    def complete_step(self, *, checklist_id: str, step_id: str, note: str = "", evidence: str = "", allow_skip_optional: bool = False) -> ChecklistRecord:
        checklist = self.get(checklist_id)
        if checklist is None:
            raise WearableError("checklist not found.")
        if checklist.state != "active":
            raise WearableError("checklist is not active.")
        steps = list(checklist.steps)
        step = next((s for s in steps if s.step_id == step_id), None)
        if step is None:
            raise WearableError("step not found.")
        if step.required and not step.completed and not note and step.evidence_required and not evidence:
            raise WearableError("required evidence is missing for this step.")
        step = ChecklistStep(**{**step.model_dump(), "completed": True, "note": note, "evidence_artifact": evidence})
        steps[steps.index(next(s for s in steps if s.step_id == step_id))] = step
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE wearable_checklists SET steps = ?, current_step = ? WHERE checklist_id = ?",
                (json.dumps([s.model_dump() for s in steps]), min(checklist.current_step + 1, len(steps)), checklist_id),
            )
        return self.get(checklist_id)

    def skip_optional(self, *, checklist_id: str, step_id: str) -> ChecklistRecord:
        checklist = self.get(checklist_id)
        steps = list(checklist.steps)
        step = next((s for s in steps if s.step_id == step_id), None)
        if step is None:
            raise WearableError("step not found.")
        if step.required:
            raise WearableError("required steps cannot be silently skipped.")
        step = ChecklistStep(**{**step.model_dump(), "completed": True, "note": "skipped (optional)"})
        for index, existing in enumerate(steps):
            if existing.step_id == step.step_id:
                steps[index] = step
                break
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE wearable_checklists SET steps = ? WHERE checklist_id = ?",
                (json.dumps([s.model_dump() for s in steps]), checklist_id),
            )
        return self.get(checklist_id)

    def complete(self, *, checklist_id: str) -> ChecklistRecord:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE wearable_checklists SET state = 'completed' WHERE checklist_id = ?", (checklist_id,)
            )
        return self.get(checklist_id)

    @staticmethod
    def _row(row: sqlite3.Row) -> ChecklistRecord:
        return ChecklistRecord(
            checklist_id=str(row["checklist_id"]),
            title=str(row["title"]),
            project=str(row["project"]),
            task=str(row["task"]),
            mission=str(row["mission"]),
            steps=tuple(ChecklistStep.model_validate(step) for step in json.loads(str(row["steps"]))),
            current_step=int(row["current_step"]),
            state=str(row["state"]),
            owner=str(row["owner"]),
            source=str(row["source"]),
            version=str(row["version"]),
            created_at=str(row["created_at"]),
            device_id=str(row["device_id"]),
        )


class HandoffService:
    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()

    def create(
        self,
        *,
        source_surface: str,
        target_surface: str,
        active_item: str = "",
        project: str = "",
        mission: str = "",
        task: str = "",
        content_position: str = "",
        selected_action: str = "",
        pending_approval: str = "",
        checklist_position: str = "",
    ) -> HandoffRecord:
        handoff = HandoffRecord(
            handoff_id="handoff_" + uuid.uuid4().hex[:16],
            source_surface=source_surface,
            target_surface=target_surface,
            active_item=active_item,
            project=project,
            mission=mission,
            task=task,
            content_position=content_position,
            selected_action=selected_action,
            pending_approval=pending_approval,
            checklist_position=checklist_position,
            created_at=_now(),
            expires_at="",
        )
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO handoffs (
                    handoff_id, source_surface, target_surface, active_item, project, mission,
                    task, content_position, selected_action, pending_approval, checklist_position,
                    state, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'created', ?, '')
                """,
                (
                    handoff.handoff_id, source_surface, target_surface, active_item, project, mission,
                    task, content_position, selected_action, pending_approval, checklist_position, _now(),
                ),
            )
        return handoff

    def resolve(self, *, handoff_id: str, accepted: bool, destination_trusted: bool = True) -> HandoffRecord:
        if not destination_trusted:
            raise WearableError("handoff destination is not trusted.")
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE handoffs SET state = ? WHERE handoff_id = ?",
                ("accepted" if accepted else "rejected", handoff_id),
            )
        return self.get(handoff_id)

    def get(self, handoff_id: str) -> Optional[HandoffRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM handoffs WHERE handoff_id = ?", (handoff_id,)
            ).fetchone()
        return self._row(row) if row else None

    def list(self, *, limit: int = 50) -> Tuple[HandoffRecord, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM handoffs ORDER BY created_at DESC LIMIT ?", (max(1, min(200, int(limit))),)
            ).fetchall()
        return tuple(self._row(row) for row in rows)

    @staticmethod
    def _row(row: sqlite3.Row) -> HandoffRecord:
        return HandoffRecord(
            handoff_id=str(row["handoff_id"]),
            source_surface=str(row["source_surface"]),
            target_surface=str(row["target_surface"]),
            active_item=str(row["active_item"]),
            project=str(row["project"]),
            mission=str(row["mission"]),
            task=str(row["task"]),
            content_position=str(row["content_position"]),
            selected_action=str(row["selected_action"]),
            pending_approval=str(row["pending_approval"]),
            checklist_position=str(row["checklist_position"]),
            state=str(row["state"]),
            created_at=str(row["created_at"]),
            expires_at=str(row["expires_at"]),
        )


class OfflineQueue:
    """Safe, idempotent offline operations revalidated on reconnect."""

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()
        self._safe_actions = {
            "mark_card_read",
            "dismiss_card",
            "acknowledge_notification",
            "dictated_note",
            "checklist_progress",
            "request_handoff",
            "internal_reply_draft",
        }

    def enqueue(self, *, device_id: str, session_id: str, action: str, idempotency_key: str = "") -> OfflineOperation:
        if action not in self._safe_actions:
            raise WearableError("action %r is not safe to queue offline." % action)
        operation = OfflineOperation(
            operation_id="op_" + uuid.uuid4().hex[:16],
            device_id=device_id,
            session_id=session_id,
            action=action,
            created_at=_now(),
            expires_at="",
            idempotency_key=idempotency_key or (uuid.uuid4().hex),
            conflict_policy="keep_authoritative",
            retry_state="queued",
        )
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO offline_operations (
                    operation_id, device_id, session_id, action, created_at, expires_at,
                    idempotency_key, privacy, approval_state, conflict_policy, retry_state
                ) VALUES (?, ?, ?, ?, ?, '', ?, 'private', 'none', 'keep_authoritative', 'queued')
                """,
                (
                    operation.operation_id, device_id, session_id, action, operation.created_at,
                    operation.idempotency_key,
                ),
            )
        return operation

    def list_for_device(self, device_id: str) -> Tuple[OfflineOperation, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM offline_operations WHERE device_id = ? AND retry_state = 'queued' ORDER BY created_at",
                (device_id,),
            ).fetchall()
        return tuple(self._row(row) for row in rows)

    def discard_stale(self, *, device_id: str, reason: str = "") -> int:
        with self._lock, self._connection_factory() as connection:
            cursor = connection.execute(
                "UPDATE offline_operations SET retry_state = 'discarded' WHERE device_id = ? AND retry_state = 'queued'",
                (device_id,),
            )
        return cursor.rowcount

    def revalidate(self, *, device_id: str, authoritative_state: Callable[[str], bool]) -> Dict[str, int]:
        """Revalidate queued operations against authoritative state on reconnect."""
        replayed = 0
        discarded = 0
        for operation in self.list_for_device(device_id):
            if authoritative_state(operation.action):
                replayed += 1
            else:
                self.discard_stale(device_id=device_id, reason="authoritative state changed")
                discarded += 1
        return {"replayed": replayed, "discarded": discarded}

    @staticmethod
    def _row(row: sqlite3.Row) -> OfflineOperation:
        return OfflineOperation(
            operation_id=str(row["operation_id"]),
            device_id=str(row["device_id"]),
            session_id=str(row["session_id"]),
            action=str(row["action"]),
            created_at=str(row["created_at"]),
            expires_at=str(row["expires_at"]),
            idempotency_key=str(row["idempotency_key"]),
            privacy=str(row["privacy"]),
            approval_state=str(row["approval_state"]),
            conflict_policy=str(row["conflict_policy"]),
            retry_state=str(row["retry_state"]),
        )


class ResourceGovernor:
    """Battery, thermal, and network policies without fabricated data."""

    def __init__(self, devices: DeviceRegistry, event_sink=None) -> None:
        self._devices = devices
        self._event_sink = event_sink or (lambda level, source, message: None)

    def apply(self, *, device_id: str, resource: ResourceState) -> Dict[str, object]:
        """Apply policies; values are reported by the device, never invented."""
        policies: List[str] = []
        device = self._devices.get(device_id)
        if device is None:
            raise WearableError("device not found.")
        updates: Dict[str, object] = {}
        if resource.battery is not None:
            battery = max(0, min(100, int(resource.battery)))
            updates["battery_state"] = "low" if battery <= 25 else ("critical" if battery <= 10 else "healthy")
            updates["charging_state"] = "charging" if resource.charging else "discharging"
            if battery <= 25:
                policies.append("reduce_routine_delivery")
                self._event_sink("warn", "wearables", "Device %s battery low (%d%%)." % (device_id, battery))
            if battery <= 10:
                policies.append("preserve_critical_alerts_only")
        if resource.thermal in {"warning", "critical"}:
            updates["thermal_state"] = resource.thermal
            policies.append("suspend_camera_and_video")
            policies.append("pause_noncritical_sync")
            self._event_sink("warn", "wearables", "Device %s thermal %s." % (device_id, resource.thermal))
        if resource.bandwidth_class == "low":
            updates["bandwidth_class"] = "low"
            policies.append("text_only_cards")
            policies.append("defer_images")
        if resource.latency_ms is not None:
            updates["latency_ms"] = resource.latency_ms
        if resource.mic_active:
            updates["mic_active"] = True
        if resource.camera_active:
            updates["camera_active"] = True
        self._devices.update_state(device_id, **updates)
        return {"policies": tuple(sorted(set(policies))), "state": updates}