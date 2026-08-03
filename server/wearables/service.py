"""WearableService facade: one authoritative entry point into the JoeOS Smart
Glasses and Wearable Device Platform.

Composes the Device Registry, Adapter Registry, Discovery, Pairing, Trust,
Authentication, Secure Sessions, Connection Manager, Capability Negotiation,
Device Permissions, Glance Cards, Wearable Notification Router, privacy modes,
Command Gateway, Voice and Camera gateways, checklists, handoff, offline
queue, resource governor, and simulator. All services share one SQLite DB.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .connections import CapabilityNegotiation, ConnectionManager, PermissionError
from .content import GlanceCardSystem, PrivacyModeService, WearableNotificationRouter
from .devices import AdapterRegistry, DeviceRegistry, DiscoveryService
from .experiences import (
    ChecklistService,
    HandoffService,
    OfflineQueue,
    ResourceGovernor,
)
from .interaction import InteractionGateway, WearableCommandGateway
from .models import (
    AdapterRecord,
    CameraCapture,
    CapabilityRecord,
    ChecklistRecord,
    DeviceRecord,
    DeviceSession,
    DeviceTrust,
    HandoffRecord,
    OfflineOperation,
    PairingChallenge,
    WearableContent,
    WearablesOverview,
)
from .permissions import DevicePermissionManager
from .security import (
    DeviceAuthenticationService,
    PairingService,
    SecureSessionService,
)
from .simulator import WearableSimulator
from .storage import WearablesStorage
from .voice_camera import CameraGateway, VisionGateway, VoiceGateway


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WearableService:
    def __init__(
        self,
        data_dir: str,
        *,
        event_sink=None,
        command_executor=None,
        transcription=None,
        vision=None,
    ) -> None:
        self.storage = WearablesStorage(data_dir)
        self.storage.prepare()
        self._data_dir = Path(data_dir)
        self._event_sink = event_sink or (lambda level, source, message: None)

        self.devices = DeviceRegistry(self._connection_factory)
        self.adapters = AdapterRegistry(self._connection_factory)
        self.discovery = DiscoveryService(self.devices, self.adapters)
        self.permissions = DevicePermissionManager(self._connection_factory)
        self.pairing = PairingService(self._connection_factory, self.devices)
        self.authentication = DeviceAuthenticationService(self._connection_factory)
        self.sessions = SecureSessionService(self._connection_factory)
        self.connection = ConnectionManager(
            sessions=self.sessions,
            devices=self.devices,
            adapters=self.adapters,
            event_sink=self._event_sink,
        )
        self.negotiation = CapabilityNegotiation(self.devices, self.adapters)
        self.privacy = PrivacyModeService(self.devices)
        self.cards = GlanceCardSystem(self._connection_factory, self.devices, self.permissions)
        self.router = WearableNotificationRouter(self.cards, self.devices)
        self.interactions = InteractionGateway(self._connection_factory)
        self.commands = WearableCommandGateway(
            connection_factory=self._connection_factory,
            devices=self.devices,
            sessions=self.sessions,
            permissions=self.permissions,
            command_executor=command_executor,
            event_sink=self._event_sink,
        )
        self.voice = VoiceGateway(
            connection_factory=self._connection_factory,
            devices=self.devices,
            sessions=self.sessions,
            permissions=self.permissions,
            transcription=transcription,
            event_sink=self._event_sink,
        )
        self.camera = CameraGateway(
            connection_factory=self._connection_factory,
            devices=self.devices,
            sessions=self.sessions,
            permissions=self.permissions,
            event_sink=self._event_sink,
        )
        self.vision = VisionGateway(vision)
        self.checklists = ChecklistService(self._connection_factory)
        self.handoffs = HandoffService(self._connection_factory)
        self.offline = OfflineQueue(self._connection_factory)
        self.resources = ResourceGovernor(self.devices, self._event_sink)
        self.simulator = WearableSimulator(self.devices, self.adapters)

    def _connection_factory(self):
        connection = self.storage.connect()
        return _BorrowedConnection(connection)

    # ------------------------------------------------------------------
    # Registry
    # ------------------------------------------------------------------

    def list_devices(self) -> Tuple[DeviceRecord, ...]:
        return self.devices.list()

    def get_device(self, device_id: str) -> Optional[DeviceRecord]:
        return self.devices.get(device_id)

    def device_types(self) -> Tuple:
        return self.devices.device_types()

    def list_adapters(self) -> Tuple[AdapterRecord, ...]:
        return self.adapters.list()

    def device_capabilities(self, device_id: str) -> Tuple[CapabilityRecord, ...]:
        return self.devices.capabilities(device_id)

    # ------------------------------------------------------------------
    # Simulator
    # ------------------------------------------------------------------

    def simulator_profiles(self) -> Tuple[dict, ...]:
        return self.simulator.list_profiles()

    def create_simulator_device(self, *, profile: str, display_name: str = "Simulated Glasses") -> DeviceRecord:
        device_id = self.simulator.create_device(profile=profile, display_name=display_name)
        return self.devices.get(device_id)

    # ------------------------------------------------------------------
    # Discovery / pairing / trust / auth / session
    # ------------------------------------------------------------------

    def discover(self, *, adapter_id: str, discovered: Sequence[dict] = ()) -> Tuple[dict, ...]:
        return self.discovery.discover(adapter_id=adapter_id, discovered=discovered)

    def begin_pairing(self, *, device_id: str, method: str = "one_time_code") -> PairingChallenge:
        return self.pairing.create_challenge(device_id=device_id, adapter_id=self._adapter_for(device_id), method=method)

    def confirm_pairing(self, *, challenge_id: str, code: str) -> DeviceTrust:
        return self.pairing.confirm(challenge_id=challenge_id, code=code)

    def trust_device(self, *, device_id: str, level: str, scope: str = "session", scope_target: str = "", capabilities: Sequence[str] = ()) -> DeviceTrust:
        return self.pairing.trust(device_id=device_id, level=level, scope=scope, scope_target=scope_target, capabilities=capabilities)

    def revoke_device(self, *, device_id: str, reason: str = "") -> DeviceRecord:
        self.pairing.revoke(device_id=device_id, reason=reason)
        return self.devices.get(device_id)

    def device_trust(self, device_id: str) -> DeviceTrust:
        return self.pairing.trust_record(device_id)

    def create_auth_nonce(self, *, device_id: str) -> Tuple[str, str]:
        return self.authentication.create_nonce(device_id=device_id)

    def verify_auth(self, *, nonce_id: str, device_id: str, signed_nonce: str) -> bool:
        return self.authentication.verify(nonce_id=nonce_id, device_id=device_id, signed_nonce=signed_nonce)

    def connect_device(
        self,
        *,
        device_id: str,
        capabilities: Sequence[str] = (),
        permissions: Sequence[str] = (),
        authenticated_user: str = "user",
    ) -> DeviceSession:
        return self.connection.connect(
            device_id=device_id,
            adapter_id=self._adapter_for(device_id),
            authenticated_user=authenticated_user,
            capabilities=capabilities,
            permissions=permissions,
        )

    def disconnect_device(self, *, device_id: str, reason: str = "user action") -> None:
        self.connection.disconnect(device_id=device_id, reason=reason)

    def active_sessions(self) -> Tuple[DeviceSession, ...]:
        return self.sessions.list_active()

    def is_session_valid(self, session_id: str) -> bool:
        return self.sessions.is_valid(session_id)

    def heartbeat(self, session_id: str) -> None:
        self.sessions.heartbeat(session_id)

    def _adapter_for(self, device_id: str) -> str:
        device = self.devices.get(device_id)
        if device is None:
            raise PermissionError("device not found.")
        return device.adapter_id or self.simulator.SIMULATOR_ADAPTER

    # ------------------------------------------------------------------
    # Permissions / capabilities
    # ------------------------------------------------------------------

    def grant_permission(self, *, device_id: str, permission: str, scope: str = "session", scope_target: str = "") -> None:
        self.permissions.grant(device_id=device_id, permission=permission, scope=scope, scope_target=scope_target)

    def revoke_permission(self, *, device_id: str, permission: str, scope_target: str = "") -> None:
        self.permissions.revoke(device_id=device_id, permission=permission, scope_target=scope_target)

    def device_permissions(self, device_id: str) -> Tuple[dict, ...]:
        return self.permissions.grants_for(device_id)

    def negotiate_capabilities(self, *, device_id: str, reported: Sequence[str]) -> Dict[str, str]:
        return self.negotiation.negotiate(device_id=device_id, adapter_id=self._adapter_for(device_id), device_reported=reported)

    # ------------------------------------------------------------------
    # Cards / routing / privacy
    # ------------------------------------------------------------------

    def deliver_card(self, *, device_id: str, content: WearableContent, session_permissions: Sequence[str] = (), privacy_mode: str = "normal") -> WearableContent:
        return self.cards.deliver(device_id=device_id, content=content, session_permissions=session_permissions, privacy_mode=privacy_mode)

    def cards_for_device(self, device_id: str, *, limit: int = 50) -> Tuple[WearableContent, ...]:
        return self.cards.list_for_device(device_id, limit=limit)

    def acknowledge_card(self, *, device_id: str, content_id: str) -> WearableContent:
        return self.cards.acknowledge(device_id=device_id, content_id=content_id)

    def route_notification(self, **kwargs) -> Dict[str, object]:
        return self.router.route(**kwargs)

    def set_privacy_mode(self, *, device_id: str, mode: str) -> str:
        return self.privacy.set_mode(device_id=device_id, mode=mode)

    def privacy_mode(self, device_id: str) -> str:
        return self.privacy.mode(device_id)

    # ------------------------------------------------------------------
    # Interaction / commands
    # ------------------------------------------------------------------

    def record_interaction(self, *, device_id: str, session_id: str, input_type: str, normalized_action: str = "", confidence: Optional[float] = None):
        return self.interactions.record(device_id=device_id, session_id=session_id, input_type=input_type, normalized_action=normalized_action, confidence=confidence)

    def execute_command(self, **kwargs) -> dict:
        return self.commands.execute(**kwargs)

    # ------------------------------------------------------------------
    # Voice / camera / vision
    # ------------------------------------------------------------------

    def start_voice(self, *, device_id: str, session_id: str, push_to_talk: bool = True) -> dict:
        return self.voice.start_session(device_id=device_id, session_id=session_id, push_to_talk=push_to_talk)

    def stop_voice(self, *, device_id: str, session_id: str) -> dict:
        return self.voice.stop_session(device_id=device_id, session_id=session_id)

    def transcribe_voice(self, *, device_id: str, session_id: str, audio_reference: str) -> dict:
        return self.voice.transcribe(device_id=device_id, session_id=session_id, audio_reference=audio_reference)

    def confirm_voice_high_risk(self, **kwargs) -> dict:
        return self.voice.confirm_high_risk(**kwargs)

    def capture_camera(self, **kwargs) -> CameraCapture:
        return self.camera.capture(**kwargs)

    def stop_camera(self, *, device_id: str, capture_id: str) -> dict:
        return self.camera.stop(device_id=device_id, capture_id=capture_id)

    def analyze_vision(self, *, capture: CameraCapture, image_reference: str) -> dict:
        result = self.vision.analyze(capture=capture, image_reference=image_reference)
        return result.model_dump()

    # ------------------------------------------------------------------
    # Checklists / handoff / offline / resources
    # ------------------------------------------------------------------

    def create_checklist(self, **kwargs) -> ChecklistRecord:
        return self.checklists.create(**kwargs)

    def list_checklists(self, *, device_id: str = "") -> Tuple[ChecklistRecord, ...]:
        return self.checklists.list(device_id=device_id)

    def complete_checklist_step(self, **kwargs) -> ChecklistRecord:
        return self.checklists.complete_step(**kwargs)

    def skip_checklist_optional(self, **kwargs) -> ChecklistRecord:
        return self.checklists.skip_optional(**kwargs)

    def complete_checklist(self, *, checklist_id: str) -> ChecklistRecord:
        return self.checklists.complete(checklist_id=checklist_id)

    def create_handoff(self, **kwargs) -> HandoffRecord:
        return self.handoffs.create(**kwargs)

    def resolve_handoff(self, *, handoff_id: str, accepted: bool, destination_trusted: bool = True) -> HandoffRecord:
        return self.handoffs.resolve(handoff_id=handoff_id, accepted=accepted, destination_trusted=destination_trusted)

    def list_handoffs(self) -> Tuple[HandoffRecord, ...]:
        return self.handoffs.list()

    def enqueue_offline(self, **kwargs) -> OfflineOperation:
        return self.offline.enqueue(**kwargs)

    def offline_operations(self, device_id: str) -> Tuple[OfflineOperation, ...]:
        return self.offline.list_for_device(device_id)

    def revalidate_offline(self, *, device_id: str, authoritative_state: Callable[[str], bool]) -> Dict[str, int]:
        return self.offline.revalidate(device_id=device_id, authoritative_state=authoritative_state)

    def apply_resources(self, **kwargs) -> Dict[str, object]:
        return self.resources.apply(**kwargs)

    # ------------------------------------------------------------------
    # Overview / health / diagnostics
    # ------------------------------------------------------------------

    def overview(self) -> WearablesOverview:
        devices = self.devices.list()
        sessions = self.sessions.list_active()
        adapters = self.adapters.list()
        offline = []
        for device in devices:
            offline.extend(self.offline.list_for_device(device.device_id))
        return WearablesOverview(
            paired_devices=sum(1 for d in devices if d.paired_state == "paired"),
            connected_devices=sum(1 for d in devices if d.connection_state == "connected"),
            active_sessions=len(sessions),
            trusted_devices=sum(1 for d in devices if d.trusted_state in {"user_trusted", "session_trusted", "paired_but_restricted", "capability_scoped", "project_scoped"}),
            revoked_devices=sum(1 for d in devices if d.revocation_state == "revoked"),
            quarantined_adapters=sum(1 for a in adapters if a.state == "quarantined"),
            mic_active=sum(1 for d in devices if d.mic_active),
            camera_active=sum(1 for d in devices if d.camera_active),
            pending_wearable_approvals=0,
            offline_operations=len(offline),
            low_battery_devices=sum(1 for d in devices if d.battery_state in {"low", "critical"}),
            thermal_warning_devices=sum(1 for d in devices if d.thermal_state in {"warning", "critical"}),
            privacy_mode_devices=sum(1 for d in devices if d.privacy_mode != "normal"),
            generated_at=_now(),
        )

    def activity(self, *, device_id: str = "", limit: int = 50) -> Tuple[dict, ...]:
        clause = " WHERE device_id = ?" if device_id else ""
        params: List[object] = [device_id] if device_id else []
        params.append(max(1, min(200, int(limit))))
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM wearable_activity" + clause + " ORDER BY recorded_at DESC LIMIT ?", params
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def storage_stats(self) -> dict:
        return {"path": self.storage.path(), "size_bytes": self.storage.size_bytes(), "version": 1}

    def backup(self) -> Optional[str]:
        return self.storage.backup_to(str(self._data_dir))


class _BorrowedConnection:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def __enter__(self) -> sqlite3.Connection:
        return self._connection

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self._connection.commit()
        else:
            self._connection.rollback()