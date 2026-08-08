"""Phase P3G runner tests: worktree isolation, ff-integration, Apple build
executor, and the constrained OpenCode adapter. Uses real local git repos for
worktree flows and deterministic adapters for ssh/xcodebuild/opencode so no
external network, no sudo, and no Mac are required."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from joeos_runner.operations import AppleBuildExecutor, GitExecutor, GitSafetyError
from joeos_runner.opencode_executor import OpenCodeCodingExecutor
from joeos_runner.process import ProcessResult


def init_repo(root: Path, branch: str = "ai-rebuild") -> None:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", branch, str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True)
    (root / "a.txt").write_text("one\n")
    subprocess.run(["git", "-C", str(root), "add", "a.txt"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "first"], check=True)


class GitWorktreeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.wt = Path(self.tmp.name) / "wt"
        init_repo(self.root)
        self.executor = GitExecutor(
            root=str(self.root),
            allowed_remotes=("origin",),
            protected_branches=("main", "master", "production", "release"),
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_worktree_add(self):
        result = self.executor.execute(
            {"operation": "worktree_add", "branch": "feature/x", "path": str(self.wt)},
            "", root=str(self.root))
        self.assertEqual(result["status"], "succeeded", result)
        self.assertTrue(self.wt.is_dir())

    def test_worktree_add_rejects_protected_branch(self):
        result = self.executor.execute(
            {"operation": "worktree_add", "branch": "main", "path": str(self.wt)},
            "", root=str(self.root))
        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(result["exit_classification"], "denied")

    def test_worktree_add_rejects_unsafe_path(self):
        result = self.executor.execute(
            {"operation": "worktree_add", "branch": "feature/x", "path": "/etc/evil"},
            "", root=str(self.root))
        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(result["exit_classification"], "denied")

    def test_worktree_add_rejects_relative_path(self):
        result = self.executor.execute(
            {"operation": "worktree_add", "branch": "feature/x", "path": "relative"},
            "", root=str(self.root))
        self.assertEqual(result["status"], "failed", result)

    def test_ff_integrate_clean_commit(self):
        self.executor.execute(
            {"operation": "worktree_add", "branch": "feature/x", "path": str(self.wt)},
            "", root=str(self.root))
        (self.wt / "b.txt").write_text("two\n")
        subprocess.run(["git", "-C", str(self.wt), "add", "b.txt"], check=True)
        subprocess.run(["git", "-C", str(self.wt), "commit", "-qm", "change"], check=True)
        result = self.executor.execute({"operation": "ff_integrate", "branch": "feature/x"},
                                       "", root=str(self.root))
        self.assertEqual(result["status"], "succeeded", result)
        self.assertIn("commit", result["data"])

    def test_ff_integrate_rejects_protected(self):
        result = self.executor.execute({"operation": "ff_integrate", "branch": "main"},
                                       "", root=str(self.root))
        self.assertEqual(result["exit_classification"], "denied")

    def test_worktree_list(self):
        self.executor.execute(
            {"operation": "worktree_add", "branch": "feature/x", "path": str(self.wt)},
            "", root=str(self.root))
        result = self.executor.execute({"operation": "worktree_list"}, "", root=str(self.root))
        self.assertEqual(result["status"], "succeeded", result)
        self.assertIn(str(self.wt), result["output"])

    def test_worktree_remove(self):
        self.executor.execute(
            {"operation": "worktree_add", "branch": "feature/x", "path": str(self.wt)},
            "", root=str(self.root))
        result = self.executor.execute(
            {"operation": "worktree_remove", "branch": "feature/x", "path": str(self.wt)},
            "", root=str(self.root))
        self.assertEqual(result["status"], "succeeded", result)
        self.assertFalse(self.wt.exists())

    def test_secret_scan_blocks_commit(self):
        scan_calls = []
        executor = GitExecutor(
            root=str(self.root),
            secret_scan=lambda root: (scan_calls.append(root), False)[1],
        )
        result = executor.execute({"operation": "commit", "message": "bad"},
                                  "", root=str(self.root))
        self.assertEqual(result["exit_classification"], "denied")
        self.assertEqual(len(scan_calls), 1)

    def test_stage_all_stages_changes(self):
        self.executor.execute(
            {"operation": "worktree_add", "branch": "feature/x", "path": str(self.wt)},
            "", root=str(self.root))
        target = self.wt / "new.txt"
        target.write_text("hello\n")
        worktree_executor = GitExecutor(root=str(self.wt), allowed_remotes=("origin",))
        staged = worktree_executor.execute({"operation": "stage_all"}, "", root=str(self.wt))
        self.assertEqual(staged["exit_classification"], "clean", staged)
        committed = worktree_executor.execute(
            {"operation": "commit", "message": "add new file"}, "", root=str(self.wt))
        self.assertEqual(committed["exit_classification"], "clean", committed)
        status = self.executor.execute({"operation": "status"}, "", root=str(self.root))
        self.assertEqual(status["status"], "succeeded", status)

    def test_unsafe_parameter_rejected(self):
        result = self.executor.execute(
            {"operation": "status", "x": "; rm -rf /"}, "", root=str(self.root))
        self.assertEqual(result["exit_classification"], "denied")


class AppleBuildExecutorTests(unittest.TestCase):
    def _make(self, adapter):
        return AppleBuildExecutor(
            host="100.68.105.127", user="user", identity_file="/home/x/.ssh/key",
            mirror_dir="/Users/user/Developer/JOEOS", source_root="/home/x/JOEOS",
            project_path="apps/mobile/Xcode/JoeOSClient.xcodeproj",
            adapter=adapter,
        )

    def test_allowlisted_operation_dispatches(self):
        calls = []
        executor = self._make(lambda args, env: (calls.append(args), {"status": "succeeded", "summary": "mock", "exit_classification": "clean"})[1])
        result = executor.execute({"operation": "sync_source"}, "", root="/")
        self.assertEqual(result["status"], "succeeded", result)
        self.assertEqual(calls[0][0], "sync_source")

    def test_unknown_operation_denied(self):
        executor = self._make(lambda args, env: {"status": "succeeded", "summary": "mock", "exit_classification": "clean"})
        result = executor.execute({"operation": "rm_rf"}, "", root="/")
        self.assertEqual(result["exit_classification"], "denied")

    def test_unsafe_parameter_rejected(self):
        executor = self._make(lambda args, env: {"status": "succeeded", "summary": "mock", "exit_classification": "clean"})
        result = executor.execute({"operation": "sync_source", "path": "$(rm -rf /)"}, "", root="/")
        self.assertEqual(result["exit_classification"], "denied")

    def test_verify_health(self):
        executor = self._make(lambda args, env: {"status": "succeeded", "summary": "mock", "exit_classification": "clean"})
        result = executor.execute({"operation": "verify_health"}, "", root="/")
        self.assertEqual(result["status"], "succeeded", result)


class OpenCodeExecutorTests(unittest.TestCase):
    def test_missing_binary_denied(self):
        executor = OpenCodeCodingExecutor(binary="/nonexistent/opencode", worktree_root="/tmp")
        result = executor.execute(
            {"model": "openrouter/deepseek-v4-flash", "prompt": "hi", "dir": "/tmp"},
            "", root="/")
        self.assertEqual(result["exit_classification"], "denied")
        self.assertEqual(result["summary"], "opencode binary unavailable")

    def test_disallowed_model_denied(self):
        executor = OpenCodeCodingExecutor(binary="/nonexistent/opencode", worktree_root="/tmp")
        result = executor.execute(
            {"model": "evil/model", "prompt": "hi", "dir": "/tmp"}, "", root="/")
        self.assertEqual(result["exit_classification"], "denied")
        self.assertEqual(result["summary"], "disallowed opencode model")

    def test_empty_prompt_denied(self):
        executor = OpenCodeCodingExecutor(binary="/nonexistent/opencode", worktree_root="/tmp")
        result = executor.execute(
            {"model": "openrouter/deepseek-v4-flash", "prompt": "", "dir": "/tmp"}, "", root="/")
        self.assertEqual(result["exit_classification"], "denied")

    def test_directory_outside_worktree_denied(self):
        executor = OpenCodeCodingExecutor(binary="/nonexistent/opencode", worktree_root="/tmp/approved")
        result = executor.execute(
            {"model": "openrouter/deepseek-v4-flash", "prompt": "hi", "dir": "/etc"}, "", root="/")
        self.assertEqual(result["exit_classification"], "denied")
        self.assertEqual(result["summary"], "opencode directory outside approved worktree")

    def test_oversized_prompt_denied(self):
        executor = OpenCodeCodingExecutor(binary="/nonexistent/opencode", worktree_root="/tmp")
        result = executor.execute(
            {"model": "openrouter/deepseek-v4-flash", "prompt": "x" * 9000, "dir": "/tmp"},
            "", root="/")
        self.assertEqual(result["exit_classification"], "denied")

    def test_adapter_success_path(self):
        calls = []
        executor = OpenCodeCodingExecutor(
            binary="/home/joewillisny/.opencode/bin/opencode",
            worktree_root="/tmp",
            adapter=lambda args, env: (calls.append(args), ProcessResult(
                exit_code=0, stdout=json.dumps({"result": "done"}), stderr="", duration_ms=1))[1],
        )
        result = executor.execute(
            {"model": "openrouter/deepseek-v4-flash", "prompt": "implement x", "dir": "/tmp"},
            "", root="/")
        self.assertEqual(result["status"], "succeeded", result)
        self.assertIn("opencode", calls[0][0])

    def test_adapter_failure_path(self):
        executor = OpenCodeCodingExecutor(
            binary="/home/joewillisny/.opencode/bin/opencode",
            worktree_root="/tmp",
            adapter=lambda args, env: ProcessResult(
                exit_code=1, stdout="boom", stderr="", duration_ms=1),
        )
        result = executor.execute(
            {"model": "openrouter/deepseek-v4-flash", "prompt": "implement x", "dir": "/tmp"},
            "", root="/")
        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(result["exit_classification"], "failed")


if __name__ == "__main__":
    unittest.main()
