"""HTTP API for the P3B control plane.

All routes require a live application session and are enforced by explicit
capabilities in the service layer. Client-supplied ids never expand scope;
cross-workspace access is denied.
"""

from __future__ import annotations

from typing import Dict
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status

from server.identity.authority_router import require_application_session

from .models import (
    AgentRequest,
    AgentRunRequest,
    AgentStateRequest,
    AgentUpdateRequest,
    ApprovalChallengeRequest,
    ApprovalDecisionRequest,
    CouncilRequest,
    CouncilRunRequest,
    DelegateRequest,
    ModelRequest,
    ModelStateRequest,
    ProposeRequest,
    ProviderRequest,
    ProviderStateRequest,
    ToolRequest,
)
from .service import ActionError, ActionService


router = APIRouter(prefix="/api/v1/control", tags=["control"])


def get_action_service(request: Request) -> ActionService:
    service = getattr(request.app.state, "action_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "control_unavailable", "message": "The control plane is not initialized."},
        )
    return service


def _raise_action_error(error: ActionError) -> None:
    raise HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": error.public_message},
    ) from error


def _run(service: ActionService, principal: Dict, operation) -> Dict:
    try:
        return operation(principal)
    except ActionError as error:
        _raise_action_error(error)


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------


@router.get("/overview")
def control_overview(
    principal: Dict = Depends(require_application_session),
    service: ActionService = Depends(get_action_service),
):
    return _run(service, principal, lambda p: service.overview(p))


# ---------------------------------------------------------------------------
# Providers / models
# ---------------------------------------------------------------------------


@router.get("/providers")
def list_providers(
    principal: Dict = Depends(require_application_session),
    service: ActionService = Depends(get_action_service),
):
    return _run(service, principal, lambda p: {"providers": service.list_providers(p)})


@router.post("/providers", status_code=status.HTTP_201_CREATED)
def register_provider(
    payload: ProviderRequest,
    principal: Dict = Depends(require_application_session),
    service: ActionService = Depends(get_action_service),
):
    return _run(service, principal, lambda p: service.register_provider(p, **payload.model_dump()))


@router.post("/providers/{provider_id}/state")
def set_provider_state(
    provider_id: UUID,
    payload: ProviderStateRequest,
    principal: Dict = Depends(require_application_session),
    service: ActionService = Depends(get_action_service),
):
    return _run(service, principal, lambda p: {
        "updated": service.set_provider_status(p, provider_id, payload.status, payload.health)
    })


@router.get("/models")
def list_models(
    principal: Dict = Depends(require_application_session),
    service: ActionService = Depends(get_action_service),
):
    return _run(service, principal, lambda p: {"models": service.list_models(p)})


@router.post("/models", status_code=status.HTTP_201_CREATED)
def register_model(
    payload: ModelRequest,
    principal: Dict = Depends(require_application_session),
    service: ActionService = Depends(get_action_service),
):
    return _run(service, principal, lambda p: service.register_model(p, **payload.model_dump()))


@router.post("/models/{model_id}/state")
def set_model_state(
    model_id: UUID,
    payload: ModelStateRequest,
    principal: Dict = Depends(require_application_session),
    service: ActionService = Depends(get_action_service),
):
    return _run(service, principal, lambda p: {"updated": service.set_model_status(p, model_id, payload.status)})


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------


@router.get("/agents")
def list_agents(
    principal: Dict = Depends(require_application_session),
    service: ActionService = Depends(get_action_service),
):
    return _run(service, principal, lambda p: {"agents": service.list_agents(p)})


@router.post("/agents", status_code=status.HTTP_201_CREATED)
def create_agent(
    payload: AgentRequest,
    principal: Dict = Depends(require_application_session),
    service: ActionService = Depends(get_action_service),
):
    return _run(service, principal, lambda p: service.create_agent(p, **payload.model_dump()))


@router.get("/agents/{agent_id}")
def get_agent(
    agent_id: UUID,
    principal: Dict = Depends(require_application_session),
    service: ActionService = Depends(get_action_service),
):
    return _run(service, principal, lambda p: service.get_agent(p, agent_id))


@router.patch("/agents/{agent_id}")
def update_agent(
    agent_id: UUID,
    payload: AgentUpdateRequest,
    principal: Dict = Depends(require_application_session),
    service: ActionService = Depends(get_action_service),
):
    changes = payload.model_dump()
    revision = changes.pop("revision")
    return _run(service, principal, lambda p: service.update_agent(p, agent_id, revision, **changes))


@router.post("/agents/{agent_id}/state")
def set_agent_state(
    agent_id: UUID,
    payload: AgentStateRequest,
    principal: Dict = Depends(require_application_session),
    service: ActionService = Depends(get_action_service),
):
    return _run(service, principal, lambda p: {"updated": service.set_agent_status(p, agent_id, payload.status)})


@router.get("/agents/{agent_id}/versions")
def list_agent_versions(
    agent_id: UUID,
    principal: Dict = Depends(require_application_session),
    service: ActionService = Depends(get_action_service),
):
    return _run(service, principal, lambda p: {"versions": service.list_agent_versions(p, agent_id)})


@router.post("/agents/{agent_id}/runs", status_code=status.HTTP_202_ACCEPTED)
def start_agent_run(
    agent_id: UUID,
    payload: AgentRunRequest,
    principal: Dict = Depends(require_application_session),
    service: ActionService = Depends(get_action_service),
):
    return _run(service, principal, lambda p: service.start_agent_run(
        p, agent_id=agent_id, conversation_id=payload.conversation_id,
        message_id=payload.message_id, model_preference=payload.model_preference,
        parent_run_id=payload.parent_run_id, delegation_depth=payload.delegation_depth,
        objective=payload.objective,
    ))


@router.get("/agents/{agent_id}/runs")
def list_agent_runs(
    agent_id: UUID,
    principal: Dict = Depends(require_application_session),
    service: ActionService = Depends(get_action_service),
):
    return _run(service, principal, lambda p: {"runs": service.list_agent_runs(p, agent_id)})


@router.get("/runs/{run_id}")
def get_agent_run(
    run_id: UUID,
    principal: Dict = Depends(require_application_session),
    service: ActionService = Depends(get_action_service),
):
    return _run(service, principal, lambda p: service.get_agent_run(p, run_id))


@router.post("/runs/{run_id}/execute", status_code=status.HTTP_200_OK)
async def execute_agent_run(
    run_id: UUID,
    principal: Dict = Depends(require_application_session),
    service: ActionService = Depends(get_action_service),
):
    try:
        return await service.execute_agent_run(principal, run_id)
    except ActionError as error:
        _raise_action_error(error)


@router.post("/runs/{run_id}/delegate", status_code=status.HTTP_200_OK)
async def delegate_agent_run(
    run_id: UUID,
    payload: DelegateRequest,
    principal: Dict = Depends(require_application_session),
    service: ActionService = Depends(get_action_service),
):
    try:
        return await service.delegate_agent_run(
            principal, parent_run_id=run_id,
            child_agent_id=payload.agent_id, objective=payload.objective,
        )
    except ActionError as error:
        _raise_action_error(error)


@router.get("/runs/{run_id}/delegations")
def list_run_delegations(
    run_id: UUID,
    principal: Dict = Depends(require_application_session),
    service: ActionService = Depends(get_action_service),
):
    return _run(service, principal, lambda p: {"delegations": service.list_run_delegations(p, run_id)})


@router.post("/runs/{run_id}/cancel")
def cancel_agent_run(
    run_id: UUID,
    principal: Dict = Depends(require_application_session),
    service: ActionService = Depends(get_action_service),
):
    return _run(service, principal, lambda p: {"cancelled": service.cancel_agent_run(p, run_id)})


@router.get("/runs/{run_id}/tasks")
def list_run_tasks(
    run_id: UUID,
    principal: Dict = Depends(require_application_session),
    service: ActionService = Depends(get_action_service),
):
    return _run(service, principal, lambda p: {"tasks": service.list_run_tasks(p, run_id)})


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@router.get("/tools")
def list_tools(
    principal: Dict = Depends(require_application_session),
    service: ActionService = Depends(get_action_service),
):
    return _run(service, principal, lambda p: {"tools": service.list_tools(p)})


@router.post("/tools", status_code=status.HTTP_201_CREATED)
def register_tool(
    payload: ToolRequest,
    principal: Dict = Depends(require_application_session),
    service: ActionService = Depends(get_action_service),
):
    return _run(service, principal, lambda p: service.register_tool(p, **payload.model_dump()))


# ---------------------------------------------------------------------------
# Proposals / policy / approvals
# ---------------------------------------------------------------------------


@router.get("/proposals")
def list_proposals(
    state: str = "",
    principal: Dict = Depends(require_application_session),
    service: ActionService = Depends(get_action_service),
):
    return _run(service, principal, lambda p: {"proposals": service.list_proposals(p, state or None)})


@router.post("/proposals", status_code=status.HTTP_201_CREATED)
def propose_action(
    payload: ProposeRequest,
    principal: Dict = Depends(require_application_session),
    service: ActionService = Depends(get_action_service),
):
    return _run(service, principal, lambda p: service.propose_action(p, **payload.model_dump()))


@router.get("/proposals/{proposal_id}")
def get_proposal(
    proposal_id: UUID,
    principal: Dict = Depends(require_application_session),
    service: ActionService = Depends(get_action_service),
):
    return _run(service, principal, lambda p: service.get_proposal(p, proposal_id))


@router.post("/proposals/{proposal_id}/revoke")
def revoke_proposal(
    proposal_id: UUID,
    principal: Dict = Depends(require_application_session),
    service: ActionService = Depends(get_action_service),
):
    return _run(service, principal, lambda p: {"revoked": service.revoke_proposal(p, proposal_id)})


@router.get("/policy/{decision_id}")
def get_policy_decision(
    decision_id: UUID,
    principal: Dict = Depends(require_application_session),
    service: ActionService = Depends(get_action_service),
):
    return _run(service, principal, lambda p: service.get_policy_decision(p, decision_id))


@router.get("/approvals")
def list_approvals(
    principal: Dict = Depends(require_application_session),
    service: ActionService = Depends(get_action_service),
):
    return _run(service, principal, lambda p: {
        "approvals": service.list_approvals(p)
    })


@router.get("/approvals/{approval_id}")
def get_approval(
    approval_id: UUID,
    principal: Dict = Depends(require_application_session),
    service: ActionService = Depends(get_action_service),
):
    return _run(service, principal, lambda p: service.get_approval(p, approval_id))


@router.post("/approvals/challenge")
def create_approval_challenge(
    payload: ApprovalChallengeRequest,
    principal: Dict = Depends(require_application_session),
    service: ActionService = Depends(get_action_service),
):
    return _run(service, principal, lambda p: service.create_approval_challenge(
        p, proposal_id=payload.proposal_id, approval_request_id=payload.approval_request_id,
        policy_decision_id=payload.policy_decision_id, requested_decision=payload.decision,
        approver_device_id=payload.device_id,
    ))


@router.post("/approvals/{approval_id}/decide")
def decide_approval(
    approval_id: UUID,
    payload: ApprovalDecisionRequest,
    principal: Dict = Depends(require_application_session),
    service: ActionService = Depends(get_action_service),
):
    return _run(service, principal, lambda p: service.submit_approval_decision(
        p, proposal_id=payload.proposal_id, approval_request_id=approval_id,
        decision=payload.decision, reason=payload.reason,
        signature_b64url=payload.signature, challenge_id=payload.challenge_id,
        approver_device_id=payload.device_id,
    ))


# ---------------------------------------------------------------------------
# Council
# ---------------------------------------------------------------------------


@router.get("/councils")
def list_councils(
    principal: Dict = Depends(require_application_session),
    service: ActionService = Depends(get_action_service),
):
    return _run(service, principal, lambda p: {"councils": service.list_councils(p)})


@router.post("/councils", status_code=status.HTTP_201_CREATED)
def create_council(
    payload: CouncilRequest,
    principal: Dict = Depends(require_application_session),
    service: ActionService = Depends(get_action_service),
):
    return _run(service, principal, lambda p: service.create_council(
        p, name=payload.name, purpose=payload.purpose, member_agent_ids=payload.member_agent_ids,
        chair_agent_id=payload.chair_agent_id, quorum_rule=payload.quorum_rule,
        maximum_rounds=payload.maximum_rounds, disagreement_policy=payload.disagreement_policy,
        output_schema=payload.output_schema,
    ))


@router.post("/councils/{council_id}/runs", status_code=status.HTTP_202_ACCEPTED)
async def run_council(
    council_id: UUID,
    payload: CouncilRunRequest,
    principal: Dict = Depends(require_application_session),
    service: ActionService = Depends(get_action_service),
):
    try:
        return await service.run_council(
            principal, council_id=council_id, objective=payload.objective,
            conversation_id=payload.conversation_id, message_id=payload.message_id,
        )
    except ActionError as error:
        _raise_action_error(error)


@router.get("/councils/runs/{run_id}")
def get_council_run(
    run_id: UUID,
    principal: Dict = Depends(require_application_session),
    service: ActionService = Depends(get_action_service),
):
    return _run(service, principal, lambda p: service.get_council_run(p, run_id))
