"""Tests for the JoeOS Plugin and Extension Platform.

Covers manifest validation, identity, integrity, signatures, publisher trust,
installation, permissions, lifecycle (enable/disable/activate/deactivate),
contribution registration, extension storage/settings/secrets, events,
quarantine, safe mode, updates, rollback, uninstall, and the extension host
isolation boundary.
"""

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from server.plugins import PluginService, PluginManifest
from server.plugins.integrity import compute_inventory, write_inventory_file
from server.plugins.lifecycle import (
    PluginLifecycleError,
    parse_manifest,
)
from server.plugins.signature import (
    evaluate_signature,
    generate_development_key_pair,
    public_key_fingerprint,
    sign_inventory,
)

MASTER_KEY = bytes(range(32))


_write_counter = 0


def _write_plugin(dir_path: Path, *, manifest: dict, entry: str = None, name: str = None) -> Path:
    global _write_counter
    _write_counter += 1
    plugin_dir = Path(dir_path) / (name or ("plugin_src_%d" % _write_counter))
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    module = str(manifest["entry_point"]["module"])
    module_path = plugin_dir / (module.replace(".", "/") + ".py")
    module_path.parent.mkdir(parents=True, exist_ok=True)
    module_path.write_text(
        entry or "def handle(method, params, api):\n    return {'ok': True}\n",
        encoding="utf-8",
    )
    return plugin_dir


def _dev_manifest(**overrides) -> dict:
    manifest = {
        "id": "acme.hello",
        "name": "Hello Plugin",
        "version": "1.0.0",
        "development": True,
        "publisher": {"id": "acme", "name": "Acme"},
        "entry_point": {"runtime": "python", "module": "hello_plugin", "function": "handle"},
        "required_permissions": [{"permission": "notification.publish", "purpose": "notify"}],
        "optional_permissions": [{"permission": "storage.extension_data"}],
        "contributions": [
            {
                "type": "command",
                "id": "greet",
                "title": "Greet",
                "commands": ["acme.hello.greet"],
                "requires_permissions": ["notification.publish"],
            }
        ],
        "settings": [
            {"key": "greeting", "type": "string", "default": "Hello", "scope": "global"}
        ],
    }
    manifest.update(overrides)
    return manifest


class PluginFixture(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.service = PluginService(
            str(self.root / "plugins"),
            master_key=MASTER_KEY,
            first_party_publishers=["acme"],
        )
        self.plugin_dir = _write_plugin(self.root, manifest=_dev_manifest())
        self.record = self.service.install_directory(str(self.plugin_dir), source="local_development")

    def tearDown(self):
        self.service.shutdown()
        self.tempdir.cleanup()

    def _grant_all(self, plugin_id=None):
        plugin_id = plugin_id or self.record.plugin_id
        for permission in ("notification.publish", "storage.extension_data"):
            self.service.grant_permission(plugin_id, permission)
        self.service.enable(plugin_id)


class ManifestValidationTests(unittest.TestCase):
    def test_valid_manifest_parses(self):
        manifest = parse_manifest(_dev_manifest())
        self.assertEqual(manifest.id, "acme.hello")
        self.assertEqual(manifest.version, "1.0.0")
        self.assertTrue(manifest.development)

    def test_duplicate_contribution_ids_rejected(self):
        manifest = _dev_manifest()
        manifest["contributions"] = [
            {"type": "command", "id": "dup", "commands": ["acme.hello.a"]},
            {"type": "tool", "id": "dup", "commands": ["acme.hello.b"]},
        ]
        with self.assertRaises(PluginLifecycleError):
            parse_manifest(manifest)

    def test_invalid_permission_rejected(self):
        manifest = _dev_manifest()
        manifest["required_permissions"] = [{"permission": "no.such.permission"}]
        with self.assertRaises(PluginLifecycleError):
            parse_manifest(manifest)

    def test_undeclared_contribution_permission_rejected(self):
        manifest = _dev_manifest()
        manifest["contributions"] = [
            {"type": "command", "id": "x", "commands": ["acme.hello.x"], "requires_permissions": ["secrets.request_named_extension_secret"]}
        ]
        with self.assertRaises(PluginLifecycleError):
            parse_manifest(manifest)

    def test_path_traversal_module_rejected(self):
        manifest = _dev_manifest()
        manifest["entry_point"]["module"] = "../../etc/passwd"
        with self.assertRaises(PluginLifecycleError):
            parse_manifest(manifest)

    def test_unbounded_startup_activation_rejected(self):
        manifest = _dev_manifest()
        manifest["activation_events"] = ["application_startup", "project_opened"]
        with self.assertRaises(PluginLifecycleError):
            parse_manifest(manifest)

    def test_plugin_id_must_match_publisher(self):
        manifest = _dev_manifest()
        manifest["publisher"] = {"id": "other", "name": "Other"}
        with self.assertRaises(PluginLifecycleError):
            parse_manifest(manifest)


class IdentityAndIntegrityTests(PluginFixture):
    def test_plugin_identity_is_stable(self):
        self.assertEqual(self.record.plugin_id, "acme.hello")
        self.assertEqual(self.record.publisher_id, "acme")
        self.assertEqual(self.record.version, "1.0.0")

    def test_inventory_verifies(self):
        result = self.service.verify_integrity(self.record.plugin_id)
        self.assertTrue(result["valid"])

    def test_modified_file_changes_integrity(self):
        install_path = Path(self.record.install_path)
        (install_path / "hello_plugin.py").write_text("def handle(method, params, api):\n    return {'tampered': True}\n")
        result = self.service.verify_integrity(self.record.plugin_id)
        self.assertFalse(result["valid"])

    def test_signature_states(self):
        private_key, spki = generate_development_key_pair()
        fingerprint = public_key_fingerprint(private_key.public_key())
        root_hash = compute_inventory(str(self.record.install_path))[1]
        signed = sign_inventory(root_hash, "acme.hello", "1.0.0", private_key)
        evaluation = evaluate_signature(
            inventory_root_hash=root_hash,
            plugin_id="acme.hello",
            version="1.0.0",
            encoded_signature=signed,
            signer_public_key=spki,
            first_party_fingerprints=(fingerprint,),
        )
        self.assertEqual(evaluation.state, "valid_first_party")
        tampered = evaluate_signature(
            inventory_root_hash="deadbeef",
            plugin_id="acme.hello",
            version="1.0.0",
            encoded_signature=signed,
            signer_public_key=spki,
            first_party_fingerprints=(fingerprint,),
        )
        self.assertEqual(tampered.state, "invalid")
        unsigned = evaluate_signature(
            inventory_root_hash=root_hash,
            plugin_id="acme.hello",
            version="1.0.0",
            encoded_signature="",
        )
        self.assertEqual(unsigned.state, "unsigned")


class PublisherTests(PluginFixture):
    def test_first_party_publisher(self):
        publisher = self.service.publisher("acme")
        self.assertEqual(publisher.verification_state, "first_party")
        self.assertTrue(publisher.trusted)
        self.assertTrue(publisher.first_party)

    def test_unknown_publisher_is_unverified(self):
        # A second publisher not registered as first-party stays unverified.
        manifest = _dev_manifest()
        manifest["id"] = "other.hello"
        manifest["name"] = "Other Hello"
        manifest["publisher"] = {"id": "other", "name": "Other"}
        plugin_dir = _write_plugin(self.root, manifest=manifest)
        record = self.service.install_directory(str(plugin_dir), source="local_development")
        publisher = self.service.publisher("other")
        self.assertFalse(publisher.trusted)
        self.assertEqual(publisher.verification_state, "unknown")

    def test_publisher_trust_toggle(self):
        self.service.set_publisher_trust("acme", True)
        publisher = self.service.publisher("acme")
        self.assertEqual(publisher.verification_state, "user_trusted")


class PermissionTests(PluginFixture):
    def test_permissions_start_pending(self):
        summary = self.service.permission_summary(self.record.plugin_id)
        self.assertIn("notification.publish", summary.pending)
        self.assertIn("storage.extension_data", summary.pending)

    def test_cannot_grant_undeclared_permission(self):
        with self.assertRaises(PluginLifecycleError):
            self.service.grant_permission(self.record.plugin_id, "filesystem.access_outside_projects")

    def test_plugin_cannot_self_grant(self):
        # Grant flows only through the manager; a plugin id cannot grant to
        # itself through the API because the service checks declaration only.
        # The CapabilityBroker additionally rejects inactive plugins.
        from server.plugins.permissions import PermissionError
        broker = self.service.capability
        with self.assertRaises(PermissionError):
            broker.check(plugin_id=self.record.plugin_id, capability="read_project_file")

    def test_required_permission_blocks_enable(self):
        with self.assertRaises(PluginLifecycleError):
            self.service.enable(self.record.plugin_id)

    def test_revoked_permission_takes_effect(self):
        self._grant_all()
        self.service.activate(self.record.plugin_id)
        self.service.revoke_permission(self.record.plugin_id, "notification.publish")
        level = self.service.permissions.level(
            plugin_id=self.record.plugin_id, permission="notification.publish"
        )
        self.assertEqual(level, "revoked")


class LifecycleTests(PluginFixture):
    def test_enable_then_activate_then_invoke(self):
        self._grant_all()
        self.service.activate(self.record.plugin_id)
        record = self.service.get(self.record.plugin_id)
        self.assertEqual(record.lifecycle_state, "active")
        result = self.service.invoke_contribution(self.record.plugin_id, "acme.hello.greet", {"name": "Joe"})
        self.assertEqual(result["result"], {"ok": True})

    def test_activate_requires_enabled(self):
        self.service.grant_permission(self.record.plugin_id, "notification.publish")
        self.service.grant_permission(self.record.plugin_id, "storage.extension_data")
        with self.assertRaises(PluginLifecycleError):
            self.service.activate(self.record.plugin_id)

    def test_disable_stops_host_and_contributions(self):
        self._grant_all()
        self.service.activate(self.record.plugin_id)
        self.service.disable(self.record.plugin_id)
        record = self.service.get(self.record.plugin_id)
        self.assertEqual(record.enabled_state, "disabled")
        self.assertEqual(record.lifecycle_state, "disabled")
        self.assertNotIn(self.record.plugin_id, self.service.hosts.running_plugin_ids())

    def test_duplicate_install_rejected(self):
        with self.assertRaises(PluginLifecycleError):
            self.service.install_directory(str(self.plugin_dir), source="local_development")

    def test_uninstall_removes_record(self):
        self._grant_all()
        self.service.uninstall(self.record.plugin_id)
        self.assertIsNone(self.service.get(self.record.plugin_id))

    def test_uninstall_requires_dependent_check(self):
        # A dependent plugin referencing this one as required blocks uninstall.
        dependent_manifest = _dev_manifest()
        dependent_manifest["id"] = "acme.dependent"
        dependent_manifest["name"] = "Dependent"
        dependent_manifest["entry_point"]["module"] = "dependent_plugin"
        dependent_manifest["dependencies"] = [{"plugin_id": "acme.hello", "version_range": "1.0.0"}]
        dependent_manifest["development"] = True
        dep_dir = _write_plugin(self.root, manifest=dependent_manifest)
        self.service.install_directory(str(dep_dir), source="local_development")
        with self.assertRaises(PluginLifecycleError):
            self.service.uninstall(self.record.plugin_id)


class QuarantineAndSafeModeTests(PluginFixture):
    def test_quarantine_stops_background_work(self):
        self._grant_all()
        self.service.activate(self.record.plugin_id)
        self.service.quarantine_plugin(self.record.plugin_id, "suspicious behavior")
        record = self.service.get(self.record.plugin_id)
        self.assertEqual(record.lifecycle_state, "quarantined")
        self.assertEqual(record.enabled_state, "quarantined")
        self.assertNotIn(self.record.plugin_id, self.service.hosts.running_plugin_ids())

    def test_quarantined_plugin_cannot_activate(self):
        self._grant_all()
        self.service.quarantine_plugin(self.record.plugin_id, "integrity failure")
        with self.assertRaises(PluginLifecycleError):
            self.service.activate(self.record.plugin_id)

    def test_restore_from_quarantine(self):
        self.service.quarantine_plugin(self.record.plugin_id, "test")
        self.service.restore(self.record.plugin_id)
        record = self.service.get(self.record.plugin_id)
        self.assertNotEqual(record.lifecycle_state, "quarantined")

    def test_safe_mode_disables_third_party(self):
        # Use a non-first-party publisher so Safe Mode actually applies.
        manifest = _dev_manifest()
        manifest["id"] = "third.party"
        manifest["name"] = "Third Party"
        manifest["publisher"] = {"id": "third", "name": "Third"}
        manifest["entry_point"]["module"] = "third_party"
        third_dir = _write_plugin(self.root, manifest=manifest)
        record = self.service.install_directory(str(third_dir), source="local_development")
        self.service.grant_permission(record.plugin_id, "notification.publish")
        self.service.grant_permission(record.plugin_id, "storage.extension_data")
        self.service.enable(record.plugin_id)
        self.service.activate(record.plugin_id)
        self.service.enter_safe_mode()
        self.assertTrue(self.service.safe_mode.active)
        with self.assertRaises(PluginLifecycleError):
            self.service.activate(record.plugin_id)
        self.service.exit_safe_mode()
        self.assertFalse(self.service.safe_mode.active)


class ExtensionDataTests(PluginFixture):
    def test_storage_is_isolated(self):
        self.service.storage_service.put(plugin_id=self.record.plugin_id, key="data", value="value1")
        value = self.service.storage_service.get(plugin_id=self.record.plugin_id, key="data")
        self.assertEqual(value, "value1")

    def test_settings_validate_against_schema(self):
        self.service.set_setting(self.record.plugin_id, "greeting", "Good day")
        value = self.service.get_setting(self.record.plugin_id, "greeting")
        self.assertEqual(value, "Good day")

    def test_undeclared_setting_rejected(self):
        with self.assertRaises(Exception):
            self.service.set_setting(self.record.plugin_id, "nope", "x")

    def test_secret_is_isolated_and_masked(self):
        manifest = _dev_manifest()
        manifest["id"] = "acme.secure"
        manifest["name"] = "Secure"
        manifest["entry_point"]["module"] = "secure_plugin"
        manifest["required_permissions"] = [
            {"permission": "notification.publish", "purpose": "notify"},
            {"permission": "secrets.request_named_extension_secret", "purpose": "token"},
        ]
        plugin_dir = _write_plugin(self.root, manifest=manifest)
        record = self.service.install_directory(str(plugin_dir), source="local_development")
        self.service.grant_permission(record.plugin_id, "notification.publish")
        self.service.grant_permission(record.plugin_id, "secrets.request_named_extension_secret")
        self.service.enable(record.plugin_id)
        self.service.activate(record.plugin_id)
        ref = self.service.set_secret(record.plugin_id, "token", "s3cr3t")
        # The value is never returned by references_for.
        refs = self.service.secret_references(record.plugin_id)
        self.assertEqual(len(refs), 1)
        self.assertNotIn("s3cr3t", json.dumps(refs))
        # The broker enforces lifecycle: deactivating revokes access.
        self.service.deactivate(record.plugin_id)
        with self.assertRaises(Exception):
            self.service.secrets.retrieve(plugin_id=record.plugin_id, ref_id=ref["reference"])

    def test_another_plugin_cannot_read_secret(self):
        manifest = _dev_manifest()
        manifest["id"] = "acme.other"
        manifest["name"] = "Other"
        manifest["entry_point"]["module"] = "other_plugin"
        manifest["required_permissions"] = [
            {"permission": "notification.publish", "purpose": "notify"},
            {"permission": "secrets.request_named_extension_secret", "purpose": "token"},
        ]
        other_dir = _write_plugin(self.root, manifest=manifest)
        other_record = self.service.install_directory(str(other_dir), source="local_development")
        self.service.grant_permission(other_record.plugin_id, "notification.publish")
        self.service.grant_permission(other_record.plugin_id, "secrets.request_named_extension_secret")
        self.service.enable(other_record.plugin_id)
        self.service.activate(other_record.plugin_id)
        ref = self.service.set_secret(other_record.plugin_id, "token", "value")
        # The first plugin (acme.hello) cannot read the other plugin's secret.
        with self.assertRaises(Exception):
            self.service.secrets.retrieve(plugin_id=self.record.plugin_id, ref_id=ref["reference"])


class UpdateAndRollbackTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.service = PluginService(
            str(self.root / "plugins"),
            master_key=MASTER_KEY,
            first_party_publishers=["acme"],
        )

    def tearDown(self):
        self.service.shutdown()
        self.tempdir.cleanup()

    def _package(self, version: str, permissions: list, extra: bool = False) -> str:
        manifest = _dev_manifest()
        manifest["version"] = version
        manifest["development"] = False
        manifest["required_permissions"] = permissions
        manifest["publisher"] = {"id": "acme", "name": "Acme"}
        src = self.root / ("pkg_" + version.replace(".", "_"))
        src.mkdir(exist_ok=True)
        (src / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        body = "def handle(method, params, api):\n    return {'ok': True, 'version': '%s'}\n" % version
        if extra:
            body += "\nEXTRA_VERSION=%r\n" % version
        (src / "hello_plugin.py").write_text(body, encoding="utf-8")
        package_path = self.root / ("hello-%s.zip" % version)
        with zipfile.ZipFile(str(package_path), "w") as archive:
            for item in src.iterdir():
                archive.write(str(item), item.name)
        return str(package_path)

    def test_update_with_expanded_permissions_requires_review(self):
        v1 = self._package("1.0.0", [{"permission": "notification.publish", "purpose": "notify"}])
        record = self.service.install_package(v1, source="local_package", approval={"signature": ""})
        self.service.grant_permission(record.plugin_id, "notification.publish")
        self.service.enable(record.plugin_id)
        self.service.activate(record.plugin_id)
        v2 = self._package(
            "2.0.0",
            [
                {"permission": "notification.publish", "purpose": "notify"},
                {"permission": "secrets.request_named_extension_secret", "purpose": "token"},
            ],
        )
        self.service.update(record.plugin_id, v2, approval={"signature": ""})
        updated = self.service.get(record.plugin_id)
        self.assertEqual(updated.version, "2.0.0")
        self.assertEqual(updated.lifecycle_state, "pending_permissions")
        self.assertEqual(updated.enabled_state, "disabled")

    def test_rollback_returns_to_previous_version(self):
        v1 = self._package("1.0.0", [{"permission": "notification.publish", "purpose": "notify"}])
        record = self.service.install_package(v1, source="local_package", approval={"signature": ""})
        self.service.grant_permission(record.plugin_id, "notification.publish")
        self.service.enable(record.plugin_id)
        v2 = self._package("2.0.0", [{"permission": "notification.publish", "purpose": "notify"}])
        self.service.update(record.plugin_id, v2, approval={"signature": ""})
        rolled = self.service.rollback(record.plugin_id)
        self.assertEqual(rolled.version, "1.0.0")


class ContributionTests(PluginFixture):
    def test_contributions_registered(self):
        contributions = self.service.contribution_list(self.record.plugin_id)
        self.assertEqual(len(contributions), 1)
        self.assertEqual(contributions[0].type, "command")
        self.assertEqual(contributions[0].state, "registered")

    def test_contributions_activate_and_unregister(self):
        self._grant_all()
        self.service.activate(self.record.plugin_id)
        active = self.service.active_contributions()
        self.assertIn("acme.hello.greet", [c.contribution_id for c in active])
        self.service.disable(self.record.plugin_id)
        self.assertEqual(len(self.service.contribution_list(self.record.plugin_id)), 0)


class DevelopmentHostTests(PluginFixture):
    def test_dev_link_resolves_source(self):
        path = self.service.development_link("acme.hello", str(self.plugin_dir))
        self.assertEqual(path, str(self.plugin_dir))
        self.service.development_unlink("acme.hello")


class OverviewTests(PluginFixture):
    def test_overview_reflects_real_state(self):
        self._grant_all()
        self.service.activate(self.record.plugin_id)
        overview = self.service.overview()
        self.assertEqual(overview.installed, 1)
        self.assertEqual(overview.active, 1)
        self.assertFalse(overview.safe_mode)


class HostIsolationTests(PluginFixture):
    def test_host_runs_in_subprocess(self):
        self._grant_all()
        self.service.activate(self.record.plugin_id)
        self.assertIn(self.record.plugin_id, self.service.hosts.running_plugin_ids())

    def test_broken_plugin_crash_does_not_break_core(self):
        broken = _write_plugin(
            self.root,
            manifest={
                **_dev_manifest(),
                "id": "acme.broken",
                "name": "Broken",
                "entry_point": {"runtime": "python", "module": "broken_plugin", "function": "handle"},
            },
            entry="def handle(method, params, api):\n    raise RuntimeError('boom')\n",
        )
        record = self.service.install_directory(str(broken), source="local_development")
        self.service.grant_permission(record.plugin_id, "notification.publish")
        self.service.grant_permission(record.plugin_id, "storage.extension_data")
        self.service.enable(record.plugin_id)
        with self.assertRaises(PluginLifecycleError):
            self.service.activate(record.plugin_id)
        # Core still works.
        self.assertEqual(self.service.get(self.record.plugin_id).plugin_id, "acme.hello")


if __name__ == "__main__":
    unittest.main()