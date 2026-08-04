"""Typed records for the JoeOS Self-Maintenance and Continuous Improvement
platform.

Every record is derived from real, authoritative service state. Check states
and improvement proposals are honest: an unavailable or unmeasured signal is
reported as `unknown` or `skipped`, never as healthy or degraded without
evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

CheckState = str  # "healthy" | "degraded" | "failed" | "skipped" | "unknown"

# Improvement lifecycle. A proposal is always observed, never invented; it may
# never self-approve. Resolving it requires an operator decision.
IMPROVEMENT_STATES = ("proposed", "approved", "applied", "dismissed", "not_actionable")


@dataclass(frozen=True)
class MaintenanceCheck:
    check_id: str
    name: str
    category: str
    state: str
    detail: str
    measured_at: str


@dataclass(frozen=True)
class Remediation:
    action: str
    detail: str
    state: str  # "applied" | "failed" | "skipped"


@dataclass(frozen=True)
class MaintenanceRun:
    run_id: str
    started_at: str
    finished_at: str
    outcome: str  # "completed" | "degraded" | "failed"
    checks: Tuple[MaintenanceCheck, ...] = ()
    remediations: Tuple[Remediation, ...] = ()
    detail: str = ""


@dataclass(frozen=True)
class MaintenanceLogEntry:
    entry_id: int
    recorded_at: str
    level: str  # "info" | "warn" | "error"
    action: str
    detail: str


@dataclass
class ImprovementProposal:
    improvement_id: str
    title: str
    category: str
    evidence: Tuple[str, ...]
    priority: str  # "high" | "medium" | "low"
    state: str
    apply_action: Optional[str]
    detail: str
    proposed_at: str
    resolved_at: Optional[str] = None