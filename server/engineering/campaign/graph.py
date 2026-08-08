"""Multi-agent engineering graph for the campaign domain.

Maps each campaign stage to the responsible engineering role agent and produces
a durable execution plan per work package. The graph is data + pure functions;
it does not execute anything. Stage handlers (injected into CampaignService)
are what actually run work, always under the runner.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from .models import StageName, WorkPackageRecord
from .state_machine import CAMPAIGN_STAGES, DEFAULT_STAGE_ORDER, gate_for_stage

# Canonical stage -> responsible role agent key.
STAGE_AGENT: Dict[StageName, str] = {
    "queued": "engineering.director",
    "eligibility": "engineering.director",
    "plan": "engineering.architect",
    "worktree": "engineering.builder",
    "implement": "engineering.builder",
    "validate": "engineering.verification",
    "review": "engineering.securityreviewer",
    "commit": "engineering.builder",
    "integrate": "engineering.release",
    "push": "engineering.release",
    "complete": "engineering.director",
}

# Optional Apple build stage inserted between validate and review when the
# package touches the iOS client.
APPLE_BUILD_STAGE: StageName = "implement"


def role_for_stage(stage: StageName) -> str:
    return STAGE_AGENT.get(stage, "engineering.director")


def stage_needs_apple_build(package: WorkPackageRecord) -> bool:
    """A package needs the Apple build stage when it references the mobile
    client tree or declares an apple owner/verifier."""
    if package.owner_agent_key == "engineering.applebuild":
        return True
    if package.verifier_agent_key == "engineering.applebuild":
        return True
    text = (package.title + " " + package.description).lower()
    return "mobile" in text or "ios" in text or "swift" in text or "xcode" in text


def build_stage_order(package: WorkPackageRecord, *, include_apple: Optional[bool] = None) -> Tuple[StageName, ...]:
    """Compute the concrete stage order for a package.

    Respects an explicit package stage order when present; otherwise returns the
    canonical default, optionally inserting the Apple build stage after the
    implementation stage.
    """
    if package.stage_order:
        return tuple(package.stage_order)
    base = list(DEFAULT_STAGE_ORDER)
    if include_apple is None:
        include_apple = stage_needs_apple_build(package)
    if include_apple:
        if APPLE_BUILD_STAGE in base:
            return tuple(base)
        # Insert a dedicated apple stage after validate, before review.
        order = list(base)
        validate_index = order.index("validate")
        order.insert(validate_index + 1, "implement")
        return tuple(order)
    return tuple(base)


def plan_package_stages(package: WorkPackageRecord) -> List[Dict]:
    """Return the per-stage execution plan for a package (data only)."""
    order = build_stage_order(package)
    plan: List[Dict] = []
    for index, stage in enumerate(order):
        gate = gate_for_stage(stage)
        plan.append({
            "stage": stage,
            "order": index,
            "role_agent": role_for_stage(stage),
            "gate": gate,
            "final_stage": index == len(order) - 1,
        })
    return plan


def agents_required(package: WorkPackageRecord) -> List[str]:
    """All role agents whose participation this package requires."""
    required: List[str] = []
    for step in plan_package_stages(package):
        agent = step["role_agent"]
        if agent not in required:
            required.append(agent)
    if package.verifier_agent_key and package.verifier_agent_key not in required:
        required.append(package.verifier_agent_key)
    if package.review_agent_key and package.review_agent_key not in required:
        required.append(package.review_agent_key)
    return required
