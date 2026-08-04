"""Performance Platform REST API.

Reads are redacted, real measured state. Mutating actions (unload idle models,
clear safe caches, pause/resume indexing, low-resource mode, benchmark runs)
honor governance (Lockdown/Emergency Stop) and never expose private content.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from .models import Workload

router = APIRouter(prefix="/api/v1/performance", tags=["performance"])


def _get_service(request: Request):
    service = getattr(request.app.state, "performance_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Performance Platform is unavailable.")
    return service


def _as_bool(value: Any) -> bool:
    return bool(value)


@router.get("/overview")
def overview(request: Request) -> Dict[str, Any]:
    service = _get_service(request)
    overview = service.overview()
    return {
        "overall": overview.overall,
        "load": overview.load,
        "memory_pressure": _pressure(overview.memory_pressure),
        "disk_pressure": _pressure(overview.disk_pressure),
        "gpu_pressure": _pressure(overview.gpu_pressure),
        "load_shedding_active": overview.load_shedding_active,
        "load_shedding_reasons": list(overview.load_shedding_reasons),
        "queue_count": overview.queue_count,
        "cache_count": overview.cache_count,
        "models_loaded": overview.models_loaded,
        "models_blocked": overview.models_blocked,
        "active_agents": overview.active_agents,
        "active_workflows": overview.active_workflows,
        "plugins_violating": overview.plugins_violating,
        "leak_indicators": overview.leak_indicators,
        "regressions": overview.regressions,
        "low_power_mode": overview.low_power_mode,
        "metered_network": overview.metered_network,
        "generated_at": overview.generated_at,
        "message": overview.message,
        "metrics": {key: item.value for key, item in overview.metrics.items()},
    }


@router.get("/metrics")
def metrics(request: Request) -> Dict[str, Any]:
    service = _get_service(request)
    items = []
    for sample in service.metrics.snapshot():
        items.append(
            {
                "metric": sample.metric,
                "value": sample.value,
                "source": sample.source,
                "sampled_at": sample.sampled_at,
                "sampling_method": sample.sampling_method,
                "unit": sample.unit,
                "available": sample.available,
            }
        )
    histograms = {}
    for metric in sorted({item.metric for item in items}):
        stats = service.metrics.histogram(metric)
        if stats and stats.count:
            histograms[metric] = {
                "count": stats.count,
                "min": stats.minimum,
                "max": stats.maximum,
                "mean": stats.mean,
                "p50": stats.p50,
                "p95": stats.p95,
                "p99": stats.p99,
            }
    return {"samples": items, "histograms": histograms, "generated_at": _now_iso()}


@router.get("/resources")
def resources(request: Request) -> Dict[str, Any]:
    service = _get_service(request)
    snapshot = service.resources.snapshot()
    return {
        "cpu_percent": snapshot.cpu_percent if snapshot.cpu_available else None,
        "memory_percent": snapshot.memory_percent if snapshot.memory_available else None,
        "gpu_percent": snapshot.gpu_percent if snapshot.gpu_available else None,
        "vram_gb": snapshot.vram_gb,
        "disk_percent": snapshot.disk_percent if snapshot.disk_available else None,
        "temperature": snapshot.temperature,
        "power_state": snapshot.power_state,
        "thermal_state": snapshot.thermal_state,
        "network_state": snapshot.network_state,
        "gpu_available": snapshot.gpu_available,
        "battery_available": snapshot.battery_available,
        "thermal_available": snapshot.thermal_available,
        "source": snapshot.source,
        "sampled_at": snapshot.sampled_at,
        "memory_pressure": _pressure(service.resources.memory_pressure()),
        "disk_pressure": _pressure(service.resources.disk_pressure()),
        "gpu_pressure": _pressure(service.resources.gpu_pressure()),
        "load": service.resources.load_state(),
    }


@router.get("/queues")
def queues(request: Request) -> Dict[str, Any]:
    service = _get_service(request)
    return {"queues": [_queue(q) for q in service.queue_snapshots()], "generated_at": _now_iso()}


@router.get("/caches")
def caches(request: Request) -> Dict[str, Any]:
    service = _get_service(request)
    return {"caches": [_cache(c) for c in service.caches.stats()], "generated_at": _now_iso()}


@router.get("/models")
def models(request: Request) -> Dict[str, Any]:
    service = _get_service(request)
    return {
        "models": [_model(m) for m in service.models.states()],
        "max_resident": service.models.max_resident(),
        "idle_unload_seconds": service.models.idle_unload_seconds(),
        "generated_at": _now_iso(),
    }


@router.get("/benchmarks")
def benchmarks(request: Request, subsystem: str = "") -> Dict[str, Any]:
    service = _get_service(request)
    rows = service.storage.list_benchmarks(subsystem=subsystem)
    return {"benchmarks": rows, "scenarios": service.benchmarks.available_scenarios(), "generated_at": _now_iso()}


@router.get("/budgets")
def budgets(request: Request, platform: str = "", hardware_profile: str = "") -> Dict[str, Any]:
    service = _get_service(request)
    return {"budgets": [_budget(b) for b in service.budgets.list(platform=platform, hardware_profile=hardware_profile)]}


@router.get("/regressions")
def regressions(request: Request, classification: str = "") -> Dict[str, Any]:
    service = _get_service(request)
    return {"regressions": [_regression(r) for r in service.regressions.list(classification=classification)]}


@router.get("/traces")
def traces(request: Request, service_name: str = "", operation: str = "") -> Dict[str, Any]:
    service = _get_service(request)
    return {"traces": service.tracer.recent(service=service_name, operation=operation, limit=200)}


@router.get("/leaks")
def leaks(request: Request) -> Dict[str, Any]:
    service = _get_service(request)
    return {"indicators": [_leak(l) for l in service.leaks.indicators()], "generated_at": _now_iso()}


@router.get("/settings")
def settings(request: Request) -> Dict[str, Any]:
    service = _get_service(request)
    return {"settings": service.settings()}


@router.post("/benchmarks/run")
def run_benchmark(request: Request, payload: dict) -> Dict[str, Any]:
    service = _get_service(request)
    benchmark_id = str(payload.get("benchmark_id") or "").strip()
    if not benchmark_id:
        raise HTTPException(status_code=400, detail="benchmark_id is required.")
    if benchmark_id not in service.benchmarks.available_scenarios():
        raise HTTPException(status_code=404, detail="Unknown benchmark scenario.")
    hardware_profile = str(payload.get("hardware_profile") or "").strip()
    iterations = payload.get("iterations")
    if iterations is not None:
        try:
            iterations = max(3, int(iterations))
        except (TypeError, ValueError):
            iterations = None
    return service.run_benchmark(benchmark_id, hardware_profile=hardware_profile, iterations=iterations)


@router.post("/actions")
def perform_action(request: Request, payload: dict) -> Dict[str, Any]:
    service = _get_service(request)
    action = str(payload.get("action") or "").strip()
    blocked, reason = service.governance_blocked()
    if action in (
        "unload-idle-models",
        "clear-safe-caches",
        "pause-indexing",
        "resume-indexing",
        "enter-low-resource-mode",
        "exit-low-resource-mode",
    ) and blocked:
        raise HTTPException(status_code=409, detail="governance: %s" % reason)
    if action == "unload-idle-models":
        unloaded = service.unload_idle_models()
        return {"action": action, "unloaded_models": unloaded}
    if action == "clear-safe-caches":
        cleared = service.clear_safe_caches()
        return {"action": action, "cleared_caches": cleared}
    if action == "pause-indexing":
        service.set_pause_indexing(True)
        return {"action": action, "paused": True}
    if action == "resume-indexing":
        service.set_pause_indexing(False)
        return {"action": action, "paused": False}
    if action == "enter-low-resource-mode":
        service.enter_low_resource_mode()
        return {"action": action, "enabled": True}
    if action == "exit-low-resource-mode":
        service.exit_low_resource_mode()
        return {"action": action, "enabled": False}
    if action == "record-metric":
        metric = str(payload.get("metric") or "").strip()
        if not metric:
            raise HTTPException(status_code=400, detail="metric is required.")
        try:
            value = float(payload.get("value", 0.0))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="value must be numeric.")
        service.record(metric, value)
        return {"action": action, "metric": metric, "value": value}
    if action == "submit-workload":
        workload = _workload_from_payload(payload)
        return service.submit(workload)
    if action == "record-leak-sample":
        kind = str(payload.get("kind") or "").strip()
        owner = str(payload.get("owner") or "unknown").strip()
        try:
            current = float(payload.get("current", 0.0))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="current must be numeric.")
        service.record_leak_sample(kind, owner, current)
        return {"action": action, "kind": kind, "owner": owner, "current": current}
    raise HTTPException(status_code=400, detail="Unknown performance action.")


def _workload_from_payload(payload: dict) -> Workload:
    wclass = str(payload.get("wclass") or "").strip()
    if not wclass:
        raise HTTPException(status_code=400, detail="wclass is required.")
    workload_id = str(payload.get("workload_id") or "workload-%d" % _counter())
    owner = str(payload.get("owner") or "user").strip()
    try:
        return Workload(
            workload_id=workload_id,
            wclass=wclass,
            owner=owner,
            service=str(payload.get("service") or ""),
            project=str(payload.get("project") or ""),
            user_visible=bool(payload.get("user_visible", False)),
            estimated_memory_mb=float(payload.get("estimated_memory_mb", 0.0) or 0.0),
            estimated_disk_mb=float(payload.get("estimated_disk_mb", 0.0) or 0.0),
            timeout=float(payload.get("timeout", 0.0) or 0.0),
            cancellable=bool(payload.get("cancellable", True)),
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid workload declaration.")


def _pressure(pressure) -> Dict[str, Any]:
    return {"pressure": pressure.pressure, "source": pressure.source, "available": pressure.available}


def _queue(queue) -> Dict[str, Any]:
    return {
        "queue_id": queue.queue_id,
        "owner": queue.owner,
        "depth": queue.depth,
        "limit": queue.limit,
        "oldest_wait_ms": queue.oldest_wait_ms,
        "average_wait_ms": queue.average_wait_ms,
        "rejected": queue.rejected,
        "cancelled": queue.cancelled,
        "throughput_per_minute": queue.throughput_per_minute,
        "backpressure": queue.backpressure,
        "preserved": queue.preserved,
    }


def _cache(cache) -> Dict[str, Any]:
    return {
        "cache_id": cache.cache_id,
        "owner": cache.owner,
        "entries": cache.entries,
        "bytes_used": cache.bytes_used,
        "maximum_bytes": cache.maximum_bytes,
        "maximum_entries": cache.maximum_entries,
        "hits": cache.hits,
        "misses": cache.misses,
        "evictions": cache.evictions,
        "ttl_seconds": cache.ttl_seconds,
        "last_cleanup": cache.last_cleanup,
    }


def _model(model) -> Dict[str, Any]:
    return {
        "model_id": model.model_id,
        "runtime": model.runtime,
        "state": model.state,
        "active_requests": model.active_requests,
        "queue_depth": model.queue_depth,
        "last_use": model.last_use,
        "pinned": model.pinned,
        "estimated_memory_mb": model.estimated_memory_mb,
        "actual_memory_mb": model.actual_memory_mb,
        "footprint_source": model.footprint_source,
    }


def _budget(budget) -> Dict[str, Any]:
    return {
        "budget_id": budget.budget_id,
        "platform": budget.platform,
        "hardware_profile": budget.hardware_profile,
        "metric": budget.metric,
        "target": budget.target,
        "warning_threshold": budget.warning_threshold,
        "failure_threshold": budget.failure_threshold,
        "measurement_method": budget.measurement_method,
        "owner": budget.owner,
        "version": budget.version,
        "exceptions": budget.exceptions,
        "review_date": budget.review_date,
        "direction": budget.direction,
    }


def _regression(regression) -> Dict[str, Any]:
    return {
        "regression_id": regression.regression_id,
        "benchmark_id": regression.benchmark_id,
        "baseline_commit": regression.baseline_commit,
        "current_commit": regression.current_commit,
        "baseline_median": regression.baseline_median,
        "current_median": regression.current_median,
        "variance": regression.variance,
        "confidence": regression.confidence,
        "classification": regression.classification,
    }


def _leak(leak) -> Dict[str, Any]:
    return {
        "indicator_id": leak.indicator_id,
        "owner": leak.owner,
        "kind": leak.kind,
        "baseline": leak.baseline,
        "current": leak.current,
        "growth_rate": leak.growth_rate,
        "state": leak.state,
        "message": leak.message,
    }


_counter_state = {"n": 0}


def _counter() -> int:
    _counter_state["n"] += 1
    return _counter_state["n"]


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
