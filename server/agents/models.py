"""Multi-Agent Collaboration and Organizational Intelligence contracts.

Every organizational record carries identity, accountability, scope, privacy,
and audit metadata. Configured agents are distinct from active agents. Nothing
is fabricated: progress, consensus, and completion are derived from task state,
evidence, and validation only. Hidden reasoning and secrets are never stored.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_field() -> str:
    return datetime.now(timezone.utc).isoformat()

# ---- Enumerated taxonomies -------------------------------------------------

OrganizationMode = Literal["personal", "team"]
OrgState = Literal["draft", "enabled", "disabled", "archived"]

UnitType = Literal[
    "executive", "architecture", "software_engineering", "product", "design",
    "quality_assurance", "security", "infrastructure", "devops", "research",
    "documentation", "project_management", "operations", "memory_knowledge",
    "automation", "data", "mobile", "hardware", "communications", "team",
]

AgentState = Literal[
    "configured", "available", "active", "executing", "waiting", "blocked",
    "paused", "degraded", "unhealthy", "unavailable", "retired",
]
Availability = Literal["available", "busy", "blocked", "paused", "offline", "unavailable"]

MissionState = Literal[
    "draft", "awaiting_approval", "ready", "planning", "staffing", "active",
    "waiting", "awaiting_user_input", "awaiting_approval", "blocked", "paused",
    "reviewing", "validating", "completing", "completed", "completed_with_limitations",
    "failed", "cancelled", "timed_out", "archived",
]
MissionPriority = Literal["urgent", "high", "normal", "low", "backlog"]
MissionOutcome = Literal[
    "successful", "successful_with_limitations", "partially_successful",
    "unsuccessful", "cancelled", "timed_out", "blocked", "superseded",
]

TaskState = Literal[
    "not_started", "planning", "staffed", "executing", "waiting", "blocked",
    "reviewing", "validating", "completing", "complete", "incomplete", "failed",
    "cancelled",
]
TaskRisk = Literal["low", "medium", "high", "critical"]
WorkloadClass = Literal["analysis", "planning", "design", "implementation", "review", "verification", "operations"]

TaskRelationship = Literal[
    "depends_on", "blocks", "informs", "reviews", "validates", "produces_input_for",
    "consumes_output_from", "supersedes", "duplicates", "conflicts_with",
]

MessageType = Literal[
    "assignment", "acknowledgement", "status_update", "question",
    "clarification_request", "evidence_request", "evidence_response",
    "artifact_delivery", "handoff", "review_request", "review_response",
    "disagreement", "risk_escalation", "approval_request", "approval_response",
    "blocker_report", "dependency_update", "scope_change_request",
    "cancellation_notice", "completion_report",
]
ThreadKind = Literal[
    "direct", "task", "mission", "team", "review", "escalation", "artifact", "decision",
]

HandoffState = Literal["sent", "accepted", "clarification_requested", "rejected", "escalated", "cancelled"]

ArtifactType = Literal[
    "plan", "research_report", "code_patch", "test_result", "build_result",
    "architecture_diagram", "review_report", "documentation_draft",
    "decision_proposal", "risk_assessment", "migration_plan", "deployment_package",
    "screenshot", "preview", "diagnostic_bundle", "report", "note",
]
ValidationState = Literal["none", "pending", "in_progress", "passed", "failed", "unavailable"]

GateType = Literal[
    "architecture_review", "code_review", "security_review", "test_review",
    "accessibility_review", "performance_review", "documentation_review",
    "compliance_review", "user_acceptance", "build_validation", "release_readiness",
]
GateState = Literal[
    "not_ready", "ready", "in_review", "passed", "passed_with_conditions",
    "failed", "waived", "expired", "blocked", "cancelled",
]

ReviewRole = Literal[
    "planner", "implementer", "validator", "reviewer", "approver", "mission_leader", "specialist",
]

DisagreementState = Literal["open", "resolved", "escalated", "superseded", "deferred"]
ResolutionMethod = Literal[
    "evidence_review", "additional_investigation", "specialist_consultation",
    "controlled_comparison", "user_decision", "accepted_policy", "test_or_experiment",
    "deferred", "split_by_scope",
]

ConsensusMethod = Literal[
    "unanimous", "majority_recommendation", "weighted_specialist",
    "evidence_weighted", "reviewer_approval", "user_decision", "policy_determined",
]
ConsensusAuthority = Literal["advisory", "authoritative", "policy", "user"]

EscalationReason = Literal[
    "missing_user_decision", "permission_denied", "approval_required",
    "conflicting_requirements", "insufficient_evidence", "unavailable_model",
    "unavailable_tool", "resource_limit", "privacy_conflict", "security_risk",
    "task_dependency_failure", "repeated_validation_failure", "scope_expansion",
    "unresolved_disagreement", "suspected_prompt_injection",
    "potential_secret_exposure", "destructive_action", "external_unavailable",
]
EscalationSeverity = Literal["info", "warning", "critical", "emergency"]
EscalationState = Literal["open", "resolved", "expired", "cancelled", "superseded"]

BudgetKind = Literal[
    "mission_duration", "task_duration", "model_calls", "tokens", "tool_calls",
    "agent_count", "active_agents", "delegation_depth", "retry_count",
    "review_rounds", "debate_rounds", "context_size",
]
BudgetState = Literal["ok", "warning", "exhausted", "exempt"]

RoutingState = Literal["proposed", "selected", "active", "failed", "fallback", "cancelled"]

DetectionKind = Literal["deadlock", "loop", "stagnation"]
DetectionState = Literal["open", "paused", "resolved", "superseded"]

OrgHealthState = Literal[
    "healthy", "attention_required", "degraded", "blocked", "resource_constrained",
    "partially_unavailable", "unavailable", "unknown",
]

MemoryProposalState = Literal["proposed", "accepted", "rejected", "superseded"]

ApprovalState = Literal["pending", "approved", "denied", "expired", "cancelled"]

ReviewFindingSeverity = Literal["critical", "high", "medium", "low", "info"]

DebateState = Literal["open", "in_progress", "synthesized", "cancelled", "concluded"]

ConsultationState = Literal["requested", "in_progress", "responded", "cancelled", "expired"]

# ---- Base -------------------------------------------------------------

class StrictAgentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Provenance(StrictAgentModel):
    """Evidence-backed provenance. Never carries hidden reasoning."""
    kind: str = Field(min_length=1, max_length=80)
    source: str = Field(min_length=1, max_length=240)
    detail: str = ""
    recorded_at: str


# ---- Organization ------------------------------------------------------

class OrganizationRecord(StrictAgentModel):
    schema_version: Literal[1] = 1
    organization_id: str = Field(min_length=8, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    purpose: str = ""
    owner: str = "user"
    mode: OrganizationMode = "personal"
    state: OrgState = "enabled"
    default_mission_leader: Optional[str] = None
    default_escalation_path: str = "user"
    default_review_policy: str = "independent_when_high_risk"
    policy_version: int = 1
    created_at: str
    updated_at: str


class OrganizationalUnit(StrictAgentModel):
    unit_id: str = Field(min_length=8, max_length=80)
    unit_type: UnitType
    name: str = Field(min_length=1, max_length=160)
    purpose: str = ""
    parent_unit: Optional[str] = None
    leader: Optional[str] = None
    capabilities: Tuple[str, ...] = Field(default=(), max_length=64)
    escalation_target: Optional[str] = None
    supported_mission_types: Tuple[str, ...] = Field(default=(), max_length=32)
    enabled: bool = True
    created_at: str
    updated_at: str


class RoleDefinition(StrictAgentModel):
    role_id: str = Field(min_length=8, max_length=80)
    title: str = Field(min_length=1, max_length=160)
    purpose: str = ""
    responsibilities: Tuple[str, ...] = Field(default=(), max_length=64)
    required_capabilities: Tuple[str, ...] = Field(default=(), max_length=64)
    preferred_capabilities: Tuple[str, ...] = Field(default=(), max_length=64)
    allowed_workload_classes: Tuple[WorkloadClass, ...] = Field(default=(), max_length=16)
    preferred_model_profile: str = ""
    allowed_tools: Tuple[str, ...] = Field(default=(), max_length=64)
    prohibited_tools: Tuple[str, ...] = Field(default=(), max_length=64)
    required_review_relationships: Tuple[ReviewRole, ...] = Field(default=(), max_length=16)
    escalation_path: str = "user"
    maximum_delegation_depth: int = Field(ge=0, le=16, default=2)
    default_task_limits: Tuple[str, ...] = Field(default=(), max_length=32)
    quality_criteria: Tuple[str, ...] = Field(default=(), max_length=32)
    memory_access_policy: str = "scoped"
    privacy_restrictions: Tuple[str, ...] = Field(default=(), max_length=32)
    role_version: int = Field(ge=1, default=1)
    enabled: bool = True
    created_at: str
    updated_at: str


class CapabilityAssignment(StrictAgentModel):
    capability: str = Field(min_length=1, max_length=120)
    skill: str = Field(min_length=1, max_length=120)
    source: str = Field(min_length=1, max_length=240)
    confidence: Literal["configured", "observed", "inferred", "unverified"] = "configured"
    validation_state: Literal["unvalidated", "validated", "under_review"] = "unvalidated"
    model_dependency: str = ""
    tool_dependency: str = ""
    recent_success: bool = False
    recent_failure: bool = False
    review_date: Optional[str] = None


class AgentProfile(StrictAgentModel):
    schema_version: Literal[1] = 1
    agent_id: str = Field(min_length=8, max_length=80)
    display_name: str = Field(min_length=1, max_length=160)
    role_id: str = Field(min_length=8, max_length=80)
    department: Optional[str] = None
    team: Optional[str] = None
    status: AgentState = "configured"
    availability: Availability = "offline"
    capabilities: Tuple[str, ...] = Field(default=(), max_length=64)
    skills: Tuple[CapabilityAssignment, ...] = Field(default=(), max_length=128)
    model_preferences: Tuple[str, ...] = Field(default=(), max_length=16)
    runtime_restrictions: Tuple[str, ...] = Field(default=(), max_length=32)
    tool_permissions: Tuple[str, ...] = Field(default=(), max_length=64)
    project_restrictions: Tuple[str, ...] = Field(default=(), max_length=32)
    privacy_restrictions: Tuple[str, ...] = Field(default=(), max_length=32)
    memory_scope: str = "mission_scoped"
    maximum_workload: int = Field(ge=0, le=1024, default=4)
    current_mission: Optional[str] = None
    current_task: Optional[str] = None
    queue_depth: int = Field(ge=0, default=0)
    reliability_state: Literal[
        "insufficient_history", "reliable_for_workload", "mixed_results", "degraded", "under_review"
    ] = "insufficient_history"
    config_version: int = Field(ge=1, default=1)
    enabled: bool = True
    created_at: str
    updated_at: str


# ---- Missions ----------------------------------------------------------

class ResourceBudget(StrictAgentModel):
    mission_duration_minutes: Optional[int] = Field(default=None, ge=1, le=100000)
    task_duration_minutes: Optional[int] = Field(default=None, ge=1, le=100000)
    model_calls: Optional[int] = Field(default=None, ge=1, le=1000000)
    token_estimate: Optional[int] = Field(default=None, ge=1, le=100_000_000)
    tool_calls: Optional[int] = Field(default=None, ge=1, le=1000000)
    agent_count: Optional[int] = Field(default=None, ge=1, le=1024)
    delegation_depth: Optional[int] = Field(default=None, ge=1, le=16)
    retry_count: Optional[int] = Field(default=None, ge=0, le=64)
    review_rounds: Optional[int] = Field(default=None, ge=1, le=64)
    debate_rounds: Optional[int] = Field(default=None, ge=1, le=32)
    state: BudgetState = "ok"
    note: str = ""


class MissionCharter(StrictAgentModel):
    charter_id: str = Field(min_length=8, max_length=80)
    mission_id: str = Field(min_length=8, max_length=80)
    objective: str = Field(min_length=1, max_length=2000)
    business_value: str = ""
    success_criteria: Tuple[str, ...] = Field(default=(), max_length=32)
    non_goals: Tuple[str, ...] = Field(default=(), max_length=32)
    constraints: Tuple[str, ...] = Field(default=(), max_length=64)
    assumptions: Tuple[str, ...] = Field(default=(), max_length=32)
    known_evidence: Tuple[str, ...] = Field(default=(), max_length=64)
    unknowns: Tuple[str, ...] = Field(default=(), max_length=32)
    project_scope: Tuple[str, ...] = Field(default=(), max_length=32)
    privacy_scope: str = "mission_scoped"
    risk: TaskRisk = "low"
    required_capabilities: Tuple[str, ...] = Field(default=(), max_length=64)
    proposed_agents: Tuple[str, ...] = Field(default=(), max_length=64)
    proposed_tools: Tuple[str, ...] = Field(default=(), max_length=64)
    proposed_model_policy: str = "local_first"
    expected_artifacts: Tuple[str, ...] = Field(default=(), max_length=32)
    expected_validation: Tuple[str, ...] = Field(default=(), max_length=32)
    expected_user_decisions: Tuple[str, ...] = Field(default=(), max_length=32)
    budget: ResourceBudget = Field(default_factory=ResourceBudget)
    cancellation_behavior: str = "preserve_state"
    rollback_approach: str = ""
    approved: bool = False
    approved_by: str = ""
    version: int = Field(ge=1, default=1)
    created_at: str
    updated_at: str


class MissionPlan(StrictAgentModel):
    plan_id: str = Field(min_length=8, max_length=80)
    mission_id: str = Field(min_length=8, max_length=80)
    workstreams: Tuple[str, ...] = Field(default=(), max_length=32)
    task_ids: Tuple[str, ...] = Field(default=(), max_length=256)
    dependencies: Tuple[str, ...] = Field(default=(), max_length=512)
    parallel_opportunities: Tuple[str, ...] = Field(default=(), max_length=64)
    required_specialists: Tuple[str, ...] = Field(default=(), max_length=32)
    required_reviews: Tuple[str, ...] = Field(default=(), max_length=64)
    required_approvals: Tuple[str, ...] = Field(default=(), max_length=64)
    validation_gates: Tuple[str, ...] = Field(default=(), max_length=64)
    decision_points: Tuple[str, ...] = Field(default=(), max_length=64)
    rollback_points: Tuple[str, ...] = Field(default=(), max_length=64)
    uncertainty: Tuple[str, ...] = Field(default=(), max_length=32)
    likely_blockers: Tuple[str, ...] = Field(default=(), max_length=32)
    evidence_used: Tuple[str, ...] = Field(default=(), max_length=128)
    informed_by_memory: bool = False
    informed_by_repository: bool = False
    version: int = Field(ge=1, default=1)
    created_at: str


class MissionRecord(StrictAgentModel):
    schema_version: Literal[1] = 1
    mission_id: str = Field(min_length=8, max_length=80)
    title: str = Field(min_length=1, max_length=240)
    objective: str = Field(min_length=1, max_length=2000)
    owner: str = "user"
    mission_leader: Optional[str] = None
    sponsoring_user: str = "user"
    project: Optional[str] = None
    workspace: str = "personal"
    priority: MissionPriority = "normal"
    status: MissionState = "draft"
    scope: Tuple[str, ...] = Field(default=(), max_length=64)
    constraints: Tuple[str, ...] = Field(default=(), max_length=64)
    privacy_classification: str = "private"
    risk: TaskRisk = "low"
    approved_tools: Tuple[str, ...] = Field(default=(), max_length=64)
    prohibited_tools: Tuple[str, ...] = Field(default=(), max_length=64)
    model_policy: str = "local_first"
    budget: ResourceBudget = Field(default_factory=ResourceBudget)
    assigned_units: Tuple[str, ...] = Field(default=(), max_length=64)
    assigned_agents: Tuple[str, ...] = Field(default=(), max_length=64)
    required_reviewers: Tuple[str, ...] = Field(default=(), max_length=64)
    progress: Literal[
        "not_started", "planning", "staffed", "executing", "waiting", "blocked",
        "reviewing", "validating", "completing", "complete", "incomplete", "failed", "cancelled",
    ] = "not_started"
    health: Literal["healthy", "attention", "degraded", "blocked"] = "healthy"
    start_time: Optional[str] = None
    target_time: Optional[str] = None
    completion_time: Optional[str] = None
    outcome: Optional[MissionOutcome] = None
    final_outcome_summary: str = ""
    scope_change_count: int = Field(ge=0, default=0)
    created_at: str
    updated_at: str


class MissionTask(StrictAgentModel):
    task_id: str = Field(min_length=8, max_length=80)
    mission_id: str = Field(min_length=8, max_length=80)
    title: str = Field(min_length=1, max_length=240)
    objective: str = Field(min_length=1, max_length=2000)
    owner: Optional[str] = None
    assigned_agent: Optional[str] = None
    collaborators: Tuple[str, ...] = Field(default=(), max_length=32)
    project: Optional[str] = None
    scope: Tuple[str, ...] = Field(default=(), max_length=64)
    expected_inputs: Tuple[str, ...] = Field(default=(), max_length=64)
    expected_outputs: Tuple[str, ...] = Field(default=(), max_length=64)
    dependencies: Tuple[str, ...] = Field(default=(), max_length=64)
    blocking_dependencies: Tuple[str, ...] = Field(default=(), max_length=64)
    privacy_classification: str = "private"
    risk: TaskRisk = "low"
    tool_requirements: Tuple[str, ...] = Field(default=(), max_length=64)
    model_requirements: Tuple[str, ...] = Field(default=(), max_length=16)
    context_requirements: Tuple[str, ...] = Field(default=(), max_length=64)
    budget: ResourceBudget = Field(default_factory=ResourceBudget)
    iteration_limit: Optional[int] = Field(default=None, ge=1, le=1000)
    timeout_minutes: Optional[int] = Field(default=None, ge=1, le=100000)
    validation_requirements: Tuple[str, ...] = Field(default=(), max_length=64)
    review_requirements: Tuple[str, ...] = Field(default=(), max_length=64)
    approval_requirements: Tuple[str, ...] = Field(default=(), max_length=64)
    status: TaskState = "not_started"
    progress_note: str = ""
    final_result: str = ""
    failure_reason: str = ""
    retry_count: int = Field(ge=0, default=0)
    depth: int = Field(ge=0, le=16, default=0)
    created_at: str
    updated_at: str


class TaskDependency(StrictAgentModel):
    dependency_id: str = Field(min_length=8, max_length=80)
    mission_id: str = Field(min_length=8, max_length=80)
    source_task_id: str = Field(min_length=8, max_length=80)
    target_task_id: str = Field(min_length=8, max_length=80)
    relationship: TaskRelationship
    optional: bool = False
    created_at: str


class TaskGraph(StrictAgentModel):
    schema_version: Literal[1] = 1
    mission_id: str = Field(min_length=8, max_length=80)
    tasks: Tuple[MissionTask, ...] = Field(default=(), max_length=1024)
    dependencies: Tuple[TaskDependency, ...] = Field(default=(), max_length=2048)
    cycles: Tuple[Tuple[str, ...], ...] = Field(default=(), max_length=64)
    critical_path: Tuple[str, ...] = Field(default=(), max_length=256)
    parallel_groups: Tuple[Tuple[str, ...], ...] = Field(default=(), max_length=64)
    truncated: bool = False
    generated_at: str


class AssignmentExplanation(StrictAgentModel):
    task_id: str = Field(min_length=8, max_length=80)
    selected_agent: Optional[str] = None
    role_match: float = Field(ge=0.0, le=1.0)
    capability_match: float = Field(ge=0.0, le=1.0)
    model_match: bool = False
    permission_match: bool = False
    workload_state: str = ""
    rejected_alternatives: Tuple[str, ...] = Field(default=(), max_length=64)
    review_relationship: Optional[str] = None
    warnings: Tuple[str, ...] = Field(default=(), max_length=16)
    confidence: Literal["high", "medium", "low", "none"] = "none"
    reason: str = Field(min_length=1, max_length=400)


# ---- Collaboration -----------------------------------------------------

class CollaborationMessage(StrictAgentModel):
    message_id: str = Field(min_length=8, max_length=80)
    sender: str = Field(min_length=1, max_length=80)
    recipient: str = Field(min_length=1, max_length=80)
    mission_id: Optional[str] = None
    task_id: Optional[str] = None
    thread_kind: ThreadKind = "direct"
    message_type: MessageType
    content: str = Field(min_length=1, max_length=2000)
    payload: str = ""
    related_evidence: Tuple[str, ...] = Field(default=(), max_length=32)
    related_artifacts: Tuple[str, ...] = Field(default=(), max_length=32)
    priority: Literal["urgent", "high", "normal", "low"] = "normal"
    privacy_classification: str = "private"
    requires_acknowledgement: bool = False
    acknowledged: bool = False
    response_deadline: Optional[str] = None
    trace_id: str = Field(min_length=8, max_length=80)
    status: Literal["sent", "acknowledged", "read", "expired"] = "sent"
    redacted: bool = False
    created_at: str = Field(default_factory=_now_iso)
    expires_at: Optional[str] = None


class HandoffRecord(StrictAgentModel):
    handoff_id: str = Field(min_length=8, max_length=80)
    mission_id: str = Field(min_length=8, max_length=80)
    source_task_id: Optional[str] = None
    destination_task_id: Optional[str] = None
    sending_agent: str = Field(min_length=8, max_length=80)
    receiving_agent: str = Field(min_length=8, max_length=80)
    objective: str = Field(min_length=1, max_length=1000)
    completed_work: str = ""
    incomplete_work: str = ""
    artifacts: Tuple[str, ...] = Field(default=(), max_length=64)
    evidence: Tuple[str, ...] = Field(default=(), max_length=64)
    decisions: Tuple[str, ...] = Field(default=(), max_length=32)
    assumptions: Tuple[str, ...] = Field(default=(), max_length=32)
    risks: Tuple[str, ...] = Field(default=(), max_length=32)
    open_questions: Tuple[str, ...] = Field(default=(), max_length=32)
    recommended_next_action: str = ""
    required_validation: Tuple[str, ...] = Field(default=(), max_length=32)
    scope_limitations: Tuple[str, ...] = Field(default=(), max_length=32)
    privacy_classification: str = "private"
    state: HandoffState = "sent"
    response_note: str = ""
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class ArtifactRecord(StrictAgentModel):
    artifact_id: str = Field(min_length=8, max_length=80)
    artifact_type: ArtifactType
    title: str = Field(min_length=1, max_length=240)
    producer: str = Field(min_length=1, max_length=80)
    mission_id: Optional[str] = None
    task_id: Optional[str] = None
    project: Optional[str] = None
    version: int = Field(ge=1, default=1)
    storage_reference: str = Field(min_length=1, max_length=1024)
    content_hash: str = Field(min_length=64, max_length=64)
    privacy_classification: str = "private"
    authority: str = "proposed"
    review_state: Literal["unreviewed", "in_review", "approved", "rejected"] = "unreviewed"
    validation_state: ValidationState = "none"
    source_inputs: Tuple[str, ...] = Field(default=(), max_length=64)
    evidence: Tuple[str, ...] = Field(default=(), max_length=64)
    superseded_state: Literal["current", "superseded"] = "current"
    deletion_state: str = "active"
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class ReviewFinding(StrictAgentModel):
    finding_id: str = Field(min_length=8, max_length=80)
    review_id: str = Field(min_length=8, max_length=80)
    severity: ReviewFindingSeverity
    summary: str = Field(min_length=1, max_length=1000)
    evidence: Tuple[str, ...] = Field(default=(), max_length=64)
    required_action: str = ""
    resolution: Literal["open", "accepted", "dismissed", "resolved"] = "open"
    resolution_rationale: str = ""
    created_at: str = Field(default_factory=_now_iso)


class QualityGate(StrictAgentModel):
    gate_id: str = Field(min_length=8, max_length=80)
    mission_id: str = Field(min_length=8, max_length=80)
    task_id: Optional[str] = None
    gate_type: GateType
    required_reviewer_role: str = Field(min_length=1, max_length=80)
    independence_required: bool = True
    required_evidence: Tuple[str, ...] = Field(default=(), max_length=64)
    required_validation: Tuple[str, ...] = Field(default=(), max_length=64)
    pass_criteria: Tuple[str, ...] = Field(default=(), max_length=64)
    failure_criteria: Tuple[str, ...] = Field(default=(), max_length=64)
    waiver_policy: str = "no_waiver"
    state: GateState = "not_ready"
    reviewer: Optional[str] = None
    findings: Tuple[ReviewFinding, ...] = Field(default=(), max_length=128)
    resolution: str = ""
    approval: Optional[str] = None
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class ReviewRecord(StrictAgentModel):
    review_id: str = Field(min_length=8, max_length=80)
    mission_id: str = Field(min_length=8, max_length=80)
    task_id: Optional[str] = None
    gate_id: Optional[str] = None
    reviewer: str = Field(min_length=8, max_length=80)
    implementer: str = Field(min_length=8, max_length=80)
    artifacts: Tuple[str, ...] = Field(default=(), max_length=64)
    evidence: Tuple[str, ...] = Field(default=(), max_length=64)
    model: str = ""
    runtime: str = ""
    findings: Tuple[ReviewFinding, ...] = Field(default=(), max_length=128)
    independence: bool = True
    disclosure: str = ""
    conclusion: Literal["pass", "fail", "pass_with_conditions"] = "fail"
    confidence: Literal["high", "medium", "low"] = "medium"
    status: Literal["requested", "in_review", "completed", "cancelled"] = "requested"
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class DisagreementRecord(StrictAgentModel):
    disagreement_id: str = Field(min_length=8, max_length=80)
    mission_id: str = Field(min_length=8, max_length=80)
    task_id: Optional[str] = None
    participants: Tuple[str, ...] = Field(default=(), max_length=32)
    subject: str = Field(min_length=1, max_length=240)
    positions: Tuple[str, ...] = Field(default=(), max_length=16)
    evidence: Tuple[str, ...] = Field(default=(), max_length=64)
    assumptions: Tuple[str, ...] = Field(default=(), max_length=32)
    affected_decision: Optional[str] = None
    urgency: Literal["low", "normal", "high"] = "normal"
    state: DisagreementState = "open"
    resolution_method: Optional[ResolutionMethod] = None
    resolution_notes: str = ""
    escalated: bool = False
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class ConsensusResult(StrictAgentModel):
    consensus_id: str = Field(min_length=8, max_length=80)
    subject: str = Field(min_length=1, max_length=240)
    participants: Tuple[str, ...] = Field(default=(), max_length=64)
    method: ConsensusMethod
    positions: Tuple[str, ...] = Field(default=(), max_length=16)
    evidence: Tuple[str, ...] = Field(default=(), max_length=64)
    conclusion: str = Field(min_length=1, max_length=1000)
    authority: ConsensusAuthority = "advisory"
    dissent: Tuple[str, ...] = Field(default=(), max_length=32)
    abstentions: Tuple[str, ...] = Field(default=(), max_length=32)
    unresolved_concerns: Tuple[str, ...] = Field(default=(), max_length=32)
    final_decision_source: str = ""
    created_at: str = Field(default_factory=_now_iso)


class DebateRecord(StrictAgentModel):
    debate_id: str = Field(min_length=8, max_length=80)
    question: str = Field(min_length=1, max_length=1000)
    participants: Tuple[str, ...] = Field(default=(), max_length=16)
    max_rounds: int = Field(ge=1, le=32, default=4)
    max_tokens: int = Field(ge=100, le=1_000_000, default=20000)
    time_limit_minutes: int = Field(ge=1, le=100000, default=60)
    round_count: int = Field(ge=0, default=0)
    state: DebateState = "open"
    synthesis: str = ""
    escalation_path: str = "user"
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class ConsultationRecord(StrictAgentModel):
    consultation_id: str = Field(min_length=8, max_length=80)
    question: str = Field(min_length=1, max_length=1000)
    requester: str = Field(min_length=8, max_length=80)
    specialist: str = Field(min_length=8, max_length=80)
    context: str = ""
    evidence: Tuple[str, ...] = Field(default=(), max_length=64)
    constraints: Tuple[str, ...] = Field(default=(), max_length=32)
    required_expertise: Tuple[str, ...] = Field(default=(), max_length=32)
    tool_use_allowed: bool = False
    deadline: Optional[str] = None
    privacy_classification: str = "private"
    budget: ResourceBudget = Field(default_factory=ResourceBudget)
    response: str = ""
    conclusion: str = ""
    assumptions: Tuple[str, ...] = Field(default=(), max_length=32)
    confidence: Literal["high", "medium", "low"] = "medium"
    limitations: Tuple[str, ...] = Field(default=(), max_length=32)
    recommended_action: str = ""
    affected_risk: TaskRisk = "low"
    state: ConsultationState = "requested"
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class EscalationRecord(StrictAgentModel):
    escalation_id: str = Field(min_length=8, max_length=80)
    source: str = Field(min_length=1, max_length=80)
    mission_id: Optional[str] = None
    task_id: Optional[str] = None
    reason: EscalationReason
    severity: EscalationSeverity = "warning"
    evidence: Tuple[str, ...] = Field(default=(), max_length=64)
    attempted_resolutions: Tuple[str, ...] = Field(default=(), max_length=32)
    required_decision: str = Field(min_length=1, max_length=1000)
    options: Tuple[str, ...] = Field(default=(), max_length=16)
    consequence_of_delay: str = ""
    privacy_classification: str = "private"
    responsible_recipient: str = "user"
    state: EscalationState = "open"
    response: str = ""
    expires_at: Optional[str] = None
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class InterventionRecord(StrictAgentModel):
    intervention_id: str = Field(min_length=8, max_length=80)
    need: str = Field(min_length=1, max_length=1000)
    rationale: str = Field(min_length=1, max_length=1000)
    mission_id: Optional[str] = None
    task_id: Optional[str] = None
    options: Tuple[str, ...] = Field(default=(), max_length=16)
    recommended_option: str = ""
    evidence: Tuple[str, ...] = Field(default=(), max_length=64)
    risk: TaskRisk = "low"
    consequence: str = ""
    deadline: Optional[str] = None
    work_can_continue: bool = True
    state: ApprovalState = "pending"
    response: str = ""
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class ApprovalRecord(StrictAgentModel):
    approval_id: str = Field(min_length=8, max_length=80)
    requester: str = Field(min_length=1, max_length=80)
    mission_id: Optional[str] = None
    task_id: Optional[str] = None
    action: str = Field(min_length=1, max_length=240)
    rationale: str = ""
    evidence: Tuple[str, ...] = Field(default=(), max_length=64)
    risk: TaskRisk = "low"
    self_approval_blocked: bool = True
    state: ApprovalState = "pending"
    approver: str = ""
    expires_at: Optional[str] = None
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class ModelRoute(StrictAgentModel):
    route_id: str = Field(min_length=8, max_length=80)
    agent_id: str = Field(min_length=8, max_length=80)
    mission_id: Optional[str] = None
    task_id: Optional[str] = None
    required_capabilities: Tuple[str, ...] = Field(default=(), max_length=64)
    model: str = ""
    provider: str = "local"
    rationale: str = Field(min_length=1, max_length=400)
    tool_use_required: bool = False
    state: RoutingState = "proposed"
    disclosure: str = ""
    created_at: str
    updated_at: str


class DetectionEvent(StrictAgentModel):
    detection_id: str = Field(min_length=8, max_length=80)
    kind: DetectionKind
    mission_id: Optional[str] = None
    task_ids: Tuple[str, ...] = Field(default=(), max_length=32)
    agent_ids: Tuple[str, ...] = Field(default=(), max_length=32)
    detail: str = Field(min_length=1, max_length=1000)
    evidence: Tuple[str, ...] = Field(default=(), max_length=64)
    state: DetectionState = "open"
    resolution: str = ""
    created_at: str
    updated_at: str


class OrgMemoryProposal(StrictAgentModel):
    proposal_id: str = Field(min_length=8, max_length=80)
    mission_id: Optional[str] = None
    kind: Literal[
        "verified_outcome", "accepted_procedure", "staffing_pattern",
        "recurring_failure", "validated_review_criteria", "model_performance",
        "tool_reliability", "escalation_outcome", "resource_lesson",
        "specialist_knowledge", "accepted_decision",
    ]
    title: str = Field(min_length=1, max_length=240)
    content: str = Field(min_length=1, max_length=4000)
    proposer: str = Field(min_length=8, max_length=80)
    evidence: Tuple[str, ...] = Field(default=(), max_length=64)
    state: MemoryProposalState = "proposed"
    reviewer: Optional[str] = None
    review_note: str = ""
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class PerformanceSnapshot(StrictAgentModel):
    period: str
    agent_id: str = Field(min_length=8, max_length=80)
    tasks_completed: int = Field(ge=0)
    tasks_failed: int = Field(ge=0)
    cancellations: int = Field(ge=0)
    validation_pass_rate: float = Field(ge=0.0, le=1.0)
    review_acceptance_rate: float = Field(ge=0.0, le=1.0)
    rework_count: int = Field(ge=0)
    average_task_minutes: float = Field(ge=0.0)
    timeout_count: int = Field(ge=0)
    budget_overrun_count: int = Field(ge=0)
    tool_failure_count: int = Field(ge=0)
    model_failure_count: int = Field(ge=0)
    escalation_count: int = Field(ge=0)
    handoff_rejection_count: int = Field(ge=0)


class OrgHealthRecord(StrictAgentModel):
    schema_version: Literal[1] = 1
    state: OrgHealthState = "unknown"
    message: str = ""
    conditions: Tuple[str, ...] = Field(default=(), max_length=64)
    active_missions: int = Field(ge=0)
    blocked_missions: int = Field(ge=0)
    failed_missions: int = Field(ge=0)
    available_agents: int = Field(ge=0)
    overloaded_agents: int = Field(ge=0)
    deadlocks: int = Field(ge=0)
    stagnation_warnings: int = Field(ge=0)
    unreviewed_work: int = Field(ge=0)
    approval_backlog: int = Field(ge=0)
    unresolved_disagreements: int = Field(ge=0)
    open_escalations: int = Field(ge=0)
    memory_proposals_pending: int = Field(ge=0)
    generated_at: str


class AgentsOverview(StrictAgentModel):
    schema_version: Literal[1] = 1
    organization: OrganizationRecord
    units: Tuple[OrganizationalUnit, ...] = Field(default=(), max_length=512)
    roles: Tuple[RoleDefinition, ...] = Field(default=(), max_length=512)
    agents: Tuple[AgentProfile, ...] = Field(default=(), max_length=1024)
    missions: Tuple[MissionRecord, ...] = Field(default=(), max_length=256)
    health: OrgHealthRecord
    attention: Tuple[str, ...] = Field(default=(), max_length=32)
    generated_at: str


class MissionEnvelope(StrictAgentModel):
    mission: MissionRecord
    charter: Optional[MissionCharter] = None
    plan: Optional[MissionPlan] = None
    graph: Optional[TaskGraph] = None
    generated_at: str
