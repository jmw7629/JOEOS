"""Engineering Campaign platform contracts.

The campaign domain is the durable orchestration glue that activates the
existing agent fabric: it owns campaign state, work packages, attempts,
checkpoints, blockers, heartbeats, and the roadmap queue. It is authority-only
state; execution still flows through the existing runner/action/approval
planes. No capability is self-granted here.
"""

from __future__ import annotations

from typing import Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from ..models import StrictEngineeringModel

CampaignState = Literal[
    "proposed",
    "active",
    "paused",
    "blocked",
    "completed",
    "cancelled",
    "failed",
]
WorkPackageState = Literal[
    "queued",
    "eligible",
    "planning",
    "planned",
    "worktree_ready",
    "implementing",
    "implemented",
    "validating",
    "validated",
    "reviewing",
    "reviewed",
    "committing",
    "committed",
    "integrating",
    "integrated",
    "pushed",
    "completed",
    "blocked",
    "failed",
    "cancelled",
]
AttemptState = Literal["running", "succeeded", "failed", "abandoned"]
BlockerState = Literal["open", "resolved", "escalated"]
CheckpointKind = Literal["attempt", "stage", "manual"]
GateName = Literal[
    "eligibility",
    "plan",
    "implementation",
    "validation",
    "review",
    "commit",
    "integration",
    "push",
]
StageName = Literal[
    "queued",
    "eligibility",
    "plan",
    "worktree",
    "implement",
    "validate",
    "review",
    "commit",
    "integrate",
    "push",
    "complete",
]
BlockReason = Literal["worktree_conflict", "gate_failed", "watchdog_expired", "operator", "missing_requirement"]


class CampaignDefinition(StrictEngineeringModel):
    key: str = Field(min_length=1, max_length=120, pattern=r"^[a-z][a-z0-9-]*$")
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(default="", max_length=4000)
    repository_path: str = Field(min_length=1, max_length=1024)
    base_branch: str = Field(min_length=1, max_length=120)
    integration_branch: str = Field(min_length=1, max_length=120)
    autonomy_policy_key: str = Field(min_length=1, max_length=160)
    worktree_root: Optional[str] = Field(default=None, max_length=1024)
    max_parallel_packages: int = Field(default=1, ge=1, le=16)
    max_attempts_per_package: int = Field(default=3, ge=1, le=16)
    heartbeat_timeout_ms: int = Field(default=300_000, ge=10_000, le=7 * 24 * 3600 * 1000)
    blocking: bool = True


class CampaignRecord(StrictEngineeringModel):
    campaign_id: str = Field(min_length=8, max_length=40)
    key: str
    title: str
    description: str = ""
    repository_path: str
    base_branch: str
    integration_branch: str
    autonomy_policy_key: str
    state: CampaignState = "proposed"
    current_stage: StageName = "queued"
    worktree_root: Optional[str] = None
    max_parallel_packages: int = 1
    max_attempts_per_package: int = 3
    heartbeat_timeout_ms: int = 300_000
    revision: int = Field(ge=0)
    checkpoints: Tuple[str, ...] = Field(default=(), max_length=512)
    created_by: str = Field(min_length=1, max_length=80)
    created_at: str
    updated_at: str
    last_heartbeat_at: Optional[str] = None
    completion_summary: Optional[str] = None
    failure_reason: Optional[str] = None


class WorkPackageDefinition(StrictEngineeringModel):
    key: str = Field(min_length=1, max_length=120, pattern=r"^[a-z][a-z0-9-]*$")
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(default="", max_length=8000)
    acceptance_criteria: Tuple[str, ...] = Field(default=(), max_length=64)
    owner_agent_key: str = Field(min_length=1, max_length=120)
    verifier_agent_key: Optional[str] = Field(default=None, max_length=120)
    review_agent_key: Optional[str] = Field(default=None, max_length=120)
    dependencies: Tuple[str, ...] = Field(default=(), max_length=64)
    stage_order: Tuple[StageName, ...] = Field(default=(), max_length=16)
    risk: str = Field(default="low", pattern=r"^(informational|low|medium|high|critical)$")
    roadmap_order: int = Field(default=0, ge=0)
    priority: int = Field(default=100, ge=0, le=10000)


class WorkPackageRecord(StrictEngineeringModel):
    package_id: str = Field(min_length=8, max_length=40)
    campaign_id: str
    key: str
    title: str
    description: str = ""
    acceptance_criteria: Tuple[str, ...] = Field(default=(), max_length=64)
    owner_agent_key: str
    verifier_agent_key: Optional[str] = None
    review_agent_key: Optional[str] = None
    dependencies: Tuple[str, ...] = Field(default=(), max_length=64)
    stage_order: Tuple[StageName, ...] = Field(default=(), max_length=16)
    state: WorkPackageState = "queued"
    current_stage: StageName = "queued"
    attempts: int = Field(default=0, ge=0)
    roadmap_order: int = 0
    priority: int = 100
    risk: str = "low"
    error_detail: Optional[str] = None
    last_gate: Optional[GateName] = None
    checkpoint_revision: int = Field(default=0, ge=0)
    created_at: str
    updated_at: str


class EngineeringAttemptRecord(StrictEngineeringModel):
    attempt_id: str = Field(min_length=8, max_length=40)
    package_id: str
    campaign_id: str
    attempt_number: int = Field(ge=1)
    state: AttemptState = "running"
    started_by: str = Field(min_length=1, max_length=80)
    started_at: str
    finished_at: Optional[str] = None
    summary: Optional[str] = None
    evidence: Tuple[str, ...] = Field(default=(), max_length=256)


class EngineeringCheckpointRecord(StrictEngineeringModel):
    checkpoint_id: str = Field(min_length=8, max_length=40)
    campaign_id: str
    package_id: Optional[str] = None
    kind: CheckpointKind
    stage: StageName
    revision: int = Field(ge=0)
    state_snapshot_digest: str = Field(min_length=64, max_length=64)
    note: str = Field(default="", max_length=2000)
    created_at: str


class EngineeringBlockerRecord(StrictEngineeringModel):
    blocker_id: str = Field(min_length=8, max_length=40)
    campaign_id: str
    package_id: Optional[str] = None
    reason: BlockReason
    detail: str = Field(default="", max_length=4000)
    state: BlockerState = "open"
    created_at: str
    resolved_at: Optional[str] = None
    resolved_by: Optional[str] = None
    resolution: Optional[str] = None


class WatchdogHeartbeatRecord(StrictEngineeringModel):
    heartbeat_id: str = Field(min_length=8, max_length=40)
    campaign_id: str
    recorded_at: str
    worker: str = Field(min_length=1, max_length=80)
    detail: str = Field(default="", max_length=2000)


class RoadmapEntry(StrictEngineeringModel):
    key: str
    title: str
    description: str = ""
    owner_agent_key: str
    verifier_agent_key: Optional[str] = None
    review_agent_key: Optional[str] = None
    dependencies: Tuple[str, ...] = Field(default=(), max_length=64)
    acceptance_criteria: Tuple[str, ...] = Field(default=(), max_length=64)
    roadmap_order: int = Field(default=0, ge=0)
    priority: int = Field(default=100, ge=0, le=10000)
    risk: str = Field(default="low", pattern=r"^(informational|low|medium|high|critical)$")
    stage_order: Tuple[StageName, ...] = Field(default=(), max_length=16)
    enabled: bool = True
    source: str = Field(default="manual", max_length=80)


class RoadmapEnvelope(StrictEngineeringModel):
    schema_version: Literal[1] = 1
    campaign_key: Optional[str] = None
    entries: Tuple[RoadmapEntry, ...] = Field(max_length=2048)
    loaded_from: Optional[str] = None
    warnings: Tuple[str, ...] = Field(default=(), max_length=32)


class GateResult(StrictEngineeringModel):
    gate: GateName
    passed: bool
    detail: str = ""
    evidence: Tuple[str, ...] = Field(default=(), max_length=64)
    blocker_created: bool = False


class StageTransition(StrictEngineeringModel):
    campaign_id: str
    package_id: str
    from_stage: StageName
    to_stage: StageName
    allowed: bool
    detail: str = ""
