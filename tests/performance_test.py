import tempfile
import threading
import time
import unittest
from pathlib import Path

from server.performance import (
    AdmissionController,
    BoundedQueue,
    Cache,
    CacheRegistration,
    CacheRegistry,
    ConcurrencyGovernor,
    LeakDetectionService,
    ModelResourceManager,
    PerformanceMetricsRegistry,
    PerformanceService,
    PressureThresholds,
    PriorityScheduler,
    ResourceGovernor,
    Tracer,
    Workload,
)
from server.performance.models import ResourceSnapshot
from server.performance.scheduler import MAX_LANE_COUNT


def _memory_high_snapshot() -> ResourceSnapshot:
    return ResourceSnapshot(
        cpu_percent=30.0,
        memory_percent=88.0,
        disk_percent=40.0,
        cpu_available=True,
        memory_available=True,
        disk_available=True,
    )


def _memory_critical_snapshot() -> ResourceSnapshot:
    return ResourceSnapshot(
        cpu_percent=30.0,
        memory_percent=96.0,
        disk_percent=40.0,
        cpu_available=True,
        memory_available=True,
        disk_available=True,
    )


class WorkloadClassificationTests(unittest.TestCase):
    def test_known_classes_validate(self):
        workload = Workload(workload_id="w", wclass="model_inference", owner="ai")
        self.assertEqual(workload.validated_priority(), 5)

    def test_unknown_class_rejected(self):
        with self.assertRaises(ValueError):
            Workload(workload_id="w", wclass="not_a_class", owner="x").validated_priority()

    def test_caller_cannot_self_declare_priority(self):
        with self.assertRaises(ValueError):
            Workload(workload_id="w", wclass="foreground_action", owner="x", priority=0).validated_priority()

    def test_caller_cannot_self_declare_critical_lane(self):
        with self.assertRaises(ValueError):
            Workload(workload_id="w", wclass="emergency", owner="x").validated_priority()

    def test_security_response_maps_to_emergency_lane(self):
        self.assertEqual(Workload(workload_id="w", wclass="security_response", owner="x").validated_priority(), 0)


class PrioritySchedulerTests(unittest.TestCase):
    def test_lane_ordering(self):
        scheduler = PriorityScheduler()
        scheduler.submit(Workload(workload_id="low", wclass="speculative_preload", owner="x"))
        scheduler.submit(Workload(workload_id="high", wclass="foreground_action", owner="x"))
        first = scheduler.next()
        self.assertEqual(first.workload_id, "high")

    def test_fairness_round_robin_across_queued(self):
        scheduler = PriorityScheduler()
        for i in range(40):
            scheduler.submit(Workload(workload_id="low%d" % i, wclass="speculative_preload", owner="x"))
        for i in range(20):
            scheduler.submit(Workload(workload_id="mid%d" % i, wclass="repository_indexing", owner="x"))
        seen = []
        while True:
            item = scheduler.next()
            if item is None:
                break
            seen.append(item.workload_id)
        self.assertTrue(any(i.startswith("mid") for i in seen[:10]))

    def test_aging_raises_low_priority_work(self):
        now = [1000.0]

        def fake_now():
            return now[0]

        scheduler = PriorityScheduler(aging_seconds=10.0, aging_steps=20, now_provider=fake_now)
        scheduler.submit(Workload(workload_id="low", wclass="speculative_preload", owner="x"))
        now[0] += 100.0
        scheduler.submit(Workload(workload_id="high", wclass="repository_indexing", owner="x"))
        scheduler.submit(Workload(workload_id="high2", wclass="repository_indexing", owner="x"))
        self.assertEqual(scheduler.peek().workload_id, "low")

    def test_cancellation_removes_queued(self):
        scheduler = PriorityScheduler()
        scheduler.submit(Workload(workload_id="c", wclass="maintenance", owner="x"))
        scheduler.cancel("c")
        self.assertIsNone(scheduler.next())

    def test_emergency_lane_never_blocked_by_queue_full(self):
        scheduler = PriorityScheduler(max_queue_depth=2)
        scheduler.submit(Workload(workload_id="a", wclass="speculative_preload", owner="x"))
        scheduler.submit(Workload(workload_id="b", wclass="speculative_preload", owner="x"))
        accepted, _ = scheduler.submit(Workload(workload_id="secure", wclass="security_response", owner="x"))
        self.assertTrue(accepted)

    def test_queue_visibility(self):
        scheduler = PriorityScheduler()
        scheduler.submit(Workload(workload_id="a", wclass="maintenance", owner="x"))
        snapshot = scheduler.snapshot()
        self.assertEqual([item["workload_id"] for item in snapshot], ["a"])


class ConcurrencyGovernorTests(unittest.TestCase):
    def test_global_limit_enforced(self):
        governor = ConcurrencyGovernor({"global": 2})
        self.assertTrue(governor.acquire("global", "project-a"))
        self.assertTrue(governor.acquire("global", "project-b"))
        self.assertFalse(governor.acquire("global", "project-c"))
        governor.release("global", "project-a")
        self.assertTrue(governor.acquire("global", "project-d"))

    def test_service_limit_separate_from_global(self):
        governor = ConcurrencyGovernor({"service": 1, "global": 16})
        self.assertTrue(governor.acquire("service", "automation"))
        self.assertFalse(governor.acquire("service", "automation2"))
        self.assertTrue(governor.acquire("global", "anything"))

    def test_subsystem_cannot_raise_own_limit(self):
        governor = ConcurrencyGovernor({"model": 2})
        with self.assertRaises(ValueError):
            governor.set_limit("model", 10)

    def test_policy_override_allows_change(self):
        governor = ConcurrencyGovernor({"model": 2})
        governor.set_limit("model", 10, override=True)
        self.assertEqual(governor.limit("model"), 10)


class BackpressureTests(unittest.TestCase):
    def test_bounded_reject(self):
        queue = BoundedQueue("q", capacity=2, overflow_policy="reject")
        self.assertTrue(queue.push({"a": 1}, eclass="ordinary")[0])
        self.assertTrue(queue.push({"b": 2}, eclass="ordinary")[0])
        accepted, status = queue.push({"c": 3}, eclass="ordinary")
        self.assertFalse(accepted)
        self.assertEqual(status, "queue_full")
        self.assertEqual(queue.depth(), 2)

    def test_coalesce_replaces_duplicate(self):
        queue = BoundedQueue("q", capacity=4, overflow_policy="coalesce", coalesce_key=lambda item: item["key"])
        queue.push({"key": "x", "n": 1}, eclass="ordinary")
        accepted, status = queue.push({"key": "x", "n": 2}, eclass="ordinary")
        self.assertTrue(accepted)
        self.assertEqual(status, "coalesced")
        self.assertEqual(queue.depth(), 1)
        self.assertEqual(queue.peek()["n"], 2)

    def test_preserved_event_never_dropped(self):
        queue = BoundedQueue("q", capacity=1, overflow_policy="reject")
        queue.push({"x": 1}, eclass="ordinary")
        accepted, _ = queue.push({"x": 2}, eclass="security_event")
        self.assertTrue(accepted)
        self.assertEqual(queue.depth(), 1)
        self.assertEqual(queue.peek()["x"], 2)

    def test_latest_value_keeps_capacity(self):
        queue = BoundedQueue("q", capacity=2, overflow_policy="latest_value")
        queue.push({"i": 1}, eclass="ordinary")
        queue.push({"i": 2}, eclass="ordinary")
        queue.push({"i": 3}, eclass="ordinary")
        self.assertEqual(queue.depth(), 2)

    def test_producer_pause_blocks_but_preserves(self):
        queue = BoundedQueue("q", capacity=4, overflow_policy="reject")
        queue.set_paused(True)
        accepted, status = queue.push({"i": 1}, eclass="ordinary")
        self.assertFalse(accepted)
        self.assertEqual(status, "producer_paused")
        accepted, _ = queue.push({"i": 2}, eclass="security_event")
        self.assertTrue(accepted)


class ResourceGovernorTests(unittest.TestCase):
    def setUp(self):
        self.governor = ResourceGovernor()

    def test_unknown_memory_is_not_normal(self):
        state = self.governor.memory_pressure()
        self.assertEqual(state.pressure, "unknown")
        self.assertFalse(state.available)

    def test_memory_pressure_levels(self):
        self.governor.update_snapshot(
            ResourceSnapshot(memory_percent=50.0, cpu_available=True, memory_available=True, disk_available=True)
        )
        self.assertEqual(self.governor.memory_pressure().pressure, "normal")
        self.governor.update_snapshot(
            ResourceSnapshot(memory_percent=75.0, cpu_available=True, memory_available=True, disk_available=True)
        )
        self.assertEqual(self.governor.memory_pressure().pressure, "elevated")
        self.governor.update_snapshot(
            ResourceSnapshot(memory_percent=85.0, cpu_available=True, memory_available=True, disk_available=True)
        )
        self.assertEqual(self.governor.memory_pressure().pressure, "high")
        self.governor.update_snapshot(_memory_high_snapshot())
        self.assertEqual(self.governor.memory_pressure().pressure, "high")
        self.governor.update_snapshot(_memory_critical_snapshot())
        self.assertEqual(self.governor.memory_pressure().pressure, "critical")

    def test_disk_pressure_levels(self):
        self.governor.update_snapshot(
            ResourceSnapshot(disk_percent=85.0, cpu_available=True, memory_available=True, disk_available=True)
        )
        self.assertEqual(self.governor.disk_pressure().pressure, "warning")
        self.governor.update_snapshot(
            ResourceSnapshot(disk_percent=92.0, cpu_available=True, memory_available=True, disk_available=True)
        )
        self.assertEqual(self.governor.disk_pressure().pressure, "high")
        self.governor.update_snapshot(
            ResourceSnapshot(disk_percent=97.0, cpu_available=True, memory_available=True, disk_available=True)
        )
        self.assertEqual(self.governor.disk_pressure().pressure, "critical")

    def test_load_shedding_activates_on_high_memory(self):
        self.governor.update_snapshot(_memory_high_snapshot())
        self.assertTrue(self.governor.load_shedding_active())
        shed, reason = self.governor.should_shed("semantic_indexing")
        self.assertTrue(shed)

    def test_speculative_preload_shed_first(self):
        order = {
            "speculative_preload": self.governor.shed_order("speculative_preload"),
            "cleanup": self.governor.shed_order("cleanup"),
            "semantic_indexing": self.governor.shed_order("semantic_indexing"),
        }
        self.assertLess(order["speculative_preload"], order["semantic_indexing"])

    def test_security_response_never_shed(self):
        self.governor.update_snapshot(_memory_high_snapshot())
        shed, _ = self.governor.should_shed("security_response")
        self.assertFalse(shed)

    def test_foreground_never_shed(self):
        self.governor.update_snapshot(_memory_high_snapshot())
        shed, _ = self.governor.should_shed("foreground_action")
        self.assertFalse(shed)

    def test_shedding_stops_after_recovery(self):
        self.governor.update_snapshot(_memory_high_snapshot())
        self.assertTrue(self.governor.load_shedding_active())
        self.governor.update_snapshot(
            ResourceSnapshot(memory_percent=40.0, cpu_available=True, memory_available=True, disk_available=True)
        )
        self.assertFalse(self.governor.load_shedding_active())


class AdmissionControlTests(unittest.TestCase):
    def setUp(self):
        self.resources = ResourceGovernor()
        self.concurrency = ConcurrencyGovernor()
        self.admission = AdmissionController(self.resources, self.concurrency)
        self.resources.update_snapshot(
            ResourceSnapshot(memory_percent=50.0, cpu_available=True, memory_available=True, disk_available=True)
        )

    def test_normal_load_admits(self):
        workload = Workload(workload_id="w", wclass="model_inference", owner="ai")
        self.assertEqual(self.admission.evaluate(workload).decision, "admit")

    def test_high_memory_queues_background(self):
        self.resources.update_snapshot(_memory_high_snapshot())
        workload = Workload(workload_id="w", wclass="repository_indexing", owner="ix")
        self.assertEqual(self.admission.evaluate(workload).decision, "queue")

    def test_critical_memory_rejects(self):
        self.resources.update_snapshot(_memory_critical_snapshot())
        workload = Workload(workload_id="w", wclass="repository_indexing", owner="ix")
        self.assertEqual(self.admission.evaluate(workload).decision, "reject")

    def test_metered_network_reduces_quality_for_sync(self):
        self.resources.set_metered_network(True)
        workload = Workload(workload_id="w", wclass="mobile_sync", owner="mobile")
        self.assertEqual(self.admission.evaluate(workload).decision, "reduce_quality")

    def test_security_response_always_admits(self):
        self.resources.update_snapshot(_memory_high_snapshot())
        workload = Workload(workload_id="w", wclass="security_response", owner="security")
        self.assertEqual(self.admission.evaluate(workload).decision, "admit")

    def test_model_load_preflight_honest_when_memory_measured(self):
        decision = self.admission.estimate_model_load(1000.0 * 1024.0)
        self.assertIn(decision.decision, ("admit", "reject"))


class LeakDetectionTests(unittest.TestCase):
    def test_single_sample_is_not_a_leak(self):
        leaks = LeakDetectionService(min_samples=5)
        leaks.record("listeners", "core", 100.0)
        self.assertEqual(leaks.leak_count(), 0)

    def test_sustained_growth_is_a_leak(self):
        leaks = LeakDetectionService(min_samples=5, growth_threshold=0.1)
        for i in range(6):
            leaks.record("listeners", "core", 100.0 + i * 50.0)
        self.assertEqual(leaks.leak_count(), 1)
        indicator = leaks.indicators(state="leak")[0]
        self.assertEqual(indicator.kind, "listeners")
        self.assertEqual(indicator.owner, "core")

    def test_stable_series_is_not_a_leak(self):
        leaks = LeakDetectionService(min_samples=5, growth_threshold=0.1)
        for i in range(6):
            leaks.record("watchers", "workspace", 10.0)
        self.assertEqual(leaks.leak_count(), 0)

    def test_unsupported_kind_rejected(self):
        leaks = LeakDetectionService()
        with self.assertRaises(ValueError):
            leaks.record("not_a_kind", "x", 1.0)


class ModelResourceManagerTests(unittest.TestCase):
    def setUp(self):
        self.manager = ModelResourceManager(max_resident=1, idle_unload_seconds=0.0)

    def test_inventory_and_state(self):
        self.manager.sync_inventory([{"id": "qwen3", "runtime": "lemonade"}])
        states = self.manager.states()
        self.assertEqual(states[0].model_id, "qwen3")
        self.assertEqual(states[0].state, "installed")

    def test_max_resident_blocks_second_model(self):
        self.manager.sync_inventory([{"id": "a", "runtime": "lemonade"}, {"id": "b", "runtime": "lemonade"}])
        self.manager.mark_loaded("a")
        self.manager.mark_loaded("b")
        blocked = [m.model_id for m in self.manager.states() if m.state == "resource_blocked"]
        self.assertEqual(blocked, ["b"])
        self.assertEqual(self.manager.blocked_count(), 1)

    def test_oom_marks_resource_blocked_no_endless_retry(self):
        self.manager.sync_inventory([{"id": "a", "runtime": "lemonade"}])
        self.manager.mark_failed("a", out_of_memory=True)
        self.assertEqual(self.manager.states()[0].state, "resource_blocked")

    def test_idle_unload(self):
        self.manager.sync_inventory([{"id": "a", "runtime": "lemonade"}])
        self.manager.mark_loaded("a")
        self.manager.mark_busy("a")
        self.manager.mark_idle("a")
        self.manager.mark_idle_since("a")
        unloaded = self.manager.unload_idle()
        self.assertEqual(unloaded, ["a"])
        self.assertEqual(self.manager.loaded_count(), 0)

    def test_pinned_model_not_unloaded(self):
        self.manager.sync_inventory([{"id": "a", "runtime": "lemonade"}])
        self.manager.mark_loaded("a")
        self.manager.mark_idle("a")
        self.manager.mark_idle_since("a")
        self.manager.pin("a", True)
        self.assertEqual(self.manager.unload_idle(), [])
        self.manager.pin("a", False)
        self.assertEqual(self.manager.unload_idle(), ["a"])

    def test_never_unload_during_active_request(self):
        self.manager.sync_inventory([{"id": "a", "runtime": "lemonade"}])
        self.manager.mark_loaded("a")
        self.manager.mark_busy("a")
        self.assertEqual(self.manager.unload_idle(), [])


class CacheRegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = CacheRegistry()

    def test_register_and_bounds(self):
        self.registry.register(
            CacheRegistration(cache_id="parsed", owner="ix", purpose="parsed file results", max_entries=3, ttl_seconds=60)
        )
        cache = self.registry.cache("parsed")
        cache.put("a", {"v": 1}, size_bytes=10)
        cache.put("b", {"v": 2}, size_bytes=10)
        cache.put("c", {"v": 3}, size_bytes=10)
        cache.put("d", {"v": 4}, size_bytes=10)
        self.assertLessEqual(cache.size(), 3)

    def test_get_put_hit_miss(self):
        self.registry.register(
            CacheRegistration(cache_id="parsed", owner="ix", purpose="parsed file results", ttl_seconds=60)
        )
        self.assertEqual(self.registry.get("parsed", "k")[1], False)
        self.registry.put("parsed", "k", {"v": 1})
        value, hit = self.registry.get("parsed", "k")
        self.assertTrue(hit)
        self.assertEqual(value, {"v": 1})
        stats = self.registry.cache("parsed").stats()
        self.assertEqual(stats.hits, 1)
        self.assertEqual(stats.misses, 1)

    def test_ttl_expiry(self):
        import server.performance.caches as module
        now = [1000.0]

        def fake_now():
            return now[0]

        registry = CacheRegistry(now_provider=fake_now)
        registry.register(CacheRegistration(cache_id="c", owner="o", purpose="parsed file results", ttl_seconds=5.0))
        registry.put("c", "k", {"v": 1})
        now[0] += 6.0
        value, hit = registry.get("c", "k")
        self.assertFalse(hit)

    def test_invalidate_tag_permission_change(self):
        self.registry.register(
            CacheRegistration(cache_id="mobile", owner="mobile", purpose="mobile command summaries", invalidation=("permission_changed", "session_revoked"))
        )
        self.registry.put("mobile", "scope:a", {"v": 1})
        self.assertEqual(self.registry.invalidate_tag("permission_changed"), 1)
        value, hit = self.registry.get("mobile", "scope:a")
        self.assertFalse(hit)

    def test_security_sensitive_purpose_refused(self):
        with self.assertRaises(ValueError):
            self.registry.register(
                CacheRegistration(cache_id="approvals", owner="security", purpose="approval_result cache")
            )

    def test_clear_safe_skips_security_caches(self):
        self.registry.register(
            CacheRegistration(cache_id="safe", owner="ix", purpose="parsed file results", security_sensitive=False)
        )
        self.registry.register(
            CacheRegistration(cache_id="trust", owner="security", purpose="trust state cache", security_sensitive=True)
        )
        self.registry.put("safe", "k", 1)
        self.registry.put("trust", "k", 1)
        cleared = self.registry.clear_safe()
        self.assertEqual(cleared, 1)
        value, hit = self.registry.get("trust", "k")
        self.assertTrue(hit)


class MetricsRegistryTests(unittest.TestCase):
    def test_unregistered_metric_rejected(self):
        registry = PerformanceMetricsRegistry()
        with self.assertRaises(ValueError):
            registry.record("made_up.metric", 1.0)

    def test_histogram_percentiles(self):
        registry = PerformanceMetricsRegistry()
        for i in range(1, 101):
            registry.record("db.query_ms", float(i))
        stats = registry.histogram("db.query_ms")
        self.assertEqual(stats.count, 100)
        self.assertEqual(stats.minimum, 1.0)
        self.assertEqual(stats.maximum, 100.0)
        self.assertAlmostEqual(stats.p50, 50.0, delta=1.5)
        self.assertAlmostEqual(stats.p99, 99.0, delta=1.5)

    def test_snapshot_carries_source_and_availability(self):
        registry = PerformanceMetricsRegistry()
        registry.record("event.dispatch_ms", 1.5)
        sample = registry.latest("event.dispatch_ms")
        self.assertTrue(sample.available)
        self.assertEqual(sample.source, "measurement")


class TracerTests(unittest.TestCase):
    def test_span_records_queue_and_execution(self):
        tracer = Tracer()
        span = tracer.begin("automation", "run").set_queue_ms(5.0)
        time.sleep(0.001)
        span.finish()
        self.assertGreater(span.execution_ms, 0.0)
        self.assertGreater(span.queue_ms, 0.0)

    def test_trace_metadata_redacts_secrets(self):
        tracer = Tracer()
        span = tracer.begin("ai", "infer")
        with self.assertRaises(ValueError):
            span.add_metadata("secret", "s3cr3t")
        with self.assertRaises(ValueError):
            span.add_metadata("prompt", "hello")
        with self.assertRaises(ValueError):
            span.add_metadata("path", "/home/user/private")

    def test_cancelled_span_status(self):
        tracer = Tracer()
        span = tracer.begin("mobile", "sync")
        span.set_cancelled()
        span.finish()
        self.assertEqual(span.status, "cancelled")


class CacheUnitTests(unittest.TestCase):
    def test_eviction_budget(self):
        cache = Cache(CacheRegistration(cache_id="c", owner="o", purpose="parsed file results", max_bytes=100))
        cache.put("a", {"v": 1}, size_bytes=80)
        cache.put("b", {"v": 2}, size_bytes=80)
        self.assertLessEqual(cache.size(), 2)


class PerformanceServiceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.service = PerformanceService(str(Path(self.tempdir.name) / "perf"))

    def tearDown(self):
        self.tempdir.cleanup()

    def test_overview_honest_unknown(self):
        overview = self.service.overview()
        self.assertIn(overview.memory_pressure.pressure, ("unknown", "normal"))
        self.assertEqual(overview.gpu_pressure.available, False)

    def test_ingest_telemetry_then_measured(self):
        self.service.ingest_telemetry(
            {"cpu_percent": 22.0, "ram_percent": 48.0, "disk_percent": 33.0, "gpu_percent": None},
            {"available_models": ["qwen3"], "vram_gb": None},
        )
        self.assertEqual(self.service.resources.load_state(), "healthy")
        self.assertEqual(self.service.resources.memory_pressure().pressure, "normal")
        self.assertEqual(self.service.resources.gpu_pressure().available, False)

    def test_record_metric_and_budget(self):
        self.service.record("db.query_ms", 1.5)
        self.assertEqual(self.service.metrics.latest("db.query_ms").value, 1.5)

    def test_workload_submit_and_cancel(self):
        result = self.service.submit(Workload(workload_id="w", wclass="maintenance", owner="x"))
        self.assertIn(result["decision"], ("admit", "queue", "reject"))
        self.assertTrue(self.service.cancel_workload("w"))

    def test_default_queues_and_caches_registered(self):
        self.assertGreaterEqual(len(self.service._queues), 8)
        self.assertGreaterEqual(len(self.service.caches.stats()), 8)

    def test_benchmark_run_real_median(self):
        result = self.service.run_benchmark("event_bus.throughput")
        self.assertGreater(result["median"], 0.0)
        self.assertGreater(result["iterations"], 0)
        self.assertIn(result["regression"], ("improved", "unchanged_within_noise", "insufficient_samples", "warning_regression", "budget_failure", "incomparable"))

    def test_enqueue_preserved_event_survives_overflow(self):
        self.service.enqueue("event_bus", {"i": 1}, eclass="security_event")
        self.assertEqual(self.service.queue("event_bus").depth(), 1)

    def test_governance_blocks_unload(self):
        def blocked():
            return (True, "lockdown active")

        tempdir2 = tempfile.TemporaryDirectory()
        service = PerformanceService(str(Path(tempdir2.name) / "perf"), governance_blocked=blocked)
        try:
            with self.assertRaises(ValueError):
                service.unload_idle_models()
        finally:
            tempdir2.cleanup()

    def test_clear_safe_caches(self):
        self.service.register_cache(
            CacheRegistration(cache_id="parsed", owner="ix", purpose="parsed file results", security_sensitive=False)
        )
        self.service.put_cache("parsed", "k", {"v": 1})
        cleared = self.service.clear_safe_caches()
        self.assertGreaterEqual(cleared, 1)
        value, hit = self.service.get_cache("parsed", "k")
        self.assertFalse(hit)

    def test_low_resource_mode_toggles(self):
        self.service.enter_low_resource_mode()
        self.assertTrue(self.service.resources.low_power())
        self.service.exit_low_resource_mode()
        self.assertFalse(self.service.resources.low_power())

    def test_visual_quality_validation(self):
        self.service.set_visual_quality("reduced")
        with self.assertRaises(ValueError):
            self.service.set_visual_quality("glow")


if __name__ == "__main__":
    unittest.main()
