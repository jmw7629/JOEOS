"""CommunicationsService facade: one authoritative entry point into the JoeOS
Communications, Inbox, and Notification Hub.

Composes the Provider, Account, Identity, and Contact registries, Recipient
Resolver, Message Store, Draft Store, Outbox, Delivery Service, Notification
Center, quiet hours/DND, digests, content safety, attachment service, and
external-send approval. All services share one SQLite database.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .delivery import (
    AttachmentService,
    DeliveryService,
    ExternalSendApprovalCoordinator,
)
from .messages import DraftStore, MessageStore, OutboxService
from .models import (
    AttachmentRef,
    CommunicationsOverview,
    ContactRecord,
    DigestRecord,
    DraftRecord,
    IdentityRecord,
    MessageRecord,
    NotificationRecord,
    OutboxItem,
    ProviderRecord,
    QuietHours,
    Recipient,
    AccountRecord,
    Origin,
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
from .safety import (
    analyze_links,
    content_hash,
    phishing_signals,
    prompt_injection_indicators,
    remote_content_links,
    sanitize_html,
    sanitize_link,
)
from .storage import CommunicationsStorage


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CommunicationsService:
    def __init__(
        self,
        data_dir: str,
        *,
        event_sink=None,
        provider_dispatch=None,
        attachment_roots: Sequence[str] = (),
    ) -> None:
        self.storage = CommunicationsStorage(data_dir)
        self.storage.prepare()
        self._data_dir = Path(data_dir)
        self._event_sink = event_sink or (lambda level, source, message: None)

        self.providers = ProviderRegistry(self._connection_factory)
        self.accounts = AccountRegistry(self._connection_factory)
        self.identities = IdentityRegistry(self._connection_factory)
        self.contacts = ContactRegistry(self._connection_factory)
        self.resolver = RecipientResolver(self.contacts, self.identities)
        self.messages = MessageStore(self._connection_factory)
        self.drafts = DraftStore(self._connection_factory)
        self.outbox = OutboxService(self._connection_factory)
        self.approvals = ExternalSendApprovalCoordinator(self._connection_factory)
        self.delivery = DeliveryService(
            connection_factory=self._connection_factory,
            messages=self.messages,
            outbox=self.outbox,
            approvals=self.approvals,
            provider_dispatch=provider_dispatch,
            event_sink=self._event_sink,
        )
        self.notifications = NotificationCenter(self._connection_factory)
        self.attachments = AttachmentService(self._connection_factory, attachment_roots)

    def _connection_factory(self):
        connection = self.storage.connect()
        return _BorrowedConnection(connection)

    # ------------------------------------------------------------------
    # Bootstrap providers / identities
    # ------------------------------------------------------------------

    def prepare_defaults(self) -> None:
        self.providers.register(
            provider_id="joeos.internal",
            display_name="JoeOS Internal",
            provider_type="internal",
            capabilities=_caps(send=True, receive=True, sync=False),
        )
        self.providers.register(
            provider_id="test.isolated",
            display_name="Isolated Test Provider",
            provider_type="test",
            capabilities=_caps(send=True, receive=True, attachments=True, delivery_receipts=True),
            is_isolated_test=True,
        )
        self.identities.create(
            identity_id="identity.user",
            display_name="You",
            identity_type="local_user",
            user_owned=True,
            sending_permission=True,
            default_state=True,
        )
        self.identities.create(
            identity_id="identity.joeos",
            display_name="JoeOS",
            identity_type="joeos_system",
            user_owned=False,
            sending_permission=True,
        )

    # ------------------------------------------------------------------
    # Providers / accounts / identities / contacts
    # ------------------------------------------------------------------

    def list_providers(self) -> Tuple[ProviderRecord, ...]:
        return self.providers.list()

    def list_accounts(self) -> Tuple[AccountRecord, ...]:
        return self.accounts.list()

    def register_account(self, *, provider_id: str, display_label: str, sending_permission: bool = False) -> AccountRecord:
        return self.accounts.register(
            provider_id=provider_id,
            display_label=display_label,
            sending_permission=sending_permission,
        )

    def list_identities(self) -> Tuple[IdentityRecord, ...]:
        return self.identities.list()

    def create_identity(self, *, identity_id: str, display_name: str, identity_type: str, user_owned: bool = False, sending_permission: bool = False) -> IdentityRecord:
        return self.identities.create(
            identity_id=identity_id,
            display_name=display_name,
            identity_type=identity_type,
            user_owned=user_owned,
            sending_permission=sending_permission,
        )

    def create_contact(self, *, display_name: str, organization: str = "", addresses: Tuple[str, ...] = (), source: str = "") -> ContactRecord:
        return self.contacts.create(display_name=display_name, organization=organization, addresses=addresses, source=source)

    def list_contacts(self) -> Tuple[ContactRecord, ...]:
        return self.contacts.list()

    def search_contacts(self, query: str) -> Tuple[ContactRecord, ...]:
        return self.contacts.search(query)

    # ------------------------------------------------------------------
    # Recipient resolution
    # ------------------------------------------------------------------

    def resolve_recipient(self, entered: str) -> dict:
        resolved, ambiguous, warnings = self.resolver.resolve(entered)
        return {
            "resolved": resolved.model_dump() if resolved else None,
            "ambiguous": ambiguous,
            "warnings": list(warnings),
        }

    # ------------------------------------------------------------------
    # Internal messaging
    # ------------------------------------------------------------------

    def send_internal(
        self,
        *,
        communication_type: str,
        recipients: Sequence[str],
        subject: str,
        body: str,
        origin: Origin,
        conversation_id: str = "",
        priority: str = "normal",
        severity: str = "informational",
    ) -> MessageRecord:
        rich_body = sanitize_html(body)
        message = MessageRecord(
            message_id="msg_" + _uuid16(),
            communication_type=communication_type,
            provider="joeos.internal",
            origin=origin,
            recipients=tuple(recipients),
            conversation_id=conversation_id,
            subject=subject,
            body=body,
            rich_body=rich_body,
            priority=priority,
            severity=severity,
            content_hash=content_hash(subject, body),
            delivery_state="sent",
            sent_at=_now(),
            created_at=_now(),
            external=False,
        )
        return self.messages.save(message)

    def receive_external(
        self,
        *,
        provider: str,
        account: str,
        sender_identity: str,
        sender_display: str,
        recipients: Sequence[str],
        subject: str,
        body: str,
        conversation_id: str = "",
        provider_message_id: str = "",
    ) -> MessageRecord:
        """Store a received external message, sanitized with safety signals."""
        rich_body = sanitize_html(body)
        links = list(remote_content_links(rich_body))
        link_warnings, _ = analyze_links(links)
        indicators = prompt_injection_indicators(subject + "\n" + body)
        verified = self.identities.get(sender_identity)
        phish = phishing_signals(
            sender_display=sender_display,
            sender_address=sender_identity,
            body=body,
            link_count=len(links),
            attachment_count=0,
            sender_verified=bool(verified and verified.verification_state in {"verified", "user_trusted"}),
        )
        message = MessageRecord(
            message_id="msg_" + _uuid16(),
            communication_type="external_email",
            provider=provider,
            provider_message_id=provider_message_id,
            account=account,
            origin=Origin(origin_type="external_provider", label=provider),
            author=sender_identity,
            sender_identity=sender_identity,
            recipients=tuple(recipients),
            conversation_id=conversation_id,
            subject=subject,
            body=body,
            rich_body=rich_body,
            links=tuple(links),
            verification_state=verified.verification_state if verified else "unverified",
            phishing_indicators=tuple(set(phish) | set(indicators)),
            content_hash=content_hash(subject, body),
            delivery_state="sent",
            received_at=_now(),
            created_at=_now(),
            external=True,
        )
        return self.messages.save(message)

    def list_messages(self, *, conversation_id: Optional[str] = None, limit: int = 50, external: Optional[bool] = None) -> Tuple[MessageRecord, ...]:
        return self.messages.list(conversation_id=conversation_id, limit=limit, external=external)

    def search_messages(self, query: str) -> Tuple[MessageRecord, ...]:
        return self.messages.search(query)

    def mark_message_read(self, message_id: str) -> MessageRecord:
        return self.messages.mark_read(message_id)

    def archive_message(self, message_id: str, archived: bool = True) -> MessageRecord:
        return self.messages.set_archive(message_id, archived)

    # ------------------------------------------------------------------
    # Drafts
    # ------------------------------------------------------------------

    def save_draft(self, draft: DraftRecord) -> DraftRecord:
        return self.drafts.save(draft)

    def list_drafts(self) -> Tuple[DraftRecord, ...]:
        return self.drafts.list()

    def get_draft(self, draft_id: str) -> Optional[DraftRecord]:
        return self.drafts.get(draft_id)

    def delete_draft(self, draft_id: str) -> None:
        self.drafts.delete(draft_id)

    # ------------------------------------------------------------------
    # External send + delivery
    # ------------------------------------------------------------------

    def request_external_send(
        self,
        *,
        draft: DraftRecord,
        subject: str,
        body: str,
        recipients: Sequence[str],
        provider: str,
        account: str,
        scheduled: str = "",
        privacy: str = "private",
    ) -> dict:
        resolved = []
        warnings: List[str] = []
        for entry in recipients:
            recipient, ambiguous, item_warnings = self.resolver.resolve(entry)
            if ambiguous:
                raise ExternalSendError("recipient resolution is ambiguous; sending blocked.")
            if recipient is None:
                raise ExternalSendError("unverified recipient; sending blocked.")
            resolved.append(recipient)
            warnings.extend(item_warnings)
        return self.approvals.request(
            draft_id=draft.draft_id,
            subject=subject,
            body=body,
            recipients=tuple(recipient.destination for recipient in resolved),
            sender_identity=draft.proposed_sender,
            provider=provider,
            account=account,
            scheduled=scheduled,
            attachments=draft.attachments,
            privacy=privacy,
        )

    def approve_external_send(self, approval_id: str, *, subject: str, body: str, recipients: Sequence[str]) -> dict:
        return self.approvals.resolve(approval_id, decision="approved", subject=subject, body=body, recipients=recipients)

    def deny_external_send(self, approval_id: str) -> dict:
        return self.approvals.deny(approval_id)

    def pending_external_approvals(self) -> Tuple[dict, ...]:
        return self.approvals.pending()

    def enqueue_and_deliver(self, *, message: MessageRecord, approval_id: str = "", scheduled: str = "") -> OutboxItem:
        return self.delivery.send(message=message, approval_id=approval_id, scheduled=scheduled)

    def list_outbox(self, *, state: Optional[str] = None) -> Tuple[OutboxItem, ...]:
        return self.outbox.list(state=state)

    def retry_delivery(self, outbox_id: str) -> OutboxItem:
        return self.delivery.retry(outbox_id)

    def cancel_delivery(self, outbox_id: str) -> OutboxItem:
        return self.delivery.cancel(outbox_id)

    # ------------------------------------------------------------------
    # Notifications / quiet hours / DND / digests
    # ------------------------------------------------------------------

    def create_notification(self, **kwargs) -> NotificationRecord:
        return self.notifications.create(**kwargs)

    def list_notifications(self, *, category: Optional[str] = None, source: Optional[str] = None, severity: Optional[str] = None, read_state: Optional[str] = None, limit: int = 50) -> Tuple[NotificationRecord, ...]:
        return self.notifications.list(category=category, source=source, severity=severity, read_state=read_state, limit=limit)

    def mark_notification_read(self, notification_id: str) -> NotificationRecord:
        return self.notifications.mark_read(notification_id)

    def acknowledge_notification(self, notification_id: str) -> NotificationRecord:
        return self.notifications.acknowledge(notification_id)

    def archive_notification(self, notification_id: str, archived: bool = True) -> NotificationRecord:
        return self.notifications.set_archive(notification_id, archived)

    def set_quiet_hours(self, quiet_hours: QuietHours) -> None:
        self.notifications.set_quiet_hours(quiet_hours)

    def quiet_hours_config(self) -> QuietHours:
        return self.notifications.quiet_hours_config()

    def set_dnd(self, active: bool) -> None:
        self.notifications.set_dnd(active)

    def dnd_active(self) -> bool:
        return self.notifications.dnd_active()

    def quiet_hours_active(self) -> bool:
        return self.notifications.quiet_hours_active()

    def build_digest(self, *, window_hours: int = 24) -> DigestRecord:
        return self.notifications.build_digest(window_hours=window_hours)

    def unread_notifications(self) -> int:
        return self.notifications.unread_count()

    def add_notification_rule(self, **kwargs):
        return self.notifications.add_rule(**kwargs)

    # ------------------------------------------------------------------
    # Attachments
    # ------------------------------------------------------------------

    def attach_file(self, *, path: str, display_name: str = "", project: str = "", owner: str = "", privacy: str = "private") -> dict:
        return self.attachments.attach(path=path, display_name=display_name, project=project, owner=owner, privacy=privacy)

    def list_attachments(self, *, project: Optional[str] = None) -> Tuple[dict, ...]:
        return self.attachments.list(project=project)

    # ------------------------------------------------------------------
    # Overview / health
    # ------------------------------------------------------------------

    def overview(self) -> CommunicationsOverview:
        accounts = self.accounts.list()
        providers = self.providers.list()
        unhealthy_accounts = sum(1 for a in accounts if a.health not in {"healthy", "unknown"})
        unhealthy_providers = sum(1 for p in providers if p.health_state == "unavailable")
        outbox = self.outbox.list()
        failed = [item for item in outbox if item.state == "failed"]
        security_unacked = sum(
            1
            for n in self.notifications.list(category="security_alert", limit=200)
            if n.read_state != "acknowledged"
        )
        return CommunicationsOverview(
            unread_focused=self.notifications.unread_count(),
            pending_approvals=len(self.approvals.pending()),
            agent_requests=self._count_type("agent_message"),
            outbox_count=len(outbox),
            failed_deliveries=len(failed),
            security_alerts_unacknowledged=security_unacked,
            snoozed=sum(1 for n in self.notifications.list(limit=200) if n.snooze_until),
            unhealthy_accounts=unhealthy_accounts,
            unhealthy_providers=unhealthy_providers,
            quiet_hours_active=self.notifications.quiet_hours_active(),
            next_digest=None,
            generated_at=_now(),
        )

    def _count_type(self, communication_type: str) -> int:
        return len(self.messages.list(communication_type=communication_type, limit=200))

    def storage_stats(self) -> dict:
        return {"path": self.storage.path(), "size_bytes": self.storage.size_bytes(), "version": 1}

    def backup(self) -> Optional[str]:
        return self.storage.backup_to(str(self._data_dir))


def _caps(send=False, receive=False, attachments=False, delivery_receipts=False, sync=False):
    from .models import ProviderCapabilities
    return ProviderCapabilities(
        send=send, receive=receive, attachments=attachments,
        delivery_receipts=delivery_receipts, sync=sync,
    )


def _uuid16() -> str:
    import uuid
    return uuid.uuid4().hex[:16]


class ExternalSendError(RuntimeError):
    pass


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