"""REST API for the Multi-Agent Collaboration and Organizational Intelligence
platform. The facade exposes state and operations only; no route fabricates
activity or grants authority beyond what the underlying services enforce.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from .models import (
    AgentProfile,
    AgentsOverview,
    ApprovalRecord,
    ArtifactRecord,
    AssignmentExplanation,
    CollaborationMessage,
    ConsensusResult,
    ConsultationRecord,
    DebateRecord,
    DetectionEvent,
    DisagreementRecord,
    EscalationRecord,
    HandoffRecord,
    InterventionRecord,
    MissionCharter,
    MissionEnvelope,
    MissionPlan,
    MissionRecord,
    MissionTask,
    ModelRoute,
    OrgHealthRecord,
    OrgMemoryProposal,
    OrganizationRecord,
    OrganizationalUnit,
    QualityGate,
    ReviewFinding,
    ReviewRecord,
    RoleDefinition,
)
from .service import AgentsService

router = APIRouter(prefix="/api/v1", tags=["agents"])


def get_agents_service(request: Request) -> AgentsService:
    service = getattr(request.app.state, "agents_service", None)
    if service is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Agents service is not initialized.")
    return service


def _require_mission(service: AgentsService, mission_id: str) -> None:
    if service.missions.get_mission(mission_id) is None:
        raise HTTPException(status_code=404, detail="Mission not found.")


def _require_task(service: AgentsService, task_id: str) -> None:
    if service.missions.task(task_id) is None:
        raise HTTPException(status_code=404, detail="Task not found.")


# ---- overview & health ----

@router.get("/agents/overview", response_model=AgentsOverview)
def overview(service: AgentsService = Depends(get_agents_service)) -> AgentsOverview:
    return service.overview()


@router.get("/agents/health", response_model=OrgHealthRecord)
def health(service: AgentsService = Depends(get_agents_service)) -> OrgHealthRecord:
    return service.current_health()


@router.get("/agents/activity")
def activity(
    limit: int = Query(default=50, ge=1, le=200),
    service: AgentsService = Depends(get_agents_service),
) -> List[dict]:
    return list(service.health.activity(limit=limit))


@router.get("/agents/storage")
def storage_stats(service: AgentsService = Depends(get_agents_service)) -> dict:
    return service.storage_stats()


@router.post("/agents/backup")
def backup(service: AgentsService = Depends(get_agents_service)) -> dict:
    path = service.backup()
    return {"backup_created": path is not None, "path": path}


# ---- organization ----

@router.post("/agents/organization", response_model=OrganizationRecord, status_code=status.HTTP_201_CREATED)
def create_organization(payload: dict, service: AgentsService = Depends(get_agents_service)) -> OrganizationRecord:
    return service.create_organization(str(payload.get("name") or "JoeOS AI Organization"), purpose=str(payload.get("purpose") or ""))


@router.get("/agents/organization", response_model=OrganizationRecord)
def get_organization(service: AgentsService = Depends(get_agents_service)) -> OrganizationRecord:
    return service.get_or_create_organization()


@router.post("/agents/units", response_model=OrganizationalUnit, status_code=status.HTTP_201_CREATED)
def create_unit(payload: dict, service: AgentsService = Depends(get_agents_service)) -> OrganizationalUnit:
    return service.organization.create_unit(
        name=str(payload.get("name") or ""),
        unit_type=str(payload.get("unit_type") or "team"),
        purpose=str(payload.get("purpose") or ""),
        parent_unit=payload.get("parent_unit"),
        leader=payload.get("leader"),
    )


@router.get("/agents/units")
def list_units(service: AgentsService = Depends(get_agents_service)) -> List[dict]:
    return [u.model_dump() for u in service.organization.units()]


@router.post("/agents/roles", response_model=RoleDefinition, status_code=status.HTTP_201_CREATED)
def create_role(payload: dict, service: AgentsService = Depends(get_agents_service)) -> RoleDefinition:
    return service.organization.create_role(
        title=str(payload.get("title") or ""),
        required_capabilities=tuple(payload.get("required_capabilities") or ()),
        purpose=str(payload.get("purpose") or ""),
    )


@router.get("/agents/roles")
def list_roles(service: AgentsService = Depends(get_agents_service)) -> List[dict]:
    return [r.model_dump() for r in service.organization.roles()]


@router.post("/agents/agents", response_model=AgentProfile, status_code=status.HTTP_201_CREATED)
def create_agent(payload: dict, service: AgentsService = Depends(get_agents_service)) -> AgentProfile:
    role_id = str(payload.get("role_id") or "")
    if service.organization.role(role_id) is None:
        raise HTTPException(status_code=422, detail="Role not found.")
    return service.organization.create_agent(
        name=str(payload.get("name") or ""),
        role_id=role_id,
        department=payload.get("department"),
        team=payload.get("team"),
    )


@router.get("/agents/agents")
def list_agents(
    include_inactive: bool = Query(default=False),
    service: AgentsService = Depends(get_agents_service),
) -> List[dict]:
    return [a.model_dump() for a in service.organization.agents(include_inactive=include_inactive)]


@router.get("/agents/agents/{agent_id}", response_model=AgentProfile)
def get_agent(agent_id: str, service: AgentsService = Depends(get_agents_service)) -> AgentProfile:
    agent = service.organization.agent(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found.")
    return agent


@router.post("/agents/agents/{agent_id}/availability", response_model=AgentProfile)
def set_agent_availability(agent_id: str, payload: dict, service: AgentsService = Depends(get_agents_service)) -> AgentProfile:
    agent = service.organization.update_agent_state(
        agent_id,
        status=str(payload.get("status") or None),
        availability=str(payload.get("availability") or None),
    )
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found.")
    return agent


# ---- missions ----

@router.post("/agents/missions", response_model=MissionRecord, status_code=status.HTTP_201_CREATED)
def create_mission(payload: dict, service: AgentsService = Depends(get_agents_service)) -> MissionRecord:
    return service.missions.create_mission(
        title=str(payload.get("title") or ""),
        objective=str(payload.get("objective") or ""),
        project=payload.get("project"),
        priority=str(payload.get("priority") or "normal"),
        risk=str(payload.get("risk") or "low"),
    )


@router.get("/agents/missions")
def list_missions(
    status_filter: Optional[str] = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    service: AgentsService = Depends(get_agents_service),
) -> List[dict]:
    return [m.model_dump() for m in service.missions.missions(status=status_filter, limit=limit)]


@router.get("/agents/missions/{mission_id}", response_model=MissionRecord)
def get_mission(mission_id: str, service: AgentsService = Depends(get_agents_service)) -> MissionRecord:
    mission = service.missions.get_mission(mission_id)
    if mission is None:
        raise HTTPException(status_code=404, detail="Mission not found.")
    return mission


@router.get("/agents/missions/{mission_id}/envelope", response_model=MissionEnvelope)
def mission_envelope(mission_id: str, service: AgentsService = Depends(get_agents_service)) -> MissionEnvelope:
    envelope = service.mission_envelope(mission_id)
    if envelope is None:
        raise HTTPException(status_code=404, detail="Mission not found.")
    return envelope


@router.post("/agents/missions/{mission_id}/charter", response_model=MissionCharter, status_code=status.HTTP_201_CREATED)
def create_charter(mission_id: str, payload: dict, service: AgentsService = Depends(get_agents_service)) -> MissionCharter:
    _require_mission(service, mission_id)
    return service.missions.new_charter(
        mission_id,
        objective=str(payload.get("objective") or ""),
        success_criteria=tuple(payload.get("success_criteria") or ()),
        business_value=str(payload.get("business_value") or ""),
        non_goals=tuple(payload.get("non_goals") or ()),
        risk=str(payload.get("risk") or "low"),
    )


@router.post("/agents/missions/{mission_id}/approve", response_model=MissionRecord)
def approve_mission(mission_id: str, payload: dict, service: AgentsService = Depends(get_agents_service)) -> MissionRecord:
    _require_mission(service, mission_id)
    if not service.missions.approve_charter(mission_id, approved_by=str(payload.get("approved_by") or "user")):
        raise HTTPException(status_code=422, detail="No charter to approve.")
    return service.missions.get_mission(mission_id)


@router.post("/agents/missions/{mission_id}/start", response_model=MissionRecord)
def start_mission(mission_id: str, service: AgentsService = Depends(get_agents_service)) -> MissionRecord:
    _require_mission(service, mission_id)
    if not service.missions.start(mission_id):
        raise HTTPException(status_code=422, detail="Mission cannot start in its current state.")
    return service.missions.get_mission(mission_id)


@router.post("/agents/missions/{mission_id}/plan", response_model=MissionPlan)
def plan_mission(mission_id: str, payload: dict, service: AgentsService = Depends(get_agents_service)) -> MissionPlan:
    _require_mission(service, mission_id)
    return service.missions.plan(mission_id, task_ids=tuple(payload.get("task_ids") or ()))


@router.get("/agents/missions/{mission_id}/plan", response_model=MissionPlan)
def get_plan(mission_id: str, service: AgentsService = Depends(get_agents_service)) -> MissionPlan:
    _require_mission(service, mission_id)
    plan = service.missions.plan_for(mission_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found.")
    return plan


@router.get("/agents/missions/{mission_id}/graph")
def mission_graph(mission_id: str, service: AgentsService = Depends(get_agents_service)) -> dict:
    _require_mission(service, mission_id)
    return service.missions.graph(mission_id).model_dump()


@router.post("/agents/missions/{mission_id}/tasks", response_model=MissionTask, status_code=status.HTTP_201_CREATED)
def create_task(mission_id: str, payload: dict, service: AgentsService = Depends(get_agents_service)) -> MissionTask:
    _require_mission(service, mission_id)
    task = service.missions.create_task(
        mission_id,
        title=str(payload.get("title") or ""),
        objective=str(payload.get("objective") or ""),
        risk=str(payload.get("risk") or "low"),
        dependencies=tuple(payload.get("dependencies") or ()),
        blocking=tuple(payload.get("blocking") or ()),
        depth=int(payload.get("depth") or 0),
    )
    if task is None:
        raise HTTPException(status_code=422, detail="Task could not be created (count or depth budget exceeded).")
    return task


@router.get("/agents/missions/{mission_id}/tasks")
def list_tasks(mission_id: str, service: AgentsService = Depends(get_agents_service)) -> List[dict]:
    _require_mission(service, mission_id)
    return [t.model_dump() for t in service.missions.tasks(mission_id)]


@router.get("/agents/tasks/{task_id}", response_model=MissionTask)
def get_task(task_id: str, service: AgentsService = Depends(get_agents_service)) -> MissionTask:
    task = service.missions.task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    return task


@router.post("/agents/tasks/{task_id}/assign", response_model=MissionTask)
def assign_task(task_id: str, payload: dict, service: AgentsService = Depends(get_agents_service)) -> MissionTask:
    _require_task(service, task_id)
    agent_id = payload.get("agent_id")
    if agent_id and service.organization.agent(str(agent_id)) is None:
        raise HTTPException(status_code=422, detail="Agent not found.")
    explanation = AssignmentExplanation.model_validate(payload.get("explanation") or {})
    task = service.missions.assign(task_id, str(agent_id) if agent_id else None, explanation)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    return task


@router.post("/agents/tasks/{task_id}/state", response_model=MissionTask)
def update_task_state(task_id: str, payload: dict, service: AgentsService = Depends(get_agents_service)) -> MissionTask:
    _require_task(service, task_id)
    task = service.missions.update_task_state(
        task_id,
        str(payload.get("status") or ""),
        note=str(payload.get("note") or ""),
        final_result=str(payload.get("final_result") or ""),
    )
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found.")
    return task


# ---- collaboration ----

@router.post("/agents/messages", response_model=CollaborationMessage, status_code=status.HTTP_201_CREATED)
def send_message(message: CollaborationMessage, service: AgentsService = Depends(get_agents_service)) -> CollaborationMessage:
    return service.collaboration.send_message(message)


@router.get("/agents/messages")
def list_messages(
    mission_id: Optional[str] = Query(default=None),
    task_id: Optional[str] = Query(default=None),
    recipient: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    service: AgentsService = Depends(get_agents_service),
) -> List[dict]:
    return [m.model_dump() for m in service.collaboration.messages(mission_id=mission_id, task_id=task_id, recipient=recipient, limit=limit)]


@router.post("/agents/handoffs", response_model=HandoffRecord, status_code=status.HTTP_201_CREATED)
def send_handoff(record: HandoffRecord, service: AgentsService = Depends(get_agents_service)) -> HandoffRecord:
    return service.collaboration.send_handoff(record)


@router.post("/agents/handoffs/{handoff_id}/respond", response_model=HandoffRecord)
def respond_handoff(handoff_id: str, payload: dict, service: AgentsService = Depends(get_agents_service)) -> HandoffRecord:
    record = service.collaboration.respond_handoff(handoff_id, str(payload.get("action") or ""), note=str(payload.get("note") or ""))
    if record is None:
        raise HTTPException(status_code=404, detail="Handoff not found or already resolved.")
    return record


@router.get("/agents/handoffs")
def list_handoffs(
    mission_id: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    service: AgentsService = Depends(get_agents_service),
) -> List[dict]:
    return [h.model_dump() for h in service.collaboration.handoffs(mission_id=mission_id, limit=limit)]


@router.post("/agents/artifacts", response_model=ArtifactRecord, status_code=status.HTTP_201_CREATED)
def register_artifact(record: ArtifactRecord, service: AgentsService = Depends(get_agents_service)) -> ArtifactRecord:
    return service.collaboration.register_artifact(record)


@router.get("/agents/artifacts")
def list_artifacts(
    mission_id: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    service: AgentsService = Depends(get_agents_service),
) -> List[dict]:
    return [a.model_dump() for a in service.collaboration.artifacts(mission_id=mission_id, limit=limit)]


@router.post("/agents/artifacts/{artifact_id}/validate", response_model=ArtifactRecord)
def validate_artifact(artifact_id: str, payload: dict, service: AgentsService = Depends(get_agents_service)) -> ArtifactRecord:
    record = service.collaboration.validate_artifact(artifact_id, str(payload.get("state") or ""))
    if record is None:
        raise HTTPException(status_code=404, detail="Artifact not found or invalid state.")
    return record


@router.post("/agents/gates", response_model=QualityGate, status_code=status.HTTP_201_CREATED)
def create_gate(record: QualityGate, service: AgentsService = Depends(get_agents_service)) -> QualityGate:
    return service.collaboration.create_gate(record)


@router.get("/agents/gates")
def list_gates(
    mission_id: Optional[str] = Query(default=None),
    state_filter: Optional[str] = Query(default=None, alias="state"),
    service: AgentsService = Depends(get_agents_service),
) -> List[dict]:
    return [g.model_dump() for g in service.collaboration.gates(mission_id=mission_id, state=state_filter)]


@router.post("/agents/reviews", response_model=ReviewRecord, status_code=status.HTTP_201_CREATED)
def request_review(record: ReviewRecord, service: AgentsService = Depends(get_agents_service)) -> ReviewRecord:
    return service.collaboration.request_review(record)


@router.post("/agents/reviews/{review_id}/complete", response_model=ReviewRecord)
def complete_review(review_id: str, payload: dict, service: AgentsService = Depends(get_agents_service)) -> ReviewRecord:
    findings = tuple(ReviewFinding.model_validate(f) for f in (payload.get("findings") or ()))
    record = service.collaboration.complete_review(
        review_id,
        conclusion=str(payload.get("conclusion") or "fail"),
        findings=findings,
        confidence=str(payload.get("confidence") or "medium"),
        disclosure=str(payload.get("disclosure") or ""),
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Review not found.")
    return record


@router.get("/agents/reviews")
def list_reviews(
    mission_id: Optional[str] = Query(default=None),
    service: AgentsService = Depends(get_agents_service),
) -> List[dict]:
    return [r.model_dump() for r in service.collaboration.reviews(mission_id=mission_id)]


@router.post("/agents/disagreements", response_model=DisagreementRecord, status_code=status.HTTP_201_CREATED)
def open_disagreement(record: DisagreementRecord, service: AgentsService = Depends(get_agents_service)) -> DisagreementRecord:
    return service.collaboration.open_disagreement(record)


@router.post("/agents/disagreements/{disagreement_id}/resolve", response_model=DisagreementRecord)
def resolve_disagreement(disagreement_id: str, payload: dict, service: AgentsService = Depends(get_agents_service)) -> DisagreementRecord:
    record = service.collaboration.resolve_disagreement(
        disagreement_id,
        method=str(payload.get("method") or "evidence_review"),
        notes=str(payload.get("notes") or ""),
        escalated=bool(payload.get("escalated") or False),
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Disagreement not found.")
    return record


@router.get("/agents/disagreements")
def list_disagreements(
    mission_id: Optional[str] = Query(default=None),
    state_filter: Optional[str] = Query(default=None, alias="state"),
    service: AgentsService = Depends(get_agents_service),
) -> List[dict]:
    return [d.model_dump() for d in service.collaboration.disagreements(mission_id=mission_id, state=state_filter)]


@router.post("/agents/consensus", response_model=ConsensusResult, status_code=status.HTTP_201_CREATED)
def record_consensus(record: ConsensusResult, service: AgentsService = Depends(get_agents_service)) -> ConsensusResult:
    return service.collaboration.record_consensus(record)


@router.get("/agents/consensus")
def list_consensus(service: AgentsService = Depends(get_agents_service)) -> List[dict]:
    return [c.model_dump() for c in service.collaboration.consensus()]


@router.post("/agents/debates", response_model=DebateRecord, status_code=status.HTTP_201_CREATED)
def create_debate(record: DebateRecord, service: AgentsService = Depends(get_agents_service)) -> DebateRecord:
    return service.collaboration.create_debate(record)


@router.post("/agents/debates/{debate_id}/advance", response_model=DebateRecord)
def advance_debate(debate_id: str, payload: dict, service: AgentsService = Depends(get_agents_service)) -> DebateRecord:
    record = service.collaboration.advance_debate(debate_id, rounds=int(payload.get("rounds") or 1))
    if record is None:
        raise HTTPException(status_code=404, detail="Debate not found or round limit reached.")
    return record


@router.post("/agents/debates/{debate_id}/conclude", response_model=DebateRecord)
def conclude_debate(debate_id: str, payload: dict, service: AgentsService = Depends(get_agents_service)) -> DebateRecord:
    record = service.collaboration.conclude_debate(debate_id, synthesis=str(payload.get("synthesis") or ""))
    if record is None:
        raise HTTPException(status_code=404, detail="Debate not found.")
    return record


@router.post("/agents/consultations", response_model=ConsultationRecord, status_code=status.HTTP_201_CREATED)
def request_consultation(record: ConsultationRecord, service: AgentsService = Depends(get_agents_service)) -> ConsultationRecord:
    return service.collaboration.request_consultation(record)


@router.post("/agents/consultations/{consultation_id}/respond", response_model=ConsultationRecord)
def respond_consultation(consultation_id: str, payload: dict, service: AgentsService = Depends(get_agents_service)) -> ConsultationRecord:
    record = service.collaboration.respond_consultation(
        consultation_id,
        response=str(payload.get("response") or ""),
        conclusion=str(payload.get("conclusion") or ""),
        confidence=str(payload.get("confidence") or "medium"),
        limitations=tuple(payload.get("limitations") or ()),
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Consultation not found.")
    return record


# ---- governance ----

@router.post("/agents/escalations", response_model=EscalationRecord, status_code=status.HTTP_201_CREATED)
def open_escalation(record: EscalationRecord, service: AgentsService = Depends(get_agents_service)) -> EscalationRecord:
    return service.governance.open_escalation(record)


@router.post("/agents/escalations/{escalation_id}/resolve", response_model=EscalationRecord)
def resolve_escalation(escalation_id: str, payload: dict, service: AgentsService = Depends(get_agents_service)) -> EscalationRecord:
    record = service.governance.resolve_escalation(escalation_id, response=str(payload.get("response") or ""), state=str(payload.get("state") or "resolved"))
    if record is None:
        raise HTTPException(status_code=404, detail="Escalation not found.")
    return record


@router.get("/agents/escalations")
def list_escalations(
    state_filter: Optional[str] = Query(default=None, alias="state"),
    mission_id: Optional[str] = Query(default=None),
    service: AgentsService = Depends(get_agents_service),
) -> List[dict]:
    return [e.model_dump() for e in service.governance.escalations(state=state_filter, mission_id=mission_id)]


@router.post("/agents/interventions", response_model=InterventionRecord, status_code=status.HTTP_201_CREATED)
def open_intervention(record: InterventionRecord, service: AgentsService = Depends(get_agents_service)) -> InterventionRecord:
    return service.governance.open_intervention(record)


@router.post("/agents/interventions/{intervention_id}/respond", response_model=InterventionRecord)
def respond_intervention(intervention_id: str, payload: dict, service: AgentsService = Depends(get_agents_service)) -> InterventionRecord:
    record = service.governance.respond_intervention(
        intervention_id,
        response=str(payload.get("response") or ""),
        approved=bool(payload.get("approved") or False),
        work_can_continue=bool(payload.get("work_can_continue") or False),
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Intervention not found.")
    return record


@router.get("/agents/interventions")
def list_interventions(
    state_filter: Optional[str] = Query(default=None, alias="state"),
    service: AgentsService = Depends(get_agents_service),
) -> List[dict]:
    return [i.model_dump() for i in service.governance.interventions(state=state_filter)]


@router.post("/agents/approvals", response_model=ApprovalRecord, status_code=status.HTTP_201_CREATED)
def request_approval(record: ApprovalRecord, service: AgentsService = Depends(get_agents_service)) -> ApprovalRecord:
    return service.governance.request_approval(record)


@router.post("/agents/approvals/{approval_id}/approve", response_model=ApprovalRecord)
def approve_approval(approval_id: str, payload: dict, service: AgentsService = Depends(get_agents_service)) -> ApprovalRecord:
    record = service.governance.approve(approval_id, approver=str(payload.get("approver") or ""))
    if record is None:
        raise HTTPException(status_code=403, detail="Approval not pending, not found, or self-approval blocked.")
    return record


@router.post("/agents/approvals/{approval_id}/deny", response_model=ApprovalRecord)
def deny_approval(approval_id: str, payload: dict, service: AgentsService = Depends(get_agents_service)) -> ApprovalRecord:
    record = service.governance.deny(approval_id, approver=str(payload.get("approver") or ""))
    if record is None:
        raise HTTPException(status_code=403, detail="Approval not pending or not found.")
    return record


@router.get("/agents/approvals")
def list_approvals(
    state_filter: Optional[str] = Query(default=None, alias="state"),
    mission_id: Optional[str] = Query(default=None),
    service: AgentsService = Depends(get_agents_service),
) -> List[dict]:
    return [a.model_dump() for a in service.governance.approvals(state=state_filter, mission_id=mission_id)]


# ---- routing, detection, memory proposals ----

@router.post("/agents/routes", response_model=ModelRoute, status_code=status.HTTP_201_CREATED)
def select_route(payload: dict, service: AgentsService = Depends(get_agents_service)) -> ModelRoute:
    return service.routing.select(
        agent_id=str(payload.get("agent_id") or ""),
        mission_id=payload.get("mission_id"),
        task_id=payload.get("task_id"),
        required_capabilities=tuple(payload.get("required_capabilities") or ()),
        model_preferences=tuple(payload.get("model_preferences") or ()),
        policy=str(payload.get("policy") or "local_first"),
        tool_use_required=bool(payload.get("tool_use_required") or False),
    )


@router.get("/agents/routes")
def list_routes(
    agent_id: Optional[str] = Query(default=None),
    mission_id: Optional[str] = Query(default=None),
    service: AgentsService = Depends(get_agents_service),
) -> List[dict]:
    return [r.model_dump() for r in service.routing.routes(agent_id=agent_id, mission_id=mission_id)]


@router.post("/agents/detections/scan/{mission_id}")
def scan_mission(mission_id: str, payload: dict, service: AgentsService = Depends(get_agents_service)) -> List[dict]:
    _require_mission(service, mission_id)
    events = service.detection.scan_mission(mission_id, stagnation_minutes=int(payload.get("stagnation_minutes") or 30))
    return [e.model_dump() for e in events]


@router.get("/agents/detections")
def list_detections(
    mission_id: Optional[str] = Query(default=None),
    state_filter: Optional[str] = Query(default=None, alias="state"),
    service: AgentsService = Depends(get_agents_service),
) -> List[dict]:
    return [d.model_dump() for d in service.detection.detections(mission_id=mission_id, state=state_filter)]


@router.post("/agents/detections/{detection_id}/resolve", response_model=DetectionEvent)
def resolve_detection(detection_id: str, payload: dict, service: AgentsService = Depends(get_agents_service)) -> DetectionEvent:
    record = service.detection.resolve(detection_id, resolution=str(payload.get("resolution") or ""))
    if record is None:
        raise HTTPException(status_code=404, detail="Detection not found.")
    return record


@router.get("/agents/performance/{agent_id}")
def agent_performance(agent_id: str, service: AgentsService = Depends(get_agents_service)) -> dict:
    if service.organization.agent(agent_id) is None:
        raise HTTPException(status_code=404, detail="Agent not found.")
    return service.health.agent_performance(agent_id).model_dump()


@router.post("/agents/memory-proposals", response_model=OrgMemoryProposal, status_code=status.HTTP_201_CREATED)
def propose_memory(record: OrgMemoryProposal, service: AgentsService = Depends(get_agents_service)) -> OrgMemoryProposal:
    return service.memory_proposals.propose(record)


@router.get("/agents/memory-proposals")
def list_memory_proposals(
    state_filter: Optional[str] = Query(default=None, alias="state"),
    service: AgentsService = Depends(get_agents_service),
) -> List[dict]:
    return [p.model_dump() for p in service.memory_proposals.list(state=state_filter)]


@router.post("/agents/memory-proposals/{proposal_id}/review", response_model=OrgMemoryProposal)
def review_memory_proposal(proposal_id: str, payload: dict, service: AgentsService = Depends(get_agents_service)) -> OrgMemoryProposal:
    record = service.memory_proposals.review(proposal_id, action=str(payload.get("action") or ""), note=str(payload.get("note") or ""))
    if record is None:
        raise HTTPException(status_code=404, detail="Proposal not found.")
    return record