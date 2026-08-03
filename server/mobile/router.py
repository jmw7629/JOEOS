"""REST API for the JoeOS Mobile Companion and Secure Remote Operations
Platform.

Every endpoint reads and mutates real mobile-client, host, session, pairing,
permission, offline, and handoff state. No fake hosts, connections, push
deliveries, or sessions are returned. Credentials, private keys, and push
tokens are never exposed. The mobile client remains a client of authoritative
JoeOS services.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

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
)
from .clients import MobileError
from .service import MobileService

router = APIRouter(prefix="/api/v1/mobile", tags=["mobile"])


def get_mobile_service(request: Request) -> MobileService:
    service = getattr(request.app.state, "mobile_service", None)
    if service is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Mobile service is not initialized.")
    return service


def _require_client(service: MobileService, client_id: str) -> MobileClientRecord:
    client = service.get_client(client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Mobile client not found.")
    return client


# ---- overview ----

@router.get("/overview", response_model=MobileOverview)
def overview(service: MobileService = Depends(get_mobile_service)) -> MobileOverview:
    return service.overview()


# ---- hosts / discovery ----

@router.get("/hosts")
def hosts(service: MobileService = Depends(get_mobile_service)) -> dict:
    return {"hosts": [record.model_dump() for record in service.list_hosts()]}


@router.post("/discovery")
def discover(payload: DiscoveryRequest, service: MobileService = Depends(get_mobile_service)) -> dict:
    return {"hosts": [result.model_dump() for result in service.discover_hosts(payload.entries or [])]}


# ---- clients ----

@router.get("/clients")
def clients(service: MobileService = Depends(get_mobile_service)) -> dict:
    return {"clients": [record.model_dump() for record in service.list_clients()]}


@router.post("/clients", status_code=status.HTTP_201_CREATED)
def register_client(payload: ClientRequest, service: MobileService = Depends(get_mobile_service)) -> MobileClientRecord:
    return service.register_client(
        client_id=payload.client_id,
        platform=payload.platform,
        app_version=payload.app_version,
        installation_identity=payload.installation_identity,
        crypto_identity_reference=payload.crypto_identity_reference,
    )


@router.get("/clients/{client_id}", response_model=MobileClientRecord)
def get_client(client_id: str, service: MobileService = Depends(get_mobile_service)) -> MobileClientRecord:
    return _require_client(service, client_id)


@router.get("/clients/{client_id}/permissions")
def client_permissions(client_id: str, service: MobileService = Depends(get_mobile_service)) -> dict:
    _require_client(service, client_id)
    return {"grants": list(service.client_permissions(client_id))}


@router.post("/clients/{client_id}/permissions/grant")
def grant_permission(
    client_id: str,
    payload: PermissionRequest,
    service: MobileService = Depends(get_mobile_service),
) -> dict:
    _require_client(service, client_id)
    service.grant_permission(client_id=client_id, permission=payload.permission, scope=payload.scope, scope_target=payload.scope_target)
    return {"granted": payload.permission}


@router.post("/clients/{client_id}/permissions/revoke")
def revoke_permission(
    client_id: str,
    payload: PermissionRequest,
    service: MobileService = Depends(get_mobile_service),
) -> dict:
    _require_client(service, client_id)
    service.revoke_permission(client_id=client_id, permission=payload.permission, scope_target=payload.scope_target)
    return {"revoked": payload.permission}


# ---- pairing ----

@router.post("/pairing", status_code=status.HTTP_201_CREATED)
def begin_pairing(payload: PairingRequest, service: MobileService = Depends(get_mobile_service)) -> PairingSession:
    return service.begin_pairing(
        host_id=payload.host_id,
        requested_permissions=tuple(payload.requested_permissions),
        requested_projects=tuple(payload.requested_projects),
    )


@router.post("/pairing/{session_id}/host-confirm")
def confirm_pairing_host(session_id: str, service: MobileService = Depends(get_mobile_service)) -> PairingSession:
    try:
        return service.confirm_pairing_host(session_id=session_id)
    except MobileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/pairing/{session_id}/client-confirm")
def confirm_pairing_client(
    session_id: str,
    payload: PairingConfirmRequest,
    service: MobileService = Depends(get_mobile_service),
) -> MobileClientRecord:
    try:
        return service.confirm_pairing_client(session_id=session_id, client_id=payload.client_id, code=payload.code)
    except MobileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/pairing/{session_id}/cancel")
def cancel_pairing(session_id: str, service: MobileService = Depends(get_mobile_service)) -> dict:
    service.cancel_pairing(session_id=session_id)
    return {"cancelled": session_id}


@router.get("/pairing/pending")
def pending_pairings(service: MobileService = Depends(get_mobile_service)) -> dict:
    return {"pairings": list(service.pending_pairings())}


# ---- auth / sessions ----

@router.post("/clients/{client_id}/refresh")
def issue_refresh(client_id: str, service: MobileService = Depends(get_mobile_service)) -> dict:
    _require_client(service, client_id)
    token = service.issue_refresh(client_id=client_id)
    return {"refresh_token": token, "note": "Returned once; stored hashed server-side."}


@router.post("/auth")
def authenticate(payload: AuthRequest, service: MobileService = Depends(get_mobile_service)) -> MobileSession:
    try:
        return service.authenticate(
            client_id=payload.client_id,
            host_id=payload.host_id,
            refresh_token=payload.refresh_token,
            capabilities=tuple(payload.capabilities),
            projects=tuple(payload.projects),
        )
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.get("/sessions")
def active_sessions(service: MobileService = Depends(get_mobile_service)) -> dict:
    return {"sessions": [record.model_dump() for record in service.active_sessions()]}


@router.post("/sessions/{session_id}/renew")
def renew_session(session_id: str, payload: SessionOwner, service: MobileService = Depends(get_mobile_service)) -> MobileSession:
    return service.renew_session(session_id=session_id, client_id=payload.client_id)


@router.post("/sessions/{session_id}/revoke")
def revoke_session(session_id: str, payload: RevokeRequest, service: MobileService = Depends(get_mobile_service)) -> dict:
    service.revoke_session(session_id, reason=payload.reason)
    return {"revoked": session_id}


# ---- remote commands / scoped queries ----

@router.get("/commands")
def allowed_commands(service: MobileService = Depends(get_mobile_service)) -> dict:
    return {"commands": list(service.allowed_commands())}


@router.post("/commands/execute")
def execute_command(payload: CommandRequest, service: MobileService = Depends(get_mobile_service)) -> dict:
    try:
        return service.execute_command(
            client_id=payload.client_id,
            session_id=payload.session_id,
            command=payload.command,
            params=payload.params,
            project=payload.project,
        )
    except MobileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/query/{resource}")
def scoped_query(
    resource: str,
    payload: QueryRequest,
    service: MobileService = Depends(get_mobile_service),
) -> dict:
    try:
        return service.scoped_query(
            client_id=payload.client_id,
            session_id=payload.session_id,
            resource=resource,
            scope=payload.scope,
        )
    except MobileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ---- offline ----

@router.post("/clients/{client_id}/offline", status_code=status.HTTP_201_CREATED)
def enqueue_offline(client_id: str, payload: OfflineRequest, service: MobileService = Depends(get_mobile_service)) -> OfflineAction:
    try:
        return service.enqueue_offline(
            client_id=client_id,
            host_id=payload.host_id,
            session_id=payload.session_id,
            action=payload.action,
            target=payload.target,
            base_version=payload.base_version,
            arguments=payload.arguments,
            project=payload.project,
        )
    except MobileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/clients/{client_id}/offline")
def offline_actions(client_id: str, service: MobileService = Depends(get_mobile_service)) -> dict:
    return {"operations": [op.model_dump() for op in service.offline_actions(client_id)]}


@router.post("/clients/{client_id}/offline/revalidate")
def revalidate_offline(
    client_id: str,
    payload: RevalidateRequest,
    service: MobileService = Depends(get_mobile_service),
) -> dict:
    def _target_state(target: str, base_version: str) -> Optional[str]:
        versions = payload.target_versions or {}
        return versions.get(target)

    return service.revalidate_offline(
        client_id=client_id,
        session_id=payload.session_id,
        target_state=_target_state,
    )


# ---- handoff / deep links ----

@router.post("/handoffs", status_code=status.HTTP_201_CREATED)
def create_handoff(payload: HandoffRequest, service: MobileService = Depends(get_mobile_service)) -> HandoffRecord:
    return service.create_handoff(
        source_surface=payload.source_surface,
        destination_surface=payload.destination_surface,
        item_type=payload.item_type,
        item_id=payload.item_id,
        content_position=payload.content_position,
        pending_action=payload.pending_action,
    )


@router.post("/handoffs/{handoff_id}/resolve")
def resolve_handoff(handoff_id: str, payload: HandoffResolveRequest, service: MobileService = Depends(get_mobile_service)) -> HandoffRecord:
    return service.resolve_handoff(handoff_id=handoff_id, accepted=payload.accepted, destination_trusted=payload.destination_trusted)


@router.get("/handoffs")
def list_handoffs(service: MobileService = Depends(get_mobile_service)) -> dict:
    return {"handoffs": [record.model_dump() for record in service.list_handoffs()]}


@router.post("/deep-links", status_code=status.HTTP_201_CREATED)
def issue_deep_link(payload: DeepLinkRequest, service: MobileService = Depends(get_mobile_service)) -> dict:
    link_id = service.issue_deep_link(
        host_id=payload.host_id,
        target_type=payload.target_type,
        target_id=payload.target_id,
        scope=payload.scope,
    )
    return {"link_id": link_id}


@router.get("/deep-links/{link_id}")
def resolve_deep_link(link_id: str, payload: Optional[DeepLinkResolveRequest] = None, service: MobileService = Depends(get_mobile_service)) -> DeepLinkReference:
    user = payload.user_identity if payload else "user"
    try:
        return service.resolve_deep_link(link_id, user_identity=user)
    except MobileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ---- push ----

@router.post("/push/register", status_code=status.HTTP_201_CREATED)
def register_push(payload: PushRegisterRequest, service: MobileService = Depends(get_mobile_service)) -> PushRegistration:
    return service.register_push(
        client_id=payload.client_id,
        platform=payload.platform,
        provider=payload.provider,
        push_token_reference=payload.push_token_reference,
        environment=payload.environment,
        enabled_categories=tuple(payload.enabled_categories),
    )


@router.post("/clients/{client_id}/push/unregister")
def unregister_push(client_id: str, service: MobileService = Depends(get_mobile_service)) -> dict:
    service.unregister_push(client_id=client_id)
    return {"unregistered": client_id}


@router.post("/push/deliver")
def deliver_notification(payload: DeliverRequest, service: MobileService = Depends(get_mobile_service)) -> NotificationDelivery:
    return service.deliver_notification(
        client_id=payload.client_id,
        category=payload.category,
        title=payload.title,
        body=payload.body,
        target_deep_link=payload.target_deep_link,
        severity=payload.severity,
        is_test_fixture=payload.is_test_fixture,
    )


# ---- revocation / lost device ----

@router.post("/clients/{client_id}/revoke")
def revoke_client(client_id: str, payload: RevokeRequest, service: MobileService = Depends(get_mobile_service)) -> MobileClientRecord:
    _require_client(service, client_id)
    return service.revoke_client(client_id, reason=payload.reason)


@router.post("/clients/{client_id}/lost")
def mark_lost(client_id: str, service: MobileService = Depends(get_mobile_service)) -> MobileClientRecord:
    _require_client(service, client_id)
    return service.mark_lost(client_id)


@router.post("/clients/revoke-all")
def revoke_all(service: MobileService = Depends(get_mobile_service)) -> dict:
    count = service.revoke_all()
    return {"revoked": count}


# ---- platform ----

@router.get("/storage")
def storage(service: MobileService = Depends(get_mobile_service)) -> dict:
    return service.storage_stats()


@router.post("/backup")
def backup(service: MobileService = Depends(get_mobile_service)) -> dict:
    path = service.backup()
    return {"backup_path": path}


# ---- request models ----

from pydantic import BaseModel, Field  # noqa: E402


class DiscoveryRequest(BaseModel):
    entries: List[dict] = Field(default_factory=list)


class ClientRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=80)
    platform: str = Field(default="ios", max_length=30)
    app_version: str = Field(default="", max_length=30)
    installation_identity: str = Field(default="", max_length=120)
    crypto_identity_reference: str = Field(default="", max_length=200)


class PermissionRequest(BaseModel):
    permission: str = Field(min_length=1, max_length=80)
    scope: str = Field(default="session", max_length=30)
    scope_target: str = Field(default="", max_length=120)


class PairingRequest(BaseModel):
    host_id: str = Field(min_length=1, max_length=80)
    requested_permissions: List[str] = Field(default_factory=list)
    requested_projects: List[str] = Field(default_factory=list)


class PairingConfirmRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=80)
    code: str = Field(min_length=1, max_length=40)


class AuthRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=80)
    host_id: str = Field(min_length=1, max_length=80)
    refresh_token: str = Field(min_length=1, max_length=200)
    capabilities: List[str] = Field(default_factory=list)
    projects: List[str] = Field(default_factory=list)


class SessionOwner(BaseModel):
    client_id: str = Field(min_length=1, max_length=80)


class RevokeRequest(BaseModel):
    reason: str = Field(default="", max_length=300)


class CommandRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=80)
    session_id: str = Field(min_length=1, max_length=80)
    command: str = Field(min_length=1, max_length=80)
    params: dict = Field(default_factory=dict)
    project: str = Field(default="", max_length=120)


class QueryRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=80)
    session_id: str = Field(min_length=1, max_length=80)
    scope: dict = Field(default_factory=dict)


class OfflineRequest(BaseModel):
    host_id: str = Field(min_length=1, max_length=80)
    session_id: str = Field(default="", max_length=80)
    action: str = Field(min_length=1, max_length=80)
    target: str = Field(default="", max_length=120)
    base_version: str = Field(default="", max_length=60)
    arguments: dict = Field(default_factory=dict)
    project: str = Field(default="", max_length=120)


class RevalidateRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=80)
    target_versions: dict = Field(default_factory=dict)


class HandoffRequest(BaseModel):
    source_surface: str = Field(min_length=1, max_length=40)
    destination_surface: str = Field(min_length=1, max_length=40)
    item_type: str = Field(default="", max_length=40)
    item_id: str = Field(default="", max_length=80)
    content_position: str = Field(default="", max_length=120)
    pending_action: str = Field(default="", max_length=120)


class HandoffResolveRequest(BaseModel):
    accepted: bool = True
    destination_trusted: bool = True


class DeepLinkRequest(BaseModel):
    host_id: str = Field(min_length=1, max_length=80)
    target_type: str = Field(min_length=1, max_length=40)
    target_id: str = Field(min_length=1, max_length=120)
    scope: str = Field(default="", max_length=120)


class DeepLinkResolveRequest(BaseModel):
    user_identity: str = Field(default="user", max_length=80)


class PushRegisterRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=80)
    platform: str = Field(default="ios", max_length=30)
    provider: str = Field(default="apns", max_length=30)
    push_token_reference: str = Field(default="", max_length=200)
    environment: str = Field(default="sandbox", max_length=30)
    enabled_categories: List[str] = Field(default_factory=list)


class DeliverRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=80)
    category: str = Field(default="", max_length=60)
    title: str = Field(default="", max_length=120)
    body: str = Field(default="", max_length=240)
    target_deep_link: str = Field(default="", max_length=120)
    severity: str = Field(default="informational", max_length=30)
    is_test_fixture: bool = False