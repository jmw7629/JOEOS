import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from joesdk.manifest import contribution, dependency, manifest, permission, setting
from joesdk.packaging import (
    ValidationError,
    calculate_integrity,
    package_plugin,
    validate_manifest,
    validate_package,
)


class ManifestHelpersTests(unittest.TestCase):
    def test_manifest_builder_produces_valid_manifest(self):
        data = manifest(
            plugin_id="acme.hello",
            name="Hello",
            version="1.0.0",
            publisher_id="acme",
            publisher_name="Acme",
            module="hello_plugin",
            required_permissions=(permission("notification.publish", "notify"),),
            contributions=(contribution("command", "greet", commands=("acme.hello.greet",)),),
        )
        validate_manifest(data)
        self.assertEqual(data["id"], "acme.hello")

    def test_manifest_builder_rejects_bad_id(self):
        with self.assertRaises(ValueError):
            manifest(
                plugin_id="Hello World",
                name="Hello",
                version="1.0.0",
                publisher_id="acme",
                publisher_name="Acme",
                module="hello_plugin",
            )

    def test_setting_helper(self):
        item = setting("mode", "enum", default="fast", choices=("fast", "slow"))
        self.assertEqual(item["validation"]["choices"], ("fast", "slow"))


class ValidationTests(unittest.TestCase):
    def test_duplicate_contribution_rejected(self):
        data = manifest(
            plugin_id="acme.hello",
            name="Hello",
            version="1.0.0",
            publisher_id="acme",
            publisher_name="Acme",
            module="hello_plugin",
            contributions=(contribution("command", "dup"), contribution("tool", "dup")),
        )
        with self.assertRaises(ValidationError):
            validate_manifest(data)

    def test_undeclared_permission_rejected(self):
        data = manifest(
            plugin_id="acme.hello",
            name="Hello",
            version="1.0.0",
            publisher_id="acme",
            publisher_name="Acme",
            module="hello_plugin",
            required_permissions=({"permission": "no.such.permission"},),
        )
        with self.assertRaises(ValidationError):
            validate_manifest(data)


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.plugin_dir = self.root / "plugin"
        self.plugin_dir.mkdir()
        data = manifest(
            plugin_id="acme.hello",
            name="Hello",
            version="1.0.0",
            publisher_id="acme",
            publisher_name="Acme",
            module="hello_plugin",
        )
        (self.plugin_dir / "manifest.json").write_text(
            __import__("json").dumps(data), encoding="utf-8"
        )
        (self.plugin_dir / "hello_plugin.py").write_text(
            "def handle(method, params, api):\n    return {'ok': True}\n", encoding="utf-8"
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_package_and_integrity(self):
        ok, reason = validate_package(str(self.plugin_dir))
        self.assertTrue(ok, reason)
        inventory = calculate_integrity(str(self.plugin_dir))
        self.assertIn("manifest.json", inventory["files"])
        output = str(self.root / "out.zip")
        package_plugin(str(self.plugin_dir), output)
        self.assertTrue(Path(output).is_file())


if __name__ == "__main__":
    unittest.main()