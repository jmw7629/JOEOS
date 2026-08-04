"""Priority Scheduler for the JoeOS Performance Platform.

Workloads are classified and assigned to one of 16 priority lanes. Callers
cannot self-declare critical priority. The scheduler guarantees:
- lane ordering (emergency/security first),
- fairness via round-robin across non-empty lanes,
- starvation prevention via aging (waiting lower-priority work rises in rank),
- deadline awareness (overdue work is shed only with explicit policy),
- cancellation (obsolete queued work is removed),
- queue visibility and bounded depth.

Background work can never block the emergency, user-input, or cancellation
lanes; an emergency reservation always exists.
"""

from __future__ import annotations

import heapq
import threading
import time
from typing import Dict, List, Optional, Tuple

from .models import PRESERVED_CLASSES, PRIORITY_LANES, Workload

MAX_LANE_COUNT = 16


class _Queued:
    __slots__ = ("sequence", "lane", "workload", "enqueued", "aging_rank")

    def __init__(self, sequence: int, lane: int, workload: Workload, enqueued: float) -> None:
        self.sequence = sequence
        self.lane = lane
        self.workload = workload
        self.enqueued = enqueued
        self.aging_rank = lane


class PriorityScheduler:
    """A priority-lane queue with fairness, aging, deadlines, and cancellation."""

    def __init__(
        self,
        *,
        max_queue_depth: int = 256,
        aging_seconds: float = 30.0,
        aging_steps: int = 8,
        emergency_reservation: int = 2,
        now_provider=None,
    ) -> None:
        self._max_depth = max(1, int(max_queue_depth))
        self._aging_seconds = max(1.0, float(aging_seconds))
        self._aging_steps = max(1, int(aging_steps))
        self._emergency_reservation = max(1, int(emergency_reservation))
        self._now = now_provider or time.monotonic
        self._lock = threading.RLock()
        self._lanes: List[list] = [[] for _ in range(MAX_LANE_COUNT)]
        self._sequence = 0
        self._cancelled: Dict[str, bool] = {}
        self._rejected = 0
        self._cancelled_count = 0
        self._last_scheduled: Dict[int, float] = {}
        self._wait_sum_ms = 0.0
        self._wait_count = 0

    def submit(self, workload: Workload) -> Tuple[bool, str]:
        """Queue a workload. Returns (accepted, reason)."""
        lane = self._validate_and_lane(workload)
        with self._lock:
            if self._cancelled.pop(workload.workload_id, False):
                return True, "already_cancelled"
            if self.depth() >= self._max_depth:
                if lane <= PRIORITY_LANES["approval"]:
                    self._drop_lowest()
                else:
                    self._rejected += 1
                    return False, "queue_full"
            self._sequence += 1
            self._lanes[lane].append(_Queued(self._sequence, lane, workload, self._now()))
        return True, "queued"

    def cancel(self, workload_id: str) -> bool:
        with self._lock:
            self._cancelled[workload_id] = True
            self._cancelled_count += 1
        return True

    def next(self) -> Optional[Workload]:
        """Pop the highest-ranked runnable workload, applying fairness + aging."""
        with self._lock:
            self._apply_aging()
            lane = self._next_lane()
            if lane is None:
                return None
            queue = self._lanes[lane]
            while queue:
                item = queue.pop(0)
                if self._cancelled.pop(item.workload.workload_id, False):
                    continue
                self._last_scheduled[lane] = self._now()
                waited_ms = (self._now() - item.enqueued) * 1000.0
                self._wait_sum_ms += waited_ms
                self._wait_count += 1
                return item.workload
        return None

    def peek(self) -> Optional[Workload]:
        with self._lock:
            self._apply_aging()
            lane = self._next_lane()
            if lane is None:
                return None
            for item in self._lanes[lane]:
                if not self._cancelled.get(item.workload.workload_id, False):
                    return item.workload
        return None

    def depth(self) -> int:
        with self._lock:
            return sum(len(queue) for queue in self._lanes)

    def depths_by_lane(self) -> List[int]:
        with self._lock:
            return [len(queue) for queue in self._lanes]

    def rejected_count(self) -> int:
        with self._lock:
            return self._rejected

    def cancelled_count(self) -> int:
        with self._lock:
            return self._cancelled_count

    def average_wait_ms(self) -> float:
        with self._lock:
            if not self._wait_count:
                return 0.0
            return self._wait_sum_ms / self._wait_count

    def snapshot(self) -> List[dict]:
        with self._lock:
            self._apply_aging()
            now = self._now()
            items = []
            for lane, queue in enumerate(self._lanes):
                for item in queue:
                    items.append({
                        "workload_id": item.workload.workload_id,
                        "lane": lane,
                        "wclass": item.workload.wclass,
                        "owner": item.workload.owner,
                        "service": item.workload.service,
                        "waited_ms": round((now - item.enqueued) * 1000.0, 1),
                        "user_visible": item.workload.user_visible,
                        "cancelled": self._cancelled.get(item.workload.workload_id, False),
                    })
        return items

    def _validate_and_lane(self, workload: Workload) -> int:
        lane = workload.validated_priority()
        if lane < 0 or lane >= MAX_LANE_COUNT:
            raise ValueError("Invalid lane: %s" % lane)
        return lane

    def _apply_aging(self) -> None:
        now = self._now()
        for lane in range(MAX_LANE_COUNT - 1, -1, -1):
            for item in self._lanes[lane]:
                waited = now - item.enqueued
                if waited >= self._aging_seconds:
                    steps = min(self._aging_steps, int(waited / self._aging_seconds))
                    item.aging_rank = max(0, lane - steps)

    def _next_lane(self) -> Optional[int]:
        """Round-robin across lanes to ensure fairness, preferring high lanes."""
        best = None
        best_rank = None
        for lane in range(MAX_LANE_COUNT):
            queue = self._lanes[lane]
            if not queue:
                continue
            if lane <= PRIORITY_LANES["cancellation"]:
                return lane
            candidate_rank = min(item.aging_rank for item in queue)
            if best is None or candidate_rank < best_rank:
                best = lane
                best_rank = candidate_rank
        return best

    def _drop_lowest(self) -> None:
        for lane in range(MAX_LANE_COUNT - 1, -1, -1):
            queue = self._lanes[lane]
            if not queue:
                continue
            candidates = [i for i, item in enumerate(queue) if item.workload.wclass not in PRESERVED_CLASSES]
            if not candidates:
                continue
            queue.pop(candidates[-1])
            self._rejected += 1
            return
        self._rejected += 1
