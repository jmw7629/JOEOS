"""Authoritative autonomous operations domain.

Durable, agent-based automations: an AutomationDefinition expresses an
intention (objective + agent + trigger), and each scheduled occurrence becomes
an AutomationRun that executes through the existing AgentFabric path
(AutomationRun -> AgentRun -> ProviderRegistry -> ModelRegistry -> Ollama ->
delegation/TaskGraph/ToolBroker). Nothing here invents a second model runtime;
state is persisted, transitions are validated server-side, and occurrences are
deduplicated by a deterministic occurrence key.
"""

from __future__ import annotations

from typing import Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

AUTONOMY_SCHEMA_VERSION = 1

AutomationState = Literal[
    "draft", "active", "paused", "disabled", "archived"
]

AutomationRunState = Literal[
    "queued", "running", "waiting_for_approval", "retry_wait", "blocked",
    "succeeded", "failed", "cancelled",
]

TriggerKind = Literal["one_time", "recurring", "event", "condition_watch", "manual"]

AgentRef = Literal["auto", "joe", "architect", "builder", "researcher", "verifier", "security", "council"]

ConcurrencyPolicy = Literal["skip_if_running", "queue_one", "allow_bounded_parallel"]

MissedRunPolicy = Literal["skip", "run_once_on_recovery", "bounded_catch_up"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RecurrenceSpec(StrictModel):
    """Deterministic recurring schedule. Prefer the structured form; the
    human-readable summary is display-only, never authoritative."""

    kind: Literal["once", "daily", "weekly", "monthly", "interval"] = "once"
    at_time: str = Field(default="09:00", pattern=r"^\d{2}:\d{2}$")
    weekdays: Tuple[int, ...] = Field(default=(), max_length=7)
    month_days: Tuple[int, ...] = Field(default=(), max_length=31)
    interval_seconds: int = Field(default=3600, ge=60, le=86400 * 90)


class TriggerSpec(StrictModel):
    kind: TriggerKind
    # one_time: scheduled_for (ISO datetime in trigger timezone)
    scheduled_for: str = Field(default="", max_length=64)
    # recurring: schedule
    schedule: Optional[RecurrenceSpec] = None
    # event: event_class + optional filters
    event_class: str = Field(default="", max_length=120)
    filters: Dict[str, str] = Field(default_factory=dict, max_length=8)
    # condition_watch: condition key + interval
    condition_key: str = Field(default="", max_length=120)
    check_interval_seconds: int = Field(default=900, ge=300, le=86400)
    timezone: str = Field(default="UTC", min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_trigger(self) -> "TriggerSpec":
        if self.kind == "one_time" and not self.scheduled_for:
            raise ValueError("one_time triggers require scheduled_for.")
        if self.kind == "recurring" and self.schedule is None:
            raise ValueError("recurring triggers require a schedule.")
        if self.kind == "event" and not self.event_class:
            raise ValueError("event triggers require an event_class.")
        if self.kind == "condition_watch" and not self.condition_key:
            raise ValueError("condition_watch triggers require a condition_key.")
        return self


class RetryPolicySpec(StrictModel):
    max_attempts: int = Field(default=3, ge=1, le=10)
    backoff_seconds: int = Field(default=60, ge=1, le=86400)
    backoff_factor: float = Field(default=2.0, ge=1.0, le=5.0)
    max_backoff_seconds: int = Field(default=3600, ge=1, le=86400)
    retryable_errors: Tuple[str, ...] = Field(
        default=("OLLAMA_UNAVAILABLE", "MODEL_TIMEOUT", "MODEL_LOADING", "OLLAMA_ERROR"),
        max_length=12,
    )


class NotificationPolicySpec(StrictModel):
    on_success: bool = Field(default=False)
    on_failure: bool = Field(default=True)
    on_approval_required: bool = Field(default=True)
    on_retry: bool = Field(default=False)
    on_blocked: bool = Field(default=True)


class AutomationDefinition(StrictModel):
    id: str = Field(min_length=4, max_length=80)
    organization_id: str = Field(min_length=1, max_length=80)
    workspace_id: str = Field(min_length=1, max_length=80)
    owner_principal_id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    objective: str = Field(min_length=1, max_length=8000)
    agent_ref: AgentRef = "auto"
    trigger: TriggerSpec
    enabled: bool = True
    state: AutomationState = "draft"
    next_run_at: str = Field(default="", max_length=64)
    last_run_at: str = Field(default="", max_length=64)
    concurrency_policy: ConcurrencyPolicy = "skip_if_running"
    missed_run_policy: MissedRunPolicy = "skip"
    retry_policy: RetryPolicySpec = Field(default_factory=RetryPolicySpec)
    notification_policy: NotificationPolicySpec = Field(default_factory=NotificationPolicySpec)
    created_at: str = Field(default="", max_length=64)
    updated_at: str = Field(default="", max_length=64)
    revision: int = Field(default=1, ge=1)


class AutomationDefinitionCreate(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    objective: str = Field(min_length=1, max_length=8000)
    agent_ref: AgentRef = "auto"
    trigger: TriggerSpec
    timezone: str = Field(default="UTC", min_length=1, max_length=64)
    concurrency_policy: ConcurrencyPolicy = "skip_if_running"
    missed_run_policy: MissedRunPolicy = "skip"
    retry_policy: RetryPolicySpec = Field(default_factory=RetryPolicySpec)
    notification_policy: NotificationPolicySpec = Field(default_factory=NotificationPolicySpec)
    enabled: bool = True
    run_now: bool = False


class AutomationRun(StrictModel):
    id: str = Field(min_length=4, max_length=80)
    automation_id: str = Field(min_length=4, max_length=80)
    occurrence_key: str = Field(min_length=1, max_length=200)
    trigger_kind: str = Field(default="", max_length=40)
    scheduled_for: str = Field(default="", max_length=64)
    triggered_at: str = Field(default="", max_length=64)
    started_at: str = Field(default="", max_length=64)
    completed_at: str = Field(default="", max_length=64)
    attempt: int = Field(default=1, ge=1)
    state: AutomationRunState = "queued"
    agent_run_id: str = Field(default="", max_length=80)
    task_graph_id: str = Field(default="", max_length=80)
    approval_id: str = Field(default="", max_length=80)
    execution_id: str = Field(default="", max_length=80)
    result_summary: str = Field(default="", max_length=12000)
    error_category: str = Field(default="", max_length=120)
    next_retry_at: str = Field(default="", max_length=64)
    worker_claimed_by: str = Field(default="", max_length=80)
    worker_claimed_at: str = Field(default="", max_length=64)
    lease_expires_at: str = Field(default="", max_length=64)
    provider_key: str = Field(default="", max_length=120)
    model_key: str = Field(default="", max_length=160)
    definition_revision: int = Field(default=1, ge=1)
    created_at: str = Field(default="", max_length=64)
    revision: int = Field(default=1, ge=1)
