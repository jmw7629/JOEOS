"""PerformanceService — the authoritative Performance and Resource Governance
facade for JoeOS.

Composes the Metrics Registry, Priority Scheduler, Concurrency Governor,
Resource Governor, Admission Control, Backpressure queues, Cache Registry,
Model Resource Manager, Leak Detection, Benchmark Registry, Budget Registry,
Regression Analyzer, and Tracer. It ingests the authoritative telemetry
snapshot produced by the existing collector (never a second telemetry source),
records real operation timings, exposes an honest aggregated overview, and
publishes normalized safe events through the platform event sink.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from .admission import AdmissionController
from .ai import ModelResourceManager
from .backpressure import BoundedQueue
from .benchmarks import BenchmarkRunner
from .budgets import BudgetRegistry
from .caches import CacheRegistry, CacheRegistration
from .governor import ConcurrencyGovernor
from .leaks import LeakDetectionService
from .models import (
    CacheStats,
    ModelResourceState,
    PerformanceOverview,
    PressureState,
    QueueState,
    ResourceSnapshot,
    Workload,
)
from .registry import ALLOWED_METRICS, PerformanceMetricsRegistry
from .regression import RegressionAnalyzer
from .resources import ResourceGovernor
from .scheduler import PriorityScheduler
from .storage import PerformanceStorage
from .traces import Tracer


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PerformanceService:
    def __init__(
        self,
        data_dir: str,
        *,
        event_sink: Optional[Callable[[str, str, str], None]] = None,
        governance_blocked: Optional[Callable[[], tuple]] = None,
        hardware_profile: str = "development-workstation",
        version: str = "2.0.0",
    ) -> None:
        import sqlite3
        from pathlib import Path

        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        db_path = self.data_dir / "performance.db"

        def connect() -> sqlite3.Connection:
            connection = sqlite3.connect(str(db_path), timeout=10)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 10000")
            return connection

        self.storage = PerformanceStorage(connect)
        self.metrics = PerformanceMetricsRegistry(self.storage)
        self.tracer = Tracer(self.storage)
        self.scheduler = PriorityScheduler()
        self.concurrency = ConcurrencyGovernor()
        self.resources = ResourceGovernor()
        self.admission = AdmissionController(self.resources, self.concurrency)
        self.models = ModelResourceManager()
        self.caches = CacheRegistry(self.storage)
        self.leaks = LeakDetectionService(self.storage)
        self.benchmarks = BenchmarkRunner(self.storage)
        self.budgets = BudgetRegistry(self.storage, hardware_profile=hardware_profile)
        self.regressions = RegressionAnalyzer(self.storage)
        self._event_sink = event_sink
        self._governance_blocked = governance_blocked or (lambda: (False, ""))
        self._hardware_profile = hardware_profile
        self._version = version
        self._lock = threading.RLock()
        self._queues: Dict[str, BoundedQueue] = {}
        self._active_agents = 0
        self._active_workflows = 0
        self._plugin_violations: Dict[str, str] = {}
        self._started_at = time.monotonic()
        self._profile = "balanced"
        self._visual_quality = "full"
        self._animation_quality = "full"
        self._metrics_enabled = {metric: True for metric in ALLOWED_METRICS}
        self._last_memory_pressure = ""
        self._last_disk_pressure = ""
        self._last_gpu_pressure = ""

        self._register_default_queues()
        self._register_default_caches()
        self._register_default_benchmarks()

    # ---- ingestion from the authoritative collector ----

    def ingest_telemetry(self, host_sample: Dict[str, Any], runtime: Dict[str, Any]) -> None:
        """Consume the existing collector's host sample + AI runtime state.

        GPU/VRAM are derived from the raw runtime values and only reported when
        the AI runtime is online and actually reported them. The host sample's
        sanitized ``gpu_percent`` (0.0 when unmeasured) is never trusted, so an
        unmeasured GPU stays ``unknown`` rather than fabricated as healthy.
        """
        runtime_online = bool(runtime.get("online"))
        gpu_raw = runtime.get("gpu_percent")
        vram_raw = runtime.get("vram_gb")
        gpu_available = runtime_online and gpu_raw is not None
        sample = ResourceSnapshot(
            cpu_percent=_float(host_sample.get("cpu_percent")),
            memory_percent=_float(host_sample.get("ram_percent")),
            gpu_percent=gpu_raw if gpu_available else None,
            vram_gb=vram_raw if gpu_available else None,
            disk_percent=_float(host_sample.get("disk_percent")),
            temperature=_opt_float(host_sample.get("temperature")) if host_sample.get("temperature") else None,
            cpu_available=True,
            memory_available=True,
            gpu_available=gpu_available,
            disk_available=True,
            battery_available=False,
            thermal_available=False,
            source="telemetry.collector",
            sampled_at=_now_iso(),
        )
        self.resources.update_snapshot(sample)
        self._emit_pressure_transitions()
        models = []
        for model_id in runtime.get("available_models") or []:
            models.append({"id": model_id, "runtime": "lemonade"})
        self.models.sync_inventory(models)

    def _emit_pressure_transitions(self) -> None:
        if self._event_sink is None:
            return
        memory = self.resources.memory_pressure().pressure
        if memory != self._last_memory_pressure:
            if memory == "critical":
                self._event_sink("warn", "performance", "Critical memory pressure: optional background work is blocked.")
            elif memory == "high":
                self._event_sink("warn", "performance", "High memory pressure: background work throttled, idle models are unload candidates.")
            self._last_memory_pressure = memory
        disk = self.resources.disk_pressure().pressure
        if disk != self._last_disk_pressure:
            if disk in ("high", "critical"):
                self._event_sink("warn", "performance", "%s disk pressure: safe caches trimmed, uploads blocked." % disk.capitalize())
            self._last_disk_pressure = disk

    def record(self, metric: str, value: float) -> None:
        if not self._metrics_enabled.get(metric, True):
            return
        self.metrics.record(metric, value)
        classification, budget = self.budgets.check(metric, value)
        if classification == "fail" and self._event_sink is not None:
            self._event_sink("warn", "performance", "Performance budget exceeded for %s." % metric)

    def governance_blocked(self) -> Tuple[bool, str]:
        return self._governance_blocked()

    def trace(self, service: str, operation: str) -> Any:
        return self.tracer.begin(service, operation)

    # ---- workload lifecycle ----

    def submit(self, workload: Workload) -> Dict[str, Any]:
        decision = self.admission.evaluate(workload)
        if decision.decision == "admit":
            return {"decision": "admit", "queued": False, "reason": decision.reason}
        if decision.decision in ("queue", "reduce_quality"):
            accepted, reason = self.scheduler.submit(workload)
            if accepted:
                return {"decision": decision.decision, "queued": True, "reason": decision.reason or reason}
            return {"decision": "reject", "queued": False, "reason": reason}
        return {"decision": decision.decision, "queued": False, "reason": decision.reason}

    def schedule_next(self) -> Optional[Workload]:
        return self.scheduler.next()

    def cancel_workload(self, workload_id: str) -> bool:
        return self.scheduler.cancel(workload_id)

    # ---- authoritative counts surfaced by platform services ----

    def report_agent_active(self, delta: int) -> None:
        with self._lock:
            self._active_agents = max(0, self._active_agents + int(delta))

    def report_workflow_active(self, delta: int) -> None:
        with self._lock:
            self._active_workflows = max(0, self._active_workflows + int(delta))

    def report_plugin_violation(self, plugin_id: str, reason: str) -> None:
        with self._lock:
            self._plugin_violations[plugin_id] = reason
        if self._event_sink is not None:
            self._event_sink("warn", "performance", "Plugin resource violation for %s: %s" % (plugin_id, reason))

    # ---- backpressure queues ----

    def queue(self, queue_id: str) -> Optional[BoundedQueue]:
        return self._queues.get(queue_id)

    def enqueue(self, queue_id: str, item: Any, *, eclass: str = "ordinary") -> Tuple[bool, str]:
        queue = self._queues.get(queue_id)
        if queue is None:
            return False, "unknown_queue"
        return queue.push(item, eclass=eclass)

    def queue_snapshots(self) -> List[QueueState]:
        states = []
        for queue_id, queue in self._queues.items():
            snapshot = queue.snapshot()
            states.append(
                QueueState(
                    queue_id=queue_id,
                    owner=snapshot["owner"],
                    workload_class="maintenance",
                    depth=snapshot["depth"],
                    limit=snapshot["limit"],
                    oldest_wait_ms=snapshot["oldest_wait_ms"],
                    average_wait_ms=snapshot["average_wait_ms"],
                    rejected=snapshot["rejected"],
                    cancelled=snapshot["cancelled"],
                    throughput_per_minute=snapshot["throughput_per_minute"],
                    backpressure=snapshot["backpressure"],
                    preserved=snapshot["preserved"],
                )
            )
            self.storage.upsert_queue(_queue_state_to_dict(states[-1]))
        return states

    # ---- cache registry ----

    def register_cache(self, registration: CacheRegistration) -> None:
        self.caches.register(registration)

    def get_cache(self, cache_id: str, key: str) -> Tuple[Any, bool]:
        return self.caches.get(cache_id, key)

    def put_cache(self, cache_id: str, key: str, value: Any, size_bytes: int = 0) -> None:
        self.caches.put(cache_id, key, value, size_bytes)

    def invalidate_cache_tag(self, tag: str) -> int:
        return self.caches.invalidate_tag(tag)

    def clear_safe_caches(self) -> int:
        return self.caches.clear_safe()

    # ---- model governance actions ----

    def unload_idle_models(self, *, force: bool = False) -> List[str]:
        blocked, reason = self._governance_blocked()
        if blocked:
            raise ValueError("governance: %s" % reason)
        return self.models.unload_idle(force=force)

    # ---- operational modes ----

    def enter_low_resource_mode(self) -> None:
        self.resources.set_low_power(True)
        self.resources.set_metered_network(True)
        self._profile = "low_resource"
        if self._event_sink is not None:
            self._event_sink("info", "performance", "Low-resource mode enabled.")

    def exit_low_resource_mode(self) -> None:
        self.resources.set_low_power(False)
        self.resources.set_metered_network(False)
        self._profile = "balanced"
        if self._event_sink is not None:
            self._event_sink("info", "performance", "Low-resource mode disabled.")

    def set_pause_indexing(self, paused: bool) -> None:
        self.resources.set_indexing_paused(paused)

    def set_visual_quality(self, level: str) -> None:
        if level not in ("full", "balanced", "reduced", "minimal"):
            raise ValueError("Unknown visual quality level: %s" % level)
        self._visual_quality = level

    def set_animation_quality(self, level: str) -> None:
        if level not in ("full", "balanced", "reduced", "minimal"):
            raise ValueError("Unknown animation quality level: %s" % level)
        self._animation_quality = level

    # ---- leak detection ----

    def record_leak_sample(self, kind: str, owner: str, current: float) -> None:
        self.leaks.record(kind, owner, current)

    # ---- benchmarks ----

    def run_benchmark(self, benchmark_id: str, *, hardware_profile: str = "", iterations: Optional[int] = None) -> Dict[str, Any]:
        from dataclasses import replace as dc_replace
        record = self.benchmarks.run(
            benchmark_id,
            iterations=iterations,
            hardware_profile=hardware_profile or self._hardware_profile,
        )
        budget_pass = None
        classification, budget = self.budgets.check(record.metric, record.median)
        if classification == "fail":
            budget_pass = False
        elif classification == "pass":
            budget_pass = True
        record = dc_replace(record, budget_pass=budget_pass)
        self.storage.upsert_benchmark(_benchmark_to_dict(record))
        regression = self.regressions.compare(record.benchmark_id, record)
        if regression.classification in ("warning_regression", "budget_failure") and self._event_sink is not None:
            self._event_sink(
                "warn", "performance",
                "Benchmark %s regression (%s): median %.1fms vs baseline %.1fms."
                % (benchmark_id, regression.classification, record.median, regression.baseline_median),
            )
        if self._event_sink is not None:
            self._event_sink("info", "performance", "Benchmark %s completed (median %.1fms)." % (benchmark_id, record.median))
        return {
            "benchmark_id": benchmark_id,
            "median": record.median,
            "variance": record.variance,
            "iterations": record.iterations,
            "budget_pass": budget_pass,
            "regression": regression.classification,
        }

    # ---- overview ----

    def overview(self) -> PerformanceOverview:
        generated_at = _now_iso()
        resource_overview = self.resources.overview(generated_at)
        metrics = {item.metric: item for item in self.metrics.snapshot()}
        loaded = self.models.loaded_count()
        blocked = self.models.blocked_count()
        with self._lock:
            agents = self._active_agents
            workflows = self._active_workflows
            plugin_violations = len(self._plugin_violations)
        return PerformanceOverview(
            overall=resource_overview.overall,
            load=resource_overview.load,
            memory_pressure=resource_overview.memory_pressure,
            disk_pressure=resource_overview.disk_pressure,
            gpu_pressure=resource_overview.gpu_pressure,
            load_shedding_active=resource_overview.load_shedding_active,
            load_shedding_reasons=resource_overview.load_shedding_reasons,
            queue_count=len(self._queues),
            cache_count=len(self.caches.stats()),
            models_loaded=loaded,
            models_blocked=blocked,
            active_agents=agents,
            active_workflows=workflows,
            plugins_violating=plugin_violations,
            leak_indicators=self.leaks.leak_count(),
            regressions=len(self.regressions.list(classification="warning_regression"))
            + len(self.regressions.list(classification="budget_failure")),
            low_power_mode=resource_overview.low_power_mode,
            metered_network=resource_overview.metered_network,
            generated_at=generated_at,
            message="Real measured performance and resource state.",
            metrics=metrics,
        )

    def settings(self) -> Dict[str, Any]:
        return {
            "profile": self._profile,
            "hardware_profile": self._hardware_profile,
            "visual_quality": self._visual_quality,
            "animation_quality": self._animation_quality,
            "low_power_mode": self.resources.low_power(),
            "metered_network": self.resources.metered_network(),
            "indexing_paused": self.resources.indexing_paused(),
            "concurrency_limits": {item["scope"]: item["limit"] for item in self.concurrency.snapshot()},
            "max_resident_models": self.models.max_resident(),
            "model_idle_unload_seconds": self.models.idle_unload_seconds(),
            "memory_thresholds": {
                "elevated": self.resources.memory_threshold("elevated"),
                "high": self.resources.memory_threshold("high"),
                "critical": self.resources.memory_threshold("critical"),
            },
        }

    # ---- defaults ----

    def _register_default_queues(self) -> None:
        for queue_id, capacity, policy in (
            ("event_bus", 256, "coalesce"),
            ("notification_delivery", 128, "coalesce"),
            ("telemetry", 256, "latest_value"),
            ("logging", 256, "coalesce"),
            ("model_requests", 64, "reject"),
            ("agent_queue", 128, "reject"),
            ("workflow_queue", 128, "reject"),
            ("plugin_events", 256, "coalesce"),
            ("indexing_queue", 128, "reject"),
            ("mobile_sync", 128, "reject"),
            ("wearable_delivery", 128, "coalesce"),
        ):
            preserved = queue_id in ("notification_delivery", "event_bus")
            self._queues[queue_id] = BoundedQueue(
                queue_id,
                owner="performance",
                capacity=capacity,
                overflow_policy=policy,
                preserved_classes=("security_event", "approval_result", "cancellation_request", "audit_required", "final_task_state", "final_workflow_state"),
            )
            self.storage.upsert_queue({
                "queue_id": queue_id,
                "owner": "performance",
                "workload_class": "maintenance",
                "depth": 0,
                "limit": capacity,
                "oldest_wait_ms": 0.0,
                "average_wait_ms": 0.0,
                "rejected": 0,
                "cancelled": 0,
                "throughput_per_minute": 0.0,
                "backpressure": "none",
                "preserved": preserved,
                "updated_at": _now_iso(),
            })

    def _register_default_caches(self) -> None:
        for cache_id, owner, purpose, max_entries, ttl, invalidation in (
            ("model_metadata", "ai-runtime", "model inventory metadata", 64, 60.0, ("model_updated", "runtime_changed")),
            ("repository_summaries", "repository-intelligence", "project summaries", 32, 600.0, ("project_closed", "branch_changed", "commit_changed", "file_changed")),
            ("parsed_files", "repository-intelligence", "parsed file results", 128, 300.0, ("file_changed", "project_closed")),
            ("token_counts", "ai-runtime", "token counting results", 256, 600.0, ("file_changed", "project_closed")),
            ("search_results", "repository-intelligence", "bounded search result sets", 32, 60.0, ("file_changed", "branch_changed", "index_updated")),
            ("command_metadata", "command-registry", "command metadata", 64, 300.0, ("policy_changed",)),
            ("static_configuration", "joeos-core", "validated static configuration", 16, 900.0, ("policy_changed", "config_changed")),
            ("image_thumbnails", "workspace", "image thumbnails", 64, 1800.0, ("file_changed", "project_closed")),
            ("mobile_summaries", "mobile", "mobile command summaries", 16, 300.0, ("session_revoked", "permission_changed")),
            ("wearable_card_templates", "wearables", "wearable card templates", 32, 600.0, ("plugin_updated", "policy_changed")),
        ):
            self.caches.register(
                CacheRegistration(
                    cache_id=cache_id,
                    owner=owner,
                    purpose=purpose,
                    max_entries=max_entries,
                    ttl_seconds=ttl,
                    invalidation=invalidation,
                )
            )

    def _register_default_benchmarks(self) -> None:
        def _event_throughput():
            from .backpressure import BoundedQueue
            queue = BoundedQueue("bm", capacity=128, overflow_policy="coalesce")
            for i in range(5000):
                queue.push({"i": i}, eclass="ordinary")

        def _queue_ops():
            from .backpressure import BoundedQueue
            queue = BoundedQueue("bm", capacity=128, overflow_policy="reject")
            for i in range(2000):
                if queue.push({"i": i}, eclass="ordinary")[0]:
                    queue.pop()

        def _cache_eviction():
            from .caches import Cache, CacheRegistration
            cache = Cache(CacheRegistration(cache_id="bm", owner="perf", purpose="cache eviction benchmark", max_entries=128, max_bytes=64 * 1024))
            for i in range(1000):
                cache.put("key%d" % i, {"value": i}, size_bytes=64)

        def _scheduler_ops():
            scheduler = PriorityScheduler()
            for i in range(2000):
                scheduler.submit(Workload(workload_id="w%d" % i, wclass="maintenance", owner="bench"))
            for _ in range(2000):
                scheduler.next()

        def _db_query():
            import sqlite3, tempfile, pathlib
            path = pathlib.Path(tempfile.mkdtemp()) / "bm.db"
            connection = sqlite3.connect(str(path))
            connection.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
            connection.executemany("INSERT INTO t (v) VALUES (?)", [(str(i),) for i in range(500)])
            connection.commit()
            for i in range(500):
                connection.execute("SELECT v FROM t WHERE id = ?", (i % 500 + 1,))
            connection.close()

        for benchmark_id, fn in (
            ("event_bus.throughput", _event_throughput),
            ("queue.operations", _queue_ops),
            ("cache.eviction", _cache_eviction),
            ("scheduler.operations", _scheduler_ops),
            ("database.query", _db_query),
        ):
            self.benchmarks.register(benchmark_id, fn)


def _benchmark_to_dict(record) -> dict:
    return {
        "benchmark_id": record.benchmark_id,
        "title": record.title,
        "subsystem": record.subsystem,
        "scenario": record.scenario,
        "dataset": record.dataset,
        "fixture": record.fixture,
        "hardware_profile": record.hardware_profile,
        "software_version": record.software_version,
        "warm": record.warm,
        "iterations": record.iterations,
        "warmup": record.warmup,
        "measurement_method": record.measurement_method,
        "metric": record.metric,
        "result": record.result,
        "median": record.median,
        "variance": record.variance,
        "timestamp": record.timestamp,
        "commit_sha": record.commit,
        "artifact": record.artifact,
        "limitations": record.limitations,
        "budget_pass": record.budget_pass,
    }


def _queue_state_to_dict(state: QueueState) -> dict:
    return {
        "queue_id": state.queue_id,
        "owner": state.owner,
        "workload_class": state.workload_class,
        "depth": state.depth,
        "limit": state.limit,
        "oldest_wait_ms": state.oldest_wait_ms,
        "average_wait_ms": state.average_wait_ms,
        "rejected": state.rejected,
        "cancelled": state.cancelled,
        "throughput_per_minute": state.throughput_per_minute,
        "backpressure": state.backpressure,
        "preserved": state.preserved,
        "updated_at": _now_iso(),
    }


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _opt_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
