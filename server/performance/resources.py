"""Resource Governor and pressure policies.

Tracks measured resource state from the authoritative telemetry collector and
derives pressure states honestly. GPU, VRAM, battery, and thermal values are
only reported when actually measurable; otherwise they remain ``unknown``.
Load shedding follows a defined order and records every shed decision with a
reason so degradation is never hidden. Security, cancellation, approvals, and
final-state transitions are never shed.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from .models import (
    LOAD_SHEDDING_ORDER,
    PRESERVED_CLASSES,
    PerformanceOverview,
    PressureState,
    ResourceSnapshot,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PressureThresholds:
    def __init__(
        self,
        *,
        memory_elevated: float = 70.0,
        memory_high: float = 82.0,
        memory_critical: float = 92.0,
        disk_warning: float = 80.0,
        disk_high: float = 90.0,
        disk_critical: float = 95.0,
        cpu_busy: float = 70.0,
        cpu_critical: float = 88.0,
    ) -> None:
        self.memory_elevated = memory_elevated
        self.memory_high = memory_high
        self.memory_critical = memory_critical
        self.disk_warning = disk_warning
        self.disk_high = disk_high
        self.disk_critical = disk_critical
        self.cpu_busy = cpu_busy
        self.cpu_critical = cpu_critical


class ResourceGovernor:
    def __init__(
        self,
        thresholds: Optional[PressureThresholds] = None,
        *,
        now_provider=None,
    ) -> None:
        self._thresholds = thresholds or PressureThresholds()
        self._now = now_provider or time.monotonic
        self._lock = threading.RLock()
        self._snapshot = ResourceSnapshot()
        self._load = "unknown"
        self._shedding = False
        self._shed_reasons: List[str] = []
        self._shed_since = 0.0
        self._shed_log: List[dict] = []
        self._low_power = False
        self._metered_network = False
        self._indexing_paused = False

    def update_snapshot(self, snapshot: ResourceSnapshot) -> None:
        with self._lock:
            self._snapshot = snapshot
            self._recompute()
        return None

    def _recompute(self) -> None:
        snapshot = self._snapshot
        load = "unknown"
        if snapshot.cpu_available and snapshot.cpu_percent >= self._thresholds.cpu_critical:
            load = "critical"
        elif snapshot.cpu_available and snapshot.cpu_percent >= self._thresholds.cpu_busy:
            load = "busy"
        elif snapshot.cpu_available:
            load = "healthy"
        self._load = load
        reasons: List[str] = []
        if snapshot.memory_available and snapshot.memory_percent >= self._thresholds.memory_high:
            reasons.append("memory_high")
        if snapshot.disk_available and snapshot.disk_percent >= self._thresholds.disk_high:
            reasons.append("disk_high")
        if snapshot.battery_available and snapshot.battery_percent is not None and snapshot.battery_percent < 15:
            reasons.append("battery_low")
        if snapshot.thermal_available and snapshot.thermal_state == "high":
            reasons.append("thermal_high")
        if snapshot.network_state == "degraded":
            reasons.append("network_degraded")
        shedding = bool(reasons) or (self._load == "critical")
        if shedding and not self._shedding:
            self._shedding = True
            self._shed_since = self._now()
        elif not shedding and self._shedding:
            self._shedding = False
            self._shed_log.append({"action": "load_shedding_ended", "at": _now_iso(), "duration_ms": (self._now() - self._shed_since) * 1000.0})
        if reasons != self._shed_reasons:
            self._shed_reasons = reasons

    # ---- pressure states ----

    def memory_pressure(self) -> PressureState:
        with self._lock:
            snapshot = self._snapshot
            if not snapshot.memory_available:
                return PressureState(pressure="unknown", source=snapshot.source, available=False)
            percent = snapshot.memory_percent
            if percent >= self._thresholds.memory_critical:
                pressure = "critical"
            elif percent >= self._thresholds.memory_high:
                pressure = "high"
            elif percent >= self._thresholds.memory_elevated:
                pressure = "elevated"
            else:
                pressure = "normal"
            return PressureState(pressure=pressure, source=snapshot.source, available=True)

    def disk_pressure(self) -> PressureState:
        with self._lock:
            snapshot = self._snapshot
            if not snapshot.disk_available:
                return PressureState(pressure="unknown", source=snapshot.source, available=False)
            percent = snapshot.disk_percent
            if percent >= self._thresholds.disk_critical:
                pressure = "critical"
            elif percent >= self._thresholds.disk_high:
                pressure = "high"
            elif percent >= self._thresholds.disk_warning:
                pressure = "warning"
            else:
                pressure = "normal"
            return PressureState(pressure=pressure, source=snapshot.source, available=True)

    def gpu_pressure(self) -> PressureState:
        with self._lock:
            snapshot = self._snapshot
            if not snapshot.gpu_available:
                return PressureState(pressure="unknown", source=snapshot.source, available=False)
            return PressureState(pressure="normal", source=snapshot.source, available=True)

    def load_state(self) -> str:
        with self._lock:
            return self._load

    def snapshot(self) -> ResourceSnapshot:
        with self._lock:
            return self._snapshot

    def load_shedding_active(self) -> bool:
        with self._lock:
            return self._shedding

    def load_shedding_reasons(self) -> Tuple[str, ...]:
        with self._lock:
            return tuple(self._shed_reasons)

    def shed_order(self, workload_class: str) -> int:
        if workload_class in PRESERVED_CLASSES:
            return -1
        try:
            return LOAD_SHEDDING_ORDER.index(workload_class)
        except ValueError:
            return len(LOAD_SHEDDING_ORDER)

    def should_shed(self, workload_class: str) -> Tuple[bool, str]:
        """Decide whether a workload class should be shed under current load."""
        with self._lock:
            if not self._shedding:
                return False, ""
            if workload_class in PRESERVED_CLASSES:
                return False, ""
            order = self.shed_order(workload_class)
            if order < 0:
                return False, ""
            reason = "load_shedding" if self._shed_reasons else "critical_load"
            return True, reason

    def record_shed(self, workload_class: str, reason: str) -> None:
        with self._lock:
            self._shed_log.append({
                "action": "shed",
                "workload_class": workload_class,
                "reason": reason,
                "at": _now_iso(),
            })
            self._shed_log = self._shed_log[-200:]

    def shed_log(self) -> List[dict]:
        with self._lock:
            return list(self._shed_log)

    # ---- operational modes ----

    def set_low_power(self, enabled: bool) -> None:
        with self._lock:
            self._low_power = bool(enabled)

    def low_power(self) -> bool:
        with self._lock:
            return self._low_power

    def set_metered_network(self, enabled: bool) -> None:
        with self._lock:
            self._metered_network = bool(enabled)

    def metered_network(self) -> bool:
        with self._lock:
            return self._metered_network

    def set_indexing_paused(self, paused: bool) -> None:
        with self._lock:
            self._indexing_paused = bool(paused)

    def indexing_paused(self) -> bool:
        with self._lock:
            return self._indexing_paused

    def memory_threshold(self, level: str) -> float:
        with self._lock:
            thresholds = self._thresholds
        return {
            "elevated": thresholds.memory_elevated,
            "high": thresholds.memory_high,
            "critical": thresholds.memory_critical,
        }.get(level, 0.0)

    def overview(self, generated_at: str) -> PerformanceOverview:
        with self._lock:
            snapshot = self._snapshot
            memory = self.memory_pressure()
            disk = self.disk_pressure()
            gpu = self.gpu_pressure()
            shedding = self._shedding
            reasons = tuple(self._shed_reasons)
        state = self._load
        if not snapshot.cpu_available:
            state = "unknown"
        elif self._shedding:
            state = "constrained"
        return PerformanceOverview(
            overall=state,
            load=self._load,
            memory_pressure=memory,
            disk_pressure=disk,
            gpu_pressure=gpu,
            load_shedding_active=shedding,
            load_shedding_reasons=reasons,
            low_power_mode=self._low_power,
            metered_network=self._metered_network,
            generated_at=generated_at,
        )
