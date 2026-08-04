"""Performance Budget Registry.

Versioned budgets scoped by platform and hardware profile. Each budget has a
target, warning threshold, and failure threshold with an explicit measurement
method. There is no single universal budget for all hardware — budgets without
a matching profile are classified ``incomparable`` rather than failed.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .models import BudgetRecord
from .storage import PerformanceStorage

# Hardware profiles the registry is aware of. Devices we cannot measure are
# still declared so expectations are explicit, but checks against them are
# classified incomparable rather than fabricating a pass/fail.
HARDWARE_PROFILES = (
    "development-workstation",
    "high-memory-ai-workstation",
    "standard-laptop",
    "low-power-laptop",
    "mobile-simulator",
    "older-supported-phone",
    "tablet",
    "remote-browser-session",
    "low-bandwidth-wearable",
)

_DEFAULT_BUDGETS = [
    # budget_id, platform, hardware_profile, metric, target, warning, failure, direction
    ("budget.startup.shell", "web", "development-workstation", "startup.to_shell_ms", 4000.0, 8000.0, 15000.0, "lower_is_better"),
    ("budget.startup.interactive", "web", "development-workstation", "startup.to_interactive_ms", 12000.0, 20000.0, 35000.0, "lower_is_better"),
    ("budget.search.first_result", "web", "development-workstation", "search.first_result_ms", 300.0, 750.0, 2000.0, "lower_is_better"),
    ("budget.event.dispatch", "core", "development-workstation", "event.dispatch_ms", 5.0, 15.0, 50.0, "lower_is_better"),
    ("budget.cancellation.latency", "core", "development-workstation", "cancellation.latency_ms", 150.0, 400.0, 1000.0, "lower_is_better"),
    ("budget.db.query", "core", "development-workstation", "db.query_ms", 5.0, 25.0, 100.0, "lower_is_better"),
    ("budget.model.first_token", "ai", "development-workstation", "model.first_token_ms", 5000.0, 15000.0, 40000.0, "lower_is_better"),
    ("budget.mobile.reconnect", "mobile", "mobile-simulator", "sync.payload_bytes", 1024.0 * 1024.0, 4.0 * 1024.0 * 1024.0, 16.0 * 1024.0 * 1024.0, "lower_is_better"),
]


class BudgetRegistry:
    def __init__(self, storage: Optional[PerformanceStorage] = None, *, hardware_profile: str = "development-workstation") -> None:
        self._storage = storage
        self._hardware_profile = hardware_profile
        self._budgets: Dict[str, BudgetRecord] = {}
        for budget_id, platform, profile, metric, target, warning, failure, direction in _DEFAULT_BUDGETS:
            record = BudgetRecord(
                budget_id=budget_id,
                platform=platform,
                hardware_profile=profile,
                metric=metric,
                target=target,
                warning_threshold=warning,
                failure_threshold=failure,
                measurement_method="direct",
                owner="performance",
                version=1,
                direction=direction,
            )
            self._budgets[budget_id] = record

    def register(self, record: BudgetRecord) -> None:
        self._budgets[record.budget_id] = record
        if self._storage is not None:
            self._storage.upsert_budget(_record_to_dict(record))

    def list(self, platform: str = "", hardware_profile: str = "") -> List[BudgetRecord]:
        items = [
            record
            for record in self._budgets.values()
            if (not platform or record.platform == platform)
            and (not hardware_profile or record.hardware_profile == hardware_profile)
        ]
        return sorted(items, key=lambda item: item.budget_id)

    def check(self, metric: str, value: float) -> Tuple[str, Optional[BudgetRecord]]:
        """Classify ``value`` against budgets for ``metric`` for this profile."""
        matched = [
            record
            for record in self._budgets.values()
            if record.metric == metric and record.hardware_profile == self._hardware_profile
        ]
        if not matched:
            return "incomparable", None
        record = matched[0]
        if record.direction == "lower_is_better":
            if value <= record.target:
                return "pass", record
            if value <= record.warning_threshold:
                return "warning", record
            if value <= record.failure_threshold:
                return "warning", record
            return "fail", record
        if value >= record.target:
            return "pass", record
        if value >= record.warning_threshold:
            return "warning", record
        return "fail", record


def _record_to_dict(record: BudgetRecord) -> dict:
    return {
        "budget_id": record.budget_id,
        "platform": record.platform,
        "hardware_profile": record.hardware_profile,
        "metric": record.metric,
        "target": record.target,
        "warning_threshold": record.warning_threshold,
        "failure_threshold": record.failure_threshold,
        "measurement_method": record.measurement_method,
        "owner": record.owner,
        "version": record.version,
        "exceptions": record.exceptions,
        "review_date": record.review_date,
        "direction": record.direction,
    }
