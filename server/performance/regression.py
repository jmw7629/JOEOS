"""Regression Analyzer.

Compares current benchmark medians against a stored baseline. A difference is
only classified as a regression when it exceeds the measured variance by a
safety factor; otherwise it is ``unchanged_within_noise`` or
``insufficient_samples``. No automatic release block is applied based on
unreliable measurements.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .models import BenchmarkRecord, RegressionRecord
from .storage import PerformanceStorage

CLASSIFICATIONS = (
    "improved",
    "unchanged_within_noise",
    "warning_regression",
    "budget_failure",
    "incomparable",
    "insufficient_samples",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RegressionAnalyzer:
    def __init__(self, storage: Optional[PerformanceStorage] = None, *, safety_factor: float = 2.0, variance_floor: float = 0.15) -> None:
        self._storage = storage
        self._safety = max(1.0, float(safety_factor))
        self._floor = max(0.01, float(variance_floor))
        self._cache: Dict[str, Optional[BenchmarkRecord]] = {}

    def compare(self, benchmark_id: str, current: BenchmarkRecord, baseline: Optional[BenchmarkRecord] = None) -> RegressionRecord:
        baseline_record = baseline or self._load_baseline(benchmark_id)
        if baseline_record is None:
            classification = "insufficient_samples"
            confidence = "low"
            baseline_median = 0.0
            variance = current.variance
        elif baseline_record.hardware_profile != current.hardware_profile:
            classification = "incomparable"
            confidence = "low"
            baseline_median = baseline_record.median
            variance = max(current.variance, baseline_record.variance)
        else:
            baseline_median = baseline_record.median
            variance = max(current.variance, baseline_record.variance)
            delta = current.median - baseline_median
            noise = max(self._floor * baseline_median, self._safety * variance)
            if abs(delta) <= noise:
                classification = "unchanged_within_noise"
                confidence = "medium" if current.iterations >= 7 else "low"
            elif delta < 0:
                classification = "improved"
                confidence = "medium"
            elif delta > noise * 2:
                classification = "budget_failure" if current.budget_pass is False else "warning_regression"
                confidence = "medium"
            else:
                classification = "warning_regression"
                confidence = "low"
        record = RegressionRecord(
            regression_id=_id(benchmark_id, current.commit, baseline_median, current.median),
            benchmark_id=benchmark_id,
            baseline_commit=baseline_record.commit if baseline_record else "",
            current_commit=current.commit or "",
            baseline_median=baseline_median,
            current_median=current.median,
            variance=variance,
            confidence=confidence,
            classification=classification,
        )
        if self._storage is not None:
            self._storage.insert_regression(_record_to_dict(record))
        return record

    def _load_baseline(self, benchmark_id: str) -> Optional[BenchmarkRecord]:
        if self._storage is None:
            return None
        if benchmark_id in self._cache:
            return self._cache[benchmark_id]
        baseline = None
        for row in self._storage.list_benchmarks(subsystem=_subsystem(benchmark_id), limit=200):
            if row["benchmark_id"] != benchmark_id:
                continue
            baseline = _benchmark_from_row(row)
            break
        self._cache[benchmark_id] = baseline
        return baseline

    def list(self, classification: str = "") -> List[RegressionRecord]:
        if self._storage is None:
            return []
        rows = self._storage.list_regressions(classification=classification)
        return [
            RegressionRecord(
                regression_id=row["regression_id"],
                benchmark_id=row["benchmark_id"],
                baseline_commit=row["baseline_commit"],
                current_commit=row["current_commit"],
                baseline_median=row["baseline_median"],
                current_median=row["current_median"],
                variance=row["variance"],
                confidence=row["confidence"],
                classification=row["classification"],
            )
            for row in rows
        ]


def _subsystem(benchmark_id: str) -> str:
    for name in ("event", "queue", "cache", "scheduler", "db", "watcher", "search", "startup", "cancellation", "mobile", "plugin", "index"):
        if benchmark_id.startswith(name):
            return name
    return "performance"


def _benchmark_from_row(row: dict) -> BenchmarkRecord:
    return BenchmarkRecord(
        benchmark_id=row["benchmark_id"],
        title=row["title"],
        subsystem=row["subsystem"],
        scenario=row["scenario"],
        dataset=row["dataset"],
        fixture=row["fixture"],
        hardware_profile=row["hardware_profile"],
        software_version=row["software_version"],
        warm=bool(row["warm"]),
        iterations=row["iterations"],
        warmup=row["warmup"],
        measurement_method=row["measurement_method"],
        metric=row["metric"],
        result=row["result"],
        median=row["median"],
        variance=row["variance"],
        timestamp=row["timestamp"],
        commit=row["commit_sha"],
        artifact=row["artifact"],
        limitations=row["limitations"],
        budget_pass=row["budget_pass"],
    )


def _record_to_dict(record: RegressionRecord) -> dict:
    return {
        "regression_id": record.regression_id,
        "benchmark_id": record.benchmark_id,
        "baseline_commit": record.baseline_commit,
        "current_commit": record.current_commit,
        "baseline_median": record.baseline_median,
        "current_median": record.current_median,
        "variance": record.variance,
        "confidence": record.confidence,
        "classification": record.classification,
        "created_at": _now_iso(),
    }


def _id(benchmark_id: str, commit: str, baseline: float, current: float) -> str:
    material = "%s|%s|%s|%s" % (benchmark_id, commit, round(baseline, 4), round(current, 4))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
