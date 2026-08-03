"""Typed contracts for the JoeOS Automation and Workflow Platform.

Workflow definitions, versions, runs, triggers, schedules, nodes, actions,
variables, conditions, and policies are all expressed as strict, versioned,
extra-forbidden models. No model carries authority by itself; enforcement
lives in the validator, compiler, and execution engine.
"""

from __future__ import annotations

import re
from typing import Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

WORKFLOW_SCHEMA_VERSION = 1
API_VERSION = 1

WORKFLOW_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{2,79}$")
NODE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,79}$")
ACTION_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,79}$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


# ---------------------------------------------------------------------------
# Node types
# ---------------------------------------------------------------------------

NodeType = Literal[
    "start",
    "end",
    "action",
    "condition",
    "switch",
    "parallel",
    "join",
    "loop",
    "delay",
    "wait_time",
    "wait_event",
    "wait_approval",
    "wait_input",
    "transform",
    "notification",
    "subworkflow",
    "failure_handler",
    "audit_marker",
]


class RetryPolicy(StrictModel):
    max_attempts: int = Field(default=1, ge=1, le=20)
    backoff_seconds: int = Field(default=1, ge=0, le=3600)
    backoff_factor: float = Field(default=2.0, ge=1.0, le=10.0)
    max_delay_seconds: int = Field(default=300, ge=1, le=86400)
    retryable_errors: Tuple[str, ...] = Field(default=("timeout", "transient"), max_length=8)
    jitter: bool = False


class TimeoutPolicy(StrictModel):
    timeout_seconds: int = Field(default=120, ge=1, le=86400)
    on_timeout: Literal["fail", "continue", "compensate"] = "fail"


class NodeConfig(StrictModel):
    id: str = Field(min_length=1, max_length=80)
    type: NodeType
    title: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=240)
    action: Optional[str] = Field(default=None, max_length=120)
    params: Dict[str, object] = Field(default_factory=dict)
    condition: str = Field(default="", max_length=500)
    branches: Dict[str, str] = Field(default_factory=dict)
    parallel_nodes: Tuple[str, ...] = Field(default=(), max_length=16)
    join_policy: Literal["all", "first_success", "first_completion", "required"] = "all"
    required_branches: Tuple[str, ...] = Field(default=(), max_length=16)
    loop: Optional["LoopConfig"] = None
    wait_until: str = Field(default="", max_length=200)
    wait_event: str = Field(default="", max_length=120)
    timeout: TimeoutPolicy = Field(default_factory=TimeoutPolicy)
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    requires_permissions: Tuple[str, ...] = Field(default=(), max_length=8)
    side_effects: Tuple[str, ...] = Field(default=(), max_length=8)
    compensation: Optional[str] = Field(default=None, max_length=120)
    output_mapping: Dict[str, str] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if NODE_ID_PATTERN.fullmatch(value) is None:
            raise ValueError("node id must be a lowercase dotted identifier.")
        return value


class LoopConfig(StrictModel):
    max_iterations: int = Field(default=10, ge=1, le=1000)
    max_duration_seconds: int = Field(default=600, ge=1, le=86400)
    item_source: str = Field(default="", max_length=200)
    item_variable: str = Field(default="item", max_length=60)
    failure_policy: Literal["stop", "continue", "compensate"] = "stop"


class EdgeConfig(StrictModel):
    source: str = Field(min_length=1, max_length=80)
    target: str = Field(min_length=1, max_length=80)
    condition: str = Field(default="", max_length=500)
    label: str = Field(default="", max_length=80)

    @field_validator("source", "target")
    @classmethod
    def validate_nodes(cls, value: str) -> str:
        if NODE_ID_PATTERN.fullmatch(value) is None:
            raise ValueError("edge endpoints must be node ids.")
        return value


# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------

TriggerType = Literal[
    "manual",
    "command",
    "scheduled",
    "interval",
    "event",
    "filesystem",
    "git",
    "task",
    "mission",
    "agent",
    "model",
    "service_health",
    "condition",
    "plugin",
]


class Recurrence(StrictModel):
    kind: Literal["once", "daily", "weekly", "monthly", "interval", "weekdays"] = "once"
    at_time: str = Field(default="09:00", pattern=r"^\d{2}:\d{2}$")
    weekdays: Tuple[int, ...] = Field(default=(), max_length=7)
    month_days: Tuple[int, ...] = Field(default=(), max_length=31)
    interval_seconds: int = Field(default=3600, ge=60, le=86400 * 90)
    timezone: str = Field(default="UTC", min_length=1, max_length=64)


class TriggerConfig(StrictModel):
    trigger_id: str = Field(min_length=1, max_length=80)
    type: TriggerType
    event_class: str = Field(default="", max_length=120)
    filters: Dict[str, object] = Field(default_factory=dict)
    schedule: Optional[Recurrence] = None
    condition: str = Field(default="", max_length=500)
    source: str = Field(default="", max_length=120)
    dedup_key: str = Field(default="", max_length=120)
    missed_run_policy: Literal["skip", "run_immediately", "catch_up_latest", "catch_up_all", "require_review"] = "skip"
    overlap_policy: Literal["skip", "queue", "cancel_previous", "parallel_bounded", "deduplicate"] = "skip"
    enabled: bool = True

    @field_validator("trigger_id")
    @classmethod
    def validate_trigger_id(cls, value: str) -> str:
        if NODE_ID_PATTERN.fullmatch(value) is None:
            raise ValueError("trigger id must be a lowercase dotted identifier.")
        return value

    @model_validator(mode="after")
    def validate_trigger(self) -> "TriggerConfig":
        if self.type in {"scheduled", "interval", "weekly", "monthly", "weekdays"} and self.schedule is None:
            raise ValueError("scheduled triggers require a schedule.")
        if self.type == "event" and not self.event_class:
            raise ValueError("event triggers require an event class.")
        return self


# ---------------------------------------------------------------------------
# Variables / secrets / policies
# ---------------------------------------------------------------------------

VariableScope = Literal["workflow", "run", "trigger", "node", "branch", "loop", "subworkflow", "input"]


class VariableDef(StrictModel):
    name: str = Field(min_length=1, max_length=60, pattern=r"^[a-z][a-z0-9_]{0,59}$")
    type: Literal["string", "number", "boolean", "object", "array"] = "string"
    default: object = None
    required: bool = False
    scope: VariableScope = "workflow"
    privacy: Literal["plain", "sensitive"] = "plain"
    max_size: int = Field(default=4096, ge=0, le=1024 * 1024)


class SecretRef(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    scope: str = Field(default="global", max_length=80)
    destination: str = Field(default="", max_length=120)


# ---------------------------------------------------------------------------
# Policies
# ---------------------------------------------------------------------------

class ResourcePolicy(StrictModel):
    max_active_runs: int = Field(default=4, ge=1, le=100)
    max_parallel_branches: int = Field(default=4, ge=1, le=32)
    max_loop_iterations: int = Field(default=50, ge=1, le=1000)
    max_duration_seconds: int = Field(default=3600, ge=60, le=86400 * 7)
    max_model_calls: int = Field(default=100, ge=0, le=10000)
    max_tool_calls: int = Field(default=200, ge=0, le=10000)
    priority: int = Field(default=50, ge=0, le=100)


class ConcurrencyPolicy(StrictModel):
    max_active_runs: int = Field(default=4, ge=1, le=100)
    per_project: int = Field(default=1, ge=0, le=100)
    one_destructive_at_a_time: bool = True


class CompensationPolicy(StrictModel):
    enabled: bool = True
    require_approval: bool = False
    preserve_independent_branches: bool = True


class FailurePolicy(StrictModel):
    on_failure: Literal[
        "stop",
        "continue_independent",
        "retry_bounded",
        "run_failure_handler",
        "compensate",
        "pause_for_review",
        "escalate",
    ] = "stop"
    escalate_to: str = Field(default="user", max_length=80)


class NotificationPolicy(StrictModel):
    notify_on: Tuple[str, ...] = Field(default=(), max_length=8)
    destination: str = Field(default="", max_length=120)


# ---------------------------------------------------------------------------
# Workflow definition / version / run
# ---------------------------------------------------------------------------

class WorkflowDefinition(StrictModel):
    workflow_id: str = Field(min_length=3, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    owner: str = Field(default="user", max_length=80)
    creator: str = Field(default="user", max_length=80)
    source: Literal["user", "template", "plugin", "imported", "assistant"] = "user"
    scope: str = Field(default="personal", max_length=60)
    project: str = Field(default="", max_length=120)
    privacy_classification: Literal["private", "mission_scoped", "public"] = "private"
    risk: Literal["low", "medium", "high"] = "low"
    version: str = Field(min_length=1, max_length=20)
    triggers: Tuple[TriggerConfig, ...] = Field(default=(), max_length=8)
    nodes: Tuple[NodeConfig, ...] = Field(default=(), max_length=64)
    edges: Tuple[EdgeConfig, ...] = Field(default=(), max_length=128)
    variables: Tuple[VariableDef, ...] = Field(default=(), max_length=32)
    secrets: Tuple[SecretRef, ...] = Field(default=(), max_length=8)
    required_permissions: Tuple[str, ...] = Field(default=(), max_length=16)
    resource: ResourcePolicy = Field(default_factory=ResourcePolicy)
    concurrency: ConcurrencyPolicy = Field(default_factory=ConcurrencyPolicy)
    failure_policy: FailurePolicy = Field(default_factory=FailurePolicy)
    compensation: CompensationPolicy = Field(default_factory=CompensationPolicy)
    notifications: NotificationPolicy = Field(default_factory=NotificationPolicy)
    template_source: str = Field(default="", max_length=120)
    plugin_source: str = Field(default="", max_length=120)
    enabled: bool = False
    status: Literal["draft", "validating", "invalid", "ready", "enabled", "disabled", "paused", "archived", "quarantined"] = "draft"
    tags: Tuple[str, ...] = Field(default=(), max_length=8)

    @field_validator("workflow_id")
    @classmethod
    def validate_workflow_id(cls, value: str) -> str:
        if WORKFLOW_ID_PATTERN.fullmatch(value) is None:
            raise ValueError("workflow id must be a lowercase dotted identifier.")
        return value

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        if not re.match(r"^\d+\.\d+\.\d+$", value):
            raise ValueError("version must be major.minor.patch.")
        return value

    @model_validator(mode="after")
    def validate_definition(self) -> "WorkflowDefinition":
        node_ids = [node.id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("node ids must be unique.")
        if not any(node.type == "start" for node in self.nodes):
            raise ValueError("a workflow must have a start node.")
        starts = [node for node in self.nodes if node.type == "start"]
        if len(starts) != 1:
            raise ValueError("a workflow must have exactly one start node.")
        known = set(node_ids)
        for edge in self.edges:
            if edge.source not in known or edge.target not in known:
                raise ValueError("edge references an unknown node.")
        command_ids = []
        for node in self.nodes:
            if node.type == "action" and node.action:
                command_ids.append(node.action)
            for permission in node.requires_permissions:
                pass
        return self


class WorkflowVersion(StrictModel):
    version_id: str
    workflow_id: str
    version: str
    definition_hash: str
    created_at: str
    creator: str = ""
    change_summary: str = ""
    definition: WorkflowDefinition
    permission_difference: Tuple[str, ...] = ()
    published: bool = False
    superseded: bool = False


class WorkflowRecord(StrictModel):
    workflow_id: str
    name: str
    current_version: str
    definition: WorkflowDefinition
    enabled: bool
    status: str
    health_state: str
    next_scheduled_run: Optional[str] = None
    last_successful_run: Optional[str] = None
    last_failed_run: Optional[str] = None
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

RunState = Literal[
    "created",
    "queued",
    "preparing",
    "running",
    "waiting",
    "delayed",
    "awaiting_approval",
    "awaiting_input",
    "retrying",
    "compensating",
    "paused",
    "cancelling",
    "cancelled",
    "succeeded",
    "succeeded_with_warnings",
    "partially_succeeded",
    "failed",
    "timed_out",
    "blocked",
    "skipped",
    "deduplicated",
    "superseded",
    "recovered",
    "unknown",
]


class RunRecord(StrictModel):
    run_id: str
    workflow_id: str
    workflow_version: str
    trigger_id: str = ""
    state: RunState
    current_node: str = ""
    started_at: str = ""
    ended_at: str = ""
    duration_seconds: float = 0.0
    trigger_context: Dict[str, object] = Field(default_factory=dict)
    inputs: Dict[str, object] = Field(default_factory=dict)
    outputs: Dict[str, object] = Field(default_factory=dict)
    error: str = ""
    error_code: str = ""
    retry_count: int = 0
    cancellation_state: str = "none"
    trace_id: str = ""


class TraceEvent(StrictModel):
    trace_id: str
    run_id: str
    node_id: str = ""
    action_id: str = ""
    event_type: str
    recorded_at: str
    duration_ms: float = 0.0
    state_transition: str = ""
    error_code: str = ""
    retry_state: str = ""
    safe_summary: str = ""


class ScheduleRecord(StrictModel):
    schedule_id: str
    workflow_id: str
    workflow_version_policy: Literal["latest", "pinned"] = "latest"
    pinned_version: str = ""
    timezone: str = "UTC"
    recurrence: Recurrence
    next_run: Optional[str] = None
    last_run: Optional[str] = None
    missed_run_policy: str = "skip"
    overlap_policy: str = "skip"
    enabled: bool = True
    health_state: str = "healthy"
    validation_state: str = "valid"


class ActionExecution(StrictModel):
    run_id: str
    node_id: str
    action_id: str
    state: Literal["pending", "running", "succeeded", "failed", "cancelled", "skipped", "blocked"] = "pending"
    started_at: str = ""
    ended_at: str = ""
    result: Dict[str, object] = Field(default_factory=dict)
    error: str = ""
    retry_count: int = 0
    idempotency_key: str = ""


class ApprovalRequest(StrictModel):
    approval_id: str
    run_id: str
    workflow_id: str
    node_id: str
    action: str
    reason: str
    risk: str
    scope: str = ""
    project: str = ""
    side_effects: Tuple[str, ...] = ()
    arguments_hash: str = ""
    state: Literal["pending", "approved", "denied", "expired"] = "pending"
    expires_at: str = ""
    requested_by: str = ""
    created_at: str = ""


class UserInputRequest(StrictModel):
    input_id: str
    run_id: str
    workflow_id: str
    node_id: str
    prompt: str
    input_schema: Dict[str, object] = Field(default_factory=dict, alias="schema")
    state: Literal["pending", "provided", "cancelled", "expired"] = "pending"
    response: Dict[str, object] = Field(default_factory=dict)
    created_at: str = ""


class WorkflowOverview(StrictModel):
    workflows_total: int
    workflows_enabled: int
    running: int
    waiting: int
    failed_recently: int
    pending_approvals: int
    unhealthy_schedules: int
    next_scheduled_workflow: Optional[str] = None
    generated_at: str