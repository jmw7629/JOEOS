"""REST API for the JoeOS Smart Glasses and Wearable Device Platform.

Every endpoint reads and mutates real device, session, capability, and
permission state. No fake paired devices, batteries, thermal readings, camera
feeds, or microphones are returned; only the isolated simulator produces
devices. Pairing codes, session keys, and device secrets are never exposed.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from .models import (
    CameraCapture,
    ChecklistRecord,
    DeviceRecord,
    DeviceSession,
    DeviceTrust,
    HandoffRecord,
    OfflineOperation,
    PairingChallenge,
    WearableContent,
    WearablesOverview,
    AdapterRecord,
    CapabilityRecord,
)
from .permissions import PermissionError
from .service import WearableService
from .simulator import WearableSimulator

router = APIRouter(prefix="/api/v1/wearables", tags=["wearables"])


def get_wearable_service(request: Request) -> WearableService:
    service = getattr(request.app.state, "wearables_service", None)
    if service is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Wearable service is not initialized.")
    return service


def _require_device(service: WearableService, device_id: str) -> DeviceRecord:
    device = service.get_device(device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found.")
    return device


# ---- overview ----

@router.get("/overview", response_model=WearablesOverview)
def overview(service: WearableService = Depends(get_wearable_service)) -> WearablesOverview:
    return service.overview()


# ---- devices / adapters / capabilities ----

@router.get("/devices")
def list_devices(service: WearableService = Depends(get_wearable_service)) -> dict:
    return {"devices": [record.model_dump() for record in service.list_devices()]}


@router.get("/devices/{device_id}", response_model=DeviceRecord)
def get_device(device_id: str, service: WearableService = Depends(get_wearable_service)) -> DeviceRecord:
    return _require_device(service, device_id)


@router.get("/devices/{device_id}/capabilities")
def device_capabilities(device_id: str, service: WearableService = Depends(get_wearable_service)) -> dict:
    _require_device(service, device_id)
    return {"capabilities": [record.model_dump() for record in service.device_capabilities(device_id)]}


@router.get("/devices/{device_id}/permissions")
def device_permissions(device_id: str, service: WearableService = Depends(get_wearable_service)) -> dict:
    _require_device(service, device_id)
    return {"grants": list(service.device_permissions(device_id))}


@router.post("/devices/{device_id}/permissions/grant")
def grant_permission(
    device_id: str,
    payload: PermissionRequest,
    service: WearableService = Depends(get_wearable_service),
) -> dict:
    _require_device(service, device_id)
    service.grant_permission(device_id=device_id, permission=payload.permission, scope=payload.scope, scope_target=payload.scope_target)
    return {"granted": payload.permission}


@router.post("/devices/{device_id}/permissions/revoke")
def revoke_permission(
    device_id: str,
    payload: PermissionRequest,
    service: WearableService = Depends(get_wearable_service),
) -> dict:
    _require_device(service, device_id)
    service.revoke_permission(device_id=device_id, permission=payload.permission, scope_target=payload.scope_target)
    return {"revoked": payload.permission}


@router.get("/adapters")
def list_adapters(service: WearableService = Depends(get_wearable_service)) -> dict:
    return {"adapters": [record.model_dump() for record in service.list_adapters()]}


@router.get("/device-types")
def device_types(service: WearableService = Depends(get_wearable_service)) -> dict:
    return {"device_types": [info.model_dump() for info in service.device_types()]}


# ---- simulator ----

@router.get("/simulator/profiles")
def simulator_profiles(service: WearableService = Depends(get_wearable_service)) -> dict:
    return {"profiles": list(service.simulator_profiles())}


@router.post("/simulator/devices", status_code=status.HTTP_201_CREATED, response_model=DeviceRecord)
def create_simulator_device(payload: SimulatorRequest, service: WearableService = Depends(get_wearable_service)) -> DeviceRecord:
    return service.create_simulator_device(profile=payload.profile, display_name=payload.display_name)


@router.post("/simulator/pairing-code/{challenge_id}")
def simulator_pairing_code(challenge_id: str, service: WearableService = Depends(get_wearable_service)) -> dict:
    # Simulator-only fixture code; never used for production devices.
    code = service.simulator.fixture_code(challenge_id)
    return {"code": code, "simulator_only": True}


# ---- discovery / pairing / trust / sessions ----

@router.post("/discovery")
def discover(payload: DiscoveryRequest, service: WearableService = Depends(get_wearable_service)) -> dict:
    return {"devices": list(service.discover(adapter_id=payload.adapter_id, discovered=payload.devices or []))}


@router.post("/pairing", status_code=status.HTTP_201_CREATED)
def begin_pairing(payload: PairingRequest, service: WearableService = Depends(get_wearable_service)) -> PairingChallenge:
    return service.begin_pairing(device_id=payload.device_id, method=payload.method)


@router.post("/pairing/{challenge_id}/confirm")
def confirm_pairing(challenge_id: str, payload: PairingConfirmRequest, service: WearableService = Depends(get_wearable_service)) -> DeviceTrust:
    try:
        return service.confirm_pairing(challenge_id=challenge_id, code=payload.code)
    except PermissionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/devices/{device_id}/trust")
def trust_device(device_id: str, payload: TrustRequest, service: WearableService = Depends(get_wearable_service)) -> DeviceTrust:
    return service.trust_device(device_id=device_id, level=payload.level, scope=payload.scope, scope_target=payload.scope_target, capabilities=tuple(payload.capabilities))


@router.post("/devices/{device_id}/revoke")
def revoke_device(device_id: str, payload: RevokeRequest, service: WearableService = Depends(get_wearable_service)) -> DeviceRecord:
    _require_device(service, device_id)
    return service.revoke_device(device_id=device_id, reason=payload.reason)


@router.get("/devices/{device_id}/trust")
def device_trust(device_id: str, service: WearableService = Depends(get_wearable_service)) -> DeviceTrust:
    return service.device_trust(device_id)


@router.post("/devices/{device_id}/connect")
def connect_device(device_id: str, payload: Optional[ConnectRequest] = None, service: WearableService = Depends(get_wearable_service)) -> DeviceSession:
    payload = payload or ConnectRequest()
    try:
        return service.connect_device(device_id=device_id, capabilities=tuple(payload.capabilities), permissions=tuple(payload.permissions))
    except PermissionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/devices/{device_id}/disconnect")
def disconnect_device(device_id: str, service: WearableService = Depends(get_wearable_service)) -> dict:
    service.disconnect_device(device_id=device_id)
    return {"disconnected": device_id}


@router.get("/sessions")
def active_sessions(service: WearableService = Depends(get_wearable_service)) -> dict:
    return {"sessions": [record.model_dump() for record in service.active_sessions()]}


@router.post("/sessions/{session_id}/heartbeat")
def heartbeat(session_id: str, service: WearableService = Depends(get_wearable_service)) -> dict:
    service.heartbeat(session_id)
    return {"ok": True}


@router.post("/devices/{device_id}/auth/challenge")
def auth_challenge(device_id: str, service: WearableService = Depends(get_wearable_service)) -> dict:
    nonce_id, nonce = service.create_auth_nonce(device_id=device_id)
    return {"nonce_id": nonce_id, "nonce": nonce}


@router.post("/devices/{device_id}/auth/verify")
def auth_verify(device_id: str, payload: AuthVerifyRequest, service: WearableService = Depends(get_wearable_service)) -> dict:
    verified = service.verify_auth(nonce_id=payload.nonce_id, device_id=device_id, signed_nonce=payload.signed_nonce)
    return {"verified": verified}


# ---- cards / privacy / routing ----

@router.post("/devices/{device_id}/cards", status_code=status.HTTP_201_CREATED)
def deliver_card(device_id: str, payload: CardRequest, service: WearableService = Depends(get_wearable_service)) -> WearableContent:
    _require_device(service, device_id)
    return service.deliver_card(
        device_id=device_id,
        content=payload.content,
        session_permissions=tuple(payload.session_permissions),
    )


@router.get("/devices/{device_id}/cards")
def cards_for_device(device_id: str, service: WearableService = Depends(get_wearable_service)) -> dict:
    _require_device(service, device_id)
    return {"cards": [card.model_dump() for card in service.cards_for_device(device_id)]}


@router.post("/devices/{device_id}/cards/{content_id}/acknowledge")
def acknowledge_card(device_id: str, content_id: str, service: WearableService = Depends(get_wearable_service)) -> WearableContent:
    return service.acknowledge_card(device_id=device_id, content_id=content_id)


@router.post("/devices/{device_id}/privacy-mode")
def set_privacy_mode(device_id: str, payload: PrivacyRequest, service: WearableService = Depends(get_wearable_service)) -> dict:
    mode = service.set_privacy_mode(device_id=device_id, mode=payload.mode)
    return {"privacy_mode": mode}


@router.post("/devices/{device_id}/route-notification")
def route_notification(device_id: str, payload: RouteNotificationRequest, service: WearableService = Depends(get_wearable_service)) -> dict:
    _require_device(service, device_id)
    return service.route_notification(
        device_id=device_id,
        severity=payload.severity,
        priority=payload.priority,
        urgency=payload.urgency,
        category=payload.category,
        source=payload.source,
        title=payload.title,
        body=payload.body,
        battery_state=payload.battery_state,
        thermal_state=payload.thermal_state,
        dnd_active=payload.dnd_active,
        quiet_hours_active=payload.quiet_hours_active,
    )


# ---- interaction / commands ----

@router.post("/devices/{device_id}/interactions")
def record_interaction(device_id: str, payload: InteractionRequest, service: WearableService = Depends(get_wearable_service)) -> dict:
    event = service.record_interaction(
        device_id=device_id,
        session_id=payload.session_id,
        input_type=payload.input_type,
        normalized_action=payload.normalized_action,
        confidence=payload.confidence,
    )
    return event.model_dump()


@router.post("/devices/{device_id}/commands")
def execute_command(device_id: str, payload: CommandRequestModel, service: WearableService = Depends(get_wearable_service)) -> dict:
    try:
        return service.execute_command(
            device_id=device_id,
            session_id=payload.session_id,
            command=payload.command,
            params=payload.params,
            confirmation=payload.confirmation,
            interactive_confirm=payload.interactive_confirm,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ---- voice / camera ----

@router.post("/devices/{device_id}/voice/start")
def start_voice(device_id: str, payload: VoiceStartRequest, service: WearableService = Depends(get_wearable_service)) -> dict:
    return service.start_voice(device_id=device_id, session_id=payload.session_id, push_to_talk=payload.push_to_talk)


@router.post("/devices/{device_id}/voice/stop")
def stop_voice(device_id: str, payload: SessionOnly, service: WearableService = Depends(get_wearable_service)) -> dict:
    return service.stop_voice(device_id=device_id, session_id=payload.session_id)


@router.post("/devices/{device_id}/voice/transcribe")
def transcribe_voice(device_id: str, payload: TranscribeRequest, service: WearableService = Depends(get_wearable_service)) -> dict:
    return service.transcribe_voice(device_id=device_id, session_id=payload.session_id, audio_reference=payload.audio_reference)


@router.post("/devices/{device_id}/camera/capture", status_code=status.HTTP_201_CREATED)
def capture_camera(device_id: str, payload: CameraRequest, service: WearableService = Depends(get_wearable_service)) -> CameraCapture:
    try:
        return service.capture_camera(
            device_id=device_id,
            session_id=payload.session_id,
            mode=payload.mode,
            retention_policy=payload.retention_policy,
            local_only=payload.local_only,
            explicit_user_action=payload.explicit_user_action,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/devices/{device_id}/camera/{capture_id}/stop")
def stop_camera(device_id: str, capture_id: str, service: WearableService = Depends(get_wearable_service)) -> dict:
    return service.stop_camera(device_id=device_id, capture_id=capture_id)


# ---- checklists / handoff / offline / resources ----

@router.post("/checklists", status_code=status.HTTP_201_CREATED, response_model=ChecklistRecord)
def create_checklist(payload: ChecklistRequest, service: WearableService = Depends(get_wearable_service)) -> ChecklistRecord:
    return service.create_checklist(
        title=payload.title,
        steps=payload.steps,
        project=payload.project,
        task=payload.task,
        mission=payload.mission,
        device_id=payload.device_id,
        source=payload.source,
    )


@router.get("/checklists")
def list_checklists(device_id: Optional[str] = Query(default=None), service: WearableService = Depends(get_wearable_service)) -> dict:
    return {"checklists": [record.model_dump() for record in service.list_checklists(device_id=device_id or "")]}


@router.post("/checklists/{checklist_id}/steps/{step_id}/complete")
def complete_checklist_step(checklist_id: str, step_id: str, payload: StepRequest, service: WearableService = Depends(get_wearable_service)) -> ChecklistRecord:
    return service.complete_checklist_step(checklist_id=checklist_id, step_id=step_id, note=payload.note, evidence=payload.evidence)


@router.post("/checklists/{checklist_id}/steps/{step_id}/skip")
def skip_checklist_optional(checklist_id: str, step_id: str, service: WearableService = Depends(get_wearable_service)) -> ChecklistRecord:
    return service.skip_checklist_optional(checklist_id=checklist_id, step_id=step_id)


@router.post("/checklists/{checklist_id}/complete")
def complete_checklist(checklist_id: str, service: WearableService = Depends(get_wearable_service)) -> ChecklistRecord:
    return service.complete_checklist(checklist_id=checklist_id)


@router.post("/handoffs", status_code=status.HTTP_201_CREATED)
def create_handoff(payload: HandoffRequest, service: WearableService = Depends(get_wearable_service)) -> HandoffRecord:
    return service.create_handoff(
        source_surface=payload.source_surface,
        target_surface=payload.target_surface,
        active_item=payload.active_item,
        project=payload.project,
        mission=payload.mission,
        task=payload.task,
        selected_action=payload.selected_action,
        pending_approval=payload.pending_approval,
    )


@router.post("/handoffs/{handoff_id}/resolve")
def resolve_handoff(handoff_id: str, payload: HandoffResolveRequest, service: WearableService = Depends(get_wearable_service)) -> HandoffRecord:
    return service.resolve_handoff(handoff_id=handoff_id, accepted=payload.accepted, destination_trusted=payload.destination_trusted)


@router.post("/devices/{device_id}/offline")
def enqueue_offline(device_id: str, payload: OfflineRequest, service: WearableService = Depends(get_wearable_service)) -> OfflineOperation:
    return service.enqueue_offline(device_id=device_id, session_id=payload.session_id, action=payload.action)


@router.get("/devices/{device_id}/offline")
def offline_operations(device_id: str, service: WearableService = Depends(get_wearable_service)) -> dict:
    return {"operations": [op.model_dump() for op in service.offline_operations(device_id)]}


@router.post("/devices/{device_id}/resources")
def apply_resources(device_id: str, payload: ResourceRequest, service: WearableService = Depends(get_wearable_service)) -> dict:
    return service.apply_resources(
        device_id=device_id,
        resource=_resource_state(device_id, payload),
    )


# ---- platform ----

@router.get("/activity")
def activity(device_id: Optional[str] = Query(default=None), service: WearableService = Depends(get_wearable_service)) -> dict:
    return {"activity": list(service.activity(device_id=device_id or ""))}


@router.get("/storage")
def storage(service: WearableService = Depends(get_wearable_service)) -> dict:
    return service.storage_stats()


@router.post("/backup")
def backup(service: WearableService = Depends(get_wearable_service)) -> dict:
    path = service.backup()
    return {"backup_path": path}


# ---- request models ----

from pydantic import BaseModel, Field  # noqa: E402
from .models import ResourceState  # noqa: E402


def _resource_state(device_id: str, payload: "ResourceRequest") -> ResourceState:
    return ResourceState(
        device_id=device_id,
        battery=payload.battery,
        charging=payload.charging,
        thermal=payload.thermal,
        latency_ms=payload.latency_ms,
        bandwidth_class=payload.bandwidth_class,
        mic_active=payload.mic_active,
        camera_active=payload.camera_active,
    )


class PermissionRequest(BaseModel):
    permission: str = Field(min_length=1, max_length=80)
    scope: str = Field(default="session", max_length=30)
    scope_target: str = Field(default="", max_length=120)


class SimulatorRequest(BaseModel):
    profile: str = Field(min_length=1, max_length=40)
    display_name: str = Field(default="Simulated Glasses", max_length=120)


class DiscoveryRequest(BaseModel):
    adapter_id: str = Field(min_length=1, max_length=80)
    devices: List[dict] = Field(default_factory=list)


class PairingRequest(BaseModel):
    device_id: str = Field(min_length=1, max_length=80)
    method: str = Field(default="one_time_code", max_length=30)


class PairingConfirmRequest(BaseModel):
    code: str = Field(min_length=1, max_length=40)


class TrustRequest(BaseModel):
    level: str = Field(default="session_trusted", max_length=40)
    scope: str = Field(default="session", max_length=30)
    scope_target: str = Field(default="", max_length=120)
    capabilities: List[str] = Field(default_factory=list)


class RevokeRequest(BaseModel):
    reason: str = Field(default="", max_length=300)


class ConnectRequest(BaseModel):
    capabilities: List[str] = Field(default_factory=list)
    permissions: List[str] = Field(default_factory=list)


class AuthVerifyRequest(BaseModel):
    nonce_id: str = Field(min_length=1, max_length=80)
    signed_nonce: str = Field(min_length=1, max_length=120)


class CardRequest(BaseModel):
    content: WearableContent
    session_permissions: List[str] = Field(default_factory=list)


class PrivacyRequest(BaseModel):
    mode: str = Field(default="normal", max_length=30)


class RouteNotificationRequest(BaseModel):
    severity: str = Field(default="informational", max_length=30)
    priority: str = Field(default="normal", max_length=20)
    urgency: str = Field(default="routine", max_length=20)
    category: str = Field(default="", max_length=60)
    source: str = Field(default="", max_length=80)
    title: str = Field(min_length=1, max_length=240)
    body: str = Field(default="", max_length=500)
    battery_state: str = Field(default="unknown", max_length=20)
    thermal_state: str = Field(default="unknown", max_length=20)
    dnd_active: bool = False
    quiet_hours_active: bool = False


class InteractionRequest(BaseModel):
    session_id: str = Field(default="", max_length=80)
    input_type: str = Field(min_length=1, max_length=30)
    normalized_action: str = Field(default="", max_length=80)
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class CommandRequestModel(BaseModel):
    session_id: str = Field(min_length=1, max_length=80)
    command: str = Field(min_length=1, max_length=80)
    params: dict = Field(default_factory=dict)
    confirmation: str = Field(default="none", max_length=20)
    interactive_confirm: bool = False


class VoiceStartRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=80)
    push_to_talk: bool = True


class SessionOnly(BaseModel):
    session_id: str = Field(min_length=1, max_length=80)


class TranscribeRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=80)
    audio_reference: str = Field(min_length=1, max_length=2000)


class CameraRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=80)
    mode: str = Field(default="still_image", max_length=20)
    retention_policy: str = Field(default="process_and_delete", max_length=30)
    local_only: bool = True
    explicit_user_action: bool = True


class ChecklistRequest(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    steps: List[dict] = Field(min_length=1, max_length=100)
    project: str = Field(default="", max_length=120)
    task: str = Field(default="", max_length=80)
    mission: str = Field(default="", max_length=80)
    device_id: str = Field(default="", max_length=80)
    source: str = Field(default="", max_length=80)


class StepRequest(BaseModel):
    note: str = Field(default="", max_length=500)
    evidence: str = Field(default="", max_length=2000)


class HandoffRequest(BaseModel):
    source_surface: str = Field(min_length=1, max_length=40)
    target_surface: str = Field(min_length=1, max_length=40)
    active_item: str = Field(default="", max_length=120)
    project: str = Field(default="", max_length=120)
    mission: str = Field(default="", max_length=80)
    task: str = Field(default="", max_length=80)
    selected_action: str = Field(default="", max_length=120)
    pending_approval: str = Field(default="", max_length=80)


class HandoffResolveRequest(BaseModel):
    accepted: bool = True
    destination_trusted: bool = True


class OfflineRequest(BaseModel):
    session_id: str = Field(default="", max_length=80)
    action: str = Field(min_length=1, max_length=80)


class ResourceRequest(BaseModel):
    battery: Optional[int] = Field(default=None, ge=0, le=100)
    charging: bool = False
    thermal: str = Field(default="unknown", max_length=20)
    latency_ms: Optional[int] = Field(default=None, ge=0)
    bandwidth_class: str = Field(default="unknown", max_length=20)
    mic_active: bool = False
    camera_active: bool = False