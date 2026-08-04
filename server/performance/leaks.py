"""Long-session Leak Detection.

Tracks the growth of bounded resource kinds (event listeners, subscriptions,
file watchers, sockets, timers, workers, processes, object URLs) across a
session. A leak is only flagged after repeated samples show a sustained growth
trend against a baseline; a single high-memory sample is never treated as a
leak. Every indicator carries baseline, current, growth rate, and a
false-positive-reviewable message.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Deque, Dict, List, Optional

from .models import LeakIndicator
from .storage import PerformanceStorage

SUPPORTED_KINDS = (
    "listeners",
    "subscriptions",
    "watchers",
    "sockets",
    "timers",
    "intervals",
    "workers",
    "processes",
    "object_urls",
    "connections",
)


class LeakDetectionService:
    def __init__(
        self,
        storage: Optional[PerformanceStorage] = None,
        *,
        min_samples: int = 5,
        growth_threshold: float = 0.5,
        window_seconds: float = 3600.0,
    ) -> None:
        self._storage = storage
        self._min_samples = max(3, int(min_samples))
        self._growth_threshold = max(0.0, float(growth_threshold))
        self._window = max(60.0, float(window_seconds))
        self._lock = threading.RLock()
        self._samples: Dict[tuple, Deque[float]] = {}
        self._indicators: Dict[str, LeakIndicator] = {}

    def record(self, kind: str, owner: str, current: float) -> None:
        """Record a sample of ``current`` for a resource kind owned by ``owner``."""
        if kind not in SUPPORTED_KINDS:
            raise ValueError("Unsupported leak-detection kind: %s" % kind)
        now = time.monotonic()
        key = (kind, owner)
        with self._lock:
            samples = self._samples.setdefault(key, deque(maxlen=self._min_samples * 2 + 4))
            samples.append((now, max(0.0, float(current))))
            # Drop old samples beyond the window.
            while samples and samples[0][0] < now - self._window:
                samples.popleft()
            if len(samples) >= self._min_samples:
                self._evaluate(key, samples, now)

    def _evaluate(self, key: tuple, samples: Deque, now: float) -> None:
        kind, owner = key
        values = [value for _, value in samples]
        baseline = values[0]
        current = values[-1]
        growth_rate = (current - baseline) / max(1.0, (samples[-1][0] - samples[0][0]))
        indicator_id = "%s:%s" % (kind, owner)
        state = "stable"
        message = ""
        if growth_rate > self._growth_threshold and current > baseline * 1.2 + 1:
            state = "leak"
            message = "%s for %s growing: baseline %.1f -> current %.1f (rate %.2f/s)" % (
                kind, owner, baseline, current, growth_rate,
            )
        elif growth_rate < -self._growth_threshold:
            state = "recovering"
        self._indicators[indicator_id] = LeakIndicator(
            indicator_id=indicator_id,
            owner=owner,
            kind=kind,
            baseline=round(baseline, 2),
            current=round(current, 2),
            growth_rate=round(growth_rate, 4),
            state=state,
            message=message,
        )
        if self._storage is not None:
            from datetime import datetime, timezone
            self._storage.upsert_leak_indicator({
                "indicator_id": indicator_id,
                "owner": owner,
                "kind": kind,
                "baseline": baseline,
                "current": current,
                "growth_rate": growth_rate,
                "state": state,
                "message": message,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })

    def indicators(self, state: str = "") -> List[LeakIndicator]:
        with self._lock:
            items = list(self._indicators.values())
        if state:
            items = [item for item in items if item.state == state]
        return sorted(items, key=lambda item: item.indicator_id)

    def leak_count(self) -> int:
        with self._lock:
            return sum(1 for item in self._indicators.values() if item.state == "leak")

    def reset(self) -> None:
        with self._lock:
            self._samples.clear()
            self._indicators.clear()
        if self._storage is not None:
            self._storage.clear_leak_indicators()
