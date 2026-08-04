"""Backpressure and bounded queues.

Bounded queues with explicit overflow policies. Preserved event classes
(security-critical, approvals, cancellation, audit, final task/workflow state)
are never dropped. When a queue overflows, the policy decides: reject with
retry guidance, coalesce duplicates, keep latest-value, or pause the producer.
Every drop/overflow is recorded so degradation is visible.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

PRESERVED_EVENT_CLASSES = (
    "security_event",
    "approval_result",
    "cancellation_request",
    "audit_required",
    "final_task_state",
    "final_workflow_state",
)


class BoundedQueue:
    def __init__(
        self,
        queue_id: str,
        *,
        owner: str = "",
        capacity: int = 128,
        overflow_policy: str = "reject",  # reject | coalesce | latest_value | pause_producer
        coalesce_key: Optional[Callable[[Any], str]] = None,
        preserved_classes: Tuple[str, ...] = PRESERVED_EVENT_CLASSES,
        now_provider=None,
    ) -> None:
        self.queue_id = queue_id
        self.owner = owner
        self.capacity = max(1, int(capacity))
        self.overflow_policy = overflow_policy
        self._coalesce_key = coalesce_key
        self._preserved = set(preserved_classes)
        self._now = now_provider or time.monotonic
        self._lock = threading.RLock()
        self._items: List[dict] = []
        self._produced = 0
        self._dropped = 0
        self._coalesced = 0
        self._consumed = 0
        self._wait_sum_ms = 0.0
        self._wait_count = 0
        self._paused = False
        self._window = _RateWindow()

    def push(self, item: Any, *, eclass: str = "ordinary") -> Tuple[bool, str]:
        """Attempt to enqueue. Returns (accepted, status)."""
        preserved = eclass in self._preserved
        now = self._now()
        with self._lock:
            if self._paused and not preserved:
                self._dropped += 1
                return False, "producer_paused"
            key = self._coalesce_key(item) if (self._coalesce_key and self.overflow_policy == "coalesce") else None
            if key is not None:
                for existing in self._items:
                    if existing.get("key") == key:
                        existing["value"] = item
                        existing["at"] = now
                        self._coalesced += 1
                        self._window.record(now)
                        return True, "coalesced"
            if len(self._items) >= self.capacity:
                if preserved:
                    self._items.pop(0)  # keep room for a required event; the evicted item is dropped
                    self._dropped += 1
                elif self.overflow_policy == "reject":
                    self._dropped += 1
                    self._window.record(now)
                    return False, "queue_full"
                elif self.overflow_policy == "latest_value":
                    self._items.pop(0)
                    self._dropped += 1
                else:
                    self._dropped += 1
                    self._window.record(now)
                    return False, "queue_full"
            self._items.append({"value": item, "at": now, "key": key, "eclass": eclass})
            self._produced += 1
            self._window.record(now)
            return True, "queued"

    def pop(self) -> Optional[Any]:
        with self._lock:
            if not self._items:
                return None
            item = self._items.pop(0)
            self._consumed += 1
            self._wait_sum_ms += (self._now() - item["at"]) * 1000.0
            self._wait_count += 1
            return item["value"]

    def peek(self) -> Optional[Any]:
        with self._lock:
            return self._items[0]["value"] if self._items else None

    def depth(self) -> int:
        with self._lock:
            return len(self._items)

    def paused(self) -> bool:
        with self._lock:
            return self._paused

    def set_paused(self, paused: bool) -> None:
        with self._lock:
            self._paused = bool(paused)

    def oldest_wait_ms(self) -> float:
        with self._lock:
            if not self._items:
                return 0.0
            return (self._now() - self._items[0]["at"]) * 1000.0

    def average_wait_ms(self) -> float:
        with self._lock:
            if not self._wait_count:
                return 0.0
            return self._wait_sum_ms / self._wait_count

    def throughput_per_minute(self) -> float:
        with self._lock:
            return self._window.rate(self._now)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "queue_id": self.queue_id,
                "owner": self.owner,
                "depth": len(self._items),
                "limit": self.capacity,
                "oldest_wait_ms": round(self.oldest_wait_ms(), 1),
                "average_wait_ms": round(self.average_wait_ms(), 1),
                "rejected": self._dropped,
                "cancelled": 0,
                "throughput_per_minute": round(self.throughput_per_minute(), 2),
                "backpressure": "paused" if self._paused else ("at_limit" if len(self._items) >= self.capacity else "none"),
                "preserved": bool(self._preserved),
                "dropped": self._dropped,
                "coalesced": self._coalesced,
            }


class _RateWindow:
    def __init__(self, window_seconds: float = 60.0) -> None:
        self.window = window_seconds
        self._stamps: List[float] = []

    def record(self, now: float) -> None:
        self._stamps = [stamp for stamp in self._stamps if now - stamp < self.window]
        self._stamps.append(now)

    def rate(self, now: float) -> float:
        self._stamps = [stamp for stamp in self._stamps if now - stamp < self.window]
        return len(self._stamps) * (60.0 / self.window)
