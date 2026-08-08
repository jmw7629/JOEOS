"""Wire models for the P3B control plane API."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProviderRequest(WireModel):
    key: str = Field(min_length=1, max_length=120)
    display_name: str = Field(min_length=1, max_length=160)
    provider_type: str = Field(min_length=1, max_length=60)
    location: str = Field(pattern="^(local|private_remote|approved_cloud)$")
    transport: str = "http"
    endpoint_reference: str = ""
    auth_reference_type: str = "none"
    streaming: bool = False
    tool_calling: bool = False
    structured_output: bool = False
    context_window: int = 0
    privacy_class: str = "restricted"
    allowed_data_classes: str = "restricted"


class ProviderStateRequest(WireModel):
    status: str = Field(pattern="^(active|disabled)$")
    health: str = Field(pattern="^(unknown|checking|healthy|degraded|unavailable|incompatible|disabled|unauthorized)$")


class ModelRequest(WireModel):
    provider_id: UUID
    key: str = Field(min_length=1, max_length=120)
    display_name: str = Field(min_length=1, max_length=160)
    model_identifier: str = Field(min_length=1, max_length=160)
    streaming: bool = False
    structured_output: bool = False
    tool_calling: bool = False
    vision: bool = False
    reasoning: bool = False
    context_limit: int = 0
    output_limit: int = 0
    privacy_class: str = "restricted"
    allowed_data_classes: str = "restricted"


class ModelStateRequest(WireModel):
    status: str = Field(pattern="^(active|disabled)$")


class AgentRequest(WireModel):
    key: str = Field(min_length=1, max_length=120)
    display_name: str = Field(min_length=1, max_length=160)
    description: str = ""
    purpose: str = ""
    system_instructions: str = ""
    allowed_tools: str = ""
    denied_tools: str = ""
    required_capabilities: str = ""
    max_delegation_depth: int = 0
    max_parallel_tasks: int = 1
    max_runtime_ms: int = 0
    max_token_budget: int = 0
    data_boundary: str = "restricted"
    approval_policy: str = "backend"
    default_provider_policy: str = "backend"
    default_model_policy: str = "backend"


class AgentUpdateRequest(AgentRequest):
    revision: int = Field(ge=1)


class AgentStateRequest(WireModel):
    status: str = Field(pattern="^(active|disabled)$")


class AgentRunRequest(WireModel):
    conversation_id: UUID
    message_id: UUID
    model_preference: Optional[str] = None
    parent_run_id: Optional[UUID] = None
    delegation_depth: int = 0
    objective: str = ""


class DelegateRequest(WireModel):
    agent_id: UUID
    objective: str = Field(min_length=1, max_length=4000)


class TaskNodeRequest(WireModel):
    key: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=240)
    objective: str = Field(min_length=1, max_length=4000)
    assigned_agent_id: Optional[UUID] = None
    dependencies: str = ""


class TaskGraphRequest(WireModel):
    tasks: List[TaskNodeRequest] = Field(min_length=1, max_length=64)


class ToolRequest(WireModel):
    key: str = Field(min_length=1, max_length=120)
    display_name: str = Field(min_length=1, max_length=160)
    description: str = ""
    version: str = "1.0.0"
    category: str = Field(pattern="^(read_only|calculation|retrieval|communication|filesystem|source_control|deployment|infrastructure|secrets|financial|physical_device|remote_control|administrative)$")
    input_schema: Dict[str, Any]
    output_schema: Dict[str, Any] = {}
    capability_requirements: str = ""
    risk: str = Field(pattern="^(informational|low|medium|high|critical)$")
    side_effect: str = Field(pattern="^(none|local_ephemeral|local_persistent|external_reversible|external_irreversible|financial|destructive|privileged)$")
    approval_policy: str = "backend"
    execution_availability: str = "unavailable"
    executor_type: str = "none"
    data_class_limits: str = "restricted"
    target_constraints: str = ""


class ProposeRequest(WireModel):
    tool_key: str
    parameters: Dict[str, Any]
    target: str = Field(min_length=1, max_length=512)
    conversation_id: Optional[UUID] = None
    conversation_run_id: Optional[UUID] = None
    agent_run_id: Optional[UUID] = None
    task_id: Optional[UUID] = None
    original_request: str = ""


class ApprovalChallengeRequest(WireModel):
    proposal_id: UUID
    approval_request_id: UUID
    policy_decision_id: UUID
    decision: str = Field(pattern="^(approve|deny)$")
    device_id: UUID


class ApprovalDecisionRequest(WireModel):
    proposal_id: UUID
    decision: str = Field(pattern="^(approve|deny)$")
    reason: str = ""
    signature: Optional[str] = None
    challenge_id: Optional[UUID] = None
    device_id: Optional[UUID] = None


class CouncilRequest(WireModel):
    name: str = Field(min_length=1, max_length=160)
    purpose: str = ""
    member_agent_ids: List[UUID]
    chair_agent_id: Optional[UUID] = None
    quorum_rule: str = "majority"
    maximum_rounds: int = 1
    disagreement_policy: str = "record"
    output_schema: str = ""


class CouncilRunRequest(WireModel):
    objective: str = Field(min_length=1, max_length=4000)
    conversation_id: Optional[UUID] = None
    message_id: Optional[UUID] = None
