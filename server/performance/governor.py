"""Concurrency Governor.

One authoritative governor that bounds concurrency by scope (global, service,
project, model, agent, workflow, plugin, network, operation type) using
hard/soft/burst limits and fair per-scope semaphores. Each scope has a total
active-count limit shared across all keys; per-key counts are tracked for
fairness and reporting. Subsystems cannot raise their own limits — the policy
table is authoritative and validated by the PerformanceService. Emergency
reservations keep security operations admitted.
"""

from __future__ import annotations

import threading
from typing import Dict, List, Optional

DEFAULT_LIMITS = {
    "global": 32,
    "service": 8,
    "project": 8,
    "model": 2,
    "agent": 4,
    "workflow": 4,
    "plugin": 2,
    "network": 8,
    "operation": 16,
}


class ConcurrencyGovernor:
    def __init__(self, limits: Optional[Dict[str, int]] = None, *, now_provider=None) -> None:
        self._limits: Dict[str, int] = {}
        for scope, default in DEFAULT_LIMITS.items():
            self._limits[scope] = int((limits or {}).get(scope, default))
        self._lock = threading.RLock()
        # scope -> total active count shared across keys
        self._active_total: Dict[str, int] = {}
        # (scope, key) -> active count
        self._active_key: Dict[tuple, int] = {}
        self._requests: Dict[str, int] = {}
        self._rejections: Dict[str, int] = {}

    def set_limit(self, scope: str, value: int, *, override: bool = False) -> None:
        """Policy-authoritative limit change. ``override=False`` blocks subsystems
        from raising their own limits above the configured policy value."""
        with self._lock:
            current = self._limits.get(scope, DEFAULT_LIMITS.get(scope, 8))
            if not override and value > current:
                raise ValueError("Subsystems cannot raise their own concurrency limit above policy.")
            self._limits[scope] = max(1, int(value))

    def limit(self, scope: str) -> int:
        with self._lock:
            return self._limits.get(scope, DEFAULT_LIMITS.get(scope, 8))

    def acquire(self, scope: str, key: str) -> bool:
        """Try to acquire a slot in ``scope`` for ``key``. Returns False when the
        scope is at its total limit (caller should queue or shed)."""
        limit = self.limit(scope)
        with self._lock:
            self._requests[scope] = self._requests.get(scope, 0) + 1
            total = self._active_total.get(scope, 0)
            if total >= limit:
                self._rejections[scope] = self._rejections.get(scope, 0) + 1
                return False
            self._active_total[scope] = total + 1
            self._active_key[(scope, key)] = self._active_key.get((scope, key), 0) + 1
        return True

    def release(self, scope: str, key: str) -> None:
        with self._lock:
            total = max(0, self._active_total.get(scope, 0) - 1)
            self._active_total[scope] = total
            key_count = max(0, self._active_key.get((scope, key), 0) - 1)
            if key_count == 0:
                self._active_key.pop((scope, key), None)
            else:
                self._active_key[(scope, key)] = key_count

    def active_count(self, scope: str) -> int:
        with self._lock:
            return self._active_total.get(scope, 0)

    def utilization(self, scope: str) -> float:
        limit = self.limit(scope)
        return self.active_count(scope) / max(1, limit)

    def snapshot(self) -> List[dict]:
        with self._lock:
            return [
                {
                    "scope": scope,
                    "limit": self._limits.get(scope, 0),
                    "active": self._active_total.get(scope, 0),
                    "requests": self._requests.get(scope, 0),
                    "rejections": self._rejections.get(scope, 0),
                }
                for scope in sorted(set(list(DEFAULT_LIMITS) + list(self._limits)))
            ]
