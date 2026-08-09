"""Versioned engineering autonomy policies.

A policy binds a campaign to concrete, bounded operating constraints. The
policy is deny-by-default: the campaign service checks every constraint before
advancing and never exceeds a limit present in the policy. Policies are pure
data; there is no code execution. Unknown/unsupported keys are rejected.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError

# Explicit autonomy levels for the self-build campaign.
#
# LEVEL 0 — PLAN ONLY: agents inspect and propose work; no source modification.
# LEVEL 1 — IMPLEMENT + VERIFY: isolated feature branches/worktrees + tests;
#           no automatic merge or deployment.
# LEVEL 2 — SAFE AUTONOMOUS DEVELOPMENT: implement, verify, commit, push
#           feature branches, integrate low-risk work; production deployment
#           remains gated.
# LEVEL 3 — CONTINUOUS SAFE BUILD: continuously select + complete low-risk
#           roadmap items including safe deployment when policy allows.
#
# Default is LEVEL 2. Levels never grant new authority; they only gate which
# policy constraints are already available. Escalation requires explicit
# operator action and is refused by the service when policy forbids it.
AUTONOMY_LEVEL_PLAN_ONLY = 0
AUTONOMY_LEVEL_IMPLEMENT_VERIFY = 1
AUTONOMY_LEVEL_SAFE_DEVELOPMENT = 2
AUTONOMY_LEVEL_CONTINUOUS_BUILD = 3

DEFAULT_AUTONOMY_LEVEL = AUTONOMY_LEVEL_SAFE_DEVELOPMENT

AUTONOMY_LEVEL_NAMES = {
    0: "plan_only",
    1: "implement_verify",
    2: "safe_autonomous_development",
    3: "continuous_safe_build",
}


def validate_autonomy_level(level: int) -> int:
    """Validate a requested autonomy level, clamping nothing; raises on invalid."""
    if int(level) not in AUTONOMY_LEVEL_NAMES:
        raise ValueError("autonomy level must be one of %s" % sorted(AUTONOMY_LEVEL_NAMES))
    return int(level)


def autonomy_level_name(level: int) -> str:
    return AUTONOMY_LEVEL_NAMES.get(validate_autonomy_level(level), "unknown")


class StrictPolicyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ProviderConstraint(StrictPolicyModel):
    key: str = Field(min_length=1, max_length=120)
    location: str = Field(default="local", pattern=r"^(local|remote)$")
    allowed: bool = True


class AutonomyLimits(StrictPolicyModel):
    max_parallel_packages: int = Field(default=1, ge=1, le=32)
    max_attempts_per_package: int = Field(default=3, ge=1, le=16)
    max_runtime_ms_per_package: int = Field(default=30 * 60 * 1000, ge=60_000, le=24 * 3600 * 1000)
    heartbeat_timeout_ms: int = Field(default=300_000, ge=10_000, le=7 * 24 * 3600 * 1000)
    max_open_blockers: int = Field(default=8, ge=1, le=64)
    allow_worktree_isolation: bool = True
    allow_ff_integration_only: bool = True
    allow_agent_branch_only: bool = True
    allow_push_after_gate: bool = True


class AutonomyPolicy(StrictPolicyModel):
    policy_key: str = Field(min_length=1, max_length=160, pattern=r"^joeos\.engineering\.[a-z0-9._-]+$")
    version: str = Field(min_length=1, max_length=32)
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(default="", max_length=4000)
    providers: Tuple[ProviderConstraint, ...] = Field(default=(), max_length=32)
    allowed_agent_keys: Tuple[str, ...] = Field(default=(), max_length=64)
    denied_agent_keys: Tuple[str, ...] = Field(default=(), max_length=64)
    protected_branches: Tuple[str, ...] = Field(default=(), max_length=32)
    allowed_remotes: Tuple[str, ...] = Field(default=(), max_length=16)
    limits: AutonomyLimits = AutonomyLimits()


SUPPORTED_POLICY_KEYS = {
    "joeos.engineering.ai-rebuild.v1",
}

AI_REBUILD_V1 = AutonomyPolicy(
    policy_key="joeos.engineering.ai-rebuild.v1",
    version="1",
    title="AI Rebuild autonomous engineering campaign",
    description=(
        "Binds the JOEOS ai-rebuild campaign to local-only providers, the eight "
        "engineering role profiles, protected main/master branches, the origin "
        "remote, worktree isolation, and ff-only integration after the gate. "
        "Deny-by-default: nothing beyond these constraints is permitted."
    ),
    providers=(
        ProviderConstraint(key="lemonade-local", location="local", allowed=True),
    ),
    allowed_agent_keys=(
        "engineering.director", "engineering.architect", "engineering.builder",
        "engineering.verification", "engineering.applebuild",
        "engineering.securityreviewer", "engineering.release", "engineering.watchdog",
    ),
    protected_branches=("main", "master", "production", "release"),
    allowed_remotes=("origin",),
    limits=AutonomyLimits(
        max_parallel_packages=1,
        max_attempts_per_package=3,
        max_runtime_ms_per_package=30 * 60 * 1000,
        heartbeat_timeout_ms=300_000,
        max_open_blockers=8,
        allow_worktree_isolation=True,
        allow_ff_integration_only=True,
        allow_agent_branch_only=True,
        allow_push_after_gate=True,
    ),
)


POLICY_CATALOG: Dict[str, AutonomyPolicy] = {
    AI_REBUILD_V1.policy_key: AI_REBUILD_V1,
}


def get_autonomy_policy(policy_key: str) -> Optional[AutonomyPolicy]:
    return POLICY_CATALOG.get(policy_key)


def registered_policy_keys() -> Tuple[str, ...]:
    return tuple(sorted(POLICY_CATALOG))


def parse_autonomy_policy(policy_key: str, document: Dict[str, Any]) -> AutonomyPolicy:
    """Build an AutonomyPolicy from a validated mapping. Rejects unknown keys."""
    if not isinstance(document, dict):
        raise ValueError("policy document must be a mapping")
    payload = dict(document)
    payload["policy_key"] = policy_key
    try:
        return AutonomyPolicy(**payload)
    except ValidationError as exc:
        raise ValueError("invalid autonomy policy: %s" % exc) from exc
