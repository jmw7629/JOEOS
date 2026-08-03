"""Typed contracts for the JoeOS Smart Glasses and Wearable Device Platform.

Devices, adapters, capabilities, sessions, pairing challenges, glance cards,
interaction events, voice intents, camera captures, checklists, handoffs, and
offline operations are expressed as strict, versioned, extra-forbidden models.
No model carries authority by itself; enforcement lives in the pairing, trust,
authentication, permission, and connection services.
"""

from __future__ import annotations

import re
from typing import Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

WEARABLES_SCHEMA_VERSION = 1
API_VERSION = 1

ID_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,79}$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


# ---------------------------------------------------------------------------
# Device types & capabilities
# ---------------------------------------------------------------------------

DeviceType = Literal[
    "display_glasses",
    "audio_glasses",
    "camera_glasses",
    "display_audio_glasses",
    "camera_display_glasses",
    "monocular_hud",
    "binocular_hud",
    "mixed_reality_headset",
    "industrial_headset",
    "wearable_display",
    "accessibility_display",
    "wearable_microphone",
    "wearable_speaker",
    "mobile_relay_device",
    "simulated_development_device",
]

CapabilityID = Literal[
    "display.text_card",
    "display.rich_card",
    "display.image",
    "display.color",
    "display.grayscale",
    "display.multiple_pages",
    "display.persistent_overlay",
    "audio.output_tone",
    "audio.output_speech",
    "audio.output_media",
    "audio.output_bone_conduction",
    "audio.input_microphone",
    "audio.input_push_to_talk",
    "audio.input_wake_word",
    "audio.input_continuous",
    "camera.still_image",
    "camera.video",
    "camera.qr_scan",
    "input.button",
    "input.touch",
    "input.gesture",
    "input.gaze",
    "input.voice",
    "sensor.accelerometer",
    "sensor.gyroscope",
    "sensor.compass",
    "sensor.ambient_light",
    "sensor.proximity",
    "sensor.location",
    "sensor.battery",
    "sensor.temperature",
    "connectivity.bluetooth",
    "connectivity.ble",
    "connectivity.wifi",
    "connectivity.usb",
    "connectivity.local_network",
    "connectivity.mobile_relay",
    "connectivity.simulator",
]

CapabilityState = Literal[
    "available",
    "available_with_limits",
    "temporarily_unavailable",
    "disabled",
    "permission_blocked",
    "unsupported",
    "unknown",
]


class CapabilityRecord(StrictModel):
    capability_id: CapabilityID
    device_id: str
    adapter_id: str = ""
    support_state: CapabilityState = "unknown"
    verification_state: Literal["unverified", "verified", "negotiated"] = "unverified"
    permission_requirement: str = ""
    privacy_classification: str = "private"
    resource_cost: Literal["low", "medium", "high"] = "low"
    limitations: str = ""
    health: str = "unknown"


class DeviceTypeInfo(StrictModel):
    device_type: DeviceType
    display_name: str
    description: str = ""
    # Device type never implies capabilities; this is metadata only.
    typical_transports: Tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Device record & lifecycle
# ---------------------------------------------------------------------------

DeviceState = Literal[
    "discovered",
    "unpaired",
    "pairing",
    "pairing_failed",
    "paired",
    "trusted",
    "restricted",
    "connecting",
    "connected",
    "disconnected",
    "reconnecting",
    "unavailable",
    "authentication_failed",
    "permission_blocked",
    "degraded",
    "low_battery",
    "thermal_warning",
    "firmware_incompatible",
    "adapter_unavailable",
    "quarantined",
    "revoked",
    "removed",
    "unknown",
]

Transport = Literal["bluetooth", "ble", "usb", "serial", "local_network", "qr", "companion_relay", "manual", "simulator"]


class DeviceRecord(StrictModel):
    device_id: str
    device_type: DeviceType
    display_name: str
    manufacturer: str = ""
    model: str = ""
    hardware_revision: str = ""
    firmware_version: str = ""
    adapter_id: str = ""
    plugin_id: str = ""
    transport: Transport = "local_network"
    connection_address_reference: str = ""
    paired_state: Literal["unpaired", "paired", "pairing", "pairing_failed"] = "unpaired"
    trusted_state: str = "untrusted"
    authentication_state: str = "unauthenticated"
    key_reference: str = ""
    user_owned: bool = True
    verified_capabilities: Tuple[str, ...] = ()
    disabled_capabilities: Tuple[str, ...] = ()
    connection_state: DeviceState = "discovered"
    battery_state: str = "unknown"
    charging_state: str = "unknown"
    thermal_state: str = "unknown"
    network_state: str = "unknown"
    latency_ms: Optional[int] = None
    bandwidth_class: str = "unknown"
    health: str = "unknown"
    privacy_mode: str = "normal"
    mic_active: bool = False
    camera_active: bool = False
    last_connected: str = ""
    last_disconnected: str = ""
    last_firmware_check: str = ""
    created_at: str = ""
    revocation_state: str = "active"
    deletion_state: str = "active"

    @field_validator("device_id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if ID_PATTERN.fullmatch(value) is None:
            raise ValueError("device id must be a lowercase dotted identifier.")
        return value


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------

AdapterState = Literal["registered", "enabled", "disabled", "quarantined", "crashed", "unknown"]


class AdapterRecord(StrictModel):
    adapter_id: str
    plugin_id: str = ""
    display_name: str
    supported_manufacturers: Tuple[str, ...] = ()
    supported_transports: Tuple[str, ...] = ()
    supports_discovery: bool = False
    supports_pairing: bool = True
    supported_capabilities: Tuple[CapabilityID, ...] = ()
    state: AdapterState = "registered"
    version: str = ""
    platform: str = ""
    health: str = "unknown"
    is_simulator: bool = False
    known_limitations: str = ""


# ---------------------------------------------------------------------------
# Pairing & trust
# ---------------------------------------------------------------------------

class PairingChallenge(StrictModel):
    challenge_id: str
    device_id: str
    adapter_id: str
    method: Literal["qr", "one_time_code", "numeric_comparison", "companion_app", "manual", "simulator"] = "one_time_code"
    code_reference: str = ""
    expires_at: str = ""
    used: bool = False
    state: Literal["pending", "confirmed", "expired", "cancelled"] = "pending"
    created_at: str = ""


class DeviceTrust(StrictModel):
    device_id: str
    trust_state: Literal[
        "untrusted",
        "paired_but_restricted",
        "user_trusted",
        "session_trusted",
        "capability_scoped",
        "project_scoped",
        "degraded",
        "reauthentication_required",
        "revoked",
        "quarantined",
    ] = "untrusted"
    scope: str = "session"
    scope_target: str = ""
    capabilities: Tuple[str, ...] = ()
    granted_at: str = ""
    revocation_reason: str = ""


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

class DeviceSession(StrictModel):
    session_id: str
    device_id: str
    adapter_id: str = ""
    authenticated_user: str = "user"
    started_at: str = ""
    expires_at: str = ""
    transport: str = ""
    encryption_state: str = "encrypted"
    permissions: Tuple[str, ...] = ()
    capabilities: Tuple[str, ...] = ()
    active_views: Tuple[str, ...] = ()
    notification_queue: Tuple[str, ...] = ()
    bandwidth_policy: str = "normal"
    privacy_mode: str = "normal"
    activity_state: str = "idle"
    last_heartbeat: str = ""
    risk_state: str = "normal"
    termination_reason: str = ""
    connection_state: str = "idle"


# ---------------------------------------------------------------------------
# Wearable content (cards)
# ---------------------------------------------------------------------------

ContentType = Literal[
    "glance_card",
    "notification_card",
    "mission_card",
    "task_card",
    "approval_card",
    "communication_card",
    "checklist_card",
    "navigation_step_card",
    "timer_card",
    "status_card",
    "incident_card",
    "image_card",
    "voice_response_card",
    "progress_card",
    "device_warning_card",
    "privacy_warning_card",
]

Severity = Literal["informational", "success", "warning", "error", "critical", "security_critical"]
Priority = Literal["low", "normal", "high", "urgent"]


class WearableContent(StrictModel):
    content_id: str
    content_type: ContentType
    source: str
    title: str
    body: str = ""
    detail_pages: Tuple[Dict[str, str], ...] = ()
    icon: str = ""
    severity: Severity = "informational"
    priority: Priority = "normal"
    privacy: Literal["public_safe", "private", "sensitive"] = "private"
    actions: Tuple[str, ...] = ()
    expiration: str = ""
    requires_acknowledgement: bool = False
    project: str = ""
    mission: str = ""
    task: str = ""
    workflow: str = ""
    agent: str = ""
    conversation: str = ""
    artifact: str = ""
    deduplication_key: str = ""
    created_at: str = ""
    delivery_state: Literal["pending", "delivered", "acknowledged", "expired", "suppressed"] = "pending"


class CardAck(StrictModel):
    content_id: str
    device_id: str
    session_id: str
    acknowledged_at: str = ""


# ---------------------------------------------------------------------------
# Interaction events & commands
# ---------------------------------------------------------------------------

InputType = Literal["button", "long_press", "double_press", "touch", "swipe", "tap", "head_nod", "head_shake", "gaze_dwell", "voice_phrase", "companion_confirmation", "gesture"]


class InteractionEvent(StrictModel):
    event_id: str
    device_id: str
    session_id: str
    input_type: InputType
    timestamp: str = ""
    confidence: Optional[float] = None
    normalized_action: str = ""
    active_content: str = ""
    permission_state: str = "denied"
    duplicate: bool = False
    expiration: str = ""


class CommandRequest(StrictModel):
    request_id: str
    device_id: str
    session_id: str
    command: str
    params: Dict[str, object] = Field(default_factory=dict)
    risk: Literal["low", "medium", "high"] = "low"
    confirmation_level: Literal["none", "low", "medium", "high"] = "low"
    created_at: str = ""


# ---------------------------------------------------------------------------
# Voice & camera
# ---------------------------------------------------------------------------

VoiceIntent = Literal[
    "ask_question",
    "open_item",
    "acknowledge_notification",
    "dismiss_notification",
    "snooze",
    "dictate_note",
    "create_task_proposal",
    "search_project",
    "read_status",
    "start_checklist",
    "continue_checklist",
    "pause_workflow",
    "cancel_task",
    "request_approval_details",
    "approve_bounded_action",
    "deny_action",
    "handoff_desktop",
    "handoff_phone",
    "stop_listening",
]


class VoiceIntentRecord(StrictModel):
    intent_id: str
    device_id: str
    session_id: str
    transcript: str
    normalized_intent: VoiceIntent
    entities: Dict[str, object] = Field(default_factory=dict)
    confidence: float = 0.0
    ambiguous: bool = False
    required_permissions: Tuple[str, ...] = ()
    required_confirmation: Literal["none", "low", "medium", "high"] = "low"
    source_device: str = ""
    active_context: str = ""
    model_source: str = "local"
    created_at: str = ""


class CameraCapture(StrictModel):
    capture_id: str
    device_id: str
    session_id: str
    mode: Literal["still_image", "bounded_burst", "document", "qr_scan", "preview"] = "still_image"
    permission_state: str = "denied"
    recording_indicator: bool = True
    artifact_reference: str = ""
    privacy_classification: str = "private"
    retention_policy: Literal["process_and_delete", "session", "task_artifact", "project_artifact", "until_user_deletes", "never_store", "local_only"] = "process_and_delete"
    local_only: bool = True
    created_at: str = ""
    stopped_at: str = ""


class VisionResult(StrictModel):
    result_id: str
    capture_id: str
    summary: str = ""
    confidence: float = 0.0
    uncertain: bool = True
    labels: Tuple[str, ...] = ()
    model_source: str = "local"
    created_at: str = ""


# ---------------------------------------------------------------------------
# Checklists & handoff & offline
# ---------------------------------------------------------------------------

class ChecklistStep(StrictModel):
    step_id: str
    title: str
    required: bool = True
    evidence_required: bool = False
    safety_warning: str = ""
    completed: bool = False
    note: str = ""
    evidence_artifact: str = ""


class ChecklistRecord(StrictModel):
    checklist_id: str
    title: str
    project: str = ""
    task: str = ""
    mission: str = ""
    steps: Tuple[ChecklistStep, ...] = ()
    current_step: int = 0
    state: Literal["active", "paused", "completed", "cancelled"] = "active"
    owner: str = "user"
    source: str = ""
    version: str = "1.0.0"
    created_at: str = ""
    device_id: str = ""


class HandoffRecord(StrictModel):
    handoff_id: str
    source_surface: str
    target_surface: str
    active_item: str = ""
    project: str = ""
    mission: str = ""
    task: str = ""
    content_position: str = ""
    selected_action: str = ""
    pending_approval: str = ""
    checklist_position: str = ""
    state: Literal["created", "accepted", "rejected", "expired"] = "created"
    created_at: str = ""
    expires_at: str = ""


class OfflineOperation(StrictModel):
    operation_id: str
    device_id: str
    session_id: str
    action: str
    created_at: str = ""
    expires_at: str = ""
    idempotency_key: str = ""
    privacy: str = "private"
    approval_state: str = "none"
    conflict_policy: str = "keep_authoritative"
    retry_state: str = "queued"


class ResourceState(StrictModel):
    device_id: str
    battery: Optional[int] = None
    charging: bool = False
    thermal: str = "unknown"
    latency_ms: Optional[int] = None
    bandwidth_class: str = "unknown"
    mic_active: bool = False
    camera_active: bool = False
    reported_at: str = ""


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------

class WearablesOverview(StrictModel):
    paired_devices: int
    connected_devices: int
    active_sessions: int
    trusted_devices: int
    revoked_devices: int
    quarantined_adapters: int
    mic_active: int
    camera_active: int
    pending_wearable_approvals: int
    offline_operations: int
    low_battery_devices: int
    thermal_warning_devices: int
    privacy_mode_devices: int
    generated_at: str