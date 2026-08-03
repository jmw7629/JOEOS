"""Typed contracts for the JoeOS Mobile Companion and Secure Remote Operations
Platform.

Mobile clients, hosts, pairing sessions, authentication, sessions, scoped
remote operations, offline actions, handoffs, deep links, and push
registrations are expressed as strict, versioned, extra-forbidden models. No
model carries authority by itself; enforcement lives in the pairing,
authentication, remote-command, offline, and revocation services. The mobile
client always remains a client of authoritative JoeOS services.
"""

from __future__ import annotations

import re
from typing import Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MOBILE_SCHEMA_VERSION = 1
REMOTE_API_VERSION = 1

ID_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,79}$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


# ---------------------------------------------------------------------------
# Mobile client identity & states
# ---------------------------------------------------------------------------

MobileClientState = Literal[
    "unconfigured",
    "host_not_selected",
    "discovered",
    "pairing",
    "pairing_failed",
    "paired",
    "trusted",
    "restricted",
    "authenticating",
    "connected",
    "reconnecting",
    "offline",
    "cached_only",
    "session_expired",
    "reauthentication_required",
    "permission_blocked",
    "host_unavailable",
    "version_incompatible",
    "revoked",
    "quarantined",
    "application_update_required",
    "unknown",
]


class MobileClientRecord(StrictModel):
    client_id: str
    platform: str = "ios"
    os_version: str = ""
    app_version: str = ""
    build_number: str = ""
    device_model_category: str = ""
    installation_identity: str = ""
    paired_host: str = ""
    paired_user: str = "user"
    crypto_identity_reference: str = ""
    pairing_state: str = "unconfigured"
    trust_state: str = "untrusted"
    authentication_state: str = "unauthenticated"
    permission_grants: Tuple[str, ...] = ()
    project_grants: Tuple[str, ...] = ()
    privacy_policy: str = "normal"
    notification_policy: str = "normal"
    last_connection: str = ""
    last_sync: str = ""
    active_session: str = ""
    push_registration_state: str = "unregistered"
    background_capability_state: str = "unknown"
    health: str = "unknown"
    revocation_state: str = "active"
    removal_state: str = "active"
    created_at: str = ""

    @field_validator("client_id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if ID_PATTERN.fullmatch(value) is None:
            raise ValueError("client id must be a lowercase dotted identifier.")
        return value


# ---------------------------------------------------------------------------
# Hosts
# ---------------------------------------------------------------------------

HostConnectionMethod = Literal["local_network", "secure_overlay", "relay", "manual"]

HostState = Literal[
    "unconfigured",
    "discovered",
    "paired",
    "trusted",
    "reconnecting",
    "offline",
    "unavailable",
    "version_incompatible",
    "revoked",
    "removed",
    "unknown",
]


class HostRecord(StrictModel):
    host_id: str
    display_name: str
    instance_identity: str = ""
    installation_identity: str = ""
    connection_methods: Tuple[str, ...] = ("local_network",)
    local_endpoint: str = ""
    secure_overlay_endpoint: str = ""
    relay_endpoint: str = ""
    tls_identity: str = ""
    certificate_fingerprint: str = ""
    api_version: int = REMOTE_API_VERSION
    supported_capabilities: Tuple[str, ...] = ()
    paired_state: str = "unpaired"
    trusted_state: str = "untrusted"
    last_connection: str = ""
    last_authentication: str = ""
    reachability: str = "unknown"
    latency_ms: Optional[int] = None
    health: str = "unknown"
    current_user: str = "user"
    compatibility_state: str = "unknown"
    revocation_state: str = "active"

    @field_validator("host_id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if ID_PATTERN.fullmatch(value) is None:
            raise ValueError("host id must be a lowercase dotted identifier.")
        return value


class DiscoveryResult(StrictModel):
    host_id: str
    display_name: str
    instance_identity: str
    connection_path: str
    trust_state: str = "untrusted"
    tls_state: str = "unknown"
    compatibility: str = "unknown"
    pairing_required: bool = True
    last_seen: str = ""
    network_classification: str = "local"


# ---------------------------------------------------------------------------
# Pairing & authentication
# ---------------------------------------------------------------------------

PairingMethod = Literal["qr", "one_time_link", "one_time_code", "certificate_exchange", "challenge_response"]


class PairingSession(StrictModel):
    session_id: str
    host_id: str
    client_id: str = ""
    method: PairingMethod = "one_time_code"
    code_reference: str = ""
    code_hash: str = ""
    # Shown once on the trusted host operator surface; never stored. Empty in
    # every persisted representation.
    display_code: str = ""
    expires_at: str = ""
    state: Literal["pending", "host_confirmed", "client_confirmed", "completed", "cancelled", "expired", "failed"] = "pending"
    api_version: int = REMOTE_API_VERSION
    requested_permissions: Tuple[str, ...] = ()
    requested_projects: Tuple[str, ...] = ()
    created_at: str = ""


class MobileSession(StrictModel):
    session_id: str
    client_id: str
    host_id: str
    user_identity: str = "user"
    started_at: str = ""
    expires_at: str = ""
    last_activity: str = ""
    transport: str = "https"
    encryption_state: str = "encrypted"
    api_version: int = REMOTE_API_VERSION
    granted_capabilities: Tuple[str, ...] = ()
    granted_projects: Tuple[str, ...] = ()
    granted_scopes: Tuple[str, ...] = ()
    background_eligible: bool = False
    notification_eligible: bool = False
    risk_state: str = "normal"
    device_lock_state: str = "unlocked"
    authentication_strength: str = "host_authenticated"
    active_subscriptions: Tuple[str, ...] = ()
    queued_operations: int = 0
    termination_reason: str = ""
    connection_state: Literal["active", "reconnecting", "offline", "expired", "revoked", "terminated"] = "active"


# ---------------------------------------------------------------------------
# Mobile permissions
# ---------------------------------------------------------------------------

# Canonical mobile permission catalog (data scopes, action scopes, hardware).
MOBILE_PERMISSIONS: Dict[str, str] = {
    "data.view_system_status": "View system status.",
    "data.view_projects": "View authorized projects.",
    "data.view_project_names_only": "View project names only.",
    "data.view_missions": "View missions.",
    "data.view_tasks": "View tasks.",
    "data.view_agents": "View agents.",
    "data.view_communications": "View communications.",
    "data.view_private_communication_previews": "View private communication previews.",
    "data.view_repository_metadata": "View repository metadata.",
    "data.view_code_excerpts": "View code excerpts.",
    "data.view_diffs": "View diffs.",
    "data.view_test_results": "View test results.",
    "data.view_build_results": "View build results.",
    "data.view_artifacts": "View artifacts.",
    "data.view_selected_memory": "View selected memory.",
    "data.view_devices": "View devices.",
    "action.acknowledge_notification": "Acknowledge a notification.",
    "action.respond_internal": "Respond internally.",
    "action.create_note": "Create a note.",
    "action.create_task_proposal": "Create a task proposal.",
    "action.pause_task": "Pause a task.",
    "action.cancel_task": "Cancel a task.",
    "action.pause_mission": "Pause a mission.",
    "action.approve_low_risk": "Approve a low-risk action.",
    "action.deny_action": "Deny an action.",
    "action.trigger_selected_workflow": "Trigger a selected workflow.",
    "action.select_model": "Select a model.",
    "action.start_bounded_agent_task": "Start a bounded agent task.",
    "action.request_test": "Request a test run.",
    "action.request_build": "Request a build.",
    "action.request_desktop_handoff": "Request a desktop handoff.",
    "hardware.camera": "Use the camera.",
    "hardware.microphone": "Use the microphone.",
    "hardware.photo_library": "Use the photo library.",
    "hardware.file_picker": "Use the file picker.",
    "hardware.notifications": "Receive notifications.",
    "hardware.local_network": "Access the local network.",
    "hardware.background_refresh": "Background refresh.",
}

NON_DEFAULT_PRIVILEGED = {
    "data.view_private_communication_previews",
    "data.view_code_excerpts",
    "data.view_diffs",
    "data.view_selected_memory",
    "action.approve_low_risk",
    "action.cancel_task",
    "action.start_bounded_agent_task",
    "hardware.camera",
    "hardware.microphone",
    "hardware.photo_library",
    "hardware.local_network",
    "hardware.background_refresh",
}

# Actions that can be queued offline safely.
OFFLINE_SAFE_ACTIONS = {
    "mark_notification_read",
    "acknowledge_notification",
    "archive_routine_item",
    "draft_internal_reply",
    "create_note",
    "create_task_proposal",
    "update_checklist",
    "request_handoff",
}

OFFLINE_PROHIBITED_ACTIONS = {
    "destructive_approval",
    "external_send_approval",
    "git_push",
    "deployment",
    "file_deletion",
    "service_restart",
    "secret_access",
    "arbitrary_command",
    "high_risk_task_cancellation",
}


# ---------------------------------------------------------------------------
# Offline actions & sync
# ---------------------------------------------------------------------------

class OfflineAction(StrictModel):
    action_id: str
    client_id: str
    host_id: str
    session_id: str = ""
    user_identity: str = "user"
    action: str
    target: str = ""
    base_version: str = ""
    arguments_hash: str = ""
    created_at: str = ""
    expires_at: str = ""
    idempotency_key: str = ""
    privacy: str = "private"
    project: str = ""
    conflict_policy: str = "keep_authoritative"
    permission_state: str = "pending"
    approval_state: str = "none"
    retry_state: str = "queued"


class SyncRecord(StrictModel):
    record_id: str
    record_version: str = ""
    source_host: str = ""
    update_time: str = ""
    freshness: Literal["live", "cached", "stale"] = "live"
    privacy: str = "private"
    project: str = ""
    deletion_state: str = "active"
    conflict_state: str = "clean"


class ConflictResolution(StrictModel):
    conflict_id: str
    action_id: str = ""
    target: str = ""
    base_version: str = ""
    current_version: str = ""
    outcome: Literal["apply_safely", "discard_stale", "merge", "keep_mobile_draft", "keep_server_state", "create_conflict_copy", "require_review", "recreate_against_current", "escalate_to_desktop"] = "keep_server_state"
    reason: str = ""


# ---------------------------------------------------------------------------
# Handoff & deep links
# ---------------------------------------------------------------------------

class HandoffRecord(StrictModel):
    handoff_id: str
    source_surface: str
    destination_surface: str
    user_identity: str = "user"
    host_id: str = ""
    item_type: str = ""
    item_id: str = ""
    content_position: str = ""
    selected_tab: str = ""
    unsent_draft: str = ""
    pending_action: str = ""
    privacy: str = "private"
    expiration: str = ""
    state: Literal["created", "accepted", "rejected", "expired"] = "created"
    idempotency_key: str = ""
    created_at: str = ""


class DeepLinkReference(StrictModel):
    link_id: str
    host_id: str = ""
    user_identity: str = "user"
    target_type: str = ""
    target_id: str = ""
    scope: str = ""
    expires_at: str = ""
    state: Literal["active", "used", "expired", "revoked"] = "active"
    created_at: str = ""


# ---------------------------------------------------------------------------
# Push & local notifications
# ---------------------------------------------------------------------------

class PushRegistration(StrictModel):
    registration_id: str
    client_id: str
    platform: str = "ios"
    provider: str = "apns"
    push_token_reference: str = ""
    environment: str = "sandbox"
    registered_at: str = ""
    last_validation: str = ""
    enabled_categories: Tuple[str, ...] = ()
    privacy_mode: str = "normal"
    quiet_hours: bool = False
    health: str = "unknown"
    revocation_state: str = "active"


class NotificationDelivery(StrictModel):
    delivery_id: str
    client_id: str
    category: str = ""
    privacy_safe_title: str = ""
    privacy_safe_body: str = ""
    target_deep_link: str = ""
    severity: str = "informational"
    created_at: str = ""
    provider_state: str = "queued"
    is_test_fixture: bool = False


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------

class MobileOverview(StrictModel):
    paired_clients: int
    connected_clients: int
    active_sessions: int
    revoked_clients: int
    pending_pairings: int
    sync_failures: int
    stale_offline_actions: int
    push_registration_failures: int
    incompatible_versions: int
    lost_devices: int
    generated_at: str