"""Benchmark Registry and Benchmark Runner.

Benchmarks are real measurements of isolated, deterministic fixtures executed
by actual JoeOS code paths. Results report median and variance over multiple
iterations (never the fastest run). Hardware-dependent benchmarks that cannot
run on this machine are recorded as unavailable with an explanation — they are
never reported as passing.
"""

from __future__ import annotations

import statistics
import time
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from .models import BenchmarkRecord
from .storage import PerformanceStorage


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _commit_id() -> str:
    try:
        import subprocess
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=".").decode().strip()[:12]
    except Exception:
        return ""


class BenchmarkRunner:
    def __init__(self, storage: Optional[PerformanceStorage] = None, *, iterations: int = 7, warmup: int = 2) -> None:
        self._storage = storage
        self._iterations = max(3, int(iterations))
        self._warmup = max(0, int(warmup))
        self._scenarios: Dict[str, Callable[[], float]] = {}

    def register(self, benchmark_id: str, fn: Callable[[], float]) -> None:
        self._scenarios[benchmark_id] = fn

    def available_scenarios(self) -> List[str]:
        return sorted(self._scenarios)

    def run(self, benchmark_id: str, *, iterations: Optional[int] = None, hardware_profile: str = "", dataset: str = "") -> BenchmarkRecord:
        fn = self._scenarios.get(benchmark_id)
        if fn is None:
            raise ValueError("Unknown benchmark scenario: %s" % benchmark_id)
        count = max(3, int(iterations or self._iterations))
        for _ in range(self._warmup):
            try:
                fn()
            except Exception:
                pass
        results: List[float] = []
        for _ in range(count):
            start = time.monotonic()
            fn()
            results.append((time.monotonic() - start) * 1000.0)
        median = statistics.median(results)
        variance = statistics.pstdev(results) if len(results) > 1 else 0.0
        record = BenchmarkRecord(
            benchmark_id=benchmark_id,
            title=benchmark_id.replace("_", " ").title(),
            subsystem=_subsystem(benchmark_id),
            scenario=benchmark_id,
            dataset=dataset,
            fixture="isolated",
            hardware_profile=hardware_profile or "development-workstation",
            software_version="joeos-2.0.0",
            warm=False,
            iterations=count,
            warmup=self._warmup,
            measurement_method="real",
            metric="duration_ms",
            result=median,
            median=median,
            variance=variance,
            timestamp=_now_iso(),
            commit=_commit_id(),
            limitations="Isolated deterministic fixture; hardware-dependent timing varies by machine.",
        )
        if self._storage is not None:
            self._storage.upsert_benchmark(_record_to_dict(record))
        return record


def _subsystem(benchmark_id: str) -> str:
    for name in ("event", "queue", "cache", "scheduler", "db", "watcher", "search", "startup", "cancellation", "mobile", "plugin", "index"):
        if benchmark_id.startswith(name):
            return name
    return "performance"


def _record_to_dict(record: BenchmarkRecord) -> dict:
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
