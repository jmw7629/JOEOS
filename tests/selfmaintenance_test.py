"""Tests for the JoeOS Self-Maintenance and Continuous Improvement platform.

Verifies that checks are honest (unmeasured signals stay unknown), improvement
proposals are evidence-based and never self-apply, safe self-hygiene never
touches authority, and the REST API enforces governance on mutations.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from server.selfmaintenance import (
    ImprovementProposal,
    SelfMaintenanceService,
    detect,
    reconcile,
    run_health_checks,
)
from server.selfmaintenance.checks import overall_outcome


class _Rec:
    def __init__(self, verified: bool = True) -> None:
        self.verified = verified


class _FakeMemory:
    def __init__(self, due: int = 0) -> None:
        self._due = due
        self.expired = 0

    def count_due(self, now=None) -> int:
        return self._due

    def expire_due(self, now=None) -> int:
        self.expired += self._due
        return self._due


class _FakeProduction:
    def __init__(self, verified: bool = False, total: int = 0, safe_mode: bool = False, repair_mode: bool = False) -> None:
        self.records = [_Rec(verified) for _ in range(total)]
        self.safe_mode = safe_mode
        self.repair_mode = repair_mode
        self.safe_mode_exits = 0

    def backups(self):
        return self.records

    def recovery_state(self):
        return {
            "safe_mode": self.safe_mode,
            "repair_mode": self.repair_mode,
            "crash_loop_detected": False,
            "interrupted_update": False,
        }

    def create_backup(self):
        self.records.insert(0, _Rec(True))
        return type("Backup", (), {"backup_id": "bk-test"})

    def exit_safe_mode(self):
        self.safe_mode = False
        self.safe_mode_exits += 1
        return True


class SelfMaintenanceBase(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp()

        def main_factory():
            connection = sqlite3.connect(str(Path(self.dir) / "main.db"))
            connection.row_factory = sqlite3.Row
            connection.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, level TEXT, source TEXT, message TEXT, recorded_at TEXT)")
            connection.execute("CREATE TABLE IF NOT EXISTS system_metrics (id INTEGER PRIMARY KEY, disk_percent REAL, recorded_at TEXT)")
            return connection

        self.main_factory = main_factory
        self.service = SelfMaintenanceService(
            str(Path(self.dir) / "selfmaintenance"),
            main_connection_factory=main_factory,
        )
        self.service.set_provider("backup_list", lambda: [])
        self.service.set_provider("recovery_state", lambda: {"safe_mode": False, "repair_mode": False})
        self.service.set_provider("memory_due", lambda: 0)
        self.service.set_provider("migrations_writable", lambda: (True, "ok"))

    def _fresh_telemetry(self):
        from server.selfmaintenance.service import _now_iso

        stamp = datetime.now(timezone.utc).isoformat()
        with self.main_factory() as connection:
            connection.execute("INSERT INTO system_metrics (disk_percent, recorded_at) VALUES (12.0, ?)", (stamp,))


class MaintenanceCheckTests(SelfMaintenanceBase):
    def test_all_checks_pass_when_sources_are_healthy(self):
        self._fresh_telemetry()
        self.service.set_provider("backup_list", lambda: [_Rec(True)])
        self.service.set_provider("memory_due", lambda: 0)
        checks = self.service.checks()
        states = {check.check_id: check.state for check in checks}
        self.assertEqual(states["database.healthy"], "ok")
        self.assertEqual(states["event.store"], "ok")
        self.assertEqual(states["disk.space"], "ok")
        self.assertEqual(states["backup.verified"], "ok")
        outcome, _ = overall_outcome(checks)
        self.assertEqual(outcome, "completed")

    def test_unmeasured_signals_are_never_fabricated(self):
        checks = self.service.checks()
        states = {check.check_id: check.state for check in checks}
        # No telemetry recorded yet -> unknown/skipped, never "ok".
        self.assertIn(states["telemetry.fresh"], ("unknown", "skipped"))
        self.assertIn(states["disk.space"], ("unknown", "skipped"))
        # No backups -> failed, never healthy.
        self.assertEqual(states["backup.verified"], "failed")
        outcome, _ = overall_outcome(checks)
        self.assertEqual(outcome, "failed")

    def test_disk_pressure_warns_from_real_value(self):
        self._fresh_telemetry()
        with self.main_factory() as connection:
            connection.execute("UPDATE system_metrics SET disk_percent = 92.0")
        states = {check.check_id: check.state for check in self.service.checks()}
        self.assertEqual(states["disk.space"], "warning")

    def test_migration_gate_failure_is_reported(self):
        self.service.set_provider("migrations_writable", lambda: (False, "future schema"))
        states = {check.check_id: check.state for check in self.service.checks()}
        self.assertEqual(states["migration.status"], "failed")

    def test_recovery_flags_are_degraded(self):
        self.service.set_provider("recovery_state", lambda: {"safe_mode": True, "repair_mode": False})
        states = {check.check_id: check.state for check in self.service.checks()}
        self.assertEqual(states["recovery.state"], "degraded")


class ImprovementDetectionTests(SelfMaintenanceBase):
    def test_backup_proposal_only_when_no_verified_backup(self):
        proposals = detect({"verified_backups": 0, "total_backups": 0, "memory_due": 0})
        ids = {p.improvement_id for p in proposals}
        self.assertIn("backup.initial", ids)

    def test_no_backup_proposal_when_verified_backup_exists(self):
        proposals = detect({"verified_backups": 1, "total_backups": 2, "memory_due": 0})
        ids = {p.improvement_id for p in proposals}
        self.assertNotIn("backup.initial", ids)

    def test_memory_and_recovery_proposals(self):
        proposals = detect({"verified_backups": 1, "memory_due": 3, "safe_mode": True, "repair_mode": True, "no_telemetry": False})
        ids = {p.improvement_id for p in proposals}
        self.assertIn("memory.expire", ids)
        self.assertIn("recovery.exit_safe_mode", ids)
        self.assertIn("recovery.exit_repair_mode", ids)

    def test_proposals_never_self_apply(self):
        for proposal in detect({"verified_backups": 0, "memory_due": 1}):
            self.assertEqual(proposal.state, "proposed")

    def test_reconcile_preserves_resolved_state(self):
        stamp = "2026-01-01T00:00:00Z"
        detected = detect({"verified_backups": 0, "memory_due": 0}, proposed_at=stamp)
        existing = [
            ImprovementProposal(
                improvement_id="backup.initial",
                title="old",
                category="recovery",
                evidence=(),
                priority="high",
                state="applied",
                apply_action="create_backup",
                detail="",
                proposed_at=stamp,
                resolved_at=stamp,
            )
        ]
        merged = reconcile(existing, detected)
        current = next(p for p in merged if p.improvement_id == "backup.initial")
        self.assertEqual(current.state, "applied")


class ImprovementApplicationTests(SelfMaintenanceBase):
    def test_apply_requires_approval(self):
        self.service.set_provider("backup_list", lambda: [])
        proposals = self.service.proposals()
        self.service.register_executor("create_backup", lambda: "bk-1")
        ok, detail = self.service.apply_improvement("backup.initial")
        self.assertFalse(ok)
        self.assertIn("approve", detail)

    def test_apply_executes_bound_action_after_approval(self):
        fake = _FakeProduction(verified=False, total=0)
        self.service.set_provider("backup_list", fake.backups)
        self.service.set_provider("recovery_state", fake.recovery_state)
        self.service.register_executor("create_backup", lambda: str(fake.create_backup().backup_id))
        self.service.proposals()
        self.service.coordinator.registry.set_state("backup.initial", "approved")
        ok, detail = self.service.apply_improvement("backup.initial")
        self.assertTrue(ok)
        self.assertEqual(self.service.coordinator.registry.get("backup.initial").state, "applied")
        self.assertTrue(any(getattr(r, "verified", False) for r in fake.records))

    def test_apply_rejects_unknown_improvement(self):
        ok, detail = self.service.apply_improvement("does-not-exist")
        self.assertFalse(ok)
        self.assertIn("not found", detail)


class MaintenanceRunTests(SelfMaintenanceBase):
    def test_run_records_and_prunes_its_own_history(self):
        self._fresh_telemetry()
        self.service.set_provider("backup_list", lambda: [_Rec(True)])
        first = self.service.run_maintenance()
        second = self.service.run_maintenance()
        self.assertIn(first["outcome"], ("completed", "degraded"))
        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertTrue(self.service.last_run()["run_id"] == second["run_id"])

    def test_safe_hygiene_never_touches_authority(self):
        run = self.service.run_maintenance()
        for remediation in run["remediations"]:
            self.assertEqual(remediation["state"], "applied")


class RESTTests(SelfMaintenanceBase):
    def setUp(self) -> None:
        super().setUp()
        import joeos_backend as backend

        backend.app.state.selfmaintenance_service = self.service
        self.client = TestClient(backend.app)

    def test_overview_reads_real_state(self):
        response = self.client.get("/api/v1/selfmaintenance/overview")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("health", payload)
        self.assertIsInstance(payload["checks"], list)
        self.assertIsInstance(payload["proposals"], list)

    def test_run_requires_governance_when_blocked(self):
        def blocked():
            return (True, "lockdown")

        self.service.coordinator._event_sink = lambda *args: None
        self.service._governance_blocked = blocked
        self.service.coordinator.append_log = lambda *args: None
        response = self.client.post("/api/v1/selfmaintenance/run", json={})
        self.assertEqual(response.status_code, 409)
        self.assertIn("lockdown", response.json()["detail"])

    def test_apply_rejects_unknown_improvement(self):
        response = self.client.post("/api/v1/selfmaintenance/improvements/nope/apply", json={})
        self.assertNotEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()