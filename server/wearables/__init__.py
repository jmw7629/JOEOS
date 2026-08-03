"""JoeOS Smart Glasses, Wearable Display, and Ambient Computing Platform.

A local-first, provider-neutral wearable integration layer. Devices, adapters,
capabilities, sessions, permissions, glance cards, voice, camera, checklists,
handoffs, and offline operations are typed and authoritative. Pairing is
secure, trust is capability-scoped and revocable, sessions authenticate and
expire, recording indicators are enforced, and wearable input never bypasses
approval or the Tool Broker. Only the isolated simulator produces devices.

See `docs/architecture/WEARABLES_PLATFORM.md` for the design.
"""

from .connections import (
    CapabilityNegotiation,
    ConnectionManager,
    PermissionError,
)
from .content import GlanceCardSystem, PrivacyModeService, WearableNotificationRouter
from .devices import (
    AdapterRegistry,
    DeviceRegistry,
    DiscoveryService,
    WearableError,
)
from .experiences import (
    ChecklistService,
    HandoffService,
    OfflineQueue,
    ResourceGovernor,
)
from .interaction import (
    InteractionGateway,
    WearableCommandGateway,
    ALLOWLISTED_COMMANDS,
    HIGH_RISK_COMMANDS,
)
from .models import (
    AdapterRecord,
    CameraCapture,
    CapabilityID,
    CapabilityRecord,
    ChecklistRecord,
    ChecklistStep,
    CommandRequest,
    DeviceRecord,
    DeviceSession,
    DeviceTrust,
    DeviceType,
    HandoffRecord,
    InteractionEvent,
    OfflineOperation,
    PairingChallenge,
    ResourceState,
    VoiceIntent,
    VoiceIntentRecord,
    WearableContent,
    WearablesOverview,
)
from .permissions import (
    DevicePermissionManager,
    WEARABLE_PERMISSIONS,
)
from .router import router as wearables_router
from .security import (
    DeviceAuthenticationService,
    PairingService,
    SecureSessionService,
)
from .service import WearableService
from .simulator import WearableSimulator, CAPABILITY_PROFILES
from .storage import WearablesStorage
from .voice_camera import CameraGateway, VisionGateway, VoiceGateway

__all__ = [
    "ALLOWLISTED_COMMANDS",
    "AdapterRecord",
    "AdapterRegistry",
    "CameraCapture",
    "CameraGateway",
    "CAPABILITY_PROFILES",
    "CapabilityID",
    "CapabilityNegotiation",
    "CapabilityRecord",
    "ChecklistRecord",
    "ChecklistService",
    "ChecklistStep",
    "CommandRequest",
    "ConnectionManager",
    "DeviceAuthenticationService",
    "DevicePermissionManager",
    "DeviceRecord",
    "DeviceRegistry",
    "DeviceSession",
    "DeviceTrust",
    "DeviceType",
    "DiscoveryService",
    "GlanceCardSystem",
    "HIGH_RISK_COMMANDS",
    "HandoffRecord",
    "HandoffService",
    "InteractionEvent",
    "InteractionGateway",
    "OfflineOperation",
    "OfflineQueue",
    "PairingChallenge",
    "PairingService",
    "PermissionError",
    "PrivacyModeService",
    "ResourceGovernor",
    "ResourceState",
    "SecureSessionService",
    "VoiceGateway",
    "VoiceIntent",
    "VoiceIntentRecord",
    "VisionGateway",
    "WEARABLE_PERMISSIONS",
    "WearableCommandGateway",
    "WearableContent",
    "WearableError",
    "WearableNotificationRouter",
    "WearableService",
    "WearableSimulator",
    "WearablesOverview",
    "WearablesStorage",
    "wearables_router",
]