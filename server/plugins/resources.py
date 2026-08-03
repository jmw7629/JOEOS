"""Resource Governor for the JoeOS Plugin Platform.

Tracks and enforces per-plugin resource limits: active jobs, event rate, log
volume, call counts, and timeouts. Measured values are reported honestly
alongside configured estimates. Exceeding limits leads to throttle, pause,
cancel, disable, or quarantine per policy.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Dict, Optional, Tuple

from .models import ResourceLimits


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ResourceGovernor:
    """Bounded in-process counters for active extension load."""

    def __init__(self, now_provider=None) -> None:
        self._now = now_provider or time.monotonic
        self._lock = threading.RLock()
        self._active_jobs: Dict[str, int] = {}
        self._call_counts: Dict[str, list] = {}
        self._log_bytes: Dict[str, list] = {}

    def begin_job(self, *, plugin_id: str, limits: ResourceLimits) -> None:
        with self._lock:
            active = self._active_jobs.get(plugin_id, 0)
            if active >= limits.max_active_jobs:
                raise ResourceLimitError("plugin exceeded its active job limit.")
            self._active_jobs[plugin_id] = active + 1

    def end_job(self, *, plugin_id: str) -> None:
        with self._lock:
            active = max(0, self._active_jobs.get(plugin_id, 0) - 1)
            self._active_jobs[plugin_id] = active

    def active_jobs(self, *, plugin_id: str) -> int:
        with self._lock:
            return self._active_jobs.get(plugin_id, 0)

    def record_call(self, *, plugin_id: str, limits: ResourceLimits) -> None:
        window = 60.0
        now = self._now()
        with self._lock:
            counts = [stamp for stamp in self._call_counts.get(plugin_id, []) if now - stamp < window]
            if len(counts) >= max(1, limits.max_events_per_minute):
                raise ResourceLimitError("plugin exceeded its per-minute call budget.")
            counts.append(now)
            self._call_counts[plugin_id] = counts

    def record_log_bytes(self, *, plugin_id: str, amount: int, limits: ResourceLimits) -> None:
        window = 3600.0
        now = self._now()
        with self._lock:
            stamps = [stamp for stamp in self._log_bytes.get(plugin_id, []) if now - stamp < window]
            total = sum(bytes for _, bytes in stamps)
            if total + amount > limits.max_log_bytes_per_hour:
                raise ResourceLimitError("plugin exceeded its hourly log volume.")
            stamps.append((now, amount))
            self._log_bytes[plugin_id] = stamps

    def snapshot(self, *, plugin_id: str, limits: ResourceLimits) -> Dict[str, float]:
        with self._lock:
            active = self._active_jobs.get(plugin_id, 0)
            calls = len([s for s in self._call_counts.get(plugin_id, []) if self._now() - s < 60.0])
            logs = sum(b for _, b in self._log_bytes.get(plugin_id, []) if self._now() - b < 3600.0)
        return {
            "active_jobs": float(active),
            "calls_per_minute": float(calls),
            "log_bytes_per_hour": float(logs),
            "max_active_jobs": float(limits.max_active_jobs),
            "max_events_per_minute": float(limits.max_events_per_minute),
            "max_log_bytes_per_hour": float(limits.max_log_bytes_per_hour),
        }


class ResourceLimitError(RuntimeError):
    pass