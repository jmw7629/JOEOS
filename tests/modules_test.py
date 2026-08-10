"""Module manifest contract + catalog tests (enterprise shared architecture)."""

import tempfile
import unittest
from pathlib import Path

from server.modules import (
    ManifestValidationError,
    ModuleCatalog,
    ModuleManifest,
    module_manifest_from,
    validate_manifest,
)


class ManifestValidationTests(unittest.TestCase):
    def test_valid_manifest(self):
        manifest = validate_manifest({
            "id": "agents", "display_name": "Agents", "route": "/os/agents",
            "category": "core", "widgets": [{"id": "list", "type": "list"}],
            "joe_context": {"kind": "module"},
        })
        self.assertEqual(manifest.id, "agents")
        self.assertEqual(manifest.route, "/os/agents")
        self.assertEqual(len(manifest.widgets), 1)
        self.assertEqual(manifest.widgets[0].type, "list")

    def test_missing_id_rejected(self):
        with self.assertRaises(ManifestValidationError):
            validate_manifest({"display_name": "x"})

    def test_unsafe_route_rejected(self):
        with self.assertRaises(ManifestValidationError):
            validate_manifest({"id": "x", "route": "http://evil.example"})

    def test_unknown_component_rejected(self):
        with self.assertRaises(ManifestValidationError):
            validate_manifest({"id": "x", "route": "/os/x",
                               "widgets": [{"id": "w", "type": "arbitrary_executable"}]})

    def test_unsafe_joe_context_rejected(self):
        with self.assertRaises(ManifestValidationError):
            validate_manifest({"id": "x", "route": "/os/x", "joe_context": {"kind": "shell"}})

    def test_invalid_visibility_rejected(self):
        with self.assertRaises(ManifestValidationError):
            validate_manifest({"id": "x", "route": "/os/x", "visibility": "everything"})

    def test_json_string_parse(self):
        manifest = module_manifest_from('{"id":"a","route":"/os/a","display_name":"A"}')
        self.assertEqual(manifest.id, "a")

    def test_to_dict_roundtrip(self):
        manifest = validate_manifest({"id": "a", "route": "/os/a", "display_name": "A",
                                      "required_permissions": ["x"]})
        data = manifest.to_dict()
        self.assertEqual(data["required_permissions"], ["x"])
        self.assertEqual(data["schema_version"], 1)


class ModuleCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.catalog = ModuleCatalog(str(Path(self.tmp.name) / "modules"))
        self.catalog.prepare()

    def tearDown(self):
        self.tmp.cleanup()

    def test_seed_and_list_builtin(self):
        self.catalog.seed_builtin(validate_manifest({"id": "cmd", "route": "/os/command", "display_name": "Cmd"}))
        manifests = self.catalog.list()
        self.assertEqual(len(manifests), 1)
        self.assertEqual(manifests[0].id, "cmd")

    def test_put_user_module_then_get(self):
        manifest = validate_manifest({"id": "my-mod", "route": "/os/my-mod", "display_name": "My Module",
                                      "widgets": [{"id": "g", "type": "grid"}]})
        self.catalog.put(manifest, scope="user", owner_id="owner-1")
        got = self.catalog.get("my-mod")
        self.assertIsNotNone(got)
        self.assertEqual(got.widgets[0].type, "grid")

    def test_remove_supersedes(self):
        self.catalog.put(validate_manifest({"id": "m", "route": "/os/m", "display_name": "M"}),
                         scope="user", owner_id="o")
        self.assertTrue(self.catalog.remove("m"))
        self.assertIsNone(self.catalog.get("m"))


if __name__ == "__main__":
    unittest.main()


class ModuleApiTests(unittest.TestCase):
    """Endpoint contract tests (auth gate + capability report)."""

    def setUp(self):
        import os
        import tempfile

        import joeos_backend as backend
        from fastapi.testclient import TestClient

        self.tmp = tempfile.TemporaryDirectory()
        os.environ["JOEOS_DB_PATH"] = str(Path(self.tmp.name) / "joeos.db")
        os.environ["LEMONADE_CONNECT_TIMEOUT"] = "0.1"
        os.environ["LEMONADE_READ_TIMEOUT"] = "0.2"
        self.client = TestClient(backend.app, base_url="http://127.0.0.1")
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    def test_module_list_requires_session(self):
        response = self.client.get("/api/v1/modules")
        self.assertIn(response.status_code, (401, 403))

    def test_capabilities_requires_session(self):
        response = self.client.get("/api/v1/modules/capabilities")
        self.assertIn(response.status_code, (401, 403))
