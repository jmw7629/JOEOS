"""Multi-Agent Collaboration and Organizational Intelligence Platform.

Local-first, evidence-based multi-agent organization: units, roles and agents,
charters, planning, task graphs with dependency enforcement, assignment with
explanations, structured messaging, handoffs, artifacts, reviews and quality
gates, disagreements and consensus, debates and consultations, escalations,
interventions, approvals (no self-approval), budget governance, local-first
model routing, deadlock/loop/stagnation detection, organizational health,
performance telemetry, and organogram memory proposals.

Configured agents are profiles, not silently running intelligence. Everything
is derived from stored, authoritative state. Hidden reasoning and secrets are
never stored.
"""

from .budget import BudgetService
from .collaboration import CollaborationService
from .detection import DetectionService
from .governance import GovernanceService
from .health import HealthService
from .memory_proposals import MemoryProposalService
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
    PerformanceSnapshot,
    QualityGate,
    ResourceBudget,
    ReviewFinding,
    ReviewRecord,
    RoleDefinition,
    TaskDependency,
    TaskGraph,
)
from .missions import MissionService
from .organization import OrganizationService
from .router import router as agents_router
from .routing import RoutingService
from .service import AgentsService
from .storage import AgentsStorage

__all__ = [
    "AgentsService",
    "AgentsStorage",
    "agents_router",
    "AgentProfile",
    "AgentsOverview",
    "ApprovalRecord",
    "ArtifactRecord",
    "AssignmentExplanation",
    "BudgetService",
    "CollaborationMessage",
    "CollaborationService",
    "ConsensusResult",
    "ConsultationRecord",
    "DebateRecord",
    "DetectionEvent",
    "DetectionService",
    "DisagreementRecord",
    "EscalationRecord",
    "GovernanceService",
    "HandoffRecord",
    "HealthService",
    "InterventionRecord",
    "MemoryProposalService",
    "MissionCharter",
    "MissionEnvelope",
    "MissionPlan",
    "MissionRecord",
    "MissionService",
    "MissionTask",
    "ModelRoute",
    "OrgHealthRecord",
    "OrgMemoryProposal",
    "OrganizationRecord",
    "OrganizationService",
    "OrganizationalUnit",
    "PerformanceSnapshot",
    "QualityGate",
    "ResourceBudget",
    "ReviewFinding",
    "ReviewRecord",
    "RoleDefinition",
    "RoutingService",
    "TaskDependency",
    "TaskGraph",
]