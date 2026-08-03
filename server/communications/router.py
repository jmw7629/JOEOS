"""REST API for the JoeOS Communications, Inbox, and Notification Hub.

Every endpoint reads and mutates real communications state. No fake accounts,
providers, contacts, messages, unread counts, or delivery receipts are ever
returned. Provider credentials and message secrets are never exposed.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from .models import (
    CommunicationsOverview,
    ContactRecord,
    DraftRecord,
    IdentityRecord,
    MessageRecord,
    NotificationRecord,
    OutboxItem,
    ProviderRecord,
    QuietHours,
    AccountRecord,
    Origin,
)
from .service import CommunicationsService, ExternalSendError

router = APIRouter(prefix="/api/v1/communications", tags=["communications"])


def get_communications_service(request: Request) -> CommunicationsService:
    service = getattr(request.app.state, "communications_service", None)
    if service is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Communications service is not initialized.")
    return service


# ---- overview ----

@router.get("/overview", response_model=CommunicationsOverview)
def overview(service: CommunicationsService = Depends(get_communications_service)) -> CommunicationsOverview:
    return service.overview()


# ---- providers / accounts / identities / contacts ----

@router.get("/providers")
def providers(service: CommunicationsService = Depends(get_communications_service)) -> dict:
    return {"providers": [record.model_dump() for record in service.list_providers()]}


@router.post("/providers")
def register_provider(
    payload: ProviderRequest,
    service: CommunicationsService = Depends(get_communications_service),
) -> ProviderRecord:
    return service.providers.register(
        provider_id=payload.provider_id,
        display_name=payload.display_name,
        provider_type=payload.provider_type,
        authentication=payload.authentication,
        plugin_source=payload.plugin_source,
        is_isolated_test=payload.is_isolated_test,
    )


@router.get("/accounts")
def accounts(service: CommunicationsService = Depends(get_communications_service)) -> dict:
    return {"accounts": [record.model_dump() for record in service.list_accounts()]}


@router.post("/accounts", status_code=status.HTTP_201_CREATED)
def register_account(payload: AccountRequest, service: CommunicationsService = Depends(get_communications_service)) -> AccountRecord:
    return service.register_account(
        provider_id=payload.provider_id,
        display_label=payload.display_label,
        sending_permission=payload.sending_permission,
    )


@router.get("/identities")
def identities(service: CommunicationsService = Depends(get_communications_service)) -> dict:
    return {"identities": [record.model_dump() for record in service.list_identities()]}


@router.post("/identities", status_code=status.HTTP_201_CREATED)
def create_identity(payload: IdentityRequest, service: CommunicationsService = Depends(get_communications_service)) -> IdentityRecord:
    return service.create_identity(
        identity_id=payload.identity_id,
        display_name=payload.display_name,
        identity_type=payload.identity_type,
        user_owned=payload.user_owned,
        sending_permission=payload.sending_permission,
    )


@router.get("/contacts")
def contacts(
    query: Optional[str] = Query(default=None),
    service: CommunicationsService = Depends(get_communications_service),
) -> dict:
    if query:
        return {"contacts": [record.model_dump() for record in service.search_contacts(query)]}
    return {"contacts": [record.model_dump() for record in service.list_contacts()]}


@router.post("/contacts", status_code=status.HTTP_201_CREATED)
def create_contact(payload: ContactRequest, service: CommunicationsService = Depends(get_communications_service)) -> ContactRecord:
    return service.create_contact(
        display_name=payload.display_name,
        organization=payload.organization,
        addresses=tuple(payload.addresses),
        source="user",
    )


@router.post("/recipients/resolve")
def resolve_recipient(payload: RecipientRequest, service: CommunicationsService = Depends(get_communications_service)) -> dict:
    return service.resolve_recipient(payload.recipient)


# ---- messages ----

@router.post("/messages/internal", status_code=status.HTTP_201_CREATED, response_model=MessageRecord)
def send_internal(payload: InternalMessageRequest, service: CommunicationsService = Depends(get_communications_service)) -> MessageRecord:
    return service.send_internal(
        communication_type=payload.communication_type,
        recipients=tuple(payload.recipients),
        subject=payload.subject,
        body=payload.body,
        origin=Origin(origin_type="user", label="You"),
        conversation_id=payload.conversation_id,
        priority=payload.priority,
    )


@router.get("/messages")
def messages(
    conversation_id: Optional[str] = Query(default=None),
    external: Optional[bool] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    service: CommunicationsService = Depends(get_communications_service),
) -> dict:
    return {"messages": [record.model_dump() for record in service.list_messages(conversation_id=conversation_id, limit=limit, external=external)]}


@router.get("/messages/search")
def search_messages(
    query: str = Query(min_length=1),
    limit: int = Query(default=50, ge=1, le=200),
    service: CommunicationsService = Depends(get_communications_service),
) -> dict:
    return {"messages": [record.model_dump() for record in service.search_messages(query)]}


@router.post("/messages/{message_id}/read")
def mark_read(message_id: str, service: CommunicationsService = Depends(get_communications_service)) -> MessageRecord:
    return service.mark_message_read(message_id)


@router.post("/messages/{message_id}/archive")
def archive_message(message_id: str, payload: ArchiveRequest, service: CommunicationsService = Depends(get_communications_service)) -> MessageRecord:
    return service.archive_message(message_id, archived=payload.archived)


# ---- drafts ----

@router.post("/drafts", status_code=status.HTTP_201_CREATED, response_model=DraftRecord)
def save_draft(payload: DraftRequest, service: CommunicationsService = Depends(get_communications_service)) -> DraftRecord:
    draft = DraftRecord(
        draft_id=payload.draft_id or ("draft_" + _hex()),
        author=payload.author or "user",
        proposed_sender=payload.proposed_sender or "identity.user",
        recipients=tuple(payload.recipients),
        provider=payload.provider,
        account=payload.account,
        subject=payload.subject,
        body=payload.body,
        privacy=payload.privacy,
        source_agent=payload.source_agent,
        source_workflow=payload.source_workflow,
        approval_required=payload.approval_required,
        scheduled_send=payload.scheduled_send,
    )
    return service.save_draft(draft)


@router.get("/drafts")
def drafts(service: CommunicationsService = Depends(get_communications_service)) -> dict:
    return {"drafts": [record.model_dump() for record in service.list_drafts()]}


@router.delete("/drafts/{draft_id}")
def delete_draft(draft_id: str, service: CommunicationsService = Depends(get_communications_service)) -> dict:
    service.delete_draft(draft_id)
    return {"deleted": draft_id}


# ---- external send ----

@router.post("/drafts/{draft_id}/request-send")
def request_external_send(draft_id: str, payload: RequestSend, service: CommunicationsService = Depends(get_communications_service)) -> dict:
    draft = service.get_draft(draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found.")
    try:
        return service.request_external_send(
            draft=draft,
            subject=payload.subject,
            body=payload.body,
            recipients=tuple(payload.recipients),
            provider=payload.provider,
            account=payload.account,
            scheduled=payload.scheduled,
            privacy=payload.privacy,
        )
    except ExternalSendError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/external-approvals")
def external_approvals(service: CommunicationsService = Depends(get_communications_service)) -> dict:
    return {"approvals": list(service.pending_external_approvals())}


@router.post("/external-approvals/{approval_id}/approve")
def approve_external(approval_id: str, payload: ApprovalDecision, service: CommunicationsService = Depends(get_communications_service)) -> dict:
    try:
        return service.approve_external_send(approval_id, subject=payload.subject, body=payload.body, recipients=tuple(payload.recipients))
    except ExternalSendError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/external-approvals/{approval_id}/deny")
def deny_external(approval_id: str, service: CommunicationsService = Depends(get_communications_service)) -> dict:
    return service.deny_external_send(approval_id)


# ---- outbox ----

@router.get("/outbox")
def outbox(
    state: Optional[str] = Query(default=None),
    service: CommunicationsService = Depends(get_communications_service),
) -> dict:
    return {"items": [item.model_dump() for item in service.list_outbox(state=state)]}


@router.post("/outbox/{outbox_id}/retry")
def retry_delivery(outbox_id: str, service: CommunicationsService = Depends(get_communications_service)) -> OutboxItem:
    return service.retry_delivery(outbox_id)


@router.post("/outbox/{outbox_id}/cancel")
def cancel_delivery(outbox_id: str, service: CommunicationsService = Depends(get_communications_service)) -> OutboxItem:
    return service.cancel_delivery(outbox_id)


# ---- notifications ----

@router.post("/notifications", status_code=status.HTTP_201_CREATED, response_model=NotificationRecord)
def create_notification(payload: NotificationRequest, service: CommunicationsService = Depends(get_communications_service)) -> NotificationRecord:
    return service.create_notification(
        source=payload.source,
        source_type=payload.source_type,
        category=payload.category,
        title=payload.title,
        message=payload.message,
        severity=payload.severity,
        priority=payload.priority,
        urgency=payload.urgency,
        project=payload.project,
        mission=payload.mission,
        workflow=payload.workflow,
        plugin=payload.plugin,
        deduplication_key=payload.deduplication_key,
    )


@router.get("/notifications")
def notifications(
    category: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    severity: Optional[str] = Query(default=None),
    read_state: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    service: CommunicationsService = Depends(get_communications_service),
) -> dict:
    return {"notifications": [record.model_dump() for record in service.list_notifications(category=category, source=source, severity=severity, read_state=read_state, limit=limit)]}


@router.post("/notifications/{notification_id}/read")
def mark_notification_read(notification_id: str, service: CommunicationsService = Depends(get_communications_service)) -> NotificationRecord:
    return service.mark_notification_read(notification_id)


@router.post("/notifications/{notification_id}/acknowledge")
def acknowledge_notification(notification_id: str, service: CommunicationsService = Depends(get_communications_service)) -> NotificationRecord:
    return service.acknowledge_notification(notification_id)


@router.get("/notifications/unread-count")
def unread_count(service: CommunicationsService = Depends(get_communications_service)) -> dict:
    return {"unread": service.unread_notifications()}


# ---- quiet hours / DND / digests ----

@router.put("/quiet-hours")
def set_quiet_hours(payload: QuietHours, service: CommunicationsService = Depends(get_communications_service)) -> QuietHours:
    service.set_quiet_hours(payload)
    return service.quiet_hours_config()


@router.get("/quiet-hours")
def quiet_hours(service: CommunicationsService = Depends(get_communications_service)) -> dict:
    return {"config": service.quiet_hours_config().model_dump(), "active": service.quiet_hours_active()}


@router.post("/dnd")
def set_dnd(payload: DndRequest, service: CommunicationsService = Depends(get_communications_service)) -> dict:
    service.set_dnd(payload.active)
    return {"dnd_active": service.dnd_active()}


@router.post("/digests")
def build_digest(payload: DigestRequest, service: CommunicationsService = Depends(get_communications_service)) -> dict:
    digest = service.build_digest(window_hours=payload.window_hours)
    return digest.model_dump()


# ---- attachments ----

@router.post("/attachments", status_code=status.HTTP_201_CREATED)
def attach_file(payload: AttachmentRequest, service: CommunicationsService = Depends(get_communications_service)) -> dict:
    try:
        return service.attach_file(
            path=payload.path,
            display_name=payload.display_name,
            project=payload.project,
            owner=payload.owner,
            privacy=payload.privacy,
        )
    except ExternalSendError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/attachments")
def attachments(project: Optional[str] = Query(default=None), service: CommunicationsService = Depends(get_communications_service)) -> dict:
    return {"attachments": list(service.list_attachments(project=project))}


# ---- platform ----

@router.get("/storage")
def storage(service: CommunicationsService = Depends(get_communications_service)) -> dict:
    return service.storage_stats()


@router.post("/backup")
def backup(service: CommunicationsService = Depends(get_communications_service)) -> dict:
    path = service.backup()
    return {"backup_path": path}


# ---- request models ----

from pydantic import BaseModel, Field  # noqa: E402


def _hex() -> str:
    import uuid
    return uuid.uuid4().hex[:16]


class ProviderRequest(BaseModel):
    provider_id: str = Field(min_length=1, max_length=80)
    display_name: str = Field(min_length=1, max_length=120)
    provider_type: str = Field(default="generic", max_length=60)
    authentication: str = Field(default="none", max_length=40)
    plugin_source: str = Field(default="", max_length=120)
    is_isolated_test: bool = False


class AccountRequest(BaseModel):
    provider_id: str = Field(min_length=1, max_length=80)
    display_label: str = Field(min_length=1, max_length=120)
    sending_permission: bool = False


class IdentityRequest(BaseModel):
    identity_id: str = Field(min_length=1, max_length=80)
    display_name: str = Field(min_length=1, max_length=120)
    identity_type: str = Field(default="local_user", max_length=40)
    user_owned: bool = False
    sending_permission: bool = False


class ContactRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    organization: str = Field(default="", max_length=120)
    addresses: List[str] = Field(default_factory=list, max_length=8)


class RecipientRequest(BaseModel):
    recipient: str = Field(min_length=1, max_length=240)


class InternalMessageRequest(BaseModel):
    communication_type: str = Field(default="internal_direct_message", max_length=60)
    recipients: List[str] = Field(min_length=1, max_length=16)
    subject: str = Field(default="", max_length=240)
    body: str = Field(min_length=1, max_length=100000)
    conversation_id: str = Field(default="", max_length=80)
    priority: str = Field(default="normal", max_length=20)


class ArchiveRequest(BaseModel):
    archived: bool = True


class DraftRequest(BaseModel):
    draft_id: str = Field(default="", max_length=80)
    author: str = Field(default="", max_length=80)
    proposed_sender: str = Field(default="", max_length=80)
    recipients: List[str] = Field(default_factory=list)
    provider: str = Field(default="", max_length=80)
    account: str = Field(default="", max_length=80)
    subject: str = Field(default="", max_length=240)
    body: str = Field(default="", max_length=100000)
    privacy: str = Field(default="private", max_length=40)
    source_agent: str = Field(default="", max_length=80)
    source_workflow: str = Field(default="", max_length=80)
    approval_required: bool = False
    scheduled_send: str = Field(default="", max_length=60)


class RequestSend(BaseModel):
    subject: str = Field(default="", max_length=240)
    body: str = Field(min_length=1, max_length=100000)
    recipients: List[str] = Field(min_length=1, max_length=16)
    provider: str = Field(default="test.isolated", max_length=80)
    account: str = Field(default="", max_length=80)
    scheduled: str = Field(default="", max_length=60)
    privacy: str = Field(default="private", max_length=40)


class ApprovalDecision(BaseModel):
    subject: str = Field(default="", max_length=240)
    body: str = Field(default="", max_length=100000)
    recipients: List[str] = Field(default_factory=list)


class NotificationRequest(BaseModel):
    source: str = Field(min_length=1, max_length=80)
    source_type: str = Field(default="", max_length=60)
    category: str = Field(default="", max_length=60)
    title: str = Field(min_length=1, max_length=240)
    message: str = Field(default="", max_length=500)
    severity: str = Field(default="informational", max_length=30)
    priority: str = Field(default="normal", max_length=20)
    urgency: str = Field(default="routine", max_length=20)
    project: str = Field(default="", max_length=120)
    mission: str = Field(default="", max_length=80)
    workflow: str = Field(default="", max_length=80)
    plugin: str = Field(default="", max_length=80)
    deduplication_key: str = Field(default="", max_length=120)


class DndRequest(BaseModel):
    active: bool = True


class DigestRequest(BaseModel):
    window_hours: int = Field(default=24, ge=1, le=168)


class AttachmentRequest(BaseModel):
    path: str = Field(min_length=1, max_length=2000)
    display_name: str = Field(default="", max_length=240)
    project: str = Field(default="", max_length=120)
    owner: str = Field(default="user", max_length=80)
    privacy: str = Field(default="private", max_length=40)