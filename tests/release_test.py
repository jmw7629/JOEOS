import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


def _load_release():
    spec = importlib.util.spec_from_file_location("release_tool", str(ROOT / "scripts" / "release.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VersionAuthorityTests(unittest.TestCase):
    def test_authoritative_version_is_semantic(self):
        import version
        self.assertRegex(version.current_version(), r"^[0-9]+\.[0-9]+\.[0-9]+$")

    def test_manifest_matches_backend(self):
        import version
        self.assertEqual(version.manifest_version(), version.current_version())

    def test_consistency_report_ok(self):
        import version
        consistent, comps, problems = version.check_consistency()
        self.assertTrue(consistent, problems)
        self.assertEqual(comps["backend"], comps["manifest"])

    def test_inconsistent_manifest_is_detected(self):
        import version
        original = version.MANIFEST.read_text(encoding="utf-8")
        try:
            version.MANIFEST.write_text(json.dumps({"version": "9.9.9"}), encoding="utf-8")
            consistent, comps, problems = version.check_consistency()
            self.assertFalse(consistent)
            self.assertTrue(any("does not match" in problem for problem in problems))
        finally:
            version.MANIFEST.write_text(original, encoding="utf-8")

    def test_manifest_is_valid_json_with_version(self):
        import version
        payload = json.loads(version.MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(payload["version"], version.current_version())


class ReleaseToolTests(unittest.TestCase):
    def setUp(self):
        self.release = _load_release()

    def test_check_reports_consistent(self):
        self.assertEqual(self.release.check(), 0)

    def test_dry_run_packages_complete_bundle(self):
        with tempfile.TemporaryDirectory() as scratch:
            output = Path(scratch) / "bundle"
            self.assertEqual(self.release.package(output), 0)
            manifest = json.loads((output / "release-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["version"], self.release.current_version())
            self.assertGreaterEqual(len(manifest["files"]), 10)
            # Critical production files must be present and hashed.
            for label in ("web/index.html", "joeos_backend.py", "requirements.txt", "web/manifest.webmanifest"):
                self.assertIn(label, manifest["files"])
                self.assertRegex(manifest["files"][label], r"^[0-9a-f]{64}$")
            self.assertTrue((output / "joeos_backend.py").exists())
            self.assertTrue((output / "server").is_dir())
            self.assertTrue((output / "web" / "index.html").exists())
            self.assertTrue((output / "sdk" / "index.js").exists())

    def test_sha256_matches_file_contents(self):
        import hashlib
        with tempfile.TemporaryDirectory() as scratch:
            output = Path(scratch) / "bundle"
            self.release.package(output)
            manifest = json.loads((output / "release-manifest.json").read_text(encoding="utf-8"))
            for label in ("web/index.html", "requirements.txt"):
                path = output / label
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                self.assertEqual(manifest["files"][label], digest)

    def test_packaging_never_writes_to_repo(self):
        # Transient SQLite WAL/SHM sidecars are created/removed by concurrent
        # test runs holding the autonomous DB open — not by the release tool.
        # Excluding them keeps the assertion about packaging purity intact.
        def snapshot():
            return set(
                p.resolve()
                for p in ROOT.rglob("*")
                if p.is_file()
                and ".git" not in p.parts
                and ".venv" not in p.parts
                and "__pycache__" not in p.parts
                and p.suffix not in (".db-wal", ".db-shm")
            )

        before = snapshot()
        with tempfile.TemporaryDirectory() as scratch:
            self.release.package(Path(scratch) / "bundle")
        after = snapshot()
        self.assertEqual(before, after)


class DiagnosticsReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "joeos-test.db"
        import joeos_backend as backend
        self.backend = backend
        backend._prepare_database(self.db_path)
        backend.psutil = None

    def tearDown(self):
        self.tempdir.cleanup()

    def test_diagnostics_is_redacted_and_complete(self):
        from tests.backend_test import make_request
        payload = self.backend.diagnostics(make_request(self.db_path, runtime={"online": True, "model": "local-test-model"}))
        self.assertEqual(payload["joeos_version"], self.backend.JOEOS_VERSION)
        self.assertIn("python_version", payload)
        self.assertIn("services", payload)
        self.assertIn("counts", payload)
        self.assertIn("storage_bytes", payload)
        self.assertTrue(payload["runtime"]["online"])
        # Redaction guarantee: no filesystem paths, secrets, or prompts.
        serialized = json.dumps(payload)
        self.assertIn("redaction", payload)
        self.assertNotIn(self.db_path.name, serialized)
        self.assertNotIn("secret", serialized.lower().replace("secrets, prompts", ""))
        self.assertNotIn("/home/", serialized)
        self.assertNotIn("-----BEGIN", serialized)

    def test_storage_sizes_reports_main_database(self):
        sizes = self.backend._storage_sizes(self.db_path)
        self.assertIn("main_database", sizes)
        self.assertGreater(sizes["main_database"], 0)

    def test_diagnostic_counts_from_real_store(self):
        counts = self.backend._diagnostic_counts(self.db_path)
        self.assertIn("bots", counts)
        self.assertIn("events", counts)
        self.assertIn("metric_samples", counts)

    def test_writable_data_dir_probe_creates_and_removes_probe(self):
        probe = self.db_path.parent / ".joeos-write-probe"
        self.backend._verify_writable_data_dir(self.db_path)
        self.assertFalse(probe.exists())


if __name__ == "__main__":
    unittest.main()
