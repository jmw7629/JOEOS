"""Synthetic repository, native sandbox, and broker-boundary acceptance tests.

No account, model, real repository writes, Internet request or worker is used.
Native command tests skip only when the OS isolation probe fails; a separate
test verifies that such a failure rejects execution without a fallback.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import socket
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import codex_sandbox as sandbox


class RepositoryFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="prfkt-sandbox-tests-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir(mode=0o700)
        self.env = sandbox._host_environment(self.root)
        self.git("init", "-q")
        (self.repo / "hello.txt").write_text("first\nsecond\n")
        (self.repo / "script.sh").write_text("#!/bin/sh\nprintf fixture\n")
        (self.repo / "script.sh").chmod(0o755)
        self.git("add", ".")
        self.git("-c", "user.name=Sandbox fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "-qm", "Synthetic sandbox baseline")
        self.factory = sandbox.SandboxWorkspace(self.root / "runs", self.repo)
        self.run = self.factory.create("fixture-run")

    def git(self, *args):
        result = subprocess.run(["/usr/bin/git", *args], cwd=self.repo, env=self.env,
                                capture_output=True, timeout=10)
        if result.returncode:
            raise AssertionError("Synthetic Git fixture failed")
        return result.stdout


class WorkspaceTests(RepositoryFixture):
    def test_export_is_committed_content_without_git_or_source_mutations(self):
        (self.repo / "hello.txt").write_text("uncommitted private source edit\n")
        (self.repo / "private-untracked").write_text("synthetic host-only value")
        another = self.factory.create("fresh-run")
        self.assertEqual(another.read("hello.txt"), b"first\nsecond\n")
        self.assertFalse((another.path / ".git").exists())
        self.assertNotIn("private-untracked", another.snapshot()["files"])
        self.assertTrue(another.snapshot()["files"]["script.sh"]["executable"])
        another.write("hello.txt", "isolated\n")
        self.assertEqual((self.repo / "hello.txt").read_text(), "uncommitted private source edit\n")
        self.assertEqual(self.run.read("hello.txt"), b"first\nsecond\n")

    def test_paths_reject_absolute_traversal_git_and_internal_storage(self):
        for value in ("../hello.txt", "/etc/passwd", "a/../b", "a//b", "./hello.txt", "a\\b", ".git/config",
                      "nested/.git/objects", "hello\x00.txt", ".sandbox-home/auth", ".sandbox-tmp/item", ""):
            with self.subTest(path=value):
                with self.assertRaises(sandbox.SandboxError):
                    self.run.read(value)
                with self.assertRaises(sandbox.SandboxError):
                    self.run.write(value, "no")
        with self.assertRaises(sandbox.SandboxError):
            self.factory.create("../outside")
        with self.assertRaises(sandbox.SandboxError):
            self.factory.create("fixture-run")

    def test_symlink_and_hardlink_escape_are_blocked(self):
        outside = self.root / "outside"
        outside.write_text("synthetic-private")
        (self.run.path / "link").symlink_to(outside)
        (self.run.path / "folder").symlink_to(self.root, target_is_directory=True)
        os.link(outside, self.run.path / "hardlink")
        for path in ("link", "folder/outside", "hardlink"):
            with self.subTest(path=path):
                with self.assertRaises(sandbox.SandboxError):
                    self.run.read(path)
                with self.assertRaises(sandbox.SandboxError):
                    self.run.write(path, "bad")
        self.assertEqual(outside.read_text(), "synthetic-private")
        self.assertEqual(next(x for x in self.run.list() if x["name"] == "link")["type"], "blocked")
        with self.assertRaises(sandbox.SandboxError):
            self.run.snapshot()
        with self.assertRaises(sandbox.SandboxError):
            self.run.diff()

    def test_write_does_not_follow_a_parent_replaced_during_open(self):
        target = self.root / "outside-dir"
        target.mkdir()
        (self.run.path / "parent").mkdir()
        original = sandbox.os.open
        replaced = False
        def race(path, flags, *args, **kwargs):
            nonlocal replaced
            if path == "parent" and kwargs.get("dir_fd") is not None and not replaced:
                replaced = True
                (self.run.path / "parent").rmdir()
                (self.run.path / "parent").symlink_to(target, target_is_directory=True)
            return original(path, flags, *args, **kwargs)
        with patch.object(sandbox.os, "open", side_effect=race):
            with self.assertRaises(sandbox.SandboxError):
                self.run.write("parent/escaped", "bad")
        self.assertFalse((target / "escaped").exists())

    def test_snapshot_and_diff_report_text_binary_deletion_and_mode(self):
        self.run.write("hello.txt", "first\nchanged\n")
        self.run.write("nested/new.txt", "new\n")
        self.run.write("binary.bin", b"\x00\xff")
        (self.run.path / "script.sh").unlink()
        self.run.write("mode.sh", "echo okay\n", executable=True)
        result = self.run.diff()
        self.assertEqual(result["base_commit"], self.git("rev-parse", "HEAD").decode().strip())
        changes = {x["path"]: x for x in result["changes"]}
        self.assertEqual(changes["script.sh"]["status"], "deleted")
        self.assertTrue(changes["binary.bin"]["binary"])
        self.assertTrue(changes["mode.sh"]["after_executable"])
        self.assertIn("-second\n+changed\n", result["patch"])
        self.assertNotIn(str(self.repo), json.dumps(result))
        self.assertEqual(self.run.snapshot()["files"]["hello.txt"]["sha256"],
                         hashlib.sha256(b"first\nchanged\n").hexdigest())

    def test_oversized_inline_diff_keeps_exact_inventory_without_unbounded_comparison(self):
        self.run.write("large.txt", "x" * (sandbox.MAX_INLINE_DIFF_FILE_BYTES + 1))
        self.run.write("many-lines.txt", "x\n" * (sandbox.MAX_INLINE_DIFF_LINES + 1))
        self.run.write("unicode-lines.txt", "x\u2028" * (sandbox.MAX_INLINE_DIFF_LINES + 1))
        original = sandbox.difflib.unified_diff
        compared = []
        def bounded(left, right, **kwargs):
            compared.append(len(left) + len(right))
            self.assertLessEqual(len(left) + len(right), sandbox.MAX_INLINE_DIFF_LINES)
            return original(left, right, **kwargs)
        with patch.object(sandbox.difflib, "unified_diff", side_effect=bounded):
            review = self.run.diff()
        self.assertEqual(compared, [], 'Oversized additions reached Python sequence matching')
        changes = {item['path']: item for item in review['changes']}
        for name in ("large.txt", "many-lines.txt", "unicode-lines.txt"):
            self.assertIn('inline_diff_omitted', changes[name])
            self.assertFalse(changes[name]['binary'])
            self.assertEqual(changes[name]['after_sha256'], hashlib.sha256(self.run.read(name)).hexdigest())
        self.assertIn('complete frozen Git patch is required', review['patch'])

    def test_inline_line_budget_is_cumulative_and_pathological_comparisons_are_omitted(self):
        self.run.write("a.txt", "a\n" * 11000)
        self.run.write("b.txt", "b\n" * 11000)
        review = self.run.diff()
        changes = {item['path']: item for item in review['changes']}
        self.assertNotIn('inline_diff_omitted', changes['a.txt'])
        self.assertIn('inline_diff_omitted', changes['b.txt'])
        # Keep both files under byte/line limits while forcing a worst-case
        # sequence-matching shape. Raw hashes remain publishable evidence.
        path = self.run.baseline / "hello.txt"
        before = ''.join(str(i) + '\n' for i in range(2100))
        path.write_text(before)
        self.run.metadata['files']['hello.txt']['sha256'] = hashlib.sha256(before.encode()).hexdigest()
        self.run.write("hello.txt", ''.join(str(i) + '\n' for i in reversed(range(2100))))
        review = self.run.diff()
        entry = next(item for item in review['changes'] if item['path'] == 'hello.txt')
        self.assertIn('comparison work limit', entry['inline_diff_omitted'])

    def test_baseline_change_fails_closed_and_cleanup_is_scoped(self):
        (self.run.baseline / "hello.txt").write_text("tampered")
        with self.assertRaisesRegex(sandbox.SandboxError, "baseline changed"):
            self.run.diff()
        self.run.cleanup()
        self.run.cleanup()
        self.assertFalse(self.run.folder.exists())
        self.assertTrue((self.repo / "hello.txt").exists())
        with self.assertRaises(sandbox.SandboxError):
            self.run.list()

    def test_reopen_preserves_changes_without_rerunning_and_checks_baseline(self):
        self.run.write("hello.txt", "continued work\n")
        with patch.object(sandbox, "_process") as process:
            reopened = self.factory.open("fixture-run")
        process.assert_not_called()
        self.assertEqual(reopened.base_commit, self.run.base_commit)
        self.assertEqual(reopened.read("hello.txt"), b"continued work\n")
        self.assertIs(reopened.lock, self.run.lock)
        self.assertEqual(reopened.diff(), self.run.diff())
        (self.run.baseline / "hello.txt").write_text("changed outside runner")
        with self.assertRaisesRegex(sandbox.SandboxError, "baseline changed"):
            self.factory.open("fixture-run")

    def test_reopen_rejects_public_metadata_and_symlink_workspace(self):
        registration = self.run.folder / "metadata.json"
        registration.chmod(0o644)
        with self.assertRaises(sandbox.SandboxError):
            self.factory.open("fixture-run")
        registration.chmod(0o600)
        self.run.path.rename(self.run.folder / "old-workspace")
        self.run.path.symlink_to(self.repo, target_is_directory=True)
        with self.assertRaises(sandbox.SandboxError):
            self.factory.open("fixture-run")

    def test_repository_symlinks_rejected_and_partial_export_removed(self):
        (self.repo / "escape").symlink_to("/etc/passwd")
        self.git("add", "escape")
        self.git("-c", "user.name=Sandbox fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "-qm", "Synthetic unsafe export")
        with self.assertRaises(sandbox.SandboxError):
            self.factory.create("unsafe-run")
        self.assertFalse((self.factory.run_root / "unsafe-run").exists())

    def test_export_never_runs_smudge_filters_or_applies_archive_attributes(self):
        import shlex
        canary = self.root / "HOST_FILTER_RAN"
        (self.repo / ".gitattributes").write_text("*.txt filter=hostfilter export-ignore\n")
        self.git("add", ".gitattributes")
        self.git("-c", "user.name=Sandbox fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "-qm", "Synthetic archive/filter trap")
        self.git("config", "filter.hostfilter.smudge", "touch " + shlex.quote(str(canary)) + "; cat")
        self.git("config", "filter.hostfilter.required", "true")
        exported = self.factory.create("raw-object-run")
        self.assertEqual(exported.read("hello.txt"), b"first\nsecond\n")
        self.assertFalse(canary.exists(), "Export must never invoke a host smudge filter")
        self.assertEqual(exported.diff()["changes"], [])

    def test_export_ignores_git_object_replacement_refs(self):
        original = self.git("rev-parse", "HEAD:hello.txt").decode().strip()
        replacement = subprocess.run(["/usr/bin/git", "hash-object", "-w", "--stdin"], input=b"replacement\n",
                                     cwd=self.repo, env=self.env, capture_output=True, timeout=5)
        self.assertEqual(replacement.returncode, 0)
        self.git("replace", original, replacement.stdout.decode().strip())
        exported = self.factory.create("no-replacements")
        self.assertEqual(exported.read("hello.txt"), b"first\nsecond\n")

    def test_unsafe_storage_or_source_overlap_rejected(self):
        public = self.root / "public"
        public.mkdir(mode=0o755)
        with self.assertRaises(sandbox.SandboxError):
            sandbox.SandboxWorkspace(public, self.repo)
        with self.assertRaises(sandbox.SandboxError):
            sandbox.SandboxWorkspace(self.repo / "inside-runs", self.repo)

    def test_unavailable_sandbox_never_falls_back_to_host_shell(self):
        self.factory.binary = Path("/nonexistent/sandbox")
        with patch.object(sandbox, "_process") as process:
            with self.assertRaisesRegex(sandbox.SandboxError, "execution is disabled"):
                self.run.execute("touch unsafe-marker")
        process.assert_not_called()
        self.assertFalse((self.run.path / "unsafe-marker").exists())

    def test_stop_before_dispatch_creates_no_process(self):
        stop = threading.Event()
        stop.set()
        with patch.object(sandbox, "_process") as process:
            result = self.run.execute("printf bad", stop_event=stop)
        process.assert_not_called()
        self.assertTrue(result["stopped"])

    def test_linux_argv_keeps_all_namespaces_private_and_mounts_no_home(self):
        self.factory.platform = "linux"
        # A trusted executable is sufficient to inspect arguments; it is never run.
        self.factory.binary = Path("/bin/sh")
        args, env = self.factory._arguments(self.run.path, "printf okay")
        self.assertIn("--unshare-all", args)
        self.assertNotIn("--share-net", args)
        self.assertIn("--cap-drop", args)
        self.assertIn("--clearenv", args)
        self.assertNotIn(str(self.repo), args)
        self.assertNotIn(str(Path.home()), args)
        mounts = [args[i + 1:i + 3] for i, arg in enumerate(args) if arg in ("--bind", "--ro-bind")]
        self.assertNotIn(["/etc", "/etc"], mounts)
        self.assertNotIn("/usr/local", args)
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("CODEX_HOME", env)

    def test_root_broker_retains_only_bootstrap_caps_then_drops_uid_and_all_caps(self):
        self.factory.platform = "linux"
        self.factory.binary = Path("/bin/sh")
        self.factory.worker_uid = self.factory.worker_gid = 1000
        original_stat, original_access = Path.stat, os.access
        def checked_stat(path, *args, **kwargs):
            if str(path) == "/usr/bin/setpriv":
                return os.stat_result((stat.S_IFREG | 0o755, 0, 0, 1, 0, 0, 0, 0, 0, 0))
            return original_stat(path, *args, **kwargs)
        def checked_access(path, mode, *args, **kwargs):
            return True if str(path) == "/usr/bin/setpriv" else original_access(path, mode, *args, **kwargs)
        with patch.object(Path, "stat", checked_stat), patch.object(sandbox.os, "access", checked_access):
            args, _ = self.factory._arguments(self.run.path, "printf okay", mount_source="/proc/self/cwd")
        for name in ("--unshare-pid", "--unshare-net", "--unshare-ipc", "--unshare-uts", "--unshare-cgroup"):
            self.assertIn(name, args)
        for name in ("--unshare-user", "--unshare-all", "--share-net", "--uid", "--gid"):
            self.assertNotIn(name, args)
        capabilities = [args[i + 1] for i, arg in enumerate(args) if arg == "--cap-add"]
        self.assertEqual(capabilities, ["CAP_SETUID", "CAP_SETGID", "CAP_SETPCAP", "CAP_DAC_OVERRIDE"])
        self.assertLess(args.index("--bind"), args.index("--proc"))
        for parent in ("/usr", "/etc"):
            index = args.index(parent)
            self.assertEqual(args[index - 1:index + 4], ["--dir", parent, "--chmod", "0755", parent])
            self.assertLess(index, args.index("--ro-bind"))
        index = args.index("/usr/bin/setpriv")
        self.assertEqual(args[index:], ["/usr/bin/setpriv", "--reuid=1000", "--regid=1000", "--clear-groups",
                                       "--bounding-set=-all", "--inh-caps=-all", "--ambient-caps=-all",
                                       "--no-new-privs", "--", "/bin/sh", "-c", "printf okay"])


class NativeSandboxTests(RepositoryFixture):
    def setUp(self):
        super().setUp()
        proof = self.factory.capabilities()
        if not proof["available"]:
            self.skipTest(proof["detail"])

    def test_native_marker_stream_and_clean_environment(self):
        events = []
        with patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-must-not-inherit",
                                     "GH_TOKEN": "synthetic-must-not-inherit",
                                     "CODEX_HOME": str(self.repo)}):
            result = self.run.execute("printf ready; printf error >&2; printf written > marker; env",
                                      on_output=lambda stream, text: events.append((stream, text)))
        self.assertEqual(result["exit_code"], 0, result)
        self.assertEqual(self.run.read("marker"), b"written")
        self.assertTrue(events)
        self.assertIn("ready", result["stdout"])
        self.assertIn("error", result["stderr"])
        self.assertNotIn("synthetic-must-not-inherit", result["stdout"])
        self.assertNotIn("CODEX_HOME=", result["stdout"])

    def test_native_cannot_read_source_or_write_host_or_connect_loopback(self):
        secret = self.repo / "host-private"
        secret.write_text("synthetic-host-secret")
        outside = self.root / "escaped"
        import shlex
        result = self.run.execute("cat " + shlex.quote(str(secret)) + "; printf bad > " + shlex.quote(str(outside)))
        self.assertNotEqual(result["exit_code"], 0)
        self.assertNotIn("synthetic-host-secret", result["stdout"])
        self.assertFalse(outside.exists())
        # The factory's native capability test also requires an isolated Linux
        # network namespace or an actual denied connection to a listening Mac port.
        self.assertTrue(self.factory.capabilities(refresh=True)["available"])

    def test_native_timeout_stop_and_bounded_output(self):
        result = self.run.execute("sleep 30", timeout=.15)
        self.assertTrue(result["timed_out"])
        stop = threading.Event()
        timer = threading.Timer(.15, stop.set)
        timer.start()
        try:
            result = self.run.execute("sleep 30", stop_event=stop)
        finally:
            timer.cancel()
        self.assertTrue(result["stopped"])
        with patch.object(sandbox, "MAX_OUTPUT", 4096):
            # _process's default is definition-bound; explicitly verify its
            # shared cap with the real isolated command arguments instead.
            args, env = self.factory._arguments(self.run.path, "while :; do printf 0123456789; done")
            output = sandbox._process(args, cwd=self.run.path, env=env, timeout=3, output_limit=4096)
        self.assertTrue(output["truncated"])
        self.assertLessEqual(len(output["stdout"]) + len(output["stderr"]), 4096)


class BrokerBoundaryTests(unittest.TestCase):
    class FakeConnection:
        def __init__(self, value, uid, *, disconnected=False):
            self.input = json.dumps(value).encode() + b"\n"
            self.uid = uid
            self.sent = []
            self.disconnected = disconnected
            self.closed = False
        def settimeout(self, _):
            pass
        def getsockopt(self, *_):
            return struct.pack("3i", 123, self.uid, self.uid)
        def recv(self, _):
            if self.input:
                value, self.input = self.input, b""
                return value
            if self.disconnected or self.closed:
                return b""
            time.sleep(.01)
            raise socket.timeout()
        def sendall(self, value):
            self.sent.append(json.loads(value))
        def close(self):
            self.closed = True

    def fixture_broker(self, root):
        broker = sandbox.SandboxBroker.__new__(sandbox.SandboxBroker)
        broker.uid, broker.gid = os.getuid(), os.getgid()
        broker.root = root
        broker.root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, broker.root_fd)
        broker.factory = Mock()
        broker.factory.capabilities.return_value = {"available": True, "backend": "fixture"}
        broker.factory._arguments.return_value = (["fixture-command"], {})
        broker.slots = threading.BoundedSemaphore(2)
        broker.active_lock = threading.Lock()
        broker.active_runs = set()
        return broker

    def test_broker_rejects_wrong_peer_before_capability_or_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            broker = self.fixture_broker(Path(tmp))
            client = self.FakeConnection({"op": "health"}, os.getuid() + 1)
            with patch.object(sandbox.socket, "SO_PEERCRED", 17, create=True), patch.object(sandbox, "_process") as process:
                broker._handle(client)
            self.assertIn("error", client.sent[-1])
            self.assertTrue(client.closed)
            broker.factory.capabilities.assert_not_called()
            process.assert_not_called()

    def test_broker_failed_native_probe_never_launches_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            broker = self.fixture_broker(Path(tmp))
            broker.factory.capabilities.return_value = {"available": False}
            client = self.FakeConnection({"op": "execute", "run_id": "run-1", "command": "touch bad", "timeout": 1}, os.getuid())
            with patch.object(sandbox.socket, "SO_PEERCRED", 17, create=True), patch.object(sandbox, "_process") as process:
                broker._handle(client)
            self.assertIn("error", client.sent[-1])
            process.assert_not_called()

    def test_broker_disconnect_sets_stop_and_releases_run_and_capacity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "run-1").mkdir(mode=0o700)
            (root / "run-1" / "workspace").mkdir(mode=0o700)
            broker = self.fixture_broker(root)
            client = self.FakeConnection({"op": "execute", "run_id": "run-1", "command": "sleep 30", "timeout": 2}, os.getuid(), disconnected=True)
            stopped = []
            def execution(args, **kwargs):
                kwargs["stop_event"].wait(.5)
                stopped.append(kwargs["stop_event"].is_set())
                return {"exit_code": -15, "stdout": b"", "stderr": b"", "stopped": True,
                        "timed_out": False, "truncated": False, "duration_ms": 1}
            with patch.object(sandbox.socket, "SO_PEERCRED", 17, create=True), patch.object(sandbox, "_process", side_effect=execution):
                broker._handle(client)
            self.assertEqual(stopped, [True])
            self.assertFalse(broker.active_runs)
            self.assertTrue(broker.slots.acquire(blocking=False))
            self.assertTrue(broker.slots.acquire(blocking=False))
            self.assertTrue(client.closed)

    def test_broker_does_not_launch_concurrent_commands_for_one_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            broker = self.fixture_broker(Path(tmp))
            broker.active_runs.add("run-1")
            client = self.FakeConnection({"op": "execute", "run_id": "run-1", "command": "printf bad", "timeout": 1}, os.getuid())
            with patch.object(sandbox.socket, "SO_PEERCRED", 17, create=True), patch.object(sandbox, "_process") as process:
                broker._handle(client)
            process.assert_not_called()
            self.assertEqual(broker.active_runs, {"run-1"})
            self.assertIn("error", client.sent[-1])

    def test_request_schema_has_no_paths_environment_or_extra_arguments(self):
        good = {"op": "execute", "run_id": "run-123", "command": "printf okay", "timeout": 2}
        self.assertEqual(sandbox._broker_request(good), good)
        for field, value in (("cwd", "/"), ("env", {}), ("uid", 0), ("socket", "/tmp/x"), ("args", [])):
            with self.subTest(field=field):
                with self.assertRaises(sandbox.SandboxError):
                    sandbox._broker_request({**good, field: value})
        for changed in ({"run_id": "../escape"}, {"timeout": True}, {"timeout": float("nan")},
                        {"timeout": float("inf")}, {"timeout": 901}, {"command": "bad\x00"}):
            with self.assertRaises(sandbox.SandboxError):
                sandbox._broker_request({**good, **changed})
        self.assertEqual(sandbox._broker_request({"op": "health"}), {"op": "health"})
        with self.assertRaises(sandbox.SandboxError):
            sandbox._strict_object(b'{"op":"health","op":"execute"}')

    def test_client_does_not_trust_regular_files_or_public_socket(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broker.sock"
            path.write_text("not a socket")
            with self.assertRaises(sandbox.SandboxError):
                sandbox._broker_client(path, {"op": "health"}, timeout=1)
            path.unlink()
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                listener.bind(str(path))
                path.chmod(0o666)
                with self.assertRaises(sandbox.SandboxError):
                    sandbox._broker_client(path, {"op": "health"}, timeout=1)
            finally:
                listener.close()

    def test_privileged_broker_refuses_untrusted_config_and_non_linux(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.json"
            config.write_text(json.dumps({"schema_version": 1, "run_root": tmp, "uid": os.getuid(),
                                          "gid": os.getgid(), "socket": "/run/fixture.sock"}))
            config.chmod(0o600)
            # A user-controlled ancestor is always unsuitable, including /tmp.
            with self.assertRaises(sandbox.SandboxError):
                sandbox._broker_configuration(config)
        with patch.object(sandbox.sys, "platform", "darwin"):
            with self.assertRaises(sandbox.SandboxError):
                sandbox.SandboxBroker("/etc/nonexistent")

    def test_workspace_descriptor_cannot_be_redirected_after_root_path_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "runs"
            root.mkdir(mode=0o700)
            (root / "run-1").mkdir(mode=0o700)
            workspace = root / "run-1" / "workspace"
            workspace.mkdir(mode=0o700)
            broker = sandbox.SandboxBroker.__new__(sandbox.SandboxBroker)
            broker.uid = os.getuid()
            broker.root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                pinned_inode = workspace.stat().st_ino
                root.rename(root.with_name("pinned"))
                root.symlink_to("/", target_is_directory=True)
                fd = broker._workspace_fd("run-1")
                try:
                    self.assertEqual(os.fstat(fd).st_ino, pinned_inode)
                finally:
                    os.close(fd)
            finally:
                os.close(broker.root_fd)

    def test_workspace_descriptor_rejects_symlink_components(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "run-1").symlink_to("/", target_is_directory=True)
            broker = sandbox.SandboxBroker.__new__(sandbox.SandboxBroker)
            broker.uid = os.getuid()
            broker.root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                with self.assertRaises(OSError):
                    broker._workspace_fd("run-1")
            finally:
                os.close(broker.root_fd)


if __name__ == "__main__":
    unittest.main(verbosity=2)
