"""REST API for the JoeOS Security Platform.

Every endpoint reads and mutates real security state: policies, identities,
scopes, approvals, secret metadata, audit events, incidents, lockdown,
emergency stop, quarantine, and circuit breakers. Secret values are never
returned. No fabricated vulnerability counts, malware results, compliance
claims, or security scores are produced.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from server.identity.authority_router import require_application_session

from .models import (
    ApprovalRequestRecord,
    AuditEvent,
    CircuitBreakerState,
    IdentityRecord,
    IncidentRecord,
    LockdownState,
    PolicyDecision,
    PolicyRequestContext,
    ScopeGrant,
    SecretMetadata,
    SecurityEvent,
    SecurityOverview,
    SecurityPolicy,
    ThreatModel,
)
from .policy import SecurityError
from .service import SecurityService

router = APIRouter(prefix="/api/v1/security", tags=["security"])


def get_security_service(request: Request) -> SecurityService:
    service = getattr(request.app.state, "security_service", None)
    if service is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Security service is not initialized.")
    return service


# ---- overview ----

@router.get("/overview", response_model=SecurityOverview)
def overview(service: SecurityService = Depends(get_security_service)) -> SecurityOverview:
    return service.overview()


# ---- policies ----

@router.get("/policies")
def list_policies(service: SecurityService = Depends(get_security_service)) -> dict:
    return {"policies": [policy.model_dump() for policy in service.list_policies()]}


@router.post("/policies", status_code=status.HTTP_201_CREATED)
def upsert_policy(payload: SecurityPolicy, service: SecurityService = Depends(get_security_service)) -> SecurityPolicy:
    return service.upsert_policy(payload)


@router.post("/evaluate")
def evaluate(payload: PolicyRequestContext, service: SecurityService = Depends(get_security_service)) -> PolicyDecision:
    return service.evaluate(payload)


# ---- identities / scopes ----

@router.get("/identities")
def list_identities(service: SecurityService = Depends(get_security_service)) -> dict:
    return {"identities": [record.model_dump() for record in service.list_identities()]}


@router.post("/identities", status_code=status.HTTP_201_CREATED)
def register_identity(payload: IdentityRecord, service: SecurityService = Depends(get_security_service)) -> IdentityRecord:
    return service.register_identity(payload)


@router.post("/identities/{identity_id}/revoke")
def revoke_identity(identity_id: str, service: SecurityService = Depends(get_security_service)) -> dict:
    service.revoke_identity(identity_id)
    return {"revoked": identity_id}


@router.get("/subjects/{subject}/grants")
def subject_grants(subject: str, service: SecurityService = Depends(get_security_service)) -> dict:
    return {"grants": [grant.model_dump() for grant in service.scope_grants_for(subject)]}


@router.post("/grants", status_code=status.HTTP_201_CREATED)
def grant_scope(payload: ScopeGrant, service: SecurityService = Depends(get_security_service)) -> ScopeGrant:
    return service.grant_scope(payload)


@router.post("/grants/{grant_id}/revoke")
def revoke_scope(grant_id: str, service: SecurityService = Depends(get_security_service)) -> dict:
    service.revoke_scope(grant_id)
    return {"revoked": grant_id}


# ---- approvals ----

@router.post("/approvals", status_code=status.HTTP_201_CREATED)
def request_approval(payload: ApprovalRequest, service: SecurityService = Depends(get_security_service)) -> ApprovalRequestRecord:
    return service.request_approval(**payload.model_dump())


@router.post("/approvals/{approval_id}/approve")
def approve(approval_id: str, payload: ApprovalDecision,
                principal: Dict = Depends(require_application_session),
                service: SecurityService = Depends(get_security_service)) -> ApprovalRequestRecord:
    try:
        return service.approve(
            approval_id=approval_id,
            approver_identity=payload.approver_identity,
            confirmation_strength=payload.confirmation_strength,
            session=payload.session,
            device=payload.device,
        )
    except SecurityError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/approvals/{approval_id}/deny")
def deny_approval(approval_id: str, payload: ApprovalDecision,
                principal: Dict = Depends(require_application_session),
                service: SecurityService = Depends(get_security_service)) -> ApprovalRequestRecord:
    return service.deny_approval(approval_id=approval_id, approver_identity=payload.approver_identity)


@router.post("/approvals/{approval_id}/verify-exact")
def verify_approval(approval_id: str, payload: ExactVerify,
                principal: Dict = Depends(require_application_session),
                service: SecurityService = Depends(get_security_service)) -> dict:
    valid = service.verify_approval_exact(
        approval_id=approval_id,
        action_id=payload.action_id,
        target_id=payload.target_id,
        arguments=payload.arguments,
        content=payload.content,
        project=payload.project,
    )
    return {"valid": valid}


@router.get("/approvals")
def approvals(state: Optional[str] = Query(default=None),
                principal: Dict = Depends(require_application_session),
                service: SecurityService = Depends(get_security_service)) -> dict:
    return {"approvals": [record.model_dump() for record in service.approvals_list(state=state)]}


# ---- secrets (metadata only) ----

@router.post("/secrets", status_code=status.HTTP_201_CREATED)
def create_secret(payload: SecretRequest, service: SecurityService = Depends(get_security_service)) -> SecretMetadata:
    return service.create_secret(
        label=payload.label,
        secret_type=payload.secret_type,
        value=payload.value,
        scope=payload.scope,
        project=payload.project,
        plugin=payload.plugin,
        workflow=payload.workflow,
        provider=payload.provider,
        allowed_operations=tuple(payload.allowed_operations),
        allowed_destinations=tuple(payload.allowed_destinations),
    )


@router.get("/secrets")
def list_secrets(service: SecurityService = Depends(get_security_service)) -> dict:
    return {"secrets": [meta.model_dump() for meta in service.list_secrets()]}


@router.post("/secrets/{secret_id}/rotate")
def rotate_secret(secret_id: str, payload: RotateRequest, service: SecurityService = Depends(get_security_service)) -> SecretMetadata:
    return service.rotate_secret(secret_id=secret_id, new_value=payload.value)


@router.post("/secrets/{secret_id}/revoke")
def revoke_secret(secret_id: str, service: SecurityService = Depends(get_security_service)) -> SecretMetadata:
    return service.revoke_secret(secret_id=secret_id)


@router.post("/secrets/scan")
def scan_secret_text(payload: ScanRequest, service: SecurityService = Depends(get_security_service)) -> dict:
    detections = service.scan_secret_text(text=payload.text, source=payload.source)
    return {"detections": [d.model_dump() for d in detections]}


@router.get("/secret-detections")
def secret_detections(service: SecurityService = Depends(get_security_service)) -> dict:
    return {"detections": [d.model_dump() for d in service.secret_detections()]}


# ---- audit / events / incidents ----

@router.get("/audit")
def audit(
    actor: Optional[str] = Query(default=None),
    action: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    service: SecurityService = Depends(get_security_service),
) -> dict:
    return {"audit": [event.model_dump() for event in service.audit_list(actor=actor, action=action, limit=limit)]}


@router.post("/audit/verify")
def audit_verify(service: SecurityService = Depends(get_security_service)) -> dict:
    valid, count = service.audit_verify_integrity()
    return {"valid": valid, "events": count}


@router.get("/security-events")
def security_events(
    status: str = Query(default="open"),
    category: Optional[str] = Query(default=None),
    service: SecurityService = Depends(get_security_service),
) -> dict:
    return {"events": [event.model_dump() for event in service.security_events(status=status, category=category)]}


@router.get("/incidents")
def incidents(
    status: Optional[str] = Query(default=None),
    service: SecurityService = Depends(get_security_service),
) -> dict:
    return {"incidents": [record.model_dump() for record in service.incidents_list(status=status)]}


@router.post("/incidents", status_code=status.HTTP_201_CREATED)
def create_incident(payload: dict, service: SecurityService = Depends(get_security_service)) -> IncidentRecord:
    return service.create_incident(**payload)


@router.post("/incidents/{incident_id}/status")
def update_incident(incident_id: str, payload: IncidentStatus, service: SecurityService = Depends(get_security_service)) -> IncidentRecord:
    return service.incident_update(incident_id, payload.status)


# ---- governance ----

@router.post("/lockdown/activate")
def activate_lockdown(payload: LockdownActivate, service: SecurityService = Depends(get_security_service)) -> LockdownState:
    return service.activate_lockdown(activated_by=payload.activated_by, reason=payload.reason)


@router.post("/lockdown/deactivate")
def deactivate_lockdown(payload: LockdownDeactivate, service: SecurityService = Depends(get_security_service)) -> LockdownState:
    try:
        return service.deactivate_lockdown(reauthenticated=payload.reauthenticated)
    except SecurityError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/lockdown")
def lockdown(service: SecurityService = Depends(get_security_service)) -> LockdownState:
    return service.lockdown_state()


@router.post("/emergency-stop")
def emergency_stop(service: SecurityService = Depends(get_security_service)) -> dict:
    return service.emergency_stop()


@router.post("/emergency-stop/release")
def release_emergency_stop(service: SecurityService = Depends(get_security_service)) -> dict:
    service.release_emergency_stop()
    return {"released": True}


@router.post("/quarantine")
def quarantine(payload: QuarantineRequest, service: SecurityService = Depends(get_security_service)) -> dict:
    return service.quarantine(kind=payload.kind, subject=payload.subject, reason=payload.reason)


# ---- circuit breakers ----

@router.post("/breakers/{target}/failure")
def breaker_failure(target: str, service: SecurityService = Depends(get_security_service)) -> CircuitBreakerState:
    return service.breaker_failure(target=target)


@router.post("/breakers/{target}/success")
def breaker_success(target: str, service: SecurityService = Depends(get_security_service)) -> CircuitBreakerState:
    return service.breaker_success(target=target)


@router.get("/breakers")
def breakers(service: SecurityService = Depends(get_security_service)) -> dict:
    return {"breakers": [breaker.model_dump() for breaker in service.breakers()]}


# ---- classification / privacy / threat models ----

@router.post("/classify")
def classify_data(payload: ClassifyRequest, service: SecurityService = Depends(get_security_service)) -> dict:
    data_class = service.classify_data(
        source=payload.source, user_label=payload.user_label, path=payload.path,
        content_hint=payload.content_hint, proposed_by_model=payload.proposed_by_model,
    )
    return {"data_class": data_class}


@router.post("/privacy/evaluate")
def privacy_evaluate(payload: PrivacyRequest, service: SecurityService = Depends(get_security_service)) -> dict:
    decision, explanation = service.privacy_evaluate(
        data_class=payload.data_class, source=payload.source, destination=payload.destination,
        provider=payload.provider, device=payload.device, consent_active=payload.consent_active,
    )
    return {"decision": decision, "explanation": explanation}


@router.get("/threat-models")
def threat_models(service: SecurityService = Depends(get_security_service)) -> dict:
    return {"threat_models": [model.model_dump() for model in service.threat_models_list()]}


# ---- platform ----

@router.get("/storage")
def storage(service: SecurityService = Depends(get_security_service)) -> dict:
    return service.storage_stats()


@router.post("/backup")
def backup(service: SecurityService = Depends(get_security_service)) -> dict:
    path = service.backup()
    return {"backup_path": path}


# ---- request models ----

from pydantic import BaseModel, Field  # noqa: E402


class ApprovalRequest(BaseModel):
    requester_identity: str = Field(min_length=1, max_length=80)
    action_id: str = Field(min_length=1, max_length=120)
    target_id: str = Field(default="", max_length=120)
    target_type: str = Field(default="", max_length=40)
    arguments: dict = Field(default_factory=dict)
    content: List[str] = Field(default_factory=list)
    attachment_hashes: List[str] = Field(default_factory=list)
    workflow_version: str = Field(default="", max_length=40)
    plugin_version: str = Field(default="", max_length=40)
    project: str = Field(default="", max_length=120)
    task: str = Field(default="", max_length=80)
    mission: str = Field(default="", max_length=80)
    risk: str = Field(default="low", max_length=20)
    ttl_hours: int = Field(default=24, ge=1, le=168)


class ApprovalDecision(BaseModel):
    approver_identity: str = Field(default="user", max_length=80)
    confirmation_strength: str = Field(default="level1", max_length=20)
    session: str = Field(default="", max_length=80)
    device: str = Field(default="", max_length=80)


class ExactVerify(BaseModel):
    action_id: str = Field(min_length=1, max_length=120)
    target_id: str = Field(default="", max_length=120)
    arguments: dict = Field(default_factory=dict)
    content: List[str] = Field(default_factory=list)
    project: str = Field(default="", max_length=120)


class SecretRequest(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    secret_type: str = Field(default="api_key", max_length=40)
    value: str = Field(min_length=1, max_length=4000)
    scope: str = Field(default="global", max_length=40)
    project: str = Field(default="", max_length=120)
    plugin: str = Field(default="", max_length=80)
    workflow: str = Field(default="", max_length=80)
    provider: str = Field(default="", max_length=80)
    allowed_operations: List[str] = Field(default_factory=list)
    allowed_destinations: List[str] = Field(default_factory=list)


class RotateRequest(BaseModel):
    value: str = Field(min_length=1, max_length=4000)


class ScanRequest(BaseModel):
    text: str = Field(min_length=1, max_length=100000)
    source: str = Field(default="", max_length=80)


class IncidentStatus(BaseModel):
    status: str = Field(min_length=1, max_length=40)


class LockdownActivate(BaseModel):
    activated_by: str = Field(default="user", max_length=80)
    reason: str = Field(default="", max_length=300)


class LockdownDeactivate(BaseModel):
    reauthenticated: bool = False


class QuarantineRequest(BaseModel):
    kind: str = Field(min_length=1, max_length=40)
    subject: str = Field(min_length=1, max_length=120)
    reason: str = Field(default="", max_length=300)


class ClassifyRequest(BaseModel):
    source: str = Field(default="", max_length=80)
    user_label: str = Field(default="", max_length=40)
    path: str = Field(default="", max_length=500)
    content_hint: str = Field(default="", max_length=2000)
    proposed_by_model: str = Field(default="", max_length=40)


class PrivacyRequest(BaseModel):
    data_class: str = Field(default="unknown", max_length=40)
    source: str = Field(default="", max_length=80)
    destination: str = Field(default="local", max_length=40)
    provider: str = Field(default="", max_length=80)
    device: str = Field(default="", max_length=80)
    consent_active: bool = False