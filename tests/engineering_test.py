import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import joeos_backend as backend
from server.engineering.models import (
    DocumentWriteRequest,
    ProjectRecord,
    TrustState,
)
from server.engineering.router import router
from server.engineering.service import EngineeringService


def _git(*args, cwd):
    return subprocess.run(
        ["git"] + list(args),
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C"},
    )


class EngineeringFixture(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.project_root = Path(self.tempdir.name) / "sample-project"
        self.project_root.mkdir()
        (self.project_root / "hello.py").write_text("def hello():\n    return 'world'\n")
        (self.project_root / "README.md").write_text("# Sample Project\n")
        (self.project_root / ".gitignore").write_text("*.pyc\n")
        self.db_path = str(Path(self.tempdir.name) / "joeos.db")
        self.service = EngineeringService(lambda: backend._connect(self.db_path))
        self.project = self.service.register_project(
            "sample", str(self.project_root)
        )

    def tearDown(self):
        self.tempdir.cleanup()


class ProjectTests(EngineeringFixture):
    def test_register_detects_characteristics_and_defaults_to_untrusted(self):
        self.assertIsInstance(self.project, ProjectRecord)
        self.assertEqual(self.project.trust_state, "untrusted")
        characteristics = {item.characteristic for item in self.project.characteristics}
        self.assertIn("git_repository" if (self.project_root / ".git").exists() else "python", characteristics)
        self.assertTrue(self.project.fingerprint)

    def test_register_requires_existing_directory(self):
        from server.engineering.projects import ProjectPathError

        with self.assertRaises(ProjectPathError):
            self.service.register_project("missing", "/nonexistent/path/xyz")

    def test_list_and_get_round_trip(self):
        listed = self.service.list_projects()
        self.assertEqual([item.project_id for item in listed.projects], [self.project.project_id])
        fetched = self.service.get_project(self.project.project_id)
        self.assertEqual(fetched.path, str(self.project_root))

    def test_set_trust_and_remove(self):
        trusted = self.service.set_project_trust(self.project.project_id, "trusted")
        self.assertEqual(trusted.trust_state, "trusted")
        self.service.remove_project(self.project.project_id)
        from server.engineering.projects import ProjectNotFoundError

        with self.assertRaises(ProjectNotFoundError):
            self.service.get_project(self.project.project_id)


class FilesystemTests(EngineeringFixture):
    def test_list_directory_bounds_to_project_root(self):
        listing = self.service.list_directory(self.project.project_id)
        names = {entry.name for entry in listing.entries}
        self.assertIn("hello.py", names)
        self.assertIn("README.md", names)

    def test_list_directory_rejects_traversal(self):
        from server.engineering.filesystem import PathBoundaryError

        with self.assertRaises(PathBoundaryError):
            self.service.list_directory(self.project.project_id, "../../etc")

    def test_read_document_masks_likely_secrets(self):
        (self.project_root / "notes.txt").write_text("API_KEY='secret-value-123'\n")
        state = self.service.read_document(self.project.project_id, "notes.txt")
        self.assertGreaterEqual(state.masked_secrets, 1)
        self.assertNotIn("secret-value-123", state.content)

    def test_write_document_conflict_detection(self):
        state = self.service.read_document(self.project.project_id, "README.md")
        request = DocumentWriteRequest(
            path="README.md",
            content="# Edited\n",
            base_revision=state.revision.sha256,
        )
        result = self.service.write_document(self.project.project_id, request)
        self.assertTrue(result.saved)
        self.assertFalse(result.conflict)
        (self.project_root / "README.md").write_text("# External change\n")
        stale = DocumentWriteRequest(
            path="README.md",
            content="# Stale overwrite\n",
            base_revision=state.revision.sha256,
        )
        conflict = self.service.write_document(self.project.project_id, stale)
        self.assertFalse(conflict.saved)
        self.assertTrue(conflict.conflict)


class SecretTests(EngineeringFixture):
    def test_scan_repository_finds_secrets_and_masks(self):
        (self.project_root / "config.py").write_text(
            "github = 'ghp_1234567890abcdefghijklmnopqrstuvwxyz'\n"
        )
        result = self.service.scan_secrets(self.project.project_id)
        categories = {item.category for item in result.matches}
        self.assertIn("github_token", categories)
        for match in result.matches:
            self.assertNotIn("1234567890abcdefghijklmnopqrstuvwxyz", match.masked)

    def test_secret_files_are_excluded_from_scan(self):
        (self.project_root / ".env").write_text("KEY='super-secret-token-99'\n")
        result = self.service.scan_secrets(self.project.project_id)
        for match in result.matches:
            self.assertNotEqual(match.file, ".env")


class GitTests(EngineeringFixture):
    def _init_repo(self):
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C"}
        subprocess.run(
            ["git", "init", "-q"], cwd=str(self.project_root), env=env, check=True
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=str(self.project_root), env=env, check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=str(self.project_root), env=env, check=True,
        )
        subprocess.run(
            ["git", "add", "-A"], cwd=str(self.project_root), env=env, check=True
        )
        subprocess.run(
            ["git", "commit", "-q", "-m", "initial"], cwd=str(self.project_root), env=env, check=True
        )

    def test_git_status_reports_branch_and_untracked(self):
        self._init_repo()
        (self.project_root / "new_file.py").write_text("x = 1\n")
        status = self.service.git_status(self.project.project_id)
        self.assertEqual(status.branch, "master" if os.path.exists("/usr/bin/git") else status.branch)
        self.assertIsNotNone(status.last_commit)
        self.assertTrue(any(path.endswith("new_file.py") for path in status.untracked))

    def test_git_stage_commit_flow(self):
        self._init_repo()
        (self.project_root / "hello.py").write_text("def hello():\n    return 'changed'\n")
        self.service.git_stage(self.project.project_id, ["hello.py"])
        status = self.service.git_status(self.project.project_id)
        self.assertTrue(any(path.endswith("hello.py") for path in status.staged))
        result = self.service.git_commit(
            self.project.project_id, "update hello", approved=True
        )
        self.assertTrue(result.committed)
        self.assertGreaterEqual(len(result.commit), 7)

    def test_git_commit_requires_approval(self):
        self._init_repo()
        (self.project_root / "hello.py").write_text("def hello():\n    return 'changed'\n")
        self.service.git_stage(self.project.project_id, ["hello.py"])
        from server.engineering.git import GitError

        with self.assertRaises(GitError):
            self.service.git_commit(self.project.project_id, "no approval")

    def test_git_commit_blocks_staged_secrets(self):
        self._init_repo()
        (self.project_root / "leak.py").write_text("ghp_1234567890abcdefghijklmnopqrstuvwxyz = 1\n")
        self.service.git_stage(self.project.project_id, ["leak.py"])
        from server.engineering.git import GitError

        with self.assertRaises(GitError):
            self.service.git_commit(self.project.project_id, "leaky commit", approved=True)


class CommandTests(EngineeringFixture):
    def test_validate_allows_read_only_command(self):
        validation = self.service.validate_command(self.project.project_id, "git status")
        self.assertTrue(validation.allowed)
        self.assertEqual(validation.risk, "medium")
        self.assertFalse(validation.approval_required)

    def test_validate_requires_approval_for_git_push(self):
        validation = self.service.validate_command(self.project.project_id, "git push origin main")
        self.assertTrue(validation.approval_required)
        self.assertFalse(validation.allowed)

    def test_validate_blocks_privilege_escalation(self):
        validation = self.service.validate_command(self.project.project_id, "sudo rm -rf /")
        self.assertFalse(validation.allowed)
        self.assertIsNotNone(validation.blocked_reason)
        self.assertEqual(validation.risk, "high")

    def test_execute_blocked_command_raises(self):
        from server.engineering.commands import CommandError

        with self.assertRaises(CommandError):
            self.service.execute_command(self.project.project_id, "sudo whoami")

    def test_execute_requires_approval(self):
        from server.engineering.commands import CommandError

        with self.assertRaises(CommandError):
            self.service.execute_command(
                self.project.project_id, "git push origin main"
            )

    def test_execute_allowed_command_returns_result(self):
        result = self.service.execute_command(self.project.project_id, "ls -1")
        self.assertEqual(result.state, "succeeded")
        self.assertEqual(result.exit_code, 0)
        self.assertIn("hello.py", result.stdout)


class SearchTests(EngineeringFixture):
    def test_search_finds_matches_across_project(self):
        envelope = self.service.search(self.project.project_id, "hello")
        self.assertGreaterEqual(envelope.files_scanned, 1)
        self.assertTrue(
            any(result.path.endswith("hello.py") for result in envelope.results)
        )

    def test_search_excludes_secret_files(self):
        (self.project_root / ".env").write_text("SECRET_HELLO='top-secret-hello'\n")
        envelope = self.service.search(self.project.project_id, "SECRET_HELLO")
        for result in envelope.results:
            self.assertNotEqual(result.path, ".env")


class EngineeringRouterTests(EngineeringFixture):
    def setUp(self):
        super().setUp()
        app = FastAPI()
        app.state.engineering_service = self.service
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        super().tearDown()

    def test_project_endpoints_round_trip(self):
        created = self.client.post(
            "/api/v1/engineering/projects",
            json={"name": "second", "path": str(self.project_root)},
        )
        self.assertEqual(created.status_code, 200)
        listed = self.client.get("/api/v1/engineering/projects")
        self.assertEqual(listed.status_code, 200)
        self.assertGreaterEqual(len(listed.json()["projects"]), 1)
        fetched = self.client.get(f"/api/v1/engineering/projects/{self.project.project_id}")
        self.assertEqual(fetched.status_code, 200)
        trusted = self.client.put(
            f"/api/v1/engineering/projects/{self.project.project_id}/trust",
            json={"state": "trusted"},
        )
        self.assertEqual(trusted.status_code, 200)
        self.assertEqual(trusted.json()["trust_state"], "trusted")
        removed = self.client.delete(f"/api/v1/engineering/projects/{self.project.project_id}")
        self.assertEqual(removed.status_code, 204)

    def test_files_endpoints(self):
        listing = self.client.get(
            f"/api/v1/engineering/projects/{self.project.project_id}/files"
        )
        self.assertEqual(listing.status_code, 200)
        names = [entry["name"] for entry in listing.json()["entries"]]
        self.assertIn("hello.py", names)
        traversal = self.client.get(
            f"/api/v1/engineering/projects/{self.project.project_id}/files",
            params={"path": "../.."},
        )
        self.assertEqual(traversal.status_code, 400)
        content = self.client.get(
            f"/api/v1/engineering/projects/{self.project.project_id}/files/content",
            params={"path": "hello.py"},
        )
        self.assertEqual(content.status_code, 200)
        self.assertIn("world", content.json()["content"])

    def test_secret_and_search_endpoints(self):
        scan = self.client.get(f"/api/v1/engineering/projects/{self.project.project_id}/secrets")
        self.assertEqual(scan.status_code, 200)
        policy = self.client.get("/api/v1/engineering/secrets/policy")
        self.assertEqual(policy.status_code, 200)
        self.assertIn("masked_categories", policy.json())
        search = self.client.get(
            f"/api/v1/engineering/projects/{self.project.project_id}/search",
            params={"q": "hello"},
        )
        self.assertEqual(search.status_code, 200)
        self.assertGreaterEqual(search.json()["files_scanned"], 1)

    def test_command_endpoints_enforce_gating(self):
        validate = self.client.post(
            f"/api/v1/engineering/projects/{self.project.project_id}/commands/validate",
            json={"command": "git push origin main"},
        )
        self.assertEqual(validate.status_code, 200)
        self.assertTrue(validate.json()["approval_required"])
        self.assertFalse(validate.json()["allowed"])
        execute = self.client.post(
            f"/api/v1/engineering/projects/{self.project.project_id}/commands/execute",
            json={"command": "sudo whoami"},
        )
        self.assertEqual(execute.status_code, 403)

    def test_router_requires_initialized_service(self):
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        response = client.get("/api/v1/engineering/projects")
        self.assertEqual(response.status_code, 503)
        client.close()


class MainApplicationEngineeringTests(EngineeringFixture):
    def test_main_lifespan_serves_engineering(self):
        with tempfile.TemporaryDirectory() as temp_name:
            db_path = str(Path(temp_name) / "joeos.db")
            environment = {
                "JOEOS_DB_PATH": db_path,
                "LEMONADE_CONNECT_TIMEOUT": "0.1",
                "LEMONADE_READ_TIMEOUT": "0.2",
            }
            from unittest.mock import patch

            with patch.dict(os.environ, environment, clear=False):
                with TestClient(backend.app, base_url="http://127.0.0.1") as client:
                    response = client.get("/api/v1/engineering/projects")
                    self.assertEqual(response.status_code, 200)
                    self.assertIsInstance(response.json(), dict)


if __name__ == "__main__":
    unittest.main()
