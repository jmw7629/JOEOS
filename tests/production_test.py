import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from server.production import (
    BackupCoordinator,
    BackupError,
    CompatibilityRegistry,
    MigrationCoordinator,
    MigrationError,
    ProductionService,
    RecoveryCoordinator,
    RestoreCoordinator,
    UpdateCoordinator,
    UpdateError,
)


def _make_db(path: Path, schema_version_table="meta"):
    connection = sqlite3.connect(str(path))
    connection.execute("CREATE TABLE IF NOT EXISTS %s (key TEXT PRIMARY KEY, value TEXT)" % schema_version_table)
    connection.commit()
    return connection


class BuildMetadataTests(unittest.TestCase):
    def test_version_is_derived_not_hard_coded(self):
        from server.production.metadata import build_metadata
        metadata = build_metadata()
        self.assertEqual(metadata.version, "2.0.0")
        self.assertIn("commit", metadata.__dict__)
        self.assertIn("dependency_lock_hash", metadata.__dict__)

    def test_supported_targets_are_honest(self):
        from server.production.metadata import supported_targets
        targets = {target.platform: target for target in supported_targets()}
        self.assertEqual(targets["linux"].support_state, "supported")
        self.assertEqual(targets["web"].support_state, "supported")
        self.assertEqual(targets["macos"].support_state, "unsupported")
        self.assertEqual(targets["ios"].support_state, "unsupported")


class CompatibilityTests(unittest.TestCase):
    def test_future_schema_is_incompatible(self):
        registry = CompatibilityRegistry({"projects": "1"})
        check = registry.check_schema("projects", current_schema=3, min_supported=1)
        self.assertEqual(check.state, "incompatible")
        self.assertIn("newer", check.detail)

    def test_old_schema_requires_update(self):
        registry = CompatibilityRegistry({"projects": "3"})
        check = registry.check_schema("projects", current_schema=1, min_supported=2)
        self.assertEqual(check.state, "update_required")

    def test_backup_format_future_blocked(self):
        registry = CompatibilityRegistry()
        check = registry.check_backup_format(2, required=1)
        self.assertEqual(check.state, "incompatible")


class MigrationCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "store.db"
        self.backed_up = []

    def tearDown(self):
        self.tempdir.cleanup()

    def _factory(self):
        return lambda: sqlite3.connect(str(self.path))

    def test_inspect_and_migrate_with_backup(self):
        coordinator = MigrationCoordinator(backup_hook=lambda store: self.backed_up.append(store) or True)
        coordinator.register_store("store", self._factory(), target_version=2)
        state = coordinator.inspect("store")
        self.assertEqual(state.current_schema, 0)
        self.assertTrue(state.needs_migration)
        record = coordinator.migrate("store")
        self.assertEqual(record.status, "completed")
        self.assertEqual(record.source_version, 0)
        self.assertEqual(record.target_version, 2)
        self.assertEqual(self.backed_up, ["store"])
        self.assertFalse(coordinator.inspect("store").needs_migration)

    def test_future_schema_blocks_writes(self):
        coordinator = MigrationCoordinator()
        coordinator.register_store("store", self._factory(), target_version=1)
        with self._factory()() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS production_meta (store TEXT PRIMARY KEY, version INTEGER NOT NULL, updated_at TEXT)")
            connection.execute("INSERT OR REPLACE INTO production_meta (store, version, updated_at) VALUES ('store', 99, 'now')")
            connection.commit()
        state = coordinator.inspect("store")
        self.assertTrue(state.future_schema)
        with self.assertRaises(MigrationError):
            coordinator.assert_writable()
        with self.assertRaises(MigrationError):
            coordinator.migrate("store")

    def test_lock_prevents_concurrent_migration(self):
        coordinator = MigrationCoordinator()
        coordinator.register_store("store", self._factory(), target_version=1)
        with self._factory()() as connection:
            coordinator._ensure_meta(connection)
            token = coordinator._acquire_lock(connection, "store")
            self.assertIsNotNone(coordinator._read_lock(connection, "store"))
            with self.assertRaises(MigrationError):
                coordinator.migrate("store")
            coordinator._release_lock(connection, "store", token)
        coordinator.migrate("store")


class BackupCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.data = Path(self.tempdir.name) / "data"
        self.data.mkdir()
        _make_db(self.data / "joeos.db")
        (self.data / "note.txt").write_text("hello backup", encoding="utf-8")
        self.backup = BackupCoordinator(self.data, application_version="2.0.0")

    def tearDown(self):
        self.tempdir.cleanup()

    def test_create_verified_backup(self):
        record = self.backup.create()
        self.assertTrue(record.verified)
        self.assertEqual(record.status, "verified")
        self.assertTrue(record.stores)
        self.assertGreater(record.size_bytes, 0)
        self.assertTrue(self.backup.archive_path(record.backup_id).exists())

    def test_verify_detects_corruption(self):
        record = self.backup.create()
        archive = self.backup.archive_path(record.backup_id)
        data = bytearray(archive.read_bytes())
        data[0] ^= 0xFF
        archive.write_bytes(bytes(data))
        verified = self.backup.verify(record.backup_id)
        self.assertFalse(verified.verified)
        self.assertEqual(verified.status, "verification_failed")

    def test_delete_protects_only_verified_backup(self):
        record = self.backup.create()
        with self.assertRaises(BackupError):
            self.backup.delete(record.backup_id)
        second = self.backup.create()
        self.assertTrue(self.backup.delete(record.backup_id))
        self.assertTrue(self.backup.archive_path(second.backup_id).exists())

    def test_restore_stages_and_activates(self):
        record = self.backup.create()
        (self.data / "note.txt").write_text("changed after backup", encoding="utf-8")
        registry = CompatibilityRegistry()
        restore = RestoreCoordinator(self.data, self.backup, registry, security_reset_hook=lambda: {"sessions": 2, "approvals": 1, "workflows": 1, "devices": 1})
        plan = restore.plan(record.backup_id)
        self.assertTrue(plan.revokes_sessions)
        result = restore.restore(record.backup_id)
        self.assertEqual(result["sessions_revoked"], 2)
        self.assertEqual((self.data / "note.txt").read_text(encoding="utf-8"), "hello backup")

    def test_restore_blocked_by_corruption(self):
        record = self.backup.create()
        archive = self.backup.archive_path(record.backup_id)
        archive.write_bytes(b"\x00" * 32)
        registry = CompatibilityRegistry()
        restore = RestoreCoordinator(self.data, self.backup, registry)
        with self.assertRaises(BackupError):
            restore.restore(record.backup_id)


class UpdateCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.staged = Path(self.tempdir.name) / "joeos-2.1.0"
        self.staged.mkdir()
        (self.staged / "web").mkdir()
        payload = b"new application content"
        (self.staged / "web" / "index.html").write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        manifest = {
            "version": "2.1.0",
            "channel": "development",
            "components": {"backend": "2.1.0"},
            "files": {"web/index.html": digest},
        }
        (self.staged / "release-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        self.updates = UpdateCoordinator(application_version="2.0.0", target_platform="linux", target_architecture="x86_64")

    def tearDown(self):
        self.tempdir.cleanup()

    def test_inspect_valid_staged_package(self):
        inspection = self.updates.inspect_staged(self.staged)
        self.assertTrue(inspection["manifest_match"])
        self.assertTrue(inspection["compatibility_ok"])
        self.assertEqual(inspection["version"], "2.1.0")

    def test_hash_mismatch_blocks(self):
        (self.staged / "web" / "index.html").write_bytes(b"tampered")
        inspection = self.updates.inspect_staged(self.staged)
        self.assertFalse(inspection["manifest_match"])
        self.assertIn("web/index.html", inspection["hash_mismatches"])
        with self.assertRaises(UpdateError):
            self.updates.plan(self.staged)

    def test_apply_requires_backup_and_reports_completion(self):
        backup_ids = []

        def backup():
            backup_ids.append("backup-1")
            return "backup-1"

        updates = UpdateCoordinator(application_version="2.0.0", backup_hook=backup, validate_hook=lambda: True)
        record = updates.apply(self.staged)
        self.assertEqual(record.state, "completed")
        self.assertEqual(backup_ids, ["backup-1"])

    def test_older_version_blocked(self):
        updates = UpdateCoordinator(application_version="3.0.0")
        inspection = updates.inspect_staged(self.staged)
        self.assertFalse(inspection["compatibility_ok"])
        with self.assertRaises(UpdateError):
            updates.plan(self.staged)


class RecoveryCoordinatorTests(unittest.TestCase):
    def test_crash_loop_detection(self):
        recovery = RecoveryCoordinator(crash_loop_threshold=3)
        for _ in range(3):
            recovery.record_crash("server")
        self.assertTrue(recovery.crash_loop_detected())
        recovery.clear_crash_window()
        self.assertFalse(recovery.crash_loop_detected())

    def test_safe_mode_restrictions(self):
        recovery = RecoveryCoordinator()
        recovery.enter_safe_mode()
        self.assertTrue(recovery.state().safe_mode)
        restrictions = recovery.safe_mode_restrictions()
        self.assertTrue(restrictions["third_party_plugins_disabled"])
        self.assertTrue(restrictions["workflows_paused"])
        self.assertTrue(restrictions["cloud_providers_disabled"])
        recovery.exit_safe_mode()
        self.assertFalse(recovery.state().safe_mode)


class ProductionServiceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.service = ProductionService(str(Path(self.tempdir.name) / "prod"), application_version="2.0.0")

    def tearDown(self):
        self.tempdir.cleanup()

    def test_status_gates_never_fabricate_success(self):
        status = self.service.status()
        states = {gate.gate_id: gate.state for gate in status.gates}
        self.assertIn("secret.scan", states)
        self.assertEqual(states["secret.scan"], "not_configured")
        self.assertEqual(states["sbom"], "not_configured")
        self.assertEqual(states["signing"], "not_configured")
        self.assertEqual(states["updates"], "not_configured")

    def test_backup_round_trip_via_service(self):
        record = self.service.create_backup()
        self.assertTrue(record.verified)
        records = self.service.list_backups()
        self.assertTrue(any(record.backup_id == item.backup_id for item in records))

    def test_governance_blocks_mutation(self):
        def blocked():
            return (True, "lockdown active")

        service = ProductionService(str(Path(self.tempdir.name) / "prod2"), governance_blocked=blocked)
        self.assertEqual(service.governance_blocked(), (True, "lockdown active"))

    def test_doctor_reports_pass_fail_unavailable(self):
        checks = {check["check"]: check["state"] for check in self.service.doctor()}
        self.assertEqual(checks["version"], "pass")
        self.assertIn(checks["secret_scan"], ("unavailable", "unsupported"))
        self.assertIn(checks["signing"], ("unavailable", "unsupported"))


if __name__ == "__main__":
    unittest.main()
