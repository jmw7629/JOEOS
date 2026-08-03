"""REST API for the JoeOS Automation and Workflow Platform.

Every endpoint reads and mutates real workflow state. Runs, schedules,
approvals, and traces are never fabricated. Secret values are never returned.
"""

from __future__ import annotations

from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from .models import (
    Recurrence,
    RunRecord,
    ScheduleRecord,
    WorkflowDefinition,
    WorkflowOverview,
    WorkflowRecord,
)
from .service import AutomationService
from .templates import list_templates, template_definition
from .workflows import WorkflowError, parse_definition

router = APIRouter(prefix="/api/v1/automation", tags=["automation"])


def get_automation_service(request: Request) -> AutomationService:
    service = getattr(request.app.state, "automation_service", None)
    if service is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Automation service is not initialized.")
    return service


def _require(service: AutomationService, workflow_id: str) -> WorkflowRecord:
    record = service.get_workflow(workflow_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Workflow not found.")
    return record


# ---- overview ----

@router.get("/overview", response_model=WorkflowOverview)
def overview(service: AutomationService = Depends(get_automation_service)) -> WorkflowOverview:
    return service.overview()


# ---- workflows ----

@router.get("/workflows", response_model=List[WorkflowRecord])
def list_workflows(service: AutomationService = Depends(get_automation_service)) -> List[WorkflowRecord]:
    return list(service.list_workflows())


@router.get("/workflows/{workflow_id}", response_model=WorkflowRecord)
def get_workflow(workflow_id: str, service: AutomationService = Depends(get_automation_service)) -> WorkflowRecord:
    return _require(service, workflow_id)


@router.post("/workflows", status_code=status.HTTP_201_CREATED, response_model=WorkflowRecord)
def create_workflow(payload: WorkflowDefinition, service: AutomationService = Depends(get_automation_service)) -> WorkflowRecord:
    try:
        return service.create_workflow(payload)
    except WorkflowError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/workflows/{workflow_id}", response_model=WorkflowRecord)
def update_workflow(workflow_id: str, payload: WorkflowDefinition, service: AutomationService = Depends(get_automation_service)) -> WorkflowRecord:
    if payload.workflow_id != workflow_id:
        raise HTTPException(status_code=422, detail="workflow_id in body must match the URL.")
    try:
        return service.update_workflow(payload)
    except WorkflowError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/workflows/{workflow_id}/enable")
def enable_workflow(workflow_id: str, service: AutomationService = Depends(get_automation_service)) -> WorkflowRecord:
    try:
        return service.enable_workflow(workflow_id)
    except WorkflowError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/workflows/{workflow_id}/disable")
def disable_workflow(workflow_id: str, service: AutomationService = Depends(get_automation_service)) -> WorkflowRecord:
    return service.disable_workflow(workflow_id)


@router.post("/workflows/{workflow_id}/pause")
def pause_workflow(workflow_id: str, service: AutomationService = Depends(get_automation_service)) -> WorkflowRecord:
    return service.pause_workflow(workflow_id)


@router.post("/workflows/{workflow_id}/run")
def run_workflow(
    workflow_id: str,
    payload: RunRequest,
    service: AutomationService = Depends(get_automation_service),
) -> RunRecord:
    try:
        return service.run_workflow(
            workflow_id,
            trigger_id=payload.trigger_id or "manual",
            inputs=payload.inputs,
        )
    except WorkflowError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ---- templates ----

@router.get("/templates")
def templates(service: AutomationService = Depends(get_automation_service)) -> dict:
    return {"templates": list(list_templates())}


@router.post("/templates/{template_id}/instantiate", status_code=status.HTTP_201_CREATED, response_model=WorkflowRecord)
def instantiate_template(
    template_id: str,
    payload: InstantiateRequest,
    service: AutomationService = Depends(get_automation_service),
) -> WorkflowRecord:
    try:
        definition = template_definition(
            template_id,
            payload.workflow_id,
            scheduled=payload.scheduled,
            timezone=payload.timezone,
            at_time=payload.at_time,
        )
        return service.create_workflow(definition, creator="user")
    except (WorkflowError, KeyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ---- schedules ----

@router.post("/schedules")
def create_schedule(payload: ScheduleRequest, service: AutomationService = Depends(get_automation_service)) -> ScheduleRecord:
    try:
        return service.schedule_workflow(
            workflow_id=payload.workflow_id,
            recurrence=payload.recurrence,
            timezone=payload.recurrence.timezone,
            missed_run_policy=payload.missed_run_policy,
            overlap_policy=payload.overlap_policy,
        )
    except (WorkflowError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/schedules")
def list_schedules(service: AutomationService = Depends(get_automation_service)) -> dict:
    return {"schedules": [record.model_dump() for record in service.list_schedules()]}


@router.post("/schedules/preview")
def preview_schedule(payload: PreviewRequest, service: AutomationService = Depends(get_automation_service)) -> dict:
    occurrences = service.preview_schedule(payload.recurrence, count=payload.count)
    return {"occurrences": list(occurrences)}


# ---- runs ----

@router.get("/runs")
def list_runs(
    workflow_id: Optional[str] = Query(default=None),
    state: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    service: AutomationService = Depends(get_automation_service),
) -> dict:
    return {"runs": [record.model_dump() for record in service.list_runs(workflow_id=workflow_id, state=state, limit=limit)]}


@router.get("/runs/{run_id}")
def get_run(run_id: str, service: AutomationService = Depends(get_automation_service)) -> RunRecord:
    run = service.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    return run


@router.get("/runs/{run_id}/traces")
def run_traces(run_id: str, service: AutomationService = Depends(get_automation_service)) -> dict:
    return {"traces": list(service.traces(run_id))}


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str, service: AutomationService = Depends(get_automation_service)) -> RunRecord:
    return service.cancel_run(run_id)


# ---- approvals ----

@router.get("/approvals")
def approvals(
    workflow_id: Optional[str] = Query(default=None),
    state: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    service: AutomationService = Depends(get_automation_service),
) -> dict:
    return {"approvals": list(service.approvals(workflow_id=workflow_id, state=state, limit=limit))}


@router.post("/approvals/{approval_id}/resolve")
def resolve_approval(
    approval_id: str,
    payload: ApprovalDecisionRequest,
    service: AutomationService = Depends(get_automation_service),
) -> dict:
    decision = "approved" if payload.decision == "approve" else "denied"
    return service.resolve_approval(approval_id, decision=decision, approver=payload.approver)


# ---- user input ----

@router.get("/inputs")
def inputs(service: AutomationService = Depends(get_automation_service)) -> dict:
    return {"inputs": list(service.inputs())}


@router.post("/inputs/{input_id}/provide")
def provide_input(input_id: str, payload: InputRequest, service: AutomationService = Depends(get_automation_service)) -> dict:
    return service.provide_input(input_id, response=payload.response)


# ---- permissions / secrets ----

@router.get("/workflows/{workflow_id}/permissions")
def workflow_permissions(workflow_id: str, service: AutomationService = Depends(get_automation_service)) -> dict:
    _require(service, workflow_id)
    return {"grants": list(service.permission_grants(workflow_id))}


@router.post("/workflows/{workflow_id}/permissions/grant")
def grant_permission(
    workflow_id: str,
    payload: GrantRequest,
    service: AutomationService = Depends(get_automation_service),
) -> dict:
    service.grant_permission(workflow_id, payload.permission, scope=payload.scope, scope_target=payload.scope_target)
    return {"granted": payload.permission}


@router.post("/workflows/{workflow_id}/permissions/revoke")
def revoke_permission(
    workflow_id: str,
    payload: GrantRequest,
    service: AutomationService = Depends(get_automation_service),
) -> dict:
    service.revoke_permission(workflow_id, payload.permission, scope_target=payload.scope_target)
    return {"revoked": payload.permission}


@router.get("/secrets")
def secret_references(service: AutomationService = Depends(get_automation_service)) -> dict:
    return {"references": list(service.secret_references())}


@router.post("/secrets")
def set_secret(payload: SecretRequest, service: AutomationService = Depends(get_automation_service)) -> dict:
    return service.set_secret(payload.name, payload.value, scope=payload.scope)


@router.delete("/secrets/{name}")
def revoke_secret(name: str, service: AutomationService = Depends(get_automation_service)) -> dict:
    service.revoke_secret(name)
    return {"revoked": name}


# ---- health / diagnostics ----

@router.get("/health")
def health(service: AutomationService = Depends(get_automation_service)) -> dict:
    return {"workflows": list(service.health())}


@router.get("/stuck-runs")
def stuck_runs(service: AutomationService = Depends(get_automation_service)) -> dict:
    return {"runs": list(service.stuck_runs())}


@router.get("/activity")
def activity(
    workflow_id: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    service: AutomationService = Depends(get_automation_service),
) -> dict:
    return {"activity": list(service.activity(workflow_id=workflow_id, limit=limit))}


# ---- actions / storage ----

@router.get("/actions")
def actions(service: AutomationService = Depends(get_automation_service)) -> dict:
    return {"actions": list(service.action_catalog())}


@router.get("/storage")
def storage(service: AutomationService = Depends(get_automation_service)) -> dict:
    return service.storage_stats()


@router.post("/backup")
def backup(service: AutomationService = Depends(get_automation_service)) -> dict:
    path = service.backup()
    return {"backup_path": path}


# ---- request models ----

from pydantic import BaseModel, Field  # noqa: E402


class RunRequest(BaseModel):
    trigger_id: str = Field(default="", max_length=80)
    inputs: dict = Field(default_factory=dict)


class InstantiateRequest(BaseModel):
    workflow_id: str = Field(min_length=3, max_length=80)
    scheduled: bool = False
    timezone: str = Field(default="UTC", max_length=64)
    at_time: str = Field(default="09:00", pattern=r"^\d{2}:\d{2}$")


class ScheduleRequest(BaseModel):
    workflow_id: str = Field(min_length=3, max_length=80)
    recurrence: Recurrence
    missed_run_policy: str = Field(default="skip", max_length=30)
    overlap_policy: str = Field(default="skip", max_length=30)


class PreviewRequest(BaseModel):
    recurrence: Recurrence
    count: int = Field(default=10, ge=1, le=100)


class ApprovalDecisionRequest(BaseModel):
    decision: Literal["approve", "deny"] = "approve"
    approver: str = Field(default="user", max_length=80)


class InputRequest(BaseModel):
    response: dict = Field(default_factory=dict)


class GrantRequest(BaseModel):
    permission: str = Field(min_length=1, max_length=100)
    scope: str = Field(default="global", max_length=40)
    scope_target: str = Field(default="", max_length=120)


class SecretRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=4000)
    scope: str = Field(default="global", max_length=80)