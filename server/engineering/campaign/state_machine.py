"""Durable state machine for engineering campaigns and work packages.

Pure functions, no I/O. The campaign state machine is the authoritative
transition table: any transition must pass `can_transition` before any storage
mutation occurs, and each transition is recorded against a checkpoint revision.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Iterable, List, Optional, Tuple

from .models import (
    BlockReason,
    CampaignState,
    StageName,
    WorkPackageState,
)

CAMPAIGN_STAGES: Tuple[StageName, ...] = (
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
)

STAGE_TO_PACKAGE_STATE: Dict[StageName, WorkPackageState] = {
    "queued": "queued",
    "eligibility": "eligible",
    "plan": "planning",
    "worktree": "planned",
    "implement": "implementing",
    "validate": "validating",
    "review": "reviewing",
    "commit": "committing",
    "integrate": "integrating",
    "push": "pushed",
    "complete": "completed",
}

# Campaign states: active campaigns may advance packages; completed/cancelled/failed are terminal.
CAMPAIGN_FORWARD: Dict[CampaignState, FrozenSet[CampaignState]] = {
    "proposed": frozenset({"active", "cancelled"}),
    "active": frozenset({"paused", "blocked", "completed", "cancelled", "failed"}),
    "paused": frozenset({"active", "cancelled"}),
    "blocked": frozenset({"active", "cancelled"}),
    "completed": frozenset(),
    "cancelled": frozenset(),
    "failed": frozenset(),
}

# Work package transitions between package-level states.
PACKAGE_FORWARD: Dict[WorkPackageState, FrozenSet[WorkPackageState]] = {
    "queued": frozenset({"eligible", "blocked", "cancelled", "failed"}),
    "eligible": frozenset({"planning", "blocked", "cancelled", "failed"}),
    "planning": frozenset({"planned", "blocked", "cancelled", "failed"}),
    "planned": frozenset({"worktree_ready", "implementing", "blocked", "cancelled", "failed"}),
    "worktree_ready": frozenset({"implementing", "blocked", "cancelled", "failed"}),
    "implementing": frozenset({"implemented", "blocked", "cancelled", "failed"}),
    "implemented": frozenset({"validating", "blocked", "cancelled", "failed"}),
    "validating": frozenset({"validated", "blocked", "cancelled", "failed"}),
    "validated": frozenset({"reviewing", "blocked", "cancelled", "failed"}),
    "reviewing": frozenset({"reviewed", "blocked", "cancelled", "failed"}),
    "reviewed": frozenset({"committing", "blocked", "cancelled", "failed"}),
    "committing": frozenset({"committed", "blocked", "cancelled", "failed"}),
    "committed": frozenset({"integrating", "blocked", "cancelled", "failed"}),
    "integrating": frozenset({"integrated", "blocked", "cancelled", "failed"}),
    "integrated": frozenset({"pushed", "blocked", "cancelled", "failed"}),
    "pushed": frozenset({"completed", "blocked", "cancelled", "failed"}),
    "completed": frozenset(),
    "blocked": frozenset({"queued", "eligible", "planning", "planned", "worktree_ready",
                          "implementing", "implemented", "validating", "validated",
                          "reviewing", "reviewed", "committing", "committed",
                          "integrating", "integrated", "pushed"}),
    "failed": frozenset({"queued", "eligible", "planning", "planned", "worktree_ready",
                         "implementing", "implemented", "validating", "validated",
                         "reviewing", "reviewed", "committing", "committed",
                         "integrating", "integrated", "pushed"}),
    "cancelled": frozenset(),
}

DEFAULT_STAGE_ORDER: Tuple[StageName, ...] = (
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
)

STAGE_TO_GATE = {
    "eligibility": "eligibility",
    "plan": "plan",
    "worktree": "plan",
    "implement": "implementation",
    "validate": "validation",
    "review": "review",
    "commit": "commit",
    "integrate": "integration",
    "push": "push",
    "complete": "push",
}


def normalize_stage_order(stage_order: Iterable[str]) -> Tuple[StageName, ...]:
    """Normalize a requested stage order to a valid StageName tuple."""
    known = set(CAMPAIGN_STAGES)
    return tuple(s for s in stage_order if s in known and s != "queued") or DEFAULT_STAGE_ORDER


def can_advance(stage: StageName, stage_order: Tuple[StageName, ...]) -> bool:
    """Whether `stage` is the last executable stage in the order (the following
    transition is the terminal `complete` stage)."""
    if stage == "complete":
        return True
    if not stage_order:
        return False
    if stage_order[-1] == "complete" and len(stage_order) >= 2:
        return stage == stage_order[-2]
    return stage == stage_order[-1]


def next_stage(stage: StageName, stage_order: Tuple[StageName, ...]) -> Optional[StageName]:
    if can_advance(stage, stage_order):
        return "complete" if stage != "complete" else None
    try:
        index = stage_order.index(stage)
        return stage_order[index + 1]
    except ValueError:
        return stage_order[0] if stage_order else None


def package_state_for_stage(stage: StageName) -> WorkPackageState:
    return STAGE_TO_PACKAGE_STATE.get(stage, "queued")


def gate_for_stage(stage: StageName) -> Optional[str]:
    return STAGE_TO_GATE.get(stage)


def campaign_transition(state: CampaignState, target: CampaignState) -> bool:
    return target in CAMPAIGN_FORWARD.get(state, frozenset())


def package_transition(state: WorkPackageState, target: WorkPackageState) -> bool:
    return target in PACKAGE_FORWARD.get(state, frozenset())


def validate_stage_sequence(stage_order: Tuple[StageName, ...]) -> Optional[str]:
    """Confirm a stage order follows the canonical campaign ordering.

    Returns an error message when the order is invalid, else None. The order may
    skip stages (a package with no Apple build stage may omit worktree/implement
    reordering), but must never move a later canonical stage before an earlier one
    unless that earlier stage is omitted.
    """
    canonical_index = {s: i for i, s in enumerate(CAMPAIGN_STAGES)}
    last_index = -1
    for stage in stage_order:
        index = canonical_index[stage]
        if index < last_index:
            return "stage order violates canonical campaign ordering: %s after %s" % (
                stage,
                _stage_at(canonical_index, last_index),
            )
        last_index = index
    return None


def _stage_at(canonical_index: Dict[str, int], index: int) -> str:
    for stage, value in canonical_index.items():
        if value == index:
            return stage
    return "?"


def resolve_dependencies(packages: Dict[str, Iterable[str]]) -> Optional[str]:
    """Resolve a dependency graph `{package_key: dependency_keys}`; returns an
    error message on unknown dependency or cycle, else None."""
    edges: Dict[str, set] = {}
    for key in packages:
        edges[key] = set()
    for key in packages:
        for dep in packages[key]:
            if dep not in packages:
                return "unknown dependency: %s depends on %s" % (key, dep)
            edges.setdefault(dep, set()).add(key)
    visited: set = set()
    path: set = set()

    def visit(node: str) -> bool:
        if node in path:
            return True
        if node in visited:
            return False
        path.add(node)
        for child in edges.get(node, set()):
            if visit(child):
                return True
        path.remove(node)
        visited.add(node)
        return False

    for node in list(edges):
        if visit(node):
            return "dependency cycle detected at %s" % node
    return None

# Sentinel to keep BlockReason import used in docs of the module.
_BLOCK_REASONS: Tuple[BlockReason, ...] = ("worktree_conflict", "gate_failed", "watchdog_expired", "operator", "missing_requirement")
