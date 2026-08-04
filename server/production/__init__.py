"""JoeOS Production Readiness and Release Engineering platform.

An honest, local-first layer that reports build metadata and supported targets
derived from the actual build, enforces release gates that never fabricate
success, coordinates versioned migrations with backup-before-risk and
future-schema protection, creates verified backups and staged restores that
reset stale authority, validates staged update packages before activation, and
provides Safe Mode, Repair Mode, and crash-loop recovery.

No publishing, signing, notarization, container publication, package-manager
publication, or network distribution is claimed unless it is genuinely
implemented and validated.

See `docs/architecture/PRODUCTION_READINESS.md` for the design and honest
guarantees.
"""

from .backup import BackupCoordinator, BackupError, RestoreCoordinator
from .compatibility import CompatibilityRegistry
from .metadata import build_metadata, source_commit, supported_targets
from .migrations import MigrationCoordinator, MigrationError
from .models import (
    BackupRecord,
    BuildMetadata,
    CompatibilityCheck,
    MigrationRecord,
    MigrationState,
    RecoveryState,
    ReleaseGate,
    ReleaseStatus,
    RestorePlan,
    SupportedTarget,
    UpdateRecord,
)
from .recovery import RecoveryCoordinator
from .router import router as production_router
from .service import ProductionService
from .updates import UpdateCoordinator, UpdateError

__all__ = [
    "BackupCoordinator",
    "BackupError",
    "BackupRecord",
    "BuildMetadata",
    "CompatibilityCheck",
    "CompatibilityRegistry",
    "MigrationCoordinator",
    "MigrationError",
    "MigrationRecord",
    "MigrationState",
    "ProductionService",
    "RecoveryCoordinator",
    "RecoveryState",
    "ReleaseGate",
    "ReleaseStatus",
    "RestoreCoordinator",
    "RestorePlan",
    "SupportedTarget",
    "UpdateCoordinator",
    "UpdateError",
    "UpdateRecord",
    "build_metadata",
    "production_router",
    "source_commit",
    "supported_targets",
]
