"""JoeOS Self-Maintenance and Continuous Improvement platform.

An honest, local-first layer that runs real maintenance checks over live JoeOS
services (Production backups/migrations/recovery, Memory hygiene, telemetry,
and the event store), applies only safe self-hygiene (bounded retention of its
own registry), detects evidence-based improvement proposals, and lets the
operator apply an approved proposal against a real service through an
executor. Nothing is fabricated, and no improvement is ever self-applied.

See `docs/architecture/SELFMAINTENANCE.md` for the design and honest
guarantees.
"""

from .checks import run_health_checks
from .improvements import ImprovementRegistry, detect, reconcile
from .maintenance import MaintenanceCoordinator
from .models import (
    ImprovementProposal,
    MaintenanceCheck,
    MaintenanceLogEntry,
    MaintenanceRun,
    Remediation,
)
from .router import router as selfmaintenance_router
from .service import SelfMaintenanceService

__all__ = [
    "ImprovementProposal",
    "ImprovementRegistry",
    "MaintenanceCheck",
    "MaintenanceCoordinator",
    "MaintenanceLogEntry",
    "MaintenanceRun",
    "Remediation",
    "SelfMaintenanceService",
    "detect",
    "reconcile",
    "run_health_checks",
    "selfmaintenance_router",
]