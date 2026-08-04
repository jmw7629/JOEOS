"""Typed models for the JoeOS Performance and Resource Governance Platform.

Everything here is either measured (with explicit source, sampling method,
availability, and uncertainty) or declared policy (budgets, limits, cache
registrations). No fabricated hardware state is representable: a metric whose
value is unknown carries ``available=False`` and must never be reported as a
real number by consumers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# Priority lanes, 0 = highest. Callers may only request a class; the scheduler
# maps a workload class to a lane. Callers cannot declare themselves critical.
PRIORITY_LANES = {
    "emergency": 0,
    "user_input": 1,
    "cancellation": 2,
    "approval": 3,
    "foreground_action": 4,
    "interactive_model": 5,
    "task_mission": 6,
    "user_build_test": 7,
    "communication": 8,
    "background_agent": 9,
    "repository_indexing": 10,
    "semantic_indexing": 11,
    "synchronization": 12,
    "maintenance": 13,
    "telemetry_cleanup": 14,
    "speculative_preload": 15,
}

# Workload classes that may be declared by callers. Classes not on this list are
# rejected during admission; nothing may self-declare as emergency/user_input.
WORKLOAD_CLASSES = (
    "security_response",
    "foreground_action",
    "user_background",
    "agent_execution",
    "model_inference",
    "repository_indexing",
    "semantic_indexing",
    "workflow_execution",
    "plugin_background",
    "communications_sync",
    "mobile_sync",
    "wearable_sync",
    "notification_delivery",
    "cleanup",
    "telemetry",
    "diagnostics",
    "benchmark",
    "maintenance",
    "speculative_preload",
)

# Classes that require reserved capacity and are never load-shed.
PRESERVED_CLASSES = (
    "security_response",
    "foreground_action",
    "user_background",
    "notification_delivery",
    "diagnostics",
)

# The order in which optional work is shed under load.
LOAD_SHEDDING_ORDER = (
    "speculative_preload",
    "cleanup",
    "telemetry",
    "maintenance",
    "plugin_background",
    "semantic_indexing",
    "repository_indexing",
    "synchronization",
    "communications_sync",
    "mobile_sync",
    "wearable_sync",
    "agent_execution",
    "workflow_execution",
    "benchmark",
)


@dataclass(frozen=True)
class Workload:
    workload_id: str
    wclass: str
    owner: str
    service: str = ""
    project: str = ""
    task: str = ""
    mission: str = ""
    agent: str = ""
    workflow: str = ""
    plugin: str = ""
    model: str = ""
    user_visible: bool = False
    priority: Optional[int] = None  # validated against class
    deadline: Optional[float] = None  # monotonic deadline
    estimated_memory_mb: float = 0.0
    estimated_disk_mb: float = 0.0
    timeout: float = 0.0
    cancellable: bool = True
    preemptible: bool = False
    retry_policy: str = "none"
    degradation_policy: str = "none"
    security_constrained: bool = False
    privacy_sensitive: bool = False
    created_at: float = field(default_factory=lambda: __import__("time").monotonic())

    def validated_priority(self) -> int:
        if self.wclass not in WORKLOAD_CLASSES:
            raise ValueError("Unknown workload class: %s" % self.wclass)
        if self.priority is not None:
            raise ValueError("Callers cannot self-declare priority; it is derived from class.")
        return PRIORITY_LANES[self._lane_key()]

    def _lane_key(self) -> str:
        mapping = {
            "security_response": "emergency",
            "foreground_action": "foreground_action",
            "user_background": "task_mission",
            "agent_execution": "background_agent",
            "model_inference": "interactive_model",
            "repository_indexing": "repository_indexing",
            "semantic_indexing": "semantic_indexing",
            "workflow_execution": "background_agent",
            "plugin_background": "maintenance",
            "communications_sync": "synchronization",
            "mobile_sync": "synchronization",
            "wearable_sync": "synchronization",
            "notification_delivery": "communication",
            "cleanup": "telemetry_cleanup",
            "telemetry": "telemetry_cleanup",
            "diagnostics": "telemetry_cleanup",
            "benchmark": "maintenance",
            "maintenance": "maintenance",
            "speculative_preload": "speculative_preload",
        }
        return mapping[self.wclass]


@dataclass(frozen=True)
class MetricsSample:
    metric: str
    value: float
    source: str = "measurement"
    sampled_at: Optional[str] = None
    sampling_method: str = "direct"
    unit: str = ""
    available: bool = True
    uncertainty: str = ""


@dataclass(frozen=True)
class HistogramStats:
    metric: str
    count: int = 0
    total: float = 0.0
    minimum: float = 0.0
    maximum: float = 0.0
    mean: float = 0.0
    p50: float = 0.0
    p95: float = 0.0
    p99: float = 0.0


@dataclass(frozen=True)
class BudgetRecord:
    budget_id: str
    platform: str
    hardware_profile: str
    metric: str
    target: float
    warning_threshold: float
    failure_threshold: float
    measurement_method: str = "direct"
    owner: str = "performance"
    version: int = 1
    exceptions: str = ""
    review_date: str = ""
    direction: str = "lower_is_better"


@dataclass(frozen=True)
class BenchmarkRecord:
    benchmark_id: str
    title: str
    subsystem: str
    scenario: str
    dataset: str = ""
    fixture: str = ""
    hardware_profile: str = ""
    software_version: str = ""
    warm: bool = False
    iterations: int = 1
    warmup: int = 0
    measurement_method: str = "real"
    metric: str = "duration_ms"
    result: float = 0.0
    median: float = 0.0
    variance: float = 0.0
    timestamp: str = ""
    commit: str = ""
    artifact: str = ""
    limitations: str = ""
    budget_pass: Optional[bool] = None


@dataclass(frozen=True)
class RegressionRecord:
    regression_id: str
    benchmark_id: str
    baseline_commit: str
    current_commit: str
    baseline_median: float
    current_median: float
    variance: float
    confidence: str
    classification: str = "insufficient_samples"


@dataclass(frozen=True)
class CacheRegistration:
    cache_id: str
    owner: str
    purpose: str
    scope: str = "project"
    max_entries: int = 256
    max_bytes: int = 8 * 1024 * 1024
    ttl_seconds: float = 300.0
    privacy: str = "private"
    invalidation: Tuple[str, ...] = ()
    persistence: str = "memory"
    encryption: str = "none"
    sharing_policy: str = "never"
    failure_behavior: str = "recompute"
    security_sensitive: bool = False


@dataclass(frozen=True)
class CacheStats:
    cache_id: str
    owner: str
    entries: int = 0
    bytes_used: int = 0
    maximum_bytes: int = 0
    maximum_entries: int = 0
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    ttl_seconds: float = 0.0
    last_cleanup: Optional[str] = None


@dataclass(frozen=True)
class QueueState:
    queue_id: str
    owner: str
    workload_class: str = "maintenance"
    depth: int = 0
    limit: int = 0
    oldest_wait_ms: float = 0.0
    average_wait_ms: float = 0.0
    rejected: int = 0
    cancelled: int = 0
    throughput_per_minute: float = 0.0
    backpressure: str = "none"
    preserved: bool = False


@dataclass(frozen=True)
class LeakIndicator:
    indicator_id: str
    owner: str
    kind: str
    baseline: float = 0.0
    current: float = 0.0
    growth_rate: float = 0.0
    state: str = "unknown"
    message: str = ""


@dataclass(frozen=True)
class ModelResourceState:
    model_id: str
    runtime: str = ""
    state: str = "unknown"
    active_requests: int = 0
    queue_depth: int = 0
    last_use: Optional[str] = None
    pinned: bool = False
    estimated_memory_mb: float = 0.0
    actual_memory_mb: Optional[float] = None
    footprint_source: str = "unmeasured"


@dataclass(frozen=True)
class PressureState:
    pressure: str = "unknown"  # normal/elevated/high/critical/recovery/unknown
    source: str = "measurement"
    available: bool = False


@dataclass(frozen=True)
class ResourceSnapshot:
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    gpu_percent: Optional[float] = None
    vram_gb: Optional[float] = None
    disk_percent: float = 0.0
    temperature: Optional[float] = None
    battery_percent: Optional[float] = None
    power_state: str = "unknown"
    thermal_state: str = "unknown"
    network_state: str = "unknown"
    cpu_available: bool = False
    memory_available: bool = False
    gpu_available: bool = False
    disk_available: bool = False
    battery_available: bool = False
    thermal_available: bool = False
    source: str = "telemetry.collector"
    sampled_at: str = ""


@dataclass(frozen=True)
class PerformanceOverview:
    overall: str = "unknown"
    load: str = "unknown"
    memory_pressure: PressureState = field(default_factory=PressureState)
    disk_pressure: PressureState = field(default_factory=PressureState)
    gpu_pressure: PressureState = field(default_factory=PressureState)
    load_shedding_active: bool = False
    load_shedding_reasons: Tuple[str, ...] = ()
    queue_count: int = 0
    cache_count: int = 0
    models_loaded: int = 0
    models_blocked: int = 0
    active_agents: int = 0
    active_workflows: int = 0
    plugins_violating: int = 0
    leak_indicators: int = 0
    regressions: int = 0
    low_power_mode: bool = False
    metered_network: bool = False
    generated_at: str = ""
    message: str = ""
    metrics: Dict[str, MetricsSample] = field(default_factory=dict)
