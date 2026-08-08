"""Engineering Campaign platform: durable orchestration of the agent fabric."""

from .models import (
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
from .service import (
    BLOCKER_RESOLVE_CAP,
    CAMPAIGN_CANCEL_CAP,
    CAMPAIGN_MANAGE_CAP,
    CAMPAIGN_PAUSE_CAP,
    CAMPAIGN_READ_CAP,
    CAMPAIGN_START_CAP,
    PACKAGE_MANAGE_CAP,
    PACKAGE_READ_CAP,
    CampaignError,
    CampaignService,
)
from .state_machine import (
    can_advance,
    next_stage,
    normalize_stage_order,
    package_state_for_stage,
    validate_stage_sequence,
)
from .storage import CampaignStore
from .router import router as campaign_router
from .graph import (
    agents_required,
    build_stage_order,
    plan_package_stages,
    role_for_stage,
    stage_needs_apple_build,
)
from .worker import CampaignWorker

__all__ = [
    "BLOCKER_RESOLVE_CAP",
    "CAMPAIGN_CANCEL_CAP",
    "CAMPAIGN_MANAGE_CAP",
    "CAMPAIGN_PAUSE_CAP",
    "CAMPAIGN_READ_CAP",
    "CAMPAIGN_START_CAP",
    "PACKAGE_MANAGE_CAP",
    "PACKAGE_READ_CAP",
    "CampaignDefinition",
    "CampaignError",
    "CampaignRecord",
    "CampaignService",
    "CampaignStore",
    "CampaignWorker",
    "EngineeringAttemptRecord",
    "EngineeringBlockerRecord",
    "EngineeringCheckpointRecord",
    "RoadmapEnvelope",
    "RoadmapEntry",
    "WatchdogHeartbeatRecord",
    "WorkPackageDefinition",
    "WorkPackageRecord",
    "agents_required",
    "build_stage_order",
    "campaign_router",
    "can_advance",
    "next_stage",
    "normalize_stage_order",
    "package_state_for_stage",
    "plan_package_stages",
    "role_for_stage",
    "stage_needs_apple_build",
    "validate_stage_sequence",
]
