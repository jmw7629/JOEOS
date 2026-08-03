"""Command Center platform types: strict typed health and activity contracts."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

HealthState = Literal[
    "healthy",
    "degraded",
    "unavailable",
    "starting",
    "paused",
    "blocked",
    "attention",
    "unknown",
]

Severity = Literal["info", "success", "warn", "error"]

CapabilityState = Literal["available", "unavailable"]


class StrictCommandCenterModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ServiceHealth(StrictCommandCenterModel):
    service_id: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9._-]*$")
    name: str = Field(min_length=1, max_length=120)
    state: HealthState
    available: bool
    version: Optional[str] = Field(default=None, max_length=40)
    started_at: Optional[str] = None
    last_check: Optional[str] = None
    last_failure: Optional[str] = None
    latency_ms: Optional[int] = Field(default=None, ge=0)
    dependencies: Tuple[str, ...] = Field(default=(), max_length=16)
    degraded_reason: Optional[str] = Field(default=None, max_length=240)
    message: str = Field(min_length=1, max_length=240)


class HealthSignal(StrictCommandCenterModel):
    subsystem: str = Field(min_length=1, max_length=80)
    state: HealthState
    message: str = Field(min_length=1, max_length=240)


class ResourceTelemetry(StrictCommandCenterModel):
    state: HealthState
    message: str = Field(default="", min_length=0, max_length=240)
    updated_at: Optional[str] = None
    cpu_percent: Optional[float] = None
    ram_percent: Optional[float] = None
    gpu_percent: Optional[float] = None
    disk_percent: Optional[float] = None
    uptime_seconds: Optional[int] = None
    cpu_detail: Optional[str] = None
    ram_detail: Optional[str] = None
    gpu_detail: Optional[str] = None
    disk_detail: Optional[str] = None


class AiRuntimeStatus(StrictCommandCenterModel):
    state: HealthState
    online: bool
    status: Optional[str] = None
    model: Optional[str] = None
    loaded_models: Tuple[str, ...] = Field(default=(), max_length=64)
    available_models: Tuple[str, ...] = Field(default=(), max_length=128)
    message: Optional[str] = Field(default=None, max_length=240)


class OverviewCounts(StrictCommandCenterModel):
    active_agents: int = Field(ge=0)
    blocked_agents: int = Field(ge=0)
    unread_attention: int = Field(ge=0)
    loaded_models: Optional[int] = Field(default=None, ge=0)
    available_models: Optional[int] = Field(default=None, ge=0)
    active_missions: Optional[int] = Field(default=None, ge=0)
    queued_missions: Optional[int] = Field(default=None, ge=0)
    pending_approvals: Optional[int] = Field(default=None, ge=0)
    failed_jobs: Optional[int] = Field(default=None, ge=0)
    active_projects: Optional[int] = Field(default=None, ge=0)
    dirty_repositories: Optional[int] = Field(default=None, ge=0)


class OverviewCapabilities(StrictCommandCenterModel):
    missions: CapabilityState = "unavailable"
    tasks: CapabilityState = "unavailable"
    approvals: CapabilityState = "unavailable"
    projects: CapabilityState = "unavailable"
    agents_execution: CapabilityState = "unavailable"
    secrets: CapabilityState = "unavailable"
    telemetry: CapabilityState = "available"
    activity_timeline: CapabilityState = "available"
    service_health: CapabilityState = "available"


class OverviewEnvelope(StrictCommandCenterModel):
    schema_version: Literal[1] = 1
    generated_at: str
    overall: HealthState
    health_signals: Tuple[HealthSignal, ...] = Field(max_length=32)
    services: Tuple[ServiceHealth, ...] = Field(max_length=64)
    capabilities: OverviewCapabilities
    counts: OverviewCounts
    resources: ResourceTelemetry
    runtime: AiRuntimeStatus
    attention: Tuple["ActivityEvent", ...] = Field(max_length=40)
    next_scheduled_automation: Optional[str] = None


class ActivityEvent(StrictCommandCenterModel):
    event_id: int = Field(ge=1)
    event_type: Literal["audit.event"] = "audit.event"
    source: str = Field(min_length=1, max_length=80)
    source_id: Optional[str] = Field(default=None, max_length=120)
    occurred_at: str
    summary: str = Field(min_length=1, max_length=500)
    severity: Severity
    project: Optional[str] = Field(default=None, max_length=120)
    mission: Optional[str] = Field(default=None, max_length=120)
    agent: Optional[str] = Field(default=None, max_length=120)
    navigation: Optional[str] = Field(default=None, max_length=200)
    privacy: Literal["public", "private"] = "public"


class ActivityEnvelope(StrictCommandCenterModel):
    schema_version: Literal[1] = 1
    generated_at: str
    items: Tuple[ActivityEvent, ...] = Field(max_length=100)
    total_available: int = Field(ge=0)
    next_before: Optional[int] = Field(default=None, ge=1)
    filters: Dict[str, Any] = Field(default_factory=dict)


class ServicesEnvelope(StrictCommandCenterModel):
    schema_version: Literal[1] = 1
    generated_at: str
    services: Tuple[ServiceHealth, ...] = Field(max_length=64)


OverviewEnvelope.model_rebuild()
