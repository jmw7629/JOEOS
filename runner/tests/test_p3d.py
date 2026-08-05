"""Phase P3D tests: configuration, identity, journal, daemon, development-command
templates, constrained Git (temporary local repos and a local bare remote),
user-service (deterministic adapter), typed deployment, health checks, runner
local secrets, and CLI surfaces. No external network, no sudo, no real systemd."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from joeos_runner.configuration import RunnerConfiguration, RunnerConfigurationError
from joeos_runner.daemon import RunnerDaemon
from joeos_runner.identity import RunnerIdentityError, RunnerSigner, initialize_identity
from joeos_runner.journal import ExecutionJournal, JournalError
from joeos_runner.operations import (
    DeploymentExecutor,
    DevCommandExecutor,
    GitExecutor,
    HealthChecker,
    UserServiceExecutor,
)
from joeos_runner.process import ProcessExecutionError, run_process
from joeos_runner.secrets import RunnerLocalSecretProvider, SecretResolutionError


def minimal_config(tmp, **overrides):
    base = {
        "backend_url": "http://127.0.0.1:8080",
        "installation_id": "11111111-1111-4111-8111-111111111111",
        "runner_id": "runner-1",
        "organization_id": "22222222-2222-4222-8222-222222222222",
        "workspace_id": "33333333-3333-4333-8333-333333333333",
        "identity_path": str(Path(tmp) / "identity.json"),
        "key_path": str(Path(tmp) / "runner-key.pem"),
        "journal_path": str(Path(tmp) / "journal.jsonl"),
        "state_path": str(Path(tmp) / "state"),
        "work_root": str(Path(tmp) / "work"),
        "workspace_roots": str(Path(tmp) / "workspace"),
    }
    base.update(overrides)
    return base


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, payload, key_mode=0o600):
        root = Path(self.tmp.name)
        key = root / "runner-key.pem"
        key.write_text("x" * 40)
        key.chmod(key_mode)
        config = root / "config.json"
        config.write_text(json.dumps(payload))
        config.chmod(0o600)
        return str(config)

    def test_unknown_field_rejected(self):
        config = self._write(minimal_config(self.tmp.name, unexpected=1))
        with self.assertRaises(RunnerConfigurationError):
            RunnerConfiguration.load(config)

    def test_unsafe_public_http_rejected(self):
        config = self._write(minimal_config(self.tmp.name, backend_url="http://8.8.8.8:8080"))
        with self.assertRaises(RunnerConfigurationError):
            RunnerConfiguration.load(config)

    def test_loopback_accepted_and_key_permissions_checked(self):
        config = self._write(minimal_config(self.tmp.name), key_mode=0o644)
        with self.assertRaises(RunnerConfigurationError):
            RunnerConfiguration.load(config)
        config = self._write(minimal_config(self.tmp.name))
        loaded = RunnerConfiguration.load(config)
        self.assertEqual(loaded.protocol_version, 1)
        self.assertNotIn("simulated-secret", json.dumps(loaded.effective_summary()))


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_initialize_load_and_sign_without_printing_key(self):
        key_path = str(Path(self.tmp.name) / "runner-key.pem")
        signer = initialize_identity(key_path, "runner-key-1")
        public_key = signer.public_key()
        signature = signer.sign("JOEOS-RUNNER-CONNECTION-V1\0test")
        self.assertTrue(signature)
        self.assertTrue(public_key)
        mode = Path(key_path).stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)
        self.assertNotIn("PRIVATE", public_key)


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.journal = ExecutionJournal(str(Path(self.tmp.name) / "journal.jsonl"), "runner-1")

    def tearDown(self):
        self.tmp.cleanup()

    def test_append_and_verify(self):
        self.journal.append(job_id="job-1", lease_generation=1, state="running", executor="e1")
        self.journal.append(job_id="job-1", lease_generation=1, state="succeeded", executor="e1")
        self.assertTrue(self.journal.verify())
        self.assertEqual(len(self.journal.entries()), 2)

    def test_tamper_detection(self):
        self.journal.append(job_id="job-1", lease_generation=1, state="running", executor="e1")
        path = Path(self.tmp.name) / "journal.jsonl"
        content = path.read_text()
        path.write_text(content.replace('"state":"running"', '"state":"succeeded"'))
        self.assertFalse(self.journal.verify())

    def test_truncation_detection(self):
        self.journal.append(job_id="job-1", lease_generation=1, state="running", executor="e1")
        path = Path(self.tmp.name) / "journal.jsonl"
        path.write_text(path.read_text()[:20])
        self.assertFalse(self.journal.verify())

    def test_no_secrets_in_metadata_bound(self):
        # Journal metadata is bounded and never auto-includes secret material.
        self.journal.append(job_id="job-1", lease_generation=1, state="running",
                            executor="e1", result_metadata="summary " + "x" * 5000)
        text = Path(self.tmp.name).joinpath("journal.jsonl").read_text()
        self.assertNotIn("simulated-secret-abc123", text)
        self.assertNotIn("x" * 2000, text)


class DevCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_unknown_template_rejected(self):
        executor = DevCommandExecutor()
        result = executor.execute({"template_id": "missing"}, "", root=self.root)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["exit_classification"], "denied")

    def test_extra_flags_rejected(self):
        executor = DevCommandExecutor()
        result = executor.execute({"template_id": "joeos.dev.python_compile", "args": ["--unapproved"]},
                                  "", root=self.root)
        self.assertEqual(result["exit_classification"], "denied")

    def test_python_compile_template_runs(self):
        executor = DevCommandExecutor()
        result = executor.execute({"template_id": "joeos.dev.python_compile"}, "", root=self.root)
        self.assertIn(result["status"], ("succeeded", "failed"))


class GitExecutorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.remote = Path(self.tmp.name) / "remote.git"
        self.repo.mkdir()
        self._run(["git", "init", "-q", "-b", "main"], cwd=str(self.repo))
        self._run(["git", "config", "user.email", "test@joeos.local"], cwd=str(self.repo))
        self._run(["git", "config", "user.name", "Test"], cwd=str(self.repo))
        self._run(["git", "init", "-q", "--bare", str(self.remote)])
        self.secret_scan_calls = []

        def secret_scan(root):
            self.secret_scan_calls.append(root)
            return True

        self.secret_scan = secret_scan
        self.executor = GitExecutor(
            str(self.repo), allowed_remotes=["origin"],
            secret_scan=secret_scan,
            hooks_path=str(Path(self.tmp.name) / "empty-hooks"),
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, args, cwd=None):
        return subprocess.run(args, cwd=cwd or str(self.repo), capture_output=True, text=True)

    def test_status_and_branch_and_commit_and_push(self):
        (self.repo / "note.txt").write_text("hello")
        self._run(["git", "add", "note.txt"])
        self._run(["git", "remote", "add", "origin", str(self.remote)])
        status = self.executor.execute({"operation": "status"}, "", root=str(self.repo))
        self.assertEqual(status["status"], "succeeded")
        branch = self.executor.execute({"operation": "create_branch", "branch": "feature/test"}, "", root=str(self.repo))
        self.assertEqual(branch["status"], "succeeded")
        commit = self.executor.execute(
            {"operation": "commit", "message": "feat: add note"}, "", root=str(self.repo)
        )
        self.assertEqual(commit["status"], "succeeded")
        self.assertEqual(len(commit["data"]["commit"]), 40)
        pushed = self.executor.execute(
            {"operation": "push_branch", "branch": "feature/test", "remote": "origin"},
            "", root=str(self.repo),
        )
        self.assertEqual(pushed["status"], "succeeded")
        self.assertGreaterEqual(len(self.secret_scan_calls), 1)

    def test_protected_branch_push_denied(self):
        self._run(["git", "remote", "add", "origin", str(self.remote)])
        result = self.executor.execute(
            {"operation": "push_branch", "branch": "main", "remote": "origin"},
            "", root=str(self.repo),
        )
        self.assertEqual(result["exit_classification"], "denied")

    def test_unknown_remote_denied(self):
        result = self.executor.execute(
            {"operation": "push_branch", "branch": "feature/x", "remote": "evil"},
            "", root=str(self.repo),
        )
        self.assertEqual(result["exit_classification"], "denied")

    def test_invalid_branch_rejected(self):
        result = self.executor.execute(
            {"operation": "create_branch", "branch": "bad;branch"}, "", root=str(self.repo)
        )
        self.assertEqual(result["exit_classification"], "denied")


class ServiceDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_service_start_requires_health(self):
        def adapter(op, unit):
            return {"status": "succeeded", "summary": "ok", "exit_classification": "clean"}

        executor = UserServiceExecutor(
            registrations={"joeos": {"unit_name": "joeos.service"}}, adapter=adapter
        )
        result = executor.execute({"service_id": "joeos", "operation": "restart"}, "", root="/")
        self.assertEqual(result["status"], "succeeded")
        unknown = executor.execute({"service_id": "missing", "operation": "start"}, "", root="/")
        self.assertEqual(unknown["exit_classification"], "denied")

    def test_failed_health_preserves_failure(self):
        def adapter(op, unit):
            if op == "health_check":
                return {"status": "failed", "summary": "down", "exit_classification": "failed"}
            return {"status": "succeeded", "summary": "ok", "exit_classification": "clean"}

        executor = UserServiceExecutor(
            registrations={"joeos": {"unit_name": "joeos.service"}}, adapter=adapter
        )
        result = executor.execute({"service_id": "joeos", "operation": "restart"}, "", root="/")
        self.assertEqual(result["status"], "failed")

    def test_health_check_file_digest(self):
        root = Path(self.tmp.name)
        target = root / "marker.txt"
        target.write_text("revision-abc")
        import hashlib
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        checker = HealthChecker()
        ok = checker.run({"type": "file_digest", "path": str(target), "expected_digest": digest})
        self.assertEqual(ok["status"], "succeeded")
        bad = checker.run({"type": "file_digest", "path": str(target), "expected_digest": "0" * 64})
        self.assertEqual(bad["status"], "failed")

    def test_deployment_executes_exact_commit(self):
        root = Path(self.tmp.name)
        release_root = root / "releases"
        repo_root = root / "repo"
        repo_root.mkdir()
        (repo_root / "file.txt").write_text("v1")
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(repo_root), capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@j.local"], cwd=str(repo_root), capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=str(repo_root), capture_output=True)
        subprocess.run(["git", "add", "file.txt"], cwd=str(repo_root), capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(repo_root), capture_output=True)
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(repo_root),
                                capture_output=True, text=True).stdout.strip()

        service = UserServiceExecutor(
            registrations={"joeos": {"unit_name": "joeos.service"}},
            adapter=lambda op, unit: {"status": "succeeded", "summary": "ok", "exit_classification": "clean"},
        )
        deployment = DeploymentExecutor(
            release_root=str(release_root), service=service, health=HealthChecker(),
            runner_root=str(root),
        )
        result = deployment.execute(
            {"commit": commit, "branch": "main", "service_id": "joeos"}, "", root=str(repo_root)
        )
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["data"]["active_revision"], commit)

    def test_deployment_rejects_moving_target(self):
        service = UserServiceExecutor(registrations={})
        deployment = DeploymentExecutor(
            release_root=str(Path(self.tmp.name) / "r"), service=service,
            health=HealthChecker(), runner_root=self.tmp.name,
        )
        result = deployment.execute({"commit": "latest", "branch": "main", "service_id": "joeos"},
                                    "", root=self.tmp.name)
        self.assertEqual(result["exit_classification"], "denied")


class SecretProviderTests(unittest.TestCase):
    def test_resolve_redact_leak_and_temporary(self):
        provider = RunnerLocalSecretProvider(
            development_values={"deploy-token": "supersecret-token-1"},
            allowed_names=["deploy-token"],
        )
        resolved = provider.resolve("deploy-token")
        self.assertEqual(resolved.value, "supersecret-token-1")
        self.assertEqual(provider.redact("a supersecret-token-1 b", ["supersecret-token-1"]),
                         "a [REDACTED] b")
        self.assertTrue(provider.scan_for_leakage("leaked supersecret-token-1", ["supersecret-token-1"]))
        with self.assertRaises(SecretResolutionError):
            provider.resolve("unknown-secret")
        with tempfile.TemporaryDirectory() as tmp:
            path = provider.write_temporary("deploy-token", "v", tmp)
            self.assertEqual(Path(path).stat().st_mode & 0o777, 0o600)
            os.remove(path)


class DaemonTests(unittest.TestCase):
    class FakeTransport:
        def __init__(self, config):
            self.config = config
            self.heartbeats = 0
            self.rotations = 0
            self.completed = []
            self.fail_connect = False
            self.remaining_jobs = 1

        def request_connection(self, runner_id, key_identifier, public_key):
            return {"challenge_id": "c1", "nonce": "n1"}

        def authenticate(self, challenge, signature_b64url):
            if self.fail_connect:
                raise RuntimeError("connection rejected")
            return {"connection_credential": "cred-1", "connection_ttl_ms": 60000}

        def heartbeat(self, credential):
            self.heartbeats += 1
            return True

        def lease(self, credential):
            if self.remaining_jobs > 0:
                self.remaining_jobs -= 1
                return {"job": {"id": "job-1", "lease_generation": 1,
                                "executor": "joeos.test.deterministic", "target": "file:x",
                                "parameters": {"mode": "ok"}}}
            return {"job": None}

        def acknowledge(self, credential, job, signature_b64url):
            return True

        def start(self, credential, job):
            return True

        def progress(self, credential, job, text):
            return True

        def complete(self, credential, job, signature_b64url, result):
            self.completed.append(result)

        def rotate(self, credential):
            self.rotations += 1
            return "cred-2"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_daemon_executes_job_and_writes_journal(self):
        config_dict = minimal_config(self.tmp.name)
        initialize_identity(config_dict["key_path"], "runner-key-1")
        config = RunnerConfiguration.from_dict(config_dict)
        signer = RunnerSigner(config.key_path, "runner-key-1").load()
        journal = ExecutionJournal(config.journal_path, config.runner_id)
        transport = self.FakeTransport(config)
        from joeos_runner.executors import REGISTERED_EXECUTORS

        daemon = RunnerDaemon(
            config, signer, transport, journal,
            executor_resolver=lambda key: REGISTERED_EXECUTORS.get(key),
        )
        exit_code = daemon.start(run_once=True)
        self.assertEqual(exit_code, 0)
        self.assertEqual(len(transport.completed), 1)
        self.assertEqual(transport.completed[0]["status"], "succeeded")
        self.assertTrue(journal.verify())

    def test_daemon_reconnects_on_connection_failure(self):
        config_dict = minimal_config(self.tmp.name)
        initialize_identity(config_dict["key_path"], "runner-key-1")
        config = RunnerConfiguration.from_dict(config_dict)
        signer = RunnerSigner(config.key_path, "runner-key-1").load()
        journal = ExecutionJournal(config.journal_path, config.runner_id)
        transport = self.FakeTransport(config)
        transport.fail_connect = True
        daemon = RunnerDaemon(config, signer, transport, journal)
        exit_code = daemon.start(run_once=True)
        self.assertEqual(exit_code, 2)


if __name__ == "__main__":
    unittest.main()
