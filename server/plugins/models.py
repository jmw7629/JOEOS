"""Typed contracts for the JoeOS Plugin and Extension Platform.

Every public boundary in the platform (manifests, records, RPC, permissions,
contributions, health) is expressed as a strict, versioned, extra-forbidden
model. No model carries authority by itself; enforcement lives in the services.
"""

from __future__ import annotations

import re
from typing import Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PluginId = Literal["plugin"]
MANIFEST_SCHEMA_VERSION = 1
API_VERSION = 1

PLUGIN_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,39}\.[a-z][a-z0-9_-]{0,39}$")
PERMISSION_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,79}$")
IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,79}$")
SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------

PermissionScope = Literal[
    "denied",
    "ask_each_time",
    "granted_once",
    "granted_session",
    "granted_workspace",
    "granted_project",
    "granted_global",
    "revoked",
    "unavailable",
    "blocked_by_policy",
]

# The canonical, typed permission catalog. A plugin may only request
# permissions that exist here; undeclared permissions are rejected at manifest
# validation time.
PERMISSION_CATALOG: Dict[str, str] = {
    "filesystem.read_selected_file": "Read a file explicitly selected by the user.",
    "filesystem.read_project_files": "Read files inside an approved project.",
    "filesystem.propose_project_edit": "Propose edits to approved project files.",
    "filesystem.create_project_files": "Create files inside an approved project.",
    "filesystem.delete_project_files": "Delete files inside an approved project.",
    "filesystem.access_outside_projects": "Access files outside registered projects (high risk).",
    "terminal.request_registered_command": "Request an approved registered command.",
    "terminal.request_execution": "Request bounded command execution through the Tool Broker.",
    "terminal.start_managed_process": "Start a managed subprocess.",
    "terminal.stop_owned_process": "Stop a process owned by the plugin.",
    "network.access_declared_domains": "Access only the network destinations declared in the manifest.",
    "network.access_local_network": "Access the local network.",
    "network.access_internet": "Access the public Internet (high risk).",
    "network.host_local_listener": "Host a listener on the local machine (high risk).",
    "ai.use_local_runtime": "Use the local AI Runtime.",
    "ai.use_approved_cloud_provider": "Use an explicitly approved cloud provider.",
    "ai.register_provider": "Register an AI provider adapter.",
    "ai.register_runtime": "Register a local inference runtime.",
    "ai.submit_embeddings": "Submit embedding requests.",
    "ai.use_vision": "Use vision capabilities.",
    "ai.use_speech": "Use speech capabilities.",
    "memory.read_task_memory": "Read task-scoped memory.",
    "memory.read_project_memory": "Read project-scoped memory.",
    "memory.read_user_memory": "Read user-scoped memory.",
    "memory.propose_memory": "Propose memory records (never direct high-authority writes).",
    "memory.write_approved_extension_memory": "Write to plugin-owned approved memory.",
    "intelligence.search_project": "Search project content.",
    "intelligence.inspect_symbols": "Inspect repository symbols.",
    "intelligence.inspect_dependencies": "Inspect repository dependency graphs.",
    "intelligence.inspect_git_history": "Inspect Git history.",
    "intelligence.contribute_analyzer_results": "Contribute analyzer findings.",
    "secrets.request_named_extension_secret": "Request a named extension secret through the broker.",
    "secrets.request_project_credential_reference": "Request a project credential reference.",
    "secrets.request_provider_token": "Request a provider token through the broker.",
    "ui.register_panel": "Register a panel.",
    "ui.register_view": "Register a view.",
    "ui.register_menu": "Register a menu item.",
    "ui.register_status_item": "Register a status item.",
    "ui.register_editor": "Register an editor.",
    "ui.register_theme": "Register a theme.",
    "hardware.microphone": "Use the microphone.",
    "hardware.camera": "Use the camera.",
    "hardware.display": "Use an additional display.",
    "hardware.smart_glasses": "Use smart-glasses hardware.",
    "hardware.serial_device": "Use a serial device.",
    "hardware.bluetooth_device": "Use a Bluetooth device.",
    "hardware.usb_device": "Use a USB device.",
    "notification.publish": "Publish notifications.",
    "events.subscribe": "Subscribe to approved event classes.",
    "automation.register_action": "Register an automation action.",
    "automation.register_trigger": "Register an automation trigger.",
    "storage.extension_data": "Store and read plugin-owned extension data.",
    "settings.contribute": "Contribute settings to the Settings framework.",
}


def is_valid_permission(permission: str) -> bool:
    return permission in PERMISSION_CATALOG


class PermissionDeclaration(StrictModel):
    permission: str = Field(min_length=1, max_length=100)
    purpose: str = Field(default="", max_length=240)

    @field_validator("permission")
    @classmethod
    def validate_permission(cls, value: str) -> str:
        if value not in PERMISSION_CATALOG:
            raise ValueError("unknown permission: %r" % value)
        return value


class PermissionGrant(StrictModel):
    plugin_id: str
    permission: str
    scope: PermissionScope
    scope_target: str = Field(default="", max_length=240)
    reason: str = Field(default="", max_length=240)
    granted_at: str


class PermissionSummary(StrictModel):
    granted: Tuple[str, ...] = ()
    denied: Tuple[str, ...] = ()
    pending: Tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

RuntimeType = Literal["python", "javascript", "static"]
NetworkMode = Literal["none", "loopback", "local", "declared_domains", "internet"]
ContributionType = Literal[
    "command",
    "menu",
    "panel",
    "view",
    "editor",
    "file_viewer",
    "theme",
    "icon_pack",
    "tool",
    "agent_role",
    "agent_template",
    "provider",
    "runtime",
    "parser",
    "project_detector",
    "repository_analyzer",
    "document_importer",
    "memory_processor",
    "automation_action",
    "automation_trigger",
    "hardware_adapter",
    "search_provider",
    "language_pack",
]


class NetworkPolicy(StrictModel):
    mode: NetworkMode = "none"
    domains: Tuple[str, ...] = Field(default=(), max_length=32)

    @field_validator("domains")
    @classmethod
    def validate_domains(cls, value: Tuple[str, ...]) -> Tuple[str, ...]:
        pattern = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9.-]{1,254}$")
        for domain in value:
            if pattern.fullmatch(domain) is None:
                raise ValueError("invalid network domain: %r" % domain)
        return value

    @model_validator(mode="after")
    def validate_mode(self) -> "NetworkPolicy":
        if self.mode == "declared_domains" and not self.domains:
            raise ValueError("declared_domains requires at least one domain.")
        if self.mode in {"none", "loopback", "local", "internet"}:
            if self.domains:
                raise ValueError("domains are only valid with declared_domains mode.")
        return self


class ResourceLimits(StrictModel):
    timeout_ms: int = Field(default=30000, ge=100, le=600000)
    max_events_per_minute: int = Field(default=120, ge=0, le=100000)
    max_storage_bytes: int = Field(default=10 * 1024 * 1024, ge=0, le=1024 * 1024 * 1024)
    max_active_jobs: int = Field(default=4, ge=0, le=64)
    max_log_bytes_per_hour: int = Field(default=1024 * 1024, ge=0, le=64 * 1024 * 1024)


class EntryPoint(StrictModel):
    runtime: RuntimeType = "python"
    module: str = Field(min_length=1, max_length=200)
    function: str = Field(default="handle", max_length=100)

    @field_validator("module")
    @classmethod
    def validate_module(cls, value: str) -> str:
        if re.search(r"\.\.", value) or not re.match(r"^[A-Za-z_][A-Za-z0-9_.]{0,199}$", value):
            raise ValueError("entry module must be a safe dotted module name.")
        return value


class ContributionDeclaration(StrictModel):
    type: ContributionType
    id: str = Field(min_length=1, max_length=80)
    title: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=240)
    commands: Tuple[str, ...] = Field(default=(), max_length=8)
    requires_permissions: Tuple[str, ...] = Field(default=(), max_length=16)
    resource_class: Literal["light", "medium", "heavy"] = "light"

    @field_validator("id")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        if IDENTIFIER_PATTERN.fullmatch(value) is None:
            raise ValueError("contribution id must be a lowercase dotted identifier.")
        return value

    @field_validator("requires_permissions")
    @classmethod
    def validate_required_permissions(cls, value: Tuple[str, ...]) -> Tuple[str, ...]:
        for permission in value:
            if permission not in PERMISSION_CATALOG:
                raise ValueError("unknown permission: %r" % permission)
        return value


class StorageDeclaration(StrictModel):
    schema_version: int = Field(default=1, ge=1, le=100)
    quota_bytes: int = Field(default=10 * 1024 * 1024, ge=0, le=1024 * 1024 * 1024)


class SettingDeclaration(StrictModel):
    key: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_.-]{0,79}$")
    title: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=240)
    type: Literal["string", "number", "boolean", "enum", "password"] = "string"
    default: object = None
    scope: Literal["global", "workspace", "project", "session"] = "global"
    sensitive: bool = False
    restart_required: bool = False
    validation: Dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_default(self) -> "SettingDeclaration":
        kind = self.type
        value = self.default
        if kind == "string" and not isinstance(value, str):
            raise ValueError("string settings require a string default.")
        if kind == "number" and not isinstance(value, (int, float)):
            raise ValueError("number settings require a numeric default.")
        if kind == "boolean" and not isinstance(value, bool):
            raise ValueError("boolean settings require a boolean default.")
        if kind == "password" and not isinstance(value, str):
            raise ValueError("password settings require a string default.")
        if kind == "enum":
            choices = self.validation.get("choices")
            if not isinstance(choices, (list, tuple)) or not choices:
                raise ValueError("enum settings require validation.choices.")
            if value not in choices:
                raise ValueError("enum default must be one of validation.choices.")
        return self


class MigrationDeclaration(StrictModel):
    source_version: int = Field(ge=1, le=100)
    target_version: int = Field(ge=1, le=100)
    migration_id: str = Field(min_length=1, max_length=80)
    rollback_supported: bool = False

    @model_validator(mode="after")
    def validate_versions(self) -> "MigrationDeclaration":
        if self.source_version >= self.target_version:
            raise ValueError("migration source_version must be less than target_version.")
        return self


class DependencyDeclaration(StrictModel):
    plugin_id: str = Field(min_length=1, max_length=100)
    version_range: str = Field(default="*", min_length=1, max_length=40)
    optional: bool = False
    activation: Literal["eager", "lazy"] = "lazy"

    @field_validator("plugin_id")
    @classmethod
    def validate_plugin_id(cls, value: str) -> str:
        if PLUGIN_ID_PATTERN.fullmatch(value) is None:
            raise ValueError("dependency plugin_id must look like publisher.name.")
        return value


class PublisherDeclaration(StrictModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    name: str = Field(default="", max_length=120)
    homepage: str = Field(default="", max_length=240)
    support: str = Field(default="", max_length=240)

    @model_validator(mode="after")
    def validate_identity(self) -> "PublisherDeclaration":
        if not self.name:
            raise ValueError("publisher.name is required.")
        return self


class PrivacyDeclaration(StrictModel):
    telemetry: bool = False
    telemetry_destination: str = Field(default="", max_length=240)
    ai_usage: Literal["none", "local", "cloud", "local_and_cloud"] = "none"
    cloud_usage: bool = False
    data_retention_days: int = Field(default=0, ge=0, le=3650)
    data_transmitted: Tuple[str, ...] = Field(default=(), max_length=16)
    user_identifying: bool = False

    @model_validator(mode="after")
    def validate_telemetry(self) -> "PrivacyDeclaration":
        if self.telemetry and not self.telemetry_destination:
            raise ValueError("telemetry requires a declared destination.")
        if self.cloud_usage and self.ai_usage == "none":
            raise ValueError("cloud_usage requires an ai_usage other than none.")
        return self


class PluginManifest(StrictModel):
    manifest_schema_version: Literal[1] = 1
    id: str = Field(min_length=3, max_length=100)
    name: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=20)
    description: str = Field(default="", max_length=500)
    publisher: PublisherDeclaration
    license: str = Field(default="", max_length=120)
    homepage: str = Field(default="", max_length=240)
    repository: str = Field(default="", max_length=240)
    support: str = Field(default="", max_length=240)
    api_version: int = Field(default=API_VERSION, ge=1, le=100)
    min_joeos_version: str = Field(default="0.0.0", max_length=20)
    max_joeos_version: str = Field(default="", max_length=20)
    entry_point: EntryPoint
    activation_events: Tuple[str, ...] = Field(default=(), max_length=12)
    contributions: Tuple[ContributionDeclaration, ...] = Field(default=(), max_length=32)
    required_permissions: Tuple[PermissionDeclaration, ...] = Field(default=(), max_length=32)
    optional_permissions: Tuple[PermissionDeclaration, ...] = Field(default=(), max_length=32)
    dependencies: Tuple[DependencyDeclaration, ...] = Field(default=(), max_length=16)
    conflicts: Tuple[str, ...] = Field(default=(), max_length=16)
    network: NetworkPolicy = Field(default_factory=NetworkPolicy)
    resource_limits: ResourceLimits = Field(default_factory=ResourceLimits)
    storage: StorageDeclaration = Field(default_factory=StorageDeclaration)
    settings: Tuple[SettingDeclaration, ...] = Field(default=(), max_length=32)
    migrations: Tuple[MigrationDeclaration, ...] = Field(default=(), max_length=8)
    privacy: PrivacyDeclaration = Field(default_factory=PrivacyDeclaration)
    changelog: str = Field(default="", max_length=2000)
    icon: str = Field(default="", max_length=240)
    development: bool = False

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if PLUGIN_ID_PATTERN.fullmatch(value) is None:
            raise ValueError("plugin id must look like publisher.name.")
        return value

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        if SEMVER_PATTERN.fullmatch(value) is None:
            raise ValueError("version must be dotted major.minor.patch.")
        return value

    @field_validator("min_joeos_version", "max_joeos_version")
    @classmethod
    def validate_joeos_version(cls, value: str) -> str:
        if value and SEMVER_PATTERN.fullmatch(value) is None:
            raise ValueError("JoeOS version bounds must be dotted major.minor.patch.")
        return value

    @field_validator("activation_events")
    @classmethod
    def validate_activation_events(cls, value: Tuple[str, ...]) -> Tuple[str, ...]:
        allowed = {
            "application_startup",
            "command_invoked",
            "project_opened",
            "project_detected",
            "file_opened",
            "document_type_opened",
            "tool_requested",
            "provider_selected",
            "agent_selected",
            "workspace_opened",
            "user_activation",
        }
        for event in value:
            if event not in allowed:
                raise ValueError("unknown activation event: %r" % event)
        if "application_startup" in value and len(value) > 1:
            raise ValueError("application_startup must be the only activation event.")
        return value

    @field_validator("conflicts")
    @classmethod
    def validate_conflicts(cls, value: Tuple[str, ...]) -> Tuple[str, ...]:
        for plugin_id in value:
            if PLUGIN_ID_PATTERN.fullmatch(plugin_id) is None:
                raise ValueError("conflict entry must look like publisher.name.")
        return value

    @model_validator(mode="after")
    def validate_manifest(self) -> "PluginManifest":
        if not self.id.startswith(self.publisher.id + "."):
            raise ValueError("plugin id must start with its publisher id.")
        contribution_ids = [contribution.id for contribution in self.contributions]
        if len(contribution_ids) != len(set(contribution_ids)):
            raise ValueError("contribution ids must be unique.")
        command_ids = [
            command
            for contribution in self.contributions
            if contribution.type == "command"
            for command in contribution.commands
        ]
        if len(command_ids) != len(set(command_ids)):
            raise ValueError("command ids must be unique.")
        setting_keys = [setting.key for setting in self.settings]
        if len(setting_keys) != len(set(setting_keys)):
            raise ValueError("setting keys must be unique.")
        required = {decl.permission for decl in self.required_permissions}
        optional = {decl.permission for decl in self.optional_permissions}
        if required.intersection(optional):
            raise ValueError("a permission cannot be both required and optional.")
        declared = required.union(optional)
        for contribution in self.contributions:
            for permission in contribution.requires_permissions:
                if permission not in declared:
                    raise ValueError(
                        "contribution %s uses undeclared permission %s" % (contribution.id, permission)
                    )
        if self.api_version > API_VERSION:
            raise ValueError("api_version is newer than this platform supports.")
        return self


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

PublisherVerificationState = Literal[
    "first_party",
    "verified",
    "user_trusted",
    "unverified",
    "unknown",
    "revoked",
    "blocked",
]

PluginLifecycleState = Literal[
    "discovered",
    "validating",
    "incompatible",
    "pending_trust",
    "pending_permissions",
    "installed",
    "disabled",
    "activating",
    "active",
    "idle",
    "deactivating",
    "crashed",
    "degraded",
    "quarantined",
    "updating",
    "rolling_back",
    "uninstalling",
    "removed",
    "unknown",
]

IntegrityState = Literal[
    "valid",
    "unsigned",
    "invalid",
    "locally_modified",
    "development_mode",
    "not_verified",
]

SignatureState = Literal[
    "valid",
    "valid_first_party",
    "valid_user_trusted",
    "unsigned",
    "invalid",
    "expired_signing_identity",
    "revoked_signing_identity",
    "unavailable",
    "locally_modified",
]

PluginHealthState = Literal[
    "healthy",
    "inactive",
    "disabled",
    "activating",
    "degraded",
    "incompatible",
    "permission_blocked",
    "dependency_blocked",
    "crashed",
    "resource_constrained",
    "quarantined",
    "update_failed",
    "migration_failed",
    "unknown",
]


class PublisherRecord(StrictModel):
    publisher_id: str
    display_name: str
    verification_state: PublisherVerificationState = "unknown"
    trusted: bool = False
    first_party: bool = False
    signing_fingerprints: Tuple[str, ...] = ()
    official_website: str = ""
    support: str = ""
    revoked: bool = False
    blocked: bool = False
    known_plugin_ids: Tuple[str, ...] = ()
    last_verified_at: str = ""
    created_at: str = ""


class PluginRecord(StrictModel):
    plugin_id: str
    display_name: str
    version: str
    publisher_id: str
    manifest: PluginManifest
    source: str = Field(default="", max_length=120)
    integrity_state: IntegrityState = "not_verified"
    signature_state: SignatureState = "unavailable"
    package_hash: str = ""
    signer_fingerprint: str = ""
    lifecycle_state: PluginLifecycleState = "discovered"
    health_state: PluginHealthState = "unknown"
    enabled_state: Literal[
        "enabled", "disabled", "enabled_workspace", "enabled_project",
        "temporarily_disabled", "disabled_after_crash", "quarantined"
    ] = "disabled"
    enabled_scope: str = "global"
    quarantine_reason: str = ""
    crash_count: int = 0
    install_path: str = ""
    package_path: str = ""
    installed_at: str = ""
    updated_at: str = ""


class ContributionRecord(StrictModel):
    contribution_id: str
    plugin_id: str
    type: ContributionType
    title: str = ""
    description: str = ""
    commands: Tuple[str, ...] = ()
    requires_permissions: Tuple[str, ...] = ()
    state: Literal["registered", "activating", "active", "disabled", "removed"] = "registered"
    registered_at: str = ""


class CompatibilityResult(StrictModel):
    plugin_id: str
    version: str
    decision: Literal[
        "compatible",
        "compatible_with_warnings",
        "incompatible",
        "requires_update",
        "requires_downgrade",
        "missing_dependency",
        "unsupported_platform",
        "unsupported_api",
        "migration_required",
        "policy_blocked",
    ]
    reasons: Tuple[str, ...] = ()


class HealthRecord(StrictModel):
    plugin_id: str
    state: PluginHealthState
    last_activation: str = ""
    last_success: str = ""
    last_crash: str = ""
    crash_count: int = 0
    recent_errors: Tuple[str, ...] = ()
    permission_state: str = "pending"
    dependency_state: str = "ok"
    resource_use: Dict[str, float] = Field(default_factory=dict)
    contribution_count: int = 0
    active_jobs: int = 0
    host_state: str = "not_running"
    update_state: str = "none"
    message: str = ""


class PluginLogRecord(StrictModel):
    plugin_id: str
    severity: Literal["debug", "info", "warn", "error"] = "info"
    category: str = ""
    message: str = ""
    recorded_at: str = ""


class PluginOverview(StrictModel):
    installed: int
    active: int
    disabled: int
    quarantined: int
    incompatible: int
    pending_permissions: int
    update_available: int
    unverified_publishers: int
    safe_mode: bool
    generated_at: str


class RpcRequest(StrictModel):
    id: int
    method: str = Field(min_length=1, max_length=80)
    params: Dict[str, object] = Field(default_factory=dict)


class RpcResponse(StrictModel):
    id: int
    status: Literal["ok", "error"] = "ok"
    result: object = None
    error_code: str = ""
    error_message: str = ""


class ActivityEvent(StrictModel):
    event_id: str
    plugin_id: str
    kind: str = Field(min_length=1, max_length=80)
    message: str = ""
    level: Literal["info", "success", "warn", "error"] = "info"
    recorded_at: str = ""
