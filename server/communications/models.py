"""Typed contracts for the JoeOS Communications, Inbox, and Notification Hub.

Messages, notifications, identities, accounts, providers, contacts,
conversations, drafts, outbox items, attachments, and policies are expressed
as strict, versioned, extra-forbidden models. No model carries authority by
itself; enforcement lives in the delivery, routing, and approval services.
"""

from __future__ import annotations

import re
from typing import Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

COMMUNICATIONS_SCHEMA_VERSION = 1
API_VERSION = 1

ID_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,79}$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


# ---------------------------------------------------------------------------
# Communication types & origins
# ---------------------------------------------------------------------------

CommunicationType = Literal[
    "internal_system_notification",
    "internal_direct_message",
    "mission_message",
    "task_message",
    "agent_message",
    "workflow_message",
    "approval_request",
    "intervention_request",
    "review_request",
    "escalation",
    "announcement",
    "reminder",
    "digest",
    "status_report",
    "incident_alert",
    "build_alert",
    "test_alert",
    "runtime_alert",
    "model_alert",
    "plugin_alert",
    "security_alert",
    "project_update",
    "external_email",
    "external_chat_message",
]

OriginType = Literal[
    "user",
    "joeos_core",
    "agent",
    "workflow",
    "plugin",
    "project",
    "external_provider",
    "external_person",
    "external_organization",
]

Severity = Literal["informational", "success", "warning", "error", "critical", "security_critical"]
Priority = Literal["low", "normal", "high", "urgent"]
Urgency = Literal["immediate", "soon", "routine", "digest_only"]

DeliveryState = Literal[
    "pending_validation",
    "pending_approval",
    "scheduled",
    "queued",
    "waiting_for_provider",
    "sending",
    "sent",
    "sent_with_warning",
    "failed",
    "partially_delivered",
    "cancelled",
    "expired",
    "blocked",
    "uncertain",
]


class Origin(StrictModel):
    origin_type: OriginType
    label: str = Field(min_length=1, max_length=80)
    source_service: str = Field(default="", max_length=80)
    source_plugin: str = Field(default="", max_length=80)
    source_workflow: str = Field(default="", max_length=80)
    source_mission: str = Field(default="", max_length=80)
    source_task: str = Field(default="", max_length=80)
    source_agent: str = Field(default="", max_length=80)

    @model_validator(mode="after")
    def validate_origin(self) -> "Origin":
        if self.origin_type in {"agent", "workflow", "plugin"}:
            if not (self.source_agent or self.source_workflow or self.source_plugin):
                raise ValueError("agent/workflow/plugin origins must identify their source.")
        return self


# ---------------------------------------------------------------------------
# Identities, providers, accounts, contacts
# ---------------------------------------------------------------------------

IdentityType = Literal[
    "local_user",
    "joeos_system",
    "agent",
    "workflow",
    "plugin",
    "external_person",
    "external_organization",
    "provider_account",
]

VerificationState = Literal["unverified", "verified", "user_trusted", "revoked"]


class IdentityRecord(StrictModel):
    identity_id: str
    display_name: str
    identity_type: IdentityType
    user_owned: bool = False
    provider: str = ""
    account: str = ""
    verified_addresses: Tuple[str, ...] = ()
    verified_handles: Tuple[str, ...] = ()
    verification_state: VerificationState = "unverified"
    sending_permission: bool = False
    default_state: bool = False
    privacy: str = "private"
    created_at: str = ""
    disabled: bool = False

    @field_validator("identity_id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if ID_PATTERN.fullmatch(value) is None:
            raise ValueError("identity id must be a lowercase dotted identifier.")
        return value


class ProviderCapabilities(StrictModel):
    send: bool = False
    receive: bool = False
    attachments: bool = False
    rich_content: bool = False
    delivery_receipts: bool = False
    read_receipts: bool = False
    drafts: bool = False
    scheduled_send: bool = False
    search: bool = False
    sync: bool = False
    threading: bool = False


class ProviderRecord(StrictModel):
    provider_id: str
    provider_type: str = "generic"
    display_name: str
    capabilities: ProviderCapabilities = Field(default_factory=ProviderCapabilities)
    authentication: Literal["none", "api_key", "oauth", "plugin"] = "none"
    plugin_source: str = ""
    health_state: str = "unknown"
    privacy: str = "private"
    is_isolated_test: bool = False


class AccountRecord(StrictModel):
    account_id: str
    provider_id: str
    display_label: str
    identity_id: str = ""
    enabled: bool = False
    connection_state: Literal[
        "configured",
        "connected",
        "disconnected",
        "authentication_required",
        "permission_blocked",
        "plugin_unavailable",
        "unsupported",
        "degraded",
        "disabled",
        "unknown",
    ] = "unknown"
    capabilities: ProviderCapabilities = Field(default_factory=ProviderCapabilities)
    sending_permission: bool = False
    last_sync: str = ""
    last_failure: str = ""
    health: str = "unknown"
    plugin_source: str = ""
    removed: bool = False
    created_at: str = ""


class ContactRecord(StrictModel):
    contact_id: str
    display_name: str
    organization: str = ""
    role: str = ""
    addresses: Tuple[str, ...] = ()
    handles: Tuple[str, ...] = ()
    aliases: Tuple[str, ...] = ()
    preferred_channel: str = ""
    timezone: str = ""
    language: str = ""
    trust_state: str = "unknown"
    verification_state: VerificationState = "unverified"
    source: str = ""
    privacy: str = "private"
    last_interaction: str = ""
    deleted: bool = False
    created_at: str = ""


class Recipient(StrictModel):
    recipient_id: str
    display_name: str = ""
    destination: str = ""
    provider: str = ""
    verification: VerificationState = "unverified"
    trust: str = "unknown"
    source: str = ""
    ambiguity: bool = False
    privacy_compatible: bool = True


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

class AttachmentRef(StrictModel):
    attachment_id: str
    display_name: str
    mime_type: str = ""
    size: int = 0
    content_hash: str = ""
    provider_state: str = "local"
    privacy: str = "private"
    approved: bool = False
    path: str = ""
    source_project: str = ""


class MessageRecord(StrictModel):
    message_id: str
    communication_type: CommunicationType
    provider: str = ""
    provider_message_id: str = ""
    account: str = ""
    origin: Origin
    author: str = ""
    sender_identity: str = ""
    recipients: Tuple[str, ...] = ()
    conversation_id: str = ""
    thread_id: str = ""
    parent_message: str = ""
    subject: str = ""
    body: str = ""
    rich_body: str = ""
    attachments: Tuple[AttachmentRef, ...] = ()
    links: Tuple[str, ...] = ()
    mentions: Tuple[str, ...] = ()
    priority: Priority = "normal"
    severity: Severity = "informational"
    privacy: str = "private"
    draft_state: bool = False
    approval_state: str = "none"
    delivery_state: DeliveryState = "pending_validation"
    read_state: Literal["delivered", "displayed", "read", "acknowledged", "acted_on"] = "delivered"
    archive_state: bool = False
    mute_state: bool = False
    snooze_until: str = ""
    scheduled_send: str = ""
    sent_at: str = ""
    received_at: str = ""
    delivery_attempts: int = 0
    content_hash: str = ""
    provenance: Dict[str, object] = Field(default_factory=dict)
    verification_state: VerificationState = "unverified"
    phishing_indicators: Tuple[str, ...] = ()
    deletion_state: str = "active"
    created_at: str = ""
    external: bool = False


class DraftRecord(StrictModel):
    draft_id: str
    author: str = ""
    proposed_sender: str = ""
    recipients: Tuple[str, ...] = ()
    provider: str = ""
    account: str = ""
    conversation_id: str = ""
    thread_id: str = ""
    subject: str = ""
    body: str = ""
    attachments: Tuple[AttachmentRef, ...] = ()
    privacy: str = "private"
    source: str = ""
    source_agent: str = ""
    source_workflow: str = ""
    source_task: str = ""
    approval_required: bool = False
    approval_state: str = "none"
    scheduled_send: str = ""
    conflict_state: str = "clean"
    created_at: str = ""
    updated_at: str = ""


class OutboxItem(StrictModel):
    outbox_id: str
    message_id: str = ""
    sender_identity: str = ""
    recipients: Tuple[str, ...] = ()
    provider: str = ""
    account: str = ""
    scheduled: str = ""
    approval_state: str = "none"
    attempts: int = 0
    idempotency_key: str = ""
    state: DeliveryState = "queued"
    failure: str = ""
    retryable: bool = False
    created_at: str = ""
    sent_at: str = ""


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

class NotificationRecord(StrictModel):
    notification_id: str
    source: str
    source_type: str = ""
    category: str = ""
    title: str
    message: str = ""
    severity: Severity = "informational"
    priority: Priority = "normal"
    urgency: Urgency = "routine"
    privacy: str = "private"
    project: str = ""
    mission: str = ""
    task: str = ""
    workflow: str = ""
    plugin: str = ""
    service: str = ""
    related_entity: str = ""
    action_links: Tuple[str, ...] = ()
    created_at: str = ""
    updated_at: str = ""
    expiration: str = ""
    delivery_channels: Tuple[str, ...] = ()
    delivery_state: str = "created"
    read_state: Literal["delivered", "displayed", "read", "acknowledged"] = "delivered"
    archive_state: bool = False
    mute_state: bool = False
    snooze_until: str = ""
    deduplication_key: str = ""
    grouping_key: str = ""
    escalation_policy: str = ""
    trace_id: str = ""


class QuietHours(StrictModel):
    enabled: bool = False
    timezone: str = "UTC"
    weekday_start: str = "22:00"
    weekday_end: str = "07:00"
    weekend_start: str = "22:00"
    weekend_end: str = "09:00"
    critical_exceptions: bool = True
    security_exceptions: bool = True


class NotificationRule(StrictModel):
    rule_id: str
    source: str = ""
    category: str = ""
    severity: Optional[Severity] = None
    action: Literal["deliver", "digest", "suppress", "mute", "escalate"] = "deliver"
    channel: str = ""
    priority: int = Field(default=50, ge=0, le=100)
    enabled: bool = True
    created_at: str = ""


class DigestRecord(StrictModel):
    digest_id: str
    time_window_start: str = ""
    time_window_end: str = ""
    source_categories: Tuple[str, ...] = ()
    important_items: Tuple[str, ...] = ()
    unresolved_items: Tuple[str, ...] = ()
    failures: Tuple[str, ...] = ()
    approvals: Tuple[str, ...] = ()
    generation_method: str = "structured"
    privacy: str = "private"
    created_at: str = ""


class CommunicationsOverview(StrictModel):
    unread_focused: int
    pending_approvals: int
    agent_requests: int
    outbox_count: int
    failed_deliveries: int
    security_alerts_unacknowledged: int
    snoozed: int
    unhealthy_accounts: int
    unhealthy_providers: int
    quiet_hours_active: bool
    next_digest: Optional[str] = None
    generated_at: str