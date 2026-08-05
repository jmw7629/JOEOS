"""Durable SQLite storage for the authoritative agent and action-governance
control plane (Phase P3B).

Provider and model definitions, agent profiles with immutable versions, agent
runs and tasks, the tool catalog, immutable action proposals, policy decisions,
approval requests/decisions/challenges, and advisory council runs. All records
use opaque UUID identifiers and revision counters. Providers/models/tools are
authoritative definitions; credentials are never stored here.
"""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional
from uuid import UUID

PROVIDER_HEALTH = ("unknown", "checking", "healthy", "degraded", "unavailable", "incompatible", "disabled", "unauthorized")
PROVIDER_TYPES = ("ollama", "openai_compatible", "lemonade", "anthropic", "gemini", "generic")
LOCATION = ("local", "private_remote", "approved_cloud")
AGENT_STATUSES = ("active", "disabled")
RUN_STATUSES = (
    "queued", "running", "waiting_for_tool", "waiting_for_approval",
    "approved_awaiting_executor", "succeeded", "failed", "cancelled", "interrupted",
)
TASK_STATES = (
    "pending", "ready", "running", "waiting_for_dependency", "waiting_for_approval",
    "blocked", "succeeded", "failed", "cancelled", "interrupted",
)
TOOL_CATEGORIES = (
    "read_only", "calculation", "retrieval", "communication", "filesystem",
    "source_control", "deployment", "infrastructure", "secrets", "financial",
    "physical_device", "remote_control", "administrative",
)
SIDE_EFFECT = (
    "none", "local_ephemeral", "local_persistent", "external_reversible",
    "external_irreversible", "financial", "destructive", "privileged",
)
RISK = ("informational", "low", "medium", "high", "critical")
PROPOSAL_STATES = (
    "proposed", "validating", "policy_denied", "approval_required",
    "approved_awaiting_executor", "denied", "expired", "superseded", "revoked",
    "cancelled", "execution_unavailable", "executing", "succeeded", "failed",
)
APPROVAL_REQUEST_STATUSES = ("pending", "approved", "denied", "expired", "revoked")
APPROVAL_DECISIONS = ("approve", "deny")


def now_ms() -> int:
    import time
    return int(time.time() * 1000)


def new_id() -> UUID:
    return uuid.uuid4()


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProviderRecord:
    id: UUID
    key: str
    display_name: str
    provider_type: str
    location: str
    transport: str
    endpoint_reference: str
    auth_reference_type: str
    status: str
    health: str
    streaming: bool
    tool_calling: bool
    structured_output: bool
    context_window: int
    privacy_class: str
    allowed_data_classes: str
    created_at: int
    updated_at: int
    revision: int


@dataclass(frozen=True)
class ModelRecord:
    id: UUID
    provider_id: UUID
    key: str
    display_name: str
    model_identifier: str
    status: str
    capabilities: str
    context_limit: int
    output_limit: int
    streaming: bool
    structured_output: bool
    tool_calling: bool
    vision: bool
    reasoning: bool
    privacy_class: str
    allowed_data_classes: str
    cost_note: str
    created_at: int
    updated_at: int
    revision: int


@dataclass(frozen=True)
class AgentProfileRecord:
    id: UUID
    organization_id: UUID
    workspace_id: Optional[UUID]
    key: str
    display_name: str
    description: str
    purpose: str
    status: str
    system_instructions: str
    instruction_version: int
    default_provider_policy: str
    default_model_policy: str
    allowed_tools: str
    denied_tools: str
    required_capabilities: str
    max_delegation_depth: int
    max_parallel_tasks: int
    max_runtime_ms: int
    max_token_budget: int
    data_boundary: str
    memory_policy: str
    approval_policy: str
    created_by: UUID
    created_at: int
    updated_at: int
    revision: int


@dataclass(frozen=True)
class AgentVersionRecord:
    version_id: UUID
    agent_id: UUID
    configuration_digest: str
    created_by: UUID
    created_at: int
    superseded: bool


@dataclass(frozen=True)
class AgentRunRecord:
    id: UUID
    conversation_id: UUID
    message_id: UUID
    agent_id: UUID
    agent_version_id: UUID
    provider_id: Optional[UUID]
    model_id: Optional[UUID]
    status: str
    parent_run_id: Optional[UUID]
    delegation_depth: int
    requested_by: UUID
    started_at: Optional[int]
    completed_at: Optional[int]
    cancellation: str
    failure: str
    token_usage: int
    trace_id: str
    revision: int


@dataclass(frozen=True)
class AgentTaskRecord:
    id: UUID
    run_id: UUID
    parent_task_id: Optional[UUID]
    title: str
    objective: str
    state: str
    assigned_agent_id: Optional[UUID]
    dependencies: str
    output_reference: str
    failure: str
    created_at: int
    started_at: Optional[int]
    completed_at: Optional[int]
    revision: int


@dataclass(frozen=True)
class ToolRecord:
    id: UUID
    key: str
    display_name: str
    description: str
    version: str
    category: str
    input_schema: str
    output_schema: str
    capability_requirements: str
    risk: str
    side_effect: str
    approval_policy: str
    execution_availability: str
    executor_type: str
    data_class_limits: str
    target_constraints: str
    timeout_policy: str
    idempotency_policy: str
    status: str
    created_at: int
    updated_at: int
    revision: int


@dataclass(frozen=True)
class ActionProposalRecord:
    id: UUID
    organization_id: UUID
    workspace_id: UUID
    conversation_id: Optional[UUID]
    conversation_run_id: Optional[UUID]
    agent_run_id: Optional[UUID]
    task_id: Optional[UUID]
    proposer_user_id: Optional[UUID]
    proposer_agent_id: Optional[UUID]
    tool_id: UUID
    tool_version: str
    action_type: str
    parameters: str
    canonical_target: str
    summary: str
    expected_effect: str
    reversibility: str
    risk: str
    required_capabilities: str
    requested_at: int
    expires_at: int
    state: str
    proposal_version: int
    previous_proposal_id: Optional[UUID]
    payload_digest: str
    policy_snapshot_id: Optional[UUID]
    trace_id: str
    revision: int
    original_request: str


@dataclass(frozen=True)
class PolicyDecisionRecord:
    id: UUID
    proposal_id: UUID
    result: str
    reason_codes: str
    explanation: str
    required_capabilities: str
    required_approval_count: int
    separation_of_duties: bool
    step_up_required: str
    expiration: int
    policy_version: str
    policy_snapshot: str
    policy_digest: str
    evaluated_at: int
    revision: int


@dataclass(frozen=True)
class ApprovalRequestRecord:
    id: UUID
    proposal_id: UUID
    proposal_digest: str
    policy_decision_id: UUID
    required_capability: str
    required_approval_count: int
    separation_of_duties: bool
    step_up_required: str
    status: str
    created_at: int
    expires_at: int
    revision: int


@dataclass(frozen=True)
class ApprovalDecisionRecord:
    id: UUID
    approval_request_id: UUID
    proposal_id: UUID
    proposal_digest: str
    decision: str
    approver_user_id: UUID
    approver_device_id: UUID
    approver_session_id: UUID
    approver_organization_id: UUID
    approver_workspace_id: UUID
    auth_strength: str
    step_up_evidence: str
    reason: str
    decided_at: int
    revocation_state: str
    decision_digest: str
    revision: int


@dataclass(frozen=True)
class ApprovalChallengeRecord:
    id: UUID
    proposal_id: UUID
    proposal_digest: str
    policy_decision_id: UUID
    approval_request_id: UUID
    approver_user_id: UUID
    approver_device_id: UUID
    organization_id: UUID
    workspace_id: UUID
    requested_decision: str
    risk: str
    nonce: str
    issued_at: int
    expires_at: int
    state: str
    signed_message: str


@dataclass(frozen=True)
class CouncilDefinitionRecord:
    id: UUID
    organization_id: UUID
    workspace_id: Optional[UUID]
    name: str
    purpose: str
    member_agents: str
    chair_agent: Optional[UUID]
    quorum_rule: str
    maximum_rounds: int
    disagreement_policy: str
    output_schema: str
    status: str
    revision: int


@dataclass(frozen=True)
class CouncilRunRecord:
    id: UUID
    conversation_id: Optional[UUID]
    message_id: Optional[UUID]
    council_definition_id: UUID
    council_snapshot: str
    state: str
    member_run_ids: str
    rounds: int
    final_recommendation: str
    dissents: str
    proposed_action_ids: str
    created_at: int
    completed_at: Optional[int]
    trace_id: str
    revision: int


class SQLiteControlRepository:
    """Single SQLite repository for the P3B control plane."""

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory

    def prepare(self) -> None:
        with self._connection_factory() as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS control_providers (
                    id TEXT PRIMARY KEY,
                    key TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    provider_type TEXT NOT NULL,
                    location TEXT NOT NULL CHECK(location IN ('local','private_remote','approved_cloud')),
                    transport TEXT NOT NULL DEFAULT 'http',
                    endpoint_reference TEXT NOT NULL DEFAULT '',
                    auth_reference_type TEXT NOT NULL DEFAULT 'none',
                    status TEXT NOT NULL CHECK(status IN ('active','disabled')),
                    health TEXT NOT NULL CHECK(health IN ('unknown','checking','healthy','degraded','unavailable','incompatible','disabled','unauthorized')),
                    streaming INTEGER NOT NULL DEFAULT 0,
                    tool_calling INTEGER NOT NULL DEFAULT 0,
                    structured_output INTEGER NOT NULL DEFAULT 0,
                    context_window INTEGER NOT NULL DEFAULT 0,
                    privacy_class TEXT NOT NULL DEFAULT 'restricted',
                    allowed_data_classes TEXT NOT NULL DEFAULT 'restricted',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    revision INTEGER NOT NULL CHECK(revision >= 1)
                );

                CREATE TABLE IF NOT EXISTS control_models (
                    id TEXT PRIMARY KEY,
                    provider_id TEXT NOT NULL REFERENCES control_providers(id),
                    key TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    model_identifier TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('active','disabled')),
                    capabilities TEXT NOT NULL DEFAULT '',
                    context_limit INTEGER NOT NULL DEFAULT 0,
                    output_limit INTEGER NOT NULL DEFAULT 0,
                    streaming INTEGER NOT NULL DEFAULT 0,
                    structured_output INTEGER NOT NULL DEFAULT 0,
                    tool_calling INTEGER NOT NULL DEFAULT 0,
                    vision INTEGER NOT NULL DEFAULT 0,
                    reasoning INTEGER NOT NULL DEFAULT 0,
                    privacy_class TEXT NOT NULL DEFAULT 'restricted',
                    allowed_data_classes TEXT NOT NULL DEFAULT 'restricted',
                    cost_note TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    revision INTEGER NOT NULL CHECK(revision >= 1),
                    UNIQUE(provider_id, key)
                );

                CREATE TABLE IF NOT EXISTS control_agents (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    workspace_id TEXT,
                    key TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    purpose TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL CHECK(status IN ('active','disabled')),
                    system_instructions TEXT NOT NULL DEFAULT '',
                    instruction_version INTEGER NOT NULL DEFAULT 1,
                    default_provider_policy TEXT NOT NULL DEFAULT 'backend',
                    default_model_policy TEXT NOT NULL DEFAULT 'backend',
                    allowed_tools TEXT NOT NULL DEFAULT '',
                    denied_tools TEXT NOT NULL DEFAULT '',
                    required_capabilities TEXT NOT NULL DEFAULT '',
                    max_delegation_depth INTEGER NOT NULL DEFAULT 0,
                    max_parallel_tasks INTEGER NOT NULL DEFAULT 1,
                    max_runtime_ms INTEGER NOT NULL DEFAULT 0,
                    max_token_budget INTEGER NOT NULL DEFAULT 0,
                    data_boundary TEXT NOT NULL DEFAULT 'restricted',
                    memory_policy TEXT NOT NULL DEFAULT 'ephemeral',
                    approval_policy TEXT NOT NULL DEFAULT 'backend',
                    created_by TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    revision INTEGER NOT NULL CHECK(revision >= 1),
                    UNIQUE(organization_id, workspace_id, key)
                );

                CREATE TABLE IF NOT EXISTS control_agent_versions (
                    version_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL REFERENCES control_agents(id),
                    configuration_digest TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    superseded INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS control_agent_runs (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL REFERENCES control_agents(id),
                    agent_version_id TEXT NOT NULL REFERENCES control_agent_versions(version_id),
                    provider_id TEXT,
                    model_id TEXT,
                    status TEXT NOT NULL CHECK(status IN (
                        'queued','running','waiting_for_tool','waiting_for_approval',
                        'approved_awaiting_executor','succeeded','failed','cancelled','interrupted'
                    )),
                    parent_run_id TEXT,
                    delegation_depth INTEGER NOT NULL DEFAULT 0,
                    requested_by TEXT NOT NULL,
                    started_at INTEGER,
                    completed_at INTEGER,
                    cancellation TEXT NOT NULL DEFAULT '',
                    failure TEXT NOT NULL DEFAULT '',
                    token_usage INTEGER NOT NULL DEFAULT 0,
                    trace_id TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1
                );

                CREATE INDEX IF NOT EXISTS idx_control_runs_conversation
                ON control_agent_runs(conversation_id, status);

                CREATE TABLE IF NOT EXISTS control_agent_tasks (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES control_agent_runs(id),
                    parent_task_id TEXT,
                    title TEXT NOT NULL,
                    objective TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL CHECK(state IN (
                        'pending','ready','running','waiting_for_dependency','waiting_for_approval',
                        'blocked','succeeded','failed','cancelled','interrupted'
                    )),
                    assigned_agent_id TEXT,
                    dependencies TEXT NOT NULL DEFAULT '',
                    output_reference TEXT NOT NULL DEFAULT '',
                    failure TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    started_at INTEGER,
                    completed_at INTEGER,
                    revision INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS control_tools (
                    id TEXT PRIMARY KEY,
                    key TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    version TEXT NOT NULL,
                    category TEXT NOT NULL,
                    input_schema TEXT NOT NULL,
                    output_schema TEXT NOT NULL DEFAULT '',
                    capability_requirements TEXT NOT NULL DEFAULT '',
                    risk TEXT NOT NULL CHECK(risk IN ('informational','low','medium','high','critical')),
                    side_effect TEXT NOT NULL CHECK(side_effect IN (
                        'none','local_ephemeral','local_persistent','external_reversible',
                        'external_irreversible','financial','destructive','privileged'
                    )),
                    approval_policy TEXT NOT NULL DEFAULT 'backend',
                    execution_availability TEXT NOT NULL DEFAULT 'unavailable',
                    executor_type TEXT NOT NULL DEFAULT 'none',
                    data_class_limits TEXT NOT NULL DEFAULT 'restricted',
                    target_constraints TEXT NOT NULL DEFAULT '',
                    timeout_policy TEXT NOT NULL DEFAULT '',
                    idempotency_policy TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL CHECK(status IN ('active','disabled')),
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    revision INTEGER NOT NULL CHECK(revision >= 1)
                );

                CREATE TABLE IF NOT EXISTS control_action_proposals (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    conversation_id TEXT,
                    conversation_run_id TEXT,
                    agent_run_id TEXT,
                    task_id TEXT,
                    proposer_user_id TEXT,
                    proposer_agent_id TEXT,
                    tool_id TEXT NOT NULL REFERENCES control_tools(id),
                    tool_version TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    parameters TEXT NOT NULL,
                    canonical_target TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    expected_effect TEXT NOT NULL DEFAULT '',
                    reversibility TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    required_capabilities TEXT NOT NULL DEFAULT '',
                    requested_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    state TEXT NOT NULL CHECK(state IN (
                        'proposed','validating','policy_denied','approval_required',
                        'approved_awaiting_executor','denied','expired','superseded',
                        'revoked','cancelled','execution_unavailable','executing',
                        'succeeded','failed'
                    )),
                    proposal_version INTEGER NOT NULL DEFAULT 1,
                    previous_proposal_id TEXT,
                    payload_digest TEXT NOT NULL,
                    policy_snapshot_id TEXT,
                    trace_id TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1,
                    original_request TEXT NOT NULL DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_control_proposals_workspace
                ON control_action_proposals(workspace_id, state);

                CREATE TABLE IF NOT EXISTS control_policy_decisions (
                    id TEXT PRIMARY KEY,
                    proposal_id TEXT NOT NULL REFERENCES control_action_proposals(id),
                    result TEXT NOT NULL,
                    reason_codes TEXT NOT NULL,
                    explanation TEXT NOT NULL DEFAULT '',
                    required_capabilities TEXT NOT NULL DEFAULT '',
                    required_approval_count INTEGER NOT NULL DEFAULT 0,
                    separation_of_duties INTEGER NOT NULL DEFAULT 0,
                    step_up_required TEXT NOT NULL DEFAULT 'none',
                    expiration INTEGER NOT NULL DEFAULT 0,
                    policy_version TEXT NOT NULL,
                    policy_snapshot TEXT NOT NULL,
                    policy_digest TEXT NOT NULL,
                    evaluated_at INTEGER NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS control_approval_requests (
                    id TEXT PRIMARY KEY,
                    proposal_id TEXT NOT NULL REFERENCES control_action_proposals(id),
                    proposal_digest TEXT NOT NULL,
                    policy_decision_id TEXT NOT NULL REFERENCES control_policy_decisions(id),
                    required_capability TEXT NOT NULL,
                    required_approval_count INTEGER NOT NULL DEFAULT 1,
                    separation_of_duties INTEGER NOT NULL DEFAULT 0,
                    step_up_required TEXT NOT NULL DEFAULT 'none',
                    status TEXT NOT NULL CHECK(status IN ('pending','approved','denied','expired','revoked')),
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS control_approval_decisions (
                    id TEXT PRIMARY KEY,
                    approval_request_id TEXT NOT NULL REFERENCES control_approval_requests(id),
                    proposal_id TEXT NOT NULL,
                    proposal_digest TEXT NOT NULL,
                    decision TEXT NOT NULL CHECK(decision IN ('approve','deny')),
                    approver_user_id TEXT NOT NULL,
                    approver_device_id TEXT NOT NULL,
                    approver_session_id TEXT NOT NULL,
                    approver_organization_id TEXT NOT NULL,
                    approver_workspace_id TEXT NOT NULL,
                    auth_strength TEXT NOT NULL DEFAULT 'session',
                    step_up_evidence TEXT NOT NULL DEFAULT '',
                    reason TEXT NOT NULL DEFAULT '',
                    decided_at INTEGER NOT NULL,
                    revocation_state TEXT NOT NULL DEFAULT 'none',
                    decision_digest TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS control_approval_challenges (
                    id TEXT PRIMARY KEY,
                    proposal_id TEXT NOT NULL,
                    proposal_digest TEXT NOT NULL,
                    policy_decision_id TEXT NOT NULL,
                    approval_request_id TEXT NOT NULL,
                    approver_user_id TEXT NOT NULL,
                    approver_device_id TEXT NOT NULL,
                    organization_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    requested_decision TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    nonce TEXT NOT NULL,
                    issued_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('open','solved','expired','revoked')),
                    signed_message TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS control_councils (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL,
                    workspace_id TEXT,
                    name TEXT NOT NULL,
                    purpose TEXT NOT NULL DEFAULT '',
                    member_agents TEXT NOT NULL DEFAULT '',
                    chair_agent TEXT,
                    quorum_rule TEXT NOT NULL DEFAULT 'majority',
                    maximum_rounds INTEGER NOT NULL DEFAULT 1,
                    disagreement_policy TEXT NOT NULL DEFAULT 'record',
                    output_schema TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL CHECK(status IN ('active','disabled')),
                    revision INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS control_council_runs (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT,
                    message_id TEXT,
                    council_definition_id TEXT NOT NULL REFERENCES control_councils(id),
                    council_snapshot TEXT NOT NULL,
                    state TEXT NOT NULL,
                    member_run_ids TEXT NOT NULL DEFAULT '',
                    rounds INTEGER NOT NULL DEFAULT 0,
                    final_recommendation TEXT NOT NULL DEFAULT '',
                    dissents TEXT NOT NULL DEFAULT '',
                    proposed_action_ids TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    completed_at INTEGER,
                    trace_id TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1
                );
                """
            )
            connection.commit()
