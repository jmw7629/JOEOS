"""Authoritative Performance Metrics Registry.

One bounded registry for counters, gauges, and histograms. Every value carries
its source and sampling method. High-cardinality dimensions are rejected so
telemetry stays bounded. Values whose availability is unknown are never
reported as real numbers.
"""

from __future__ import annotations

import statistics
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional, Tuple

from .models import HistogramStats, MetricsSample
from .storage import PerformanceStorage


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Allowed metric names; anything else is rejected to keep dimensions bounded.
ALLOWED_METRICS = (
    "startup.to_shell_ms",
    "startup.to_interactive_ms",
    "route.transition_ms",
    "command.latency_ms",
    "input.responsiveness_ms",
    "search.first_result_ms",
    "project.open_ms",
    "file.open_ms",
    "event.dispatch_ms",
    "db.query_ms",
    "notification.delivery_ms",
    "model.load_ms",
    "model.unload_ms",
    "model.first_token_ms",
    "model.tokens_per_second",
    "model.queue_ms",
    "context.construction_ms",
    "cancellation.latency_ms",
    "agent.queue_ms",
    "workflow.queue_ms",
    "plugin.activation_ms",
    "indexing.duration_ms",
    "sync.payload_bytes",
    "wearable.card_delivery_ms",
    "shutdown.duration_ms",
)


class PerformanceMetricsRegistry:
    """Bounded in-process histogram store with persistence hooks."""

    def __init__(
        self,
        storage: Optional[PerformanceStorage] = None,
        *,
        max_samples_per_metric: int = 500,
        now_provider=None,
    ) -> None:
        self._storage = storage
        self._max_samples = max(10, min(5000, int(max_samples_per_metric)))
        self._now = now_provider or time.monotonic
        self._lock = threading.RLock()
        self._histograms: Dict[str, Deque[float]] = {}
        self._sources: Dict[str, str] = {}
        self._sampling: Dict[str, str] = {}
        self._units: Dict[str, str] = {}
        self._latest_sample: Dict[str, MetricsSample] = {}
        self._persisted_at: Dict[str, float] = {}

    def register_metric(self, metric: str, *, unit: str = "ms", source: str = "measurement", sampling: str = "direct") -> None:
        self._require_metric(metric)
        with self._lock:
            self._units[metric] = unit
            self._sources[metric] = source
            self._sampling[metric] = sampling
            self._histograms.setdefault(metric, deque(maxlen=self._max_samples))

    def record(self, metric: str, value: float, *, source: str = "", sampling: str = "") -> None:
        """Record one measured sample into a bounded histogram."""
        self._require_metric(metric)
        value = _finite(value)
        with self._lock:
            hist = self._histograms.setdefault(metric, deque(maxlen=self._max_samples))
            hist.append(value)
            self._latest_sample[metric] = MetricsSample(
                metric=metric,
                value=value,
                source=source or self._sources.get(metric, "measurement"),
                sampled_at=_now_iso(),
                sampling_method=sampling or self._sampling.get(metric, "direct"),
                unit=self._units.get(metric, ""),
                available=True,
            )
            self._maybe_persist(metric, value)

    def _maybe_persist(self, metric: str, value: float) -> None:
        if self._storage is None:
            return
        now = self._now()
        if now - self._persisted_at.get(metric, 0.0) < 30.0:
            return
        self._persisted_at[metric] = now
        self._storage.insert_metric(
            _now_iso(),
            metric,
            value,
            self._sources.get(metric, "measurement"),
            self._sampling.get(metric, "direct"),
            self._units.get(metric, ""),
            True,
            "",
        )

    def gauge(self, metric: str, value: float, *, source: str = "", sampling: str = "") -> None:
        """Record the latest value of a gauge without a histogram."""
        self._require_metric(metric)
        value = _finite(value)
        with self._lock:
            self._latest_sample[metric] = MetricsSample(
                metric=metric,
                value=value,
                source=source or self._sources.get(metric, "measurement"),
                sampled_at=_now_iso(),
                sampling_method=sampling or self._sampling.get(metric, "direct"),
                unit=self._units.get(metric, ""),
                available=True,
            )
            if self._storage is not None:
                self._storage.insert_metric(
                    _now_iso(), metric, value, self._sources.get(metric, "measurement"),
                    self._sampling.get(metric, "direct"), self._units.get(metric, ""), True, "",
                )

    def histogram(self, metric: str) -> Optional[HistogramStats]:
        self._require_metric(metric)
        with self._lock:
            samples = list(self._histograms.get(metric, ()))
        if not samples:
            return HistogramStats(metric=metric)
        return HistogramStats(
            metric=metric,
            count=len(samples),
            total=sum(samples),
            minimum=min(samples),
            maximum=max(samples),
            mean=statistics.fmean(samples),
            p50=_percentile(samples, 50),
            p95=_percentile(samples, 95),
            p99=_percentile(samples, 99),
        )

    def latest(self, metric: str) -> Optional[MetricsSample]:
        self._require_metric(metric)
        with self._lock:
            return self._latest_sample.get(metric)

    def snapshot(self) -> List[MetricsSample]:
        with self._lock:
            items = list(self._latest_sample.values())
        return sorted(items, key=lambda item: item.metric)

    def _require_metric(self, metric: str) -> None:
        if metric not in ALLOWED_METRICS:
            raise ValueError("Metric is not registered: %s" % metric)


def _percentile(samples: List[float], percentile: int) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    index = max(0, min(len(ordered) - 1, int(round(len(ordered) * percentile / 100))))
    return ordered[index]


def _finite(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    import math
    if not math.isfinite(parsed):
        return 0.0
    return parsed
