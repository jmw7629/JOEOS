"""JoeOS Plugin and Extension Platform.

A local-first, secure-by-default platform for extending JoeOS. Installed
plugins are typed, versioned, permission-based, integrity-checked, and
executed inside an isolated Extension Host. Plugins can never grant
permissions to themselves, modify core registries, bypass the Tool Broker, or
access secrets/storage outside explicit grants.

See `docs/architecture/PLUGIN_PLATFORM.md` for the design and security model.
"""

from .contributions import ContributionRegistry
from .events import EventGateway
from .extension_data import ExtensionSettingsService, ExtensionStorageService
from .health import PluginHealthService
from .host import ExtensionHostManager, RestartPolicy
from .host_protocol import RpcProtocolError
from .integrity import (
    IntegrityError,
    compute_inventory,
    verify_inventory,
    write_inventory_file,
)
from .lifecycle import (
    DevelopmentHost,
    ExtensionLifecycleManager,
    PluginLifecycleError,
    SafeModeState,
)
from .models import (
    CompatibilityResult,
    ContributionDeclaration,
    ContributionRecord,
    DependencyDeclaration,
    EntryPoint,
    HealthRecord,
    NetworkPolicy,
    PermissionDeclaration,
    PermissionGrant,
    PermissionScope,
    PermissionSummary,
    PluginHealthState,
    PluginLifecycleState,
    PluginManifest,
    PluginOverview,
    PluginRecord,
    PrivacyDeclaration,
    PublisherDeclaration,
    PublisherRecord,
    ResourceLimits,
    RpcRequest,
    RpcResponse,
    SettingDeclaration,
    StorageDeclaration,
)
from .permissions import CapabilityBroker, PermissionManager
from .publishers import PublisherService
from .resources import ResourceGovernor, ResourceLimitError
from .router import router as plugins_router
from .secrets import ExtensionSecretBroker
from .service import PluginService
from .signature import SignatureState
from .storage import PluginRegistryStorage

__all__ = [
    "CapabilityBroker",
    "CompatibilityResult",
    "ContributionDeclaration",
    "ContributionRecord",
    "ContributionRegistry",
    "DependencyDeclaration",
    "DevelopmentHost",
    "EntryPoint",
    "EventGateway",
    "ExtensionHostManager",
    "ExtensionLifecycleManager",
    "ExtensionSecretBroker",
    "ExtensionSettingsService",
    "ExtensionStorageService",
    "HealthRecord",
    "IntegrityError",
    "NetworkPolicy",
    "PermissionDeclaration",
    "PermissionGrant",
    "PermissionManager",
    "PermissionScope",
    "PermissionSummary",
    "PluginHealthState",
    "PluginHealthService",
    "PluginLifecycleError",
    "PluginLifecycleState",
    "PluginManifest",
    "PluginOverview",
    "PluginRecord",
    "PluginRegistryStorage",
    "PluginService",
    "plugins_router",
    "PrivacyDeclaration",
    "PublisherDeclaration",
    "PublisherRecord",
    "PublisherService",
    "ResourceGovernor",
    "ResourceLimitError",
    "ResourceLimits",
    "RestartPolicy",
    "RpcProtocolError",
    "RpcRequest",
    "RpcResponse",
    "SafeModeState",
    "SettingDeclaration",
    "SignatureState",
    "StorageDeclaration",
    "compute_inventory",
    "verify_inventory",
    "write_inventory_file",
]