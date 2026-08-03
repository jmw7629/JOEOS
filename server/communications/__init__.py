"""JoeOS Communications, Inbox, and Notification Hub.

A local-first, provider-neutral communications platform. Messages,
notifications, drafts, outbox items, identities, accounts, providers,
contacts, and attachments are typed and authoritative. External sending
requires explicit approval bound to content and recipients; message content is
always treated as untrusted (sanitized, link-checked, prompt-injection-marked).

See `docs/architecture/COMMUNICATIONS_PLATFORM.md` for the design.
"""

from .delivery import (
    AttachmentService,
    DeliveryService,
    ExternalSendApprovalCoordinator,
)
from .messages import DraftStore, MessageStore, OutboxService
from .models import (
    AccountRecord,
    AttachmentRef,
    CommunicationsOverview,
    CommunicationType,
    ContactRecord,
    DigestRecord,
    DraftRecord,
    IdentityRecord,
    MessageRecord,
    NotificationRecord,
    NotificationRule,
    Origin,
    OutboxItem,
    ProviderRecord,
    QuietHours,
    Recipient,
    Severity,
    Priority,
    Urgency,
)
from .notifications import NotificationCenter
from .registries import (
    AccountRegistry,
    ContactRegistry,
    IdentityRegistry,
    ProviderRegistry,
    RecipientResolver,
    RegistryError,
)
from .router import router as communications_router
from .safety import (
    analyze_links,
    content_hash,
    phishing_signals,
    prompt_injection_indicators,
    remote_content_links,
    sanitize_html,
    sanitize_link,
)
from .service import CommunicationsService
from .storage import CommunicationsStorage

__all__ = [
    "AccountRecord",
    "AccountRegistry",
    "AttachmentRef",
    "AttachmentService",
    "CommunicationsOverview",
    "CommunicationsService",
    "CommunicationsStorage",
    "communications_router",
    "CommunicationType",
    "ContactRecord",
    "ContactRegistry",
    "DeliveryService",
    "DigestRecord",
    "DraftRecord",
    "DraftStore",
    "ExternalSendApprovalCoordinator",
    "IdentityRecord",
    "IdentityRegistry",
    "MessageRecord",
    "MessageStore",
    "NotificationCenter",
    "NotificationRecord",
    "NotificationRule",
    "Origin",
    "OutboxItem",
    "OutboxService",
    "Priority",
    "ProviderRecord",
    "ProviderRegistry",
    "QuietHours",
    "Recipient",
    "RecipientResolver",
    "RegistryError",
    "Severity",
    "Urgency",
    "analyze_links",
    "content_hash",
    "phishing_signals",
    "prompt_injection_indicators",
    "remote_content_links",
    "sanitize_html",
    "sanitize_link",
]