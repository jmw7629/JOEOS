"""REST transport for the engineering campaign platform.

Transport-only: policy lives in CampaignService. Every route resolves the
authenticated principal through the shared request dependency and relies on
CampaignService capability enforcement.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status

from server.identity.authority_router import require_application_session

from .models import (
    BlockReason,
    CampaignDefinition,
    CampaignRecord,
    EngineeringAttemptRecord,
    EngineeringBlockerRecord,
    EngineeringCheckpointRecord,
    RoadmapEnvelope,
    RoadmapEntry,
    WatchdogHeartbeatRecord,
    WorkPackageDefinition,
    WorkPackageRecord,
)

VALID_BLOCK_REASONS = ("worktree_conflict", "gate_failed", "watchdog_expired", "operator", "missing_requirement")
from .service import CampaignError, CampaignService
from .roadmap import parse_roadmap_document

router = APIRouter(prefix="/api/v1", tags=["engineering-campaign"])


def get_campaign_service(request: Request) -> CampaignService:
    service = getattr(request.app.state, "campaign_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Engineering campaign service is not initialized.",
        )
    return service


def _translate(exc: CampaignError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.code)


def _coerce_tuple_fields(payload: dict) -> dict:
    """Strict pydantic models require tuples; JSON arrays arrive as lists."""
    data = dict(payload)
    for field in ("stage_order", "dependencies", "acceptance_criteria", "providers",
                  "allowed_agent_keys", "denied_agent_keys", "protected_branches",
                  "allowed_remotes", "checkpoints", "evidence", "warnings"):
        value = data.get(field)
        if value is None:
            continue
        if isinstance(value, list):
            data[field] = tuple(value)
    return data


@router.post("/engineering/campaigns", response_model=CampaignRecord, status_code=status.HTTP_201_CREATED)
def create_campaign(
    payload: dict,
    service: CampaignService = Depends(get_campaign_service),
    principal: dict = Depends(require_application_session),
) -> CampaignRecord:
    try:
        return service.create_campaign(principal, CampaignDefinition(**_coerce_tuple_fields(payload)))
    except (CampaignError, Exception) as exc:
        if isinstance(exc, CampaignError):
            raise _translate(exc) from exc
        raise HTTPException(status_code=422, detail="invalid_campaign_definition") from exc


@router.get("/engineering/campaigns", response_model=List[CampaignRecord])
def list_campaigns(
    service: CampaignService = Depends(get_campaign_service),
    principal: dict = Depends(require_application_session),
) -> List[CampaignRecord]:
    try:
        return list(service.list_campaigns(principal))
    except CampaignError as exc:
        raise _translate(exc) from exc


@router.get("/engineering/campaigns/{campaign_id}", response_model=CampaignRecord)
def get_campaign(
    campaign_id: str,
    service: CampaignService = Depends(get_campaign_service),
    principal: dict = Depends(require_application_session),
) -> CampaignRecord:
    try:
        return service.get_campaign(principal, campaign_id)
    except CampaignError as exc:
        raise _translate(exc) from exc


@router.post("/engineering/campaigns/{campaign_id}/start", response_model=CampaignRecord)
def start_campaign(
    campaign_id: str,
    service: CampaignService = Depends(get_campaign_service),
    principal: dict = Depends(require_application_session),
) -> CampaignRecord:
    try:
        return service.start_campaign(principal, campaign_id)
    except CampaignError as exc:
        raise _translate(exc) from exc


@router.post("/engineering/campaigns/{campaign_id}/pause", response_model=CampaignRecord)
def pause_campaign(
    campaign_id: str,
    service: CampaignService = Depends(get_campaign_service),
    principal: dict = Depends(require_application_session),
) -> CampaignRecord:
    try:
        return service.pause_campaign(principal, campaign_id)
    except CampaignError as exc:
        raise _translate(exc) from exc


@router.post("/engineering/campaigns/{campaign_id}/resume", response_model=CampaignRecord)
def resume_campaign(
    campaign_id: str,
    service: CampaignService = Depends(get_campaign_service),
    principal: dict = Depends(require_application_session),
) -> CampaignRecord:
    try:
        return service.resume_campaign(principal, campaign_id)
    except CampaignError as exc:
        raise _translate(exc) from exc


@router.post("/engineering/campaigns/{campaign_id}/cancel", response_model=CampaignRecord)
def cancel_campaign(
    campaign_id: str,
    service: CampaignService = Depends(get_campaign_service),
    principal: dict = Depends(require_application_session),
) -> CampaignRecord:
    try:
        return service.cancel_campaign(principal, campaign_id)
    except CampaignError as exc:
        raise _translate(exc) from exc


@router.post("/engineering/campaigns/{campaign_id}/heartbeat", response_model=WatchdogHeartbeatRecord)
def campaign_heartbeat(
    campaign_id: str,
    payload: dict,
    service: CampaignService = Depends(get_campaign_service),
    principal: dict = Depends(require_application_session),
) -> WatchdogHeartbeatRecord:
    try:
        return service.heartbeat(principal, campaign_id,
                                 worker=str(payload.get("worker") or "watchdog"),
                                 detail=str(payload.get("detail") or ""))
    except CampaignError as exc:
        raise _translate(exc) from exc


@router.get("/engineering/campaigns/{campaign_id}/watchdog")
def watchdog_state(
    campaign_id: str,
    service: CampaignService = Depends(get_campaign_service),
    principal: dict = Depends(require_application_session),
) -> dict:
    try:
        return service.watchdog_state(principal, campaign_id)
    except CampaignError as exc:
        raise _translate(exc) from exc


@router.post("/engineering/campaigns/{campaign_id}/roadmap/import", response_model=RoadmapEnvelope)
def import_roadmap(
    campaign_id: str,
    payload: dict,
    service: CampaignService = Depends(get_campaign_service),
    principal: dict = Depends(require_application_session),
) -> RoadmapEnvelope:
    document = str(payload.get("yaml") or "")
    if not document.strip():
        raise HTTPException(status_code=422, detail="yaml_required")
    try:
        parsed = parse_roadmap_document(document)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        return service.import_roadmap(principal, campaign_id, list(parsed.entries))
    except CampaignError as exc:
        raise _translate(exc) from exc


@router.get("/engineering/campaigns/{campaign_id}/roadmap", response_model=RoadmapEnvelope)
def get_roadmap(
    campaign_id: str,
    service: CampaignService = Depends(get_campaign_service),
    principal: dict = Depends(require_application_session),
) -> RoadmapEnvelope:
    try:
        return service.roadmap(principal, campaign_id)
    except CampaignError as exc:
        raise _translate(exc) from exc


@router.post("/engineering/campaigns/{campaign_id}/packages", response_model=WorkPackageRecord,
             status_code=status.HTTP_201_CREATED)
def create_work_package(
    campaign_id: str,
    payload: dict,
    service: CampaignService = Depends(get_campaign_service),
    principal: dict = Depends(require_application_session),
) -> WorkPackageRecord:
    try:
        return service.create_work_package(principal, campaign_id, WorkPackageDefinition(**_coerce_tuple_fields(payload)))
    except (CampaignError, Exception) as exc:
        if isinstance(exc, CampaignError):
            raise _translate(exc) from exc
        raise HTTPException(status_code=422, detail="invalid_work_package_definition") from exc


@router.get("/engineering/campaigns/{campaign_id}/packages", response_model=List[WorkPackageRecord])
def list_work_packages(
    campaign_id: str,
    service: CampaignService = Depends(get_campaign_service),
    principal: dict = Depends(require_application_session),
) -> List[WorkPackageRecord]:
    try:
        return list(service.list_work_packages(principal, campaign_id))
    except CampaignError as exc:
        raise _translate(exc) from exc


@router.get("/engineering/packages/{package_id}", response_model=WorkPackageRecord)
def get_work_package(
    package_id: str,
    service: CampaignService = Depends(get_campaign_service),
    principal: dict = Depends(require_application_session),
) -> WorkPackageRecord:
    try:
        return service.get_work_package(principal, package_id)
    except CampaignError as exc:
        raise _translate(exc) from exc


@router.post("/engineering/packages/{package_id}/start", response_model=WorkPackageRecord)
def start_work_package(
    package_id: str,
    service: CampaignService = Depends(get_campaign_service),
    principal: dict = Depends(require_application_session),
) -> WorkPackageRecord:
    try:
        return service.start_work_package(principal, package_id)
    except CampaignError as exc:
        raise _translate(exc) from exc


@router.post("/engineering/packages/{package_id}/advance")
def advance_work_package(
    package_id: str,
    service: CampaignService = Depends(get_campaign_service),
    principal: dict = Depends(require_application_session),
) -> dict:
    try:
        return service.advance_package(principal, package_id)
    except CampaignError as exc:
        raise _translate(exc) from exc


@router.post("/engineering/packages/{package_id}/attempts", response_model=EngineeringAttemptRecord)
def begin_attempt(
    package_id: str,
    service: CampaignService = Depends(get_campaign_service),
    principal: dict = Depends(require_application_session),
) -> EngineeringAttemptRecord:
    try:
        return service.begin_attempt(principal, package_id)
    except CampaignError as exc:
        raise _translate(exc) from exc


@router.get("/engineering/packages/{package_id}/attempts", response_model=List[EngineeringAttemptRecord])
def list_attempts(
    package_id: str,
    service: CampaignService = Depends(get_campaign_service),
    principal: dict = Depends(require_application_session),
) -> List[EngineeringAttemptRecord]:
    try:
        return list(service.attempts(principal, package_id))
    except CampaignError as exc:
        raise _translate(exc) from exc


@router.post("/engineering/attempts/{attempt_id}/finish", response_model=EngineeringAttemptRecord)
def finish_attempt(
    attempt_id: str,
    payload: dict,
    service: CampaignService = Depends(get_campaign_service),
    principal: dict = Depends(require_application_session),
) -> EngineeringAttemptRecord:
    try:
        return service.finish_attempt(
            principal, attempt_id, state=str(payload.get("state") or ""),
            summary=str(payload.get("summary") or "") or None,
            evidence=[str(e) for e in payload.get("evidence", [])])
    except CampaignError as exc:
        raise _translate(exc) from exc


@router.post("/engineering/packages/{package_id}/blockers", response_model=EngineeringBlockerRecord,
             status_code=status.HTTP_201_CREATED)
def raise_blocker(
    package_id: str,
    payload: dict,
    service: CampaignService = Depends(get_campaign_service),
    principal: dict = Depends(require_application_session),
) -> EngineeringBlockerRecord:
    try:
        reason = str(payload.get("reason") or "gate_failed")
        if reason not in VALID_BLOCK_REASONS:
            raise HTTPException(status_code=422, detail="invalid_blocker_reason")
        return service.raise_blocker(
            principal, package_id, reason,
            detail=str(payload.get("detail") or ""))
    except CampaignError as exc:
        raise _translate(exc) from exc


@router.post("/engineering/blockers/{blocker_id}/resolve", response_model=EngineeringBlockerRecord)
def resolve_blocker(
    blocker_id: str,
    payload: dict,
    service: CampaignService = Depends(get_campaign_service),
    principal: dict = Depends(require_application_session),
) -> EngineeringBlockerRecord:
    try:
        return service.resolve_blocker(principal, blocker_id,
                                       resolution=str(payload.get("resolution") or ""))
    except CampaignError as exc:
        raise _translate(exc) from exc


@router.get("/engineering/campaigns/{campaign_id}/blockers", response_model=List[EngineeringBlockerRecord])
def list_blockers(
    campaign_id: str,
    service: CampaignService = Depends(get_campaign_service),
    principal: dict = Depends(require_application_session),
) -> List[EngineeringBlockerRecord]:
    try:
        return list(service.blockers(principal, campaign_id))
    except CampaignError as exc:
        raise _translate(exc) from exc


@router.get("/engineering/campaigns/{campaign_id}/checkpoints", response_model=List[EngineeringCheckpointRecord])
def list_checkpoints(
    campaign_id: str,
    service: CampaignService = Depends(get_campaign_service),
    principal: dict = Depends(require_application_session),
) -> List[EngineeringCheckpointRecord]:
    try:
        return list(service.checkpoints(principal, campaign_id))
    except CampaignError as exc:
        raise _translate(exc) from exc
