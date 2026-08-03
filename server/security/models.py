"""Typed contracts for the JoeOS Security Platform.

Security policies, threat models, identities, scopes, approvals, secrets,
audit events, incidents, lockdown, emergency stop, quarantine, circuit
breakers, data classifications, and privacy decisions are expressed as strict,
versioned, extra-forbidden models. No model carries authority by itself;
enforcement lives in the policy evaluation engine and the authoritative
services that consult it.
"""

from __future__ import annotations

import re
from typing import Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SECURITY_SCHEMA_VERSION = 1
POLICY_SCHEMA_VERSION = 1

ID_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,79}$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

PolicyEffect = Literal[
    "allow",
    "deny",
    "require_approval",
    "require_stronger_authentication",
    "require_local_only",
    "require_redaction",
    "require_quarantine",
    "require_review",
    "limit_scope",
    "limit_duration",
    "limit_resources",
    "log",
    "alert",
]

PolicyScope = Literal[
    "organization",
    "user",
    "host",
    "workspace",
    "project",
    "task",
    "mission",
    "workflow",
    "plugin",
    "agent",
    "device",
    "provider",
    "identity",
    "all",
]


class SecurityPolicy(StrictModel):
    policy_id: str
    version: int = 1
    title: str
    description: str = ""
    scope: PolicyScope
    scope_target: str = ""
    action: str
    resource: str = ""
    effect: PolicyEffect
    priority: int = Field(default=50, ge=0, le=100)
    conditions: Dict[str, object] = Field(default_factory=dict)
    exceptions: Tuple[str, ...] = ()
    authority: str = "user"
    owner: str = "user"
    created_at: str = ""
    review_time: str = ""
    expiration: str = ""
    enabled: bool = True
    superseded: bool = False

    @field_validator("policy_id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if ID_PATTERN.fullmatch(value) is None:
            raise ValueError("policy id must be a lowercase dotted identifier.")
        return value

    @model_validator(mode="after")
    def validate_effect_scope(self) -> "SecurityPolicy":
        if not self.action:
            raise ValueError("policy action is required.")
        return self


class PolicyDecision(StrictModel):
    decision_id: str
    effect: PolicyEffect
    matched_rules: Tuple[str, ...] = ()
    denied_rules: Tuple[str, ...] = ()
    required_approval: Optional[str] = None
    required_authentication: str = "none"
    allowed_scope: str = ""
    explanation: str = ""
    policy_version: int = POLICY_SCHEMA_VERSION
    trace_id: str = ""


class PolicyRequestContext(StrictModel):
    subject: str
    subject_type: str = "identity"
    device: str = ""
    session: str = ""
    role: str = ""
    agent: str = ""
    plugin: str = ""
    workflow: str = ""
    task: str = ""
    mission: str = ""
    project: str = ""
    workspace: str = ""
    action: str
    target: str = ""
    data_classification: str = "unknown"
    provider: str = ""
    runtime: str = ""
    model: str = ""
    network_destination: str = ""
    time: str = ""
    risk: str = "low"
    approval_state: str = "none"
    trust_state: str = "unknown"
    trace_id: str = ""


# ---------------------------------------------------------------------------
# Threat models
# ---------------------------------------------------------------------------

class ThreatModel(StrictModel):
    threat_model_id: str
    subsystem: str
    version: int = 1
    assets: Tuple[str, ...] = ()
    actors: Tuple[str, ...] = ()
    trust_boundaries: Tuple[str, ...] = ()
    entry_points: Tuple[str, ...] = ()
    data_flows: Tuple[str, ...] = ()
    assumptions: Tuple[str, ...] = ()
    threats: Tuple[str, ...] = ()
    mitigations: Tuple[str, ...] = ()
    residual_risk: str = ""
    owner: str = ""
    review_date: str = ""
    status: str = "draft"
    created_at: str = ""

    @field_validator("threat_model_id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if ID_PATTERN.fullmatch(value) is None:
            raise ValueError("threat model id must be a lowercase dotted identifier.")
        return value


# ---------------------------------------------------------------------------
# Identity & scope
# ---------------------------------------------------------------------------

IdentityType = Literal[
    "human_user",
    "local_system_service",
    "privileged_process",
    "renderer_client",
    "web_client",
    "mobile_client",
    "wearable_client",
    "plugin",
    "plugin_publisher",
    "workflow",
    "automation_trigger",
    "agent",
    "agent_role",
    "model_runtime",
    "model_provider",
    "communications_provider",
    "project",
    "organization",
    "device",
    "test_fixture",
]


class IdentityRecord(StrictModel):
    identity_id: str
    identity_type: IdentityType
    display_label: str
    owner: str = ""
    issuer: str = ""
    trust_state: str = "untrusted"
    status: Literal["active", "revoked", "quarantined", "disabled"] = "active"
    credentials_reference: str = ""
    created_at: str = ""
    last_activity: str = ""
    expiration: str = ""
    revocation_state: str = "active"

    @field_validator("identity_id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if ID_PATTERN.fullmatch(value) is None:
            raise ValueError("identity id must be a lowercase dotted identifier.")
        return value


class ScopeGrant(StrictModel):
    grant_id: str
    subject: str
    capability: str
    action: str = ""
    resource: str = ""
    scope: str = "session"
    project: str = ""
    task: str = ""
    mission: str = ""
    device: str = ""
    conditions: Dict[str, object] = Field(default_factory=dict)
    duration: str = ""
    issued_by: str = "user"
    authority: str = "user"
    approval: str = ""
    created_at: str = ""
    expiration: str = ""
    usage_count: int = 0
    last_use: str = ""
    revocation_state: str = "active"


# ---------------------------------------------------------------------------
# Approval
# ---------------------------------------------------------------------------

ApprovalStrength = Literal["level0", "level1", "level2", "level3", "level4", "level5"]


class ApprovalRequestRecord(StrictModel):
    approval_id: str
    requester_identity: str
    approver_identity: str = ""
    host: str = ""
    device: str = ""
    session: str = ""
    action_id: str
    target_id: str = ""
    target_type: str = ""
    arguments_hash: str = ""
    content_hash: str = ""
    attachment_hashes: Tuple[str, ...] = ()
    workflow_version: str = ""
    plugin_version: str = ""
    project: str = ""
    task: str = ""
    mission: str = ""
    data_classification: str = "unknown"
    risk: str = "low"
    strength_required: ApprovalStrength = "level1"
    expiration: str = ""
    policy_version: int = POLICY_SCHEMA_VERSION
    state: Literal["pending", "approved", "denied", "expired", "invalidated"] = "pending"
    created_at: str = ""
    resolved_at: str = ""


class ConsentRecord(StrictModel):
    consent_id: str
    identity: str
    purpose: str
    data: str = ""
    destination: str = ""
    duration: str = ""
    policy_version: int = POLICY_SCHEMA_VERSION
    state: Literal["active", "withdrawn", "expired"] = "active"
    created_at: str = ""


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------

SecretType = Literal[
    "api_key", "access_token", "refresh_token", "password", "database_credential",
    "private_key", "signing_key", "certificate", "provider_credential",
    "git_credential", "ssh_credential", "plugin_secret", "workflow_secret",
    "device_secret", "mobile_credential", "webhook_secret", "encryption_key",
]


class SecretMetadata(StrictModel):
    secret_id: str
    display_label: str
    secret_type: SecretType
    owner: str = "user"
    scope: str = "global"
    project: str = ""
    plugin: str = ""
    workflow: str = ""
    provider: str = ""
    device: str = ""
    storage_adapter: str = "encrypted_vault"
    created_at: str = ""
    last_rotation: str = ""
    expiration: str = ""
    last_use: str = ""
    usage_count: int = 0
    allowed_operations: Tuple[str, ...] = ()
    allowed_destinations: Tuple[str, ...] = ()
    revoked_state: str = "active"
    health: str = "healthy"


class SecretDetection(StrictModel):
    detection_id: str
    candidate_type: str
    confidence: Literal["candidate", "likely", "confirmed"] = "candidate"
    masked_fingerprint: str = ""
    location: str = ""
    source: str = ""
    status: str = "open"
    created_at: str = ""


# ---------------------------------------------------------------------------
# Data classification & privacy
# ---------------------------------------------------------------------------

DataClass = Literal[
    "public", "internal", "personal", "confidential", "restricted", "secret",
    "credential", "security_sensitive", "regulated", "unknown",
]

PrivacyDecision = Literal[
    "allow_local_processing",
    "allow_encrypted_storage",
    "allow_selected_device_display",
    "redact",
    "mask",
    "summarize",
    "block_external_provider",
    "block_cloud_ai",
    "block_semantic_indexing",
    "block_notification_preview",
    "block_backup",
    "require_explicit_consent",
    "require_deletion_after_use",
]


# ---------------------------------------------------------------------------
# Audit & events & incidents
# ---------------------------------------------------------------------------

class AuditEvent(StrictModel):
    event_id: str
    timestamp: str
    actor: str
    actor_type: str = "identity"
    session: str = ""
    device: str = ""
    action: str
    target: str = ""
    project: str = ""
    task: str = ""
    mission: str = ""
    plugin: str = ""
    workflow: str = ""
    provider: str = ""
    permission_decision: str = ""
    approval: str = ""
    policy_version: int = POLICY_SCHEMA_VERSION
    result: str = "allowed"
    risk: str = "low"
    source: str = ""
    trace_id: str = ""
    integrity_hash: str = ""
    previous_hash: str = ""


class SecurityEvent(StrictModel):
    event_id: str
    category: str
    severity: str = "warning"
    confidence: str = "candidate"
    evidence: str = ""
    affected_identity: str = ""
    affected_project: str = ""
    affected_service: str = ""
    timestamp: str = ""
    recommended_action: str = ""
    status: str = "open"
    trace_id: str = ""


class IncidentRecord(StrictModel):
    incident_id: str
    title: str
    severity: str = "medium"
    status: str = "new"
    detection_source: str = ""
    affected_assets: Tuple[str, ...] = ()
    affected_identities: Tuple[str, ...] = ()
    affected_secrets: Tuple[str, ...] = ()
    timeline: Tuple[Dict[str, str], ...] = ()
    evidence: str = ""
    containment: str = ""
    eradication: str = ""
    recovery: str = ""
    residual_risk: str = ""
    owner: str = ""
    created_at: str = ""
    resolved_at: str = ""


# ---------------------------------------------------------------------------
# Governance & resources
# ---------------------------------------------------------------------------

class LockdownState(StrictModel):
    active: bool = False
    activated_by: str = ""
    activated_at: str = ""
    reason: str = ""
    restrictions: Tuple[str, ...] = ()


class CircuitBreakerState(StrictModel):
    breaker_id: str
    target: str
    state: Literal["closed", "open", "half_open", "manually_disabled", "quarantined"] = "closed"
    failures: int = 0
    opened_at: str = ""
    retry_after: str = ""
    last_error: str = ""


class SecurityOverview(StrictModel):
    active_sessions: int
    revoked_devices: int
    revoked_mobile_clients: int
    quarantined_plugins: int
    pending_approvals: int
    secrets_requiring_rotation: int
    security_events_open: int
    incidents_open: int
    audit_integrity_verified: bool
    lockdown_active: bool
    public_listeners: int
    untrusted_projects: int
    generated_at: str