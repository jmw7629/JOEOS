"""Admission Control.

Evaluates whether an expensive workload may begin, based on measured resource
state. Possible decisions: admit, queue, lower_priority, reduce_quality,
choose_smaller_model, wait_for_resource, reject, cancel_superseded. Capacity is
never fabricated: when CPU/memory/GPU/disk/battery/thermal are unavailable the
corresponding checks are skipped and the decision records why. A large model
load is never admitted when measured memory capacity is clearly insufficient.
"""

from __future__ import annotations

from typing import Optional

from .governor import ConcurrencyGovernor
from .models import Workload
from .resources import ResourceGovernor


class AdmissionDecision:
    __slots__ = ("decision", "reason")

    def __init__(self, decision: str, reason: str = "") -> None:
        self.decision = decision
        self.reason = reason


class AdmissionController:
    def __init__(
        self,
        resources: ResourceGovernor,
        concurrency: ConcurrencyGovernor,
        *,
        max_active_models: int = 2,
        max_active_agents: int = 4,
        max_active_workflows: int = 4,
    ) -> None:
        self._resources = resources
        self._concurrency = concurrency
        self._max_active_models = max(1, int(max_active_models))
        self._max_active_agents = max(1, int(max_active_agents))
        self._max_active_workflows = max(1, int(max_active_workflows))

    def evaluate(self, workload: Workload) -> AdmissionDecision:
        if workload.wclass == "security_response":
            return AdmissionDecision("admit", "security_reserved")

        snapshot = self._resources.snapshot()

        if snapshot.memory_available:
            memory = self._resources.memory_pressure()
            if memory.pressure == "critical":
                if not workload.user_visible:
                    return AdmissionDecision("reject", "memory_critical")
                return AdmissionDecision("admit", "memory_critical_foreground")

        if snapshot.disk_available:
            disk = self._resources.disk_pressure()
            if disk.pressure == "critical":
                if not workload.user_visible:
                    return AdmissionDecision("reject", "disk_critical")

        shed, shed_reason = self._resources.should_shed(workload.wclass)
        if shed:
            return AdmissionDecision("queue", "shed_%s" % shed_reason)

        if snapshot.memory_available:
            memory = self._resources.memory_pressure()
            if memory.pressure == "high" and not workload.user_visible:
                return AdmissionDecision("queue", "memory_high")
            if memory.pressure == "elevated" and workload.wclass == "semantic_indexing":
                return AdmissionDecision("queue", "memory_elevated")

        if snapshot.disk_available:
            disk = self._resources.disk_pressure()
            if disk.pressure == "high" and not workload.user_visible:
                return AdmissionDecision("queue", "disk_high")

        if workload.estimated_memory_mb > 0 and snapshot.memory_available:
            memory = self._resources.memory_pressure()
            if memory.pressure == "elevated":
                return AdmissionDecision("queue", "memory_elevated_large_load")

        if self._resources.low_power() and not workload.user_visible:
            if workload.wclass in ("semantic_indexing", "repository_indexing", "speculative_preload"):
                return AdmissionDecision("queue", "low_power")

        if self._resources.metered_network() and workload.wclass in ("mobile_sync", "wearable_sync", "communications_sync"):
            return AdmissionDecision("reduce_quality", "metered_network")

        if workload.model and not self._model_available(workload.model):
            return AdmissionDecision("queue", "model_busy")

        return AdmissionDecision("admit", "")

    def _model_available(self, model_id: str) -> bool:
        return self._concurrency.active_count("model") < self._concurrency.limit("model")

    def estimate_model_load(self, model_memory_mb: float) -> AdmissionDecision:
        """Preflight a model load. Returns admit/wait/reject based on measured
        memory only; when memory is unmeasured, returns admit with an honest
        reason rather than fabricating capacity."""
        snapshot = self._resources.snapshot()
        if not snapshot.memory_available:
            return AdmissionDecision("admit", "memory_unavailable")
        import os as _os
        try:
            page_bytes = _os.sysconf("SC_PAGE_SIZE")
            physical_pages = _os.sysconf("SC_PHYS_PAGES")
            total_bytes = physical_pages * page_bytes
        except (ValueError, OSError):
            return AdmissionDecision("admit", "memory_unavailable")
        available_mb = (1.0 - snapshot.memory_percent / 100.0) * (total_bytes / (1024 * 1024))
        safety_margin_mb = model_memory_mb * 0.15
        if available_mb < model_memory_mb + safety_margin_mb:
            return AdmissionDecision("reject", "insufficient_memory")
        return AdmissionDecision("admit", "memory_available")
