"""HTTP API for autonomous operations.

All routes require a live application session and enforce workspace
isolation. Clients can create/read/pause/resume/archive automations and
inspect runs. Schedule configuration is structured (never raw shell/cron
execution)."""

from __future__ import annotations

from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from server.identity.authority_router import require_application_session

from .models import AutomationDefinitionCreate
from .service import AutonomousError, AutonomousService

router = APIRouter(prefix="/api/v1/automations", tags=["automations"])


def get_autonomous_service(request: Request) -> AutonomousService:
    service = getattr(request.app.state, "autonomous_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "autonomous_unavailable",
                    "message": "The autonomous operations service is not initialized."},
        )
    return service


def _raise(error: AutonomousError) -> None:
    raise HTTPException(status_code=error.status_code,
                        detail={"code": error.code, "message": error.public_message}) from error


def _run(service: AutonomousService, principal: Dict, operation) -> Dict:
    try:
        return operation(principal)
    except AutonomousError as error:
        _raise(error)


@router.get("/overview")
def overview(
    principal: Dict = Depends(require_application_session),
    service: AutonomousService = Depends(get_autonomous_service),
):
    return _run(service, principal, lambda p: {
        "definitions": len(service.list_definitions(p)),
        "active": len([d for d in service.list_definitions(p) if d.state == "active"]),
        "paused": len([d for d in service.list_definitions(p) if d.state == "paused"]),
        "needs_attention": len(service.list_runs_by_state("failed", [str(p["workspace"]["id"])], 50)),
    })


@router.get("")
def list_automations(
    state: Optional[str] = Query(default=None),
    principal: Dict = Depends(require_application_session),
    service: AutonomousService = Depends(get_autonomous_service),
):
    return _run(service, principal, lambda p: {
        "automations": [d.model_dump(mode="json") for d in service.list_definitions(p, state)]
    })


@router.post("", status_code=status.HTTP_201_CREATED)
def create_automation(
    payload: AutomationDefinitionCreate,
    principal: Dict = Depends(require_application_session),
    service: AutonomousService = Depends(get_autonomous_service),
):
    return _run(service, principal, lambda p: service.create_definition(p, payload).model_dump(mode="json"))


@router.get("/{automation_id}")
def get_automation(
    automation_id: str,
    principal: Dict = Depends(require_application_session),
    service: AutonomousService = Depends(get_autonomous_service),
):
    return _run(service, principal, lambda p: service.get_definition(p, automation_id).model_dump(mode="json"))


@router.put("/{automation_id}")
def update_automation(
    automation_id: str,
    payload: AutomationDefinitionCreate,
    principal: Dict = Depends(require_application_session),
    service: AutonomousService = Depends(get_autonomous_service),
):
    return _run(service, principal, lambda p: service.update_definition(p, automation_id, payload).model_dump(mode="json"))


@router.post("/{automation_id}/run-now")
def run_now(
    automation_id: str,
    principal: Dict = Depends(require_application_session),
    service: AutonomousService = Depends(get_autonomous_service),
):
    return _run(service, principal, lambda p: service.run_now(p, automation_id).model_dump(mode="json"))


@router.post("/{automation_id}/pause")
def pause_automation(
    automation_id: str,
    principal: Dict = Depends(require_application_session),
    service: AutonomousService = Depends(get_autonomous_service),
):
    return _run(service, principal, lambda p: service.set_state(p, automation_id, "paused").model_dump(mode="json"))


@router.post("/{automation_id}/resume")
def resume_automation(
    automation_id: str,
    principal: Dict = Depends(require_application_session),
    service: AutonomousService = Depends(get_autonomous_service),
):
    return _run(service, principal, lambda p: service.set_state(p, automation_id, "active").model_dump(mode="json"))


@router.post("/{automation_id}/archive")
def archive_automation(
    automation_id: str,
    principal: Dict = Depends(require_application_session),
    service: AutonomousService = Depends(get_autonomous_service),
):
    return _run(service, principal, lambda p: service.archive_definition(p, automation_id).model_dump(mode="json"))


@router.get("/{automation_id}/runs")
def list_runs(
    automation_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    principal: Dict = Depends(require_application_session),
    service: AutonomousService = Depends(get_autonomous_service),
):
    return _run(service, principal, lambda p: {
        "runs": [r.model_dump(mode="json") for r in service.list_runs_for_definition(p, automation_id, limit)]
    })


@router.get("/{automation_id}/runs/{run_id}")
def get_run(
    automation_id: str,
    run_id: str,
    principal: Dict = Depends(require_application_session),
    service: AutonomousService = Depends(get_autonomous_service),
):
    return _run(service, principal, lambda p: service.get_run_for_definition(p, automation_id, run_id).model_dump(mode="json"))
