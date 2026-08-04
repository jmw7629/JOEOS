"""JoeOS Performance and Resource Governance Platform.

The authoritative, measurement-driven layer that makes JoeOS fast, responsive,
stable under load, efficient with resources, and resistant to exhaustion. It
extends the existing telemetry/health architecture rather than duplicating it:
the collector's host sample and AI runtime state are ingested here, real
operation timings are recorded into a bounded metrics registry, workloads are
classified and scheduled by priority, concurrency is bounded per scope,
backpressure and load shedding protect queues, caches are registered with
explicit invalidation, model resources are governed, leaks are detected, and
benchmarks/budgets/regressions are tracked with honest measurement metadata.

Nothing here fabricates hardware state: GPU, VRAM, battery, and thermal values
are reported only when actually measurable, and unknown stays unknown.

See `docs/architecture/PERFORMANCE_PLATFORM.md` for the design and guarantees.
"""

from .admission import AdmissionController, AdmissionDecision
from .ai import ModelResourceManager
from .backpressure import BoundedQueue, PRESERVED_EVENT_CLASSES
from .benchmarks import BenchmarkRunner
from .budgets import BudgetRegistry, HARDWARE_PROFILES
from .caches import Cache, CacheRegistry, CacheRegistration, NEVER_CACHE_PURPOSES
from .governor import ConcurrencyGovernor, DEFAULT_LIMITS
from .leaks import LeakDetectionService, SUPPORTED_KINDS
from .models import (
    BenchmarkRecord,
    BudgetRecord,
    CacheRegistration as CacheRegistrationModel,
    CacheStats,
    HistogramStats,
    LeakIndicator,
    MetricsSample,
    ModelResourceState,
    PerformanceOverview,
    PressureState,
    PRIORITY_LANES,
    QueueState,
    RegressionRecord,
    ResourceSnapshot,
    Workload,
    WORKLOAD_CLASSES,
)
from .registry import ALLOWED_METRICS, PerformanceMetricsRegistry
from .regression import RegressionAnalyzer
from .resources import PressureThresholds, ResourceGovernor
from .router import router as performance_router
from .scheduler import PriorityScheduler
from .service import PerformanceService
from .storage import PerformanceStorage
from .traces import Span, Tracer

__all__ = [
    "ALLOWED_METRICS",
    "AdmissionController",
    "AdmissionDecision",
    "BenchmarkRecord",
    "BenchmarkRunner",
    "BoundedQueue",
    "BudgetRecord",
    "BudgetRegistry",
    "Cache",
    "CacheRegistration",
    "CacheRegistry",
    "CacheRegistrationModel",
    "CacheStats",
    "ConcurrencyGovernor",
    "DEFAULT_LIMITS",
    "HARDWARE_PROFILES",
    "HistogramStats",
    "LeakDetectionService",
    "LeakIndicator",
    "MetricsSample",
    "ModelResourceManager",
    "ModelResourceState",
    "NEVER_CACHE_PURPOSES",
    "PerformanceMetricsRegistry",
    "PerformanceOverview",
    "PerformanceService",
    "PerformanceStorage",
    "PRESERVED_EVENT_CLASSES",
    "PRIORITY_LANES",
    "PressureState",
    "PressureThresholds",
    "QueueState",
    "RegressionAnalyzer",
    "RegressionRecord",
    "ResourceGovernor",
    "ResourceSnapshot",
    "SUPPORTED_KINDS",
    "Span",
    "Tracer",
    "WORKLOAD_CLASSES",
    "Workload",
    "performance_router",
]
