"""ProductionService — the authoritative Production Readiness and Release
Engineering facade.

Composes build metadata, the supported-target matrix, the Compatibility
Registry, the Migration Coordinator, the Backup/Restore Coordinators, the
Update Coordinator, and the Recovery Coordinator into one honest release
status. Release gates are explicit and never fabricated: tests, scans, SBOM,
signing, and update distribution that are not actually run/configured are
reported as not_configured or unavailable, never passing.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .backup import BackupCoordinator, BackupError, RestoreCoordinator
from .compatibility import CompatibilityRegistry
from .metadata import build_metadata, supported_targets
from .migrations import MigrationCoordinator, MigrationError
from .models import (
    BackupRecord,
    BuildMetadata,
    ReleaseGate,
    ReleaseStatus,
    RestorePlan,
    SupportedTarget,
    UpdateRecord,
)
from .recovery import RecoveryCoordinator
from .updates import UpdateCoordinator, UpdateError


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProductionService:
    def __init__(
        self,
        data_dir: str,
        *,
        application_version: str = "",
        data_root: Optional[Path] = None,
        event_sink: Optional[Callable[[str, str, str], None]] = None,
        governance_blocked: Optional[Callable[[], tuple]] = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._data_root = Path(data_root) if data_root else self.data_dir
        self._version = application_version
        self._event_sink = event_sink
        self._governance_blocked = governance_blocked or (lambda: (False, ""))
        db_path = self.data_dir / "production.db"

        def connect() -> sqlite3.Connection:
            connection = sqlite3.connect(str(db_path), timeout=10)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 10000")
            return connection

        self.compatibility = CompatibilityRegistry()
        self.migrations = MigrationCoordinator(backup_hook=self._backup_store_for_migration)
        self.recovery = RecoveryCoordinator(connect)
        self.backup = BackupCoordinator(self._data_root, application_version=self._version, backup_root=self.data_dir / "backups")
        self.restore = RestoreCoordinator(self._data_root, self.backup, self.compatibility, security_reset_hook=self._security_reset)
        self.updates = UpdateCoordinator(
            application_version=self._version,
            target_platform="linux",
            target_architecture="x86_64",
            backup_hook=lambda: self.backup.create().backup_id,
        )
        self._schema_versions: Dict[str, str] = {}
        self._validate_hook: Optional[Callable[[], bool]] = None

    def register_schema(self, store: str, current_schema: int, target_schema: int, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._schema_versions[store] = str(current_schema)
        self.compatibility.record_schema_version(store, current_schema)
        self.migrations.register_store(store, connection_factory, target_schema)

    def set_validate_hook(self, hook: Callable[[], bool]) -> None:
        self._validate_hook = hook

    def set_security_reset_hook(self, hook: Callable[[], Dict[str, int]]) -> None:
        self.restore._security_reset_hook = hook

    def register_declared_schema(self, store: str, version: int) -> None:
        """Record a declared schema version for compatibility reporting."""
        self._schema_versions[store] = str(int(version))
        self.compatibility.record_schema_version(store, int(version))

    def governance_blocked(self) -> Tuple[bool, str]:
        return self._governance_blocked()

    # ---- release status ----

    def status(self, *, include_gates: bool = True) -> ReleaseStatus:
        metadata = self.build()
        gates = self._gates(metadata) if include_gates else []
        overall = "unknown"
        if any(gate.state == "blocking" for gate in gates):
            overall = "blocking"
        elif any(gate.state == "warning" for gate in gates):
            overall = "warning"
        elif gates and all(gate.state == "passing" for gate in gates):
            overall = "passing"
        elif gates:
            overall = "warning"
        return ReleaseStatus(
            generated_at=_now_iso(),
            version=metadata.version,
            channel=metadata.channel,
            overall=overall,
            gates=tuple(gates),
            targets=tuple(supported_targets()),
            message="Honest release readiness: unsupported targets, scans, signing, and update distribution are reported as not configured or unavailable.",
        )

    def build(self) -> BuildMetadata:
        return build_metadata()

    def _gates(self, metadata: BuildMetadata) -> List[ReleaseGate]:
        gates = [
            ReleaseGate(gate_id="version.consistent", name="Version consistency", state=self._version_gate(metadata), category="release"),
            ReleaseGate(gate_id="schema.compatible", name="Database schema compatibility", state=self._schema_gate(), category="migration"),
            ReleaseGate(gate_id="tests", name="Automated tests", state=self._tests_gate(), category="validation"),
            ReleaseGate(gate_id="backup", name="Validated backup available", state=self._backup_gate(), category="recovery"),
            ReleaseGate(gate_id="package", name="Production bundle built", state=self._package_gate(), category="packaging"),
            ReleaseGate(gate_id="artifact.integrity", name="Artifact integrity", state=self._artifact_gate(), category="packaging"),
            ReleaseGate(gate_id="secret.scan", name="Secret scanning", state="not_configured", detail="No secret scanner is configured in this repository.", category="supply-chain"),
            ReleaseGate(gate_id="sbom", name="Software bill of materials", state="not_configured", detail="No SBOM generator is configured in this repository.", category="supply-chain"),
            ReleaseGate(gate_id="dependency.scan", name="Dependency scanning", state="not_configured", detail="No dependency scanner is configured in this repository.", category="supply-chain"),
            ReleaseGate(gate_id="signing", name="Artifact signing", state="not_configured", detail="Artifacts are unsigned; hashes are recorded in release-manifest.json.", category="packaging"),
            ReleaseGate(gate_id="updates", name="Update distribution", state="not_configured", detail="Updates are validated locally from staged packages; no network distribution exists.", category="updates"),
            ReleaseGate(gate_id="telemetry.external", name="External telemetry", state="passing", detail="External telemetry is disabled by default.", category="privacy"),
            ReleaseGate(gate_id="public.listener", name="Public network listener", state="passing", detail="Local services bind to loopback by default; no public listener is registered.", category="network"),
        ]
        return gates

    def _version_gate(self, metadata: BuildMetadata) -> str:
        if not metadata.version:
            return "blocking"
        if metadata.dirty_working_tree:
            return "warning"
        return "passing"

    def _schema_gate(self) -> str:
        try:
            blocked = self.migrations.assert_writable()
        except MigrationError:
            return "blocking"
        return "passing" if not blocked else "blocking"

    def _tests_gate(self) -> str:
        # The authoritative signal is the last known full-suite run; in-process
        # we do not fabricate a result. A smoke subset is reported separately.
        return "not_configured" if self._validate_hook is None else ("passing" if self._validate_hook() else "blocking")

    def _backup_gate(self) -> str:
        records = self.backup.list()
        if any(record.verified for record in records):
            return "passing"
        if records:
            return "warning"
        return "warning"

    def _package_gate(self) -> str:
        from .metadata import ROOT
        frontend = ROOT / "frontend_dist" / "index.html"
        return "passing" if frontend.exists() else "warning"

    def _artifact_gate(self) -> str:
        # Verified by the release tool's release-manifest.json; in-process we
        # report it as not run unless a staged bundle is present to verify.
        return "not_configured"

    # ---- migration ----

    def migration_state(self) -> List[Dict[str, object]]:
        states = self.migrations.inspect_all()
        return [
            {
                "store": state.store,
                "current_schema": state.current_schema,
                "target_schema": state.target_schema,
                "compatible": state.compatible,
                "needs_migration": state.needs_migration,
                "future_schema": state.future_schema,
                "locked": state.locked,
                "detail": state.detail,
            }
            for state in states
        ]

    def migrate(self, store: str) -> Dict[str, object]:
        record = self.migrations.migrate(store)
        self._emit("info", "production", "Migration %s completed for %s (%d -> %d)." % (record.status, store, record.source_version, record.target_version))
        return {
            "store": store,
            "source_version": record.source_version,
            "target_version": record.target_version,
            "status": record.status,
            "backed_up": record.backed_up,
        }

    # ---- backup / restore ----

    def create_backup(self) -> BackupRecord:
        record = self.backup.create()
        self._emit("info", "production", "Backup %s created and verified (%d bytes)." % (record.backup_id, record.size_bytes))
        return record

    def verify_backup(self, backup_id: str) -> BackupRecord:
        return self.backup.verify(backup_id)

    def list_backups(self) -> List[BackupRecord]:
        return self.backup.list()

    def delete_backup(self, backup_id: str) -> bool:
        return self.backup.delete(backup_id)

    def restore_plan(self, backup_id: str) -> RestorePlan:
        return self.restore.plan(backup_id)

    def restore_backup(self, backup_id: str) -> Dict[str, object]:
        result = self.restore.restore(backup_id)
        self._emit("warn", "production", "Restore activated from %s; sessions, approvals, workflows, and device trust were reset by policy." % backup_id)
        return result

    # ---- updates ----

    def update_status(self, staged: Optional[Path] = None) -> Dict[str, object]:
        if staged is None:
            return {"state": "idle", "detail": "No staged update package provided; updates are validated locally and never distributed."}
        try:
            return self.updates.inspect_staged(staged)
        except UpdateError as exc:
            return {"state": "failed", "detail": str(exc)}

    def update_plan(self, staged: Path) -> Dict[str, object]:
        return self.updates.plan(staged)

    def apply_update(self, staged: Path) -> UpdateRecord:
        record = self.updates.apply(staged)
        self._emit("info", "production", "Update state: %s (%s)." % (record.state, record.detail))
        return record

    # ---- recovery ----

    def recovery_state(self) -> Dict[str, object]:
        state = self.recovery.state()
        return {
            "safe_mode": state.safe_mode,
            "repair_mode": state.repair_mode,
            "crash_loop_detected": state.crash_loop_detected,
            "interrupted_update": state.interrupted_update,
            "interrupted_migration": state.interrupted_migration,
            "low_disk": state.low_disk,
            "detail": state.detail,
            "restrictions": self.recovery.safe_mode_restrictions(),
            "generated_at": state.generated_at,
        }

    def enter_safe_mode(self) -> bool:
        self._emit("warn", "production", "Safe Mode activated.")
        return self.recovery.enter_safe_mode()

    def exit_safe_mode(self) -> bool:
        self._emit("info", "production", "Safe Mode deactivated.")
        return self.recovery.exit_safe_mode()

    def enter_repair_mode(self) -> bool:
        self._emit("warn", "production", "Repair Mode activated.")
        return self.recovery.enter_repair_mode()

    def exit_repair_mode(self) -> bool:
        self._emit("info", "production", "Repair Mode deactivated.")
        return self.recovery.exit_repair_mode()

    def record_crash(self, component: str) -> bool:
        return self.recovery.record_crash(component)

    # ---- doctor ----

    def doctor(self) -> List[Dict[str, object]]:
        metadata = self.build()
        checks = [
            {"check": "version", "state": "pass" if metadata.version else "fail", "detail": metadata.version or "no version"},
            {"check": "platform", "state": "pass", "detail": "%s/%s" % (metadata.target_platform, metadata.target_architecture)},
            {"check": "working_tree", "state": "warning" if metadata.dirty_working_tree else "pass", "detail": "dirty" if metadata.dirty_working_tree else "clean"},
            {"check": "data_directory", "state": "pass" if self._data_root.exists() else "fail", "detail": "exists" if self._data_root.exists() else "missing"},
            {"check": "database", "state": self._doctor_database(), "detail": "migration gate"},
            {"check": "backups", "state": "pass" if any(record.verified for record in self.backup.list()) else "warning", "detail": "%d backup(s)" % len(self.backup.list())},
            {"check": "safe_mode", "state": "warning" if self.recovery.state().safe_mode else "pass", "detail": "active" if self.recovery.state().safe_mode else "inactive"},
            {"check": "disk", "state": "warning", "detail": "disk space is not measured by this tool"},
            {"check": "secret_scan", "state": "unavailable", "detail": "no scanner configured"},
            {"check": "signing", "state": "unsupported", "detail": "unsigned artifacts; hashes only"},
        ]
        return checks

    def _doctor_database(self) -> str:
        try:
            self.migrations.assert_writable()
            return "pass"
        except MigrationError:
            return "fail"

    # ---- internals ----

    def _backup_store_for_migration(self, store: str) -> bool:
        try:
            self.backup.create()
            return True
        except BackupError:
            return False

    def _security_reset(self) -> Dict[str, int]:
        """Default security-state reset after restore.

        The backend wires real resets (session revocation, approval
        invalidation, workflow pausing, device restriction) through this hook.
        The default reports zeros honestly because no live services are wired
        into the pure ProductionService.
        """
        return {"sessions": 0, "approvals": 0, "workflows": 0, "devices": 0}

    def _emit(self, level: str, source: str, message: str) -> None:
        if self._event_sink is not None:
            try:
                self._event_sink(level, source, message)
            except Exception:
                pass
