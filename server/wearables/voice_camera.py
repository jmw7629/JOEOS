"""Voice, camera, and vision gateways for the JoeOS Wearable Platform.

Voice is push-to-talk by default, local-first, permission controlled, with a
visible recording indicator. Camera capture is explicit, permission gated,
with enforced recording indicators. Vision output never authorizes an action.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Callable, Dict, Optional, Sequence, Tuple

from .devices import DeviceRegistry
from .models import CameraCapture, VoiceIntent, VoiceIntentRecord, VisionResult
from .permissions import DevicePermissionManager, PermissionError
from .security import SecureSessionService

# A constrained voice-intent classifier. Real intents would be produced by a
# local model; this maps exact/loose phrases to intents with a confidence and
# marks ambiguous inputs for clarification.
_INTENT_KEYWORDS: Dict[str, VoiceIntent] = {
    "open mission": "open_item",
    "open item": "open_item",
    "show next task": "open_item",
    "acknowledge": "acknowledge_notification",
    "dismiss": "dismiss_notification",
    "snooze": "snooze",
    "note": "dictate_note",
    "create task": "create_task_proposal",
    "search project": "search_project",
    "status": "read_status",
    "checklist": "start_checklist",
    "pause workflow": "pause_workflow",
    "cancel task": "cancel_task",
    "approve": "approve_bounded_action",
    "deny": "deny_action",
    "hand off to desktop": "handoff_desktop",
    "stop listening": "stop_listening",
}

_HIGH_RISK_INTENTS = {"approve_bounded_action", "cancel_task"}

_VOICE_INTENT_PERMISSIONS: Dict[str, str] = {
    "dictate_note": "joeos.create_note",
    "create_task_proposal": "joeos.create_task_proposal",
    "approve_bounded_action": "joeos.approve_low_risk",
    "cancel_task": "joeos.deny_action",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RecordingIndicatorError(RuntimeError):
    pass


class VoiceGateway:
    """Push-to-talk, local-first voice interaction with visible indicators."""

    def __init__(
        self,
        *,
        connection_factory: Callable[[], sqlite3.Connection],
        devices: DeviceRegistry,
        sessions: SecureSessionService,
        permissions: DevicePermissionManager,
        transcription=None,
        event_sink=None,
    ) -> None:
        self._connection_factory = connection_factory
        self._devices = devices
        self._sessions = sessions
        self._permissions = permissions
        self._transcribe = transcription or (lambda audio_ref: {"transcript": "", "confidence": 0.0, "ambiguous": True})
        self._event_sink = event_sink or (lambda level, source, message: None)
        self._lock = threading.RLock()

    def start_session(self, *, device_id: str, session_id: str, push_to_talk: bool = True) -> dict:
        if not self._sessions.is_valid(session_id):
            raise PermissionError("device session is not valid.")
        if not self._permissions.granted(device_id=device_id, permission="audio.input_microphone"):
            raise PermissionError("device lacks microphone permission.")
        self._devices.update_state(device_id, mic_active=True)
        # Recording indicators are enforced: mic_active is visible in Device
        # Manager and audit events. Plugins cannot hide this state.
        self._event_sink("info", "wearables", "Microphone activated on %s." % device_id)
        return {"state": "recording", "mode": "push_to_talk" if push_to_talk else "bounded", "indicator": True}

    def stop_session(self, *, device_id: str, session_id: str) -> dict:
        self._devices.update_state(device_id, mic_active=False)
        self._event_sink("info", "wearables", "Microphone deactivated on %s." % device_id)
        return {"state": "stopped", "indicator": False}

    def transcribe(self, *, device_id: str, session_id: str, audio_reference: str, mode: str = "push_to_talk") -> dict:
        if not self._sessions.is_valid(session_id):
            raise PermissionError("device session is not valid.")
        if not self._devices.get(device_id).mic_active:
            raise PermissionError("microphone session is not active.")
        result = self._transcribe(audio_reference)
        transcript = str(result.get("transcript") or "")
        confidence = float(result.get("confidence") or 0.0)
        intent, ambiguous = self._classify(transcript, confidence)
        intent_id = "intent_" + uuid.uuid4().hex[:16]
        required_permission = _VOICE_INTENT_PERMISSIONS.get(intent, "")
        required_confirmation = "high" if intent in _HIGH_RISK_INTENTS else "low"
        record = VoiceIntentRecord(
            intent_id=intent_id,
            device_id=device_id,
            session_id=session_id,
            transcript=transcript,
            normalized_intent=intent,
            confidence=confidence,
            ambiguous=ambiguous,
            required_permissions=(required_permission,) if required_permission else (),
            required_confirmation=required_confirmation,
            source_device=device_id,
            active_context="",
            model_source="local",
            created_at=_now(),
        )
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO voice_intents (
                    intent_id, device_id, session_id, transcript, normalized_intent, entities,
                    confidence, ambiguous, required_permissions, required_confirmation,
                    source_device, active_context, model_source, created_at
                ) VALUES (?, ?, ?, ?, ?, '{}', ?, ?, ?, ?, ?, '', 'local', ?)
                """,
                (
                    intent_id, device_id, session_id, transcript, intent, confidence,
                    1 if ambiguous else 0, "\n".join(record.required_permissions),
                    required_confirmation, device_id, _now(),
                ),
            )
        return {
            "intent_id": intent_id,
            "transcript": transcript,
            "intent": intent,
            "confidence": confidence,
            "ambiguous": ambiguous,
            "required_confirmation": required_confirmation,
        }

    def _classify(self, transcript: str, confidence: float) -> Tuple[str, bool]:
        lowered = (transcript or "").lower().strip()
        if not lowered or confidence < 0.4:
            return "ask_question", True
        for keyword, intent in _INTENT_KEYWORDS.items():
            if keyword in lowered:
                return intent, confidence < 0.7
        return "ask_question", confidence < 0.6

    def confirm_high_risk(self, *, device_id: str, session_id: str, intent_id: str, interactive_confirm: bool = False) -> dict:
        """High-risk voice intents can never be approved by voice alone."""
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM voice_intents WHERE intent_id = ?", (intent_id,)
            ).fetchone()
        if row is None:
            raise PermissionError("voice intent not found.")
        intent = str(row["normalized_intent"])
        if intent not in _HIGH_RISK_INTENTS:
            raise PermissionError("intent does not require high-risk confirmation.")
        if not interactive_confirm:
            return {"state": "escalated", "reason": "high-risk voice action requires desktop or companion confirmation."}
        return {"state": "confirmed", "intent": intent}


class CameraGateway:
    """Explicit, permission-gated camera capture with enforced indicators."""

    def __init__(
        self,
        *,
        connection_factory: Callable[[], sqlite3.Connection],
        devices: DeviceRegistry,
        sessions: SecureSessionService,
        permissions: DevicePermissionManager,
        event_sink=None,
    ) -> None:
        self._connection_factory = connection_factory
        self._devices = devices
        self._sessions = sessions
        self._permissions = permissions
        self._event_sink = event_sink or (lambda level, source, message: None)
        self._lock = threading.RLock()

    def capture(
        self,
        *,
        device_id: str,
        session_id: str,
        mode: str = "still_image",
        retention_policy: str = "process_and_delete",
        local_only: bool = True,
        explicit_user_action: bool = True,
    ) -> CameraCapture:
        if not self._sessions.is_valid(session_id):
            raise PermissionError("device session is not valid.")
        if not explicit_user_action:
            raise PermissionError("camera capture requires an explicit user action.")
        if not self._permissions.granted(device_id=device_id, permission="camera.capture_still"):
            raise PermissionError("device lacks camera permission.")
        if mode == "video" and not self._permissions.granted(device_id=device_id, permission="camera.capture_video"):
            raise PermissionError("video capture is disabled by default.")
        if not local_only and not self._permissions.granted(device_id=device_id, permission="camera.send_to_model"):
            raise PermissionError("sending images to a model requires permission.")
        self._devices.update_state(device_id, camera_active=True)
        capture = CameraCapture(
            capture_id="capture_" + uuid.uuid4().hex[:16],
            device_id=device_id,
            session_id=session_id,
            mode=mode,
            permission_state="granted",
            recording_indicator=True,
            privacy_classification="private",
            retention_policy=retention_policy,
            local_only=local_only,
            created_at=_now(),
        )
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO camera_captures (
                    capture_id, device_id, session_id, mode, permission_state, recording_indicator,
                    artifact_reference, privacy_classification, retention_policy, local_only, created_at, stopped_at
                ) VALUES (?, ?, ?, ?, 'granted', 1, '', ?, ?, ?, ?, '')
                """,
                (
                    capture.capture_id, device_id, session_id, mode, capture.privacy_classification,
                    retention_policy, 1 if local_only else 0, _now(),
                ),
            )
        self._event_sink("info", "wearables", "Camera capture started on %s." % device_id)
        return capture

    def stop(self, *, device_id: str, capture_id: str) -> dict:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE camera_captures SET stopped_at = ? WHERE capture_id = ?",
                (_now(), capture_id),
            )
        self._devices.update_state(device_id, camera_active=False)
        self._event_sink("info", "wearables", "Camera capture stopped on %s." % device_id)
        return {"stopped": True, "capture_id": capture_id}

    def stop_all(self, *, device_id: str) -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE camera_captures SET stopped_at = ? WHERE device_id = ? AND stopped_at = ''",
                (_now(), device_id),
            )
        self._devices.update_state(device_id, camera_active=False)


class VisionGateway:
    """Local-first vision analysis; output never authorizes an action."""

    def __init__(self, vision=None) -> None:
        self._vision = vision or (lambda image_ref: {"summary": "", "confidence": 0.0, "labels": ()})

    def analyze(self, *, capture: CameraCapture, image_reference: str, local_only: bool = True) -> VisionResult:
        if capture.recording_indicator is False:
            raise RecordingIndicatorError("cannot analyze a capture with a hidden recording indicator.")
        result = self._vision(image_reference)
        return VisionResult(
            result_id="vision_" + uuid.uuid4().hex[:16],
            capture_id=capture.capture_id,
            summary=str(result.get("summary") or ""),
            confidence=float(result.get("confidence") or 0.0),
            uncertain=bool(result.get("uncertain", float(result.get("confidence") or 0.0) < 0.6)),
            labels=tuple(result.get("labels") or ()),
            model_source="local" if local_only else "approved_cloud",
            created_at=_now(),
        )