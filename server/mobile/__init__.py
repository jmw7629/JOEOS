"""JoeOS Mobile Companion and Secure Remote Operations Platform.

A client of authoritative JoeOS services. The mobile companion supports secure
pairing, host and client identity, short-lived sessions, scoped remote
commands, safe offline actions, handoff, deep links, and privacy-safe push
contracts. It never accesses core databases, secrets, arbitrary service
methods, or unrestricted terminals. Push delivery is implemented as contracts
plus an isolated test fixture; production APNs/FCM delivery is documented, not
claimed.

See `docs/architecture/MOBILE_COMPANION.md` for the design and platform
strategy.
"""

from .clients import HostRegistry, MobileClientRegistry, MobileError
from .models import (
    DeepLinkReference,
    HandoffRecord,
    HostRecord,
    MobileClientRecord,
    MobileOverview,
    MobileSession,
    NotificationDelivery,
    OfflineAction,
    PairingSession,
    PushRegistration,
    REMOTE_API_VERSION,
    OFFLINE_PROHIBITED_ACTIONS,
    OFFLINE_SAFE_ACTIONS,
    MOBILE_PERMISSIONS,
)
from .offline import DeepLinkRegistry, HandoffCoordinator, OfflineActionQueue
from .push import PushCoordinator
from .remote import (
    ALLOWED_REMOTE_COMMANDS,
    PROHIBITED_REMOTE_COMMANDS,
    RemoteCommandGateway,
    ScopedRemoteAPI,
)
from .router import router as mobile_router
from .security import (
    MobileAuthenticationService,
    MobileSessionManager,
    PairingCoordinator,
)
from .service import MobileService
from .storage import MobileStorage

__all__ = [
    "ALLOWED_REMOTE_COMMANDS",
    "DeepLinkReference",
    "DeepLinkRegistry",
    "HandoffCoordinator",
    "HandoffRecord",
    "HostRecord",
    "HostRegistry",
    "MOBILE_PERMISSIONS",
    "MobileAuthenticationService",
    "MobileClientRecord",
    "MobileClientRegistry",
    "MobileError",
    "MobileOverview",
    "MobileService",
    "MobileSession",
    "MobileSessionManager",
    "MobileStorage",
    "NotificationDelivery",
    "OFFLINE_PROHIBITED_ACTIONS",
    "OFFLINE_SAFE_ACTIONS",
    "OfflineAction",
    "OfflineActionQueue",
    "PROHIBITED_REMOTE_COMMANDS",
    "PairingCoordinator",
    "PairingSession",
    "PushCoordinator",
    "PushRegistration",
    "REMOTE_API_VERSION",
    "RemoteCommandGateway",
    "ScopedRemoteAPI",
    "mobile_router",
]