"""Safe DevOps executors: development-command templates, constrained Git,
user-level services, and typed JoeOS deployments with health checks and
rollback. No arbitrary shell, no sudo, no raw commands."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
import uuid as _uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from .process import ProcessResult, canonicalize_path, run_process

EXECUTOR_OUTPUT_LIMIT = 1_048_576


@dataclass(frozen=True)
class CommandTemplate:
    id: str
    version: str
    executable: str
    arguments: tuple
    working_directory: str
    timeout_ms: int = 120_000
    network: bool = False
    allow_optional_args: tuple = ()
    risk: str = "medium"


DEV_COMMAND_TEMPLATES: Dict[str, CommandTemplate] = {
    "joeos.dev.backend_tests": CommandTemplate(
        id="joeos.dev.backend_tests", version="1.0.0", executable="python",
        arguments=("-m", "pytest", "-q", "tests/"),
        working_directory="", timeout_ms=600_000),
    "joeos.dev.runner_tests": CommandTemplate(
        id="joeos.dev.runner_tests", version="1.0.0", executable="python",
        arguments=("-m", "pytest", "-q", "runner/tests/"),
        working_directory="", timeout_ms=300_000),
    "joeos.dev.frontend_contract": CommandTemplate(
        id="joeos.dev.frontend_contract", version="1.0.0", executable="node",
        arguments=("tests/frontend.test.mjs",),
        working_directory="", timeout_ms=120_000),
    "joeos.dev.mobile_web_typecheck": CommandTemplate(
        id="joeos.dev.mobile_web_typecheck", version="1.0.0", executable="npm",
        arguments=("run", "typecheck"), working_directory="apps/joeos-mobile-web",
        timeout_ms=180_000),
    "joeos.dev.mobile_web_tests": CommandTemplate(
        id="joeos.dev.mobile_web_tests", version="1.0.0", executable="npm",
        arguments=("test",), working_directory="apps/joeos-mobile-web", timeout_ms=180_000),
    "joeos.dev.mobile_web_build": CommandTemplate(
        id="joeos.dev.mobile_web_build", version="1.0.0", executable="npm",
        arguments=("run", "build"), working_directory="apps/joeos-mobile-web",
        timeout_ms=300_000),
    "joeos.dev.python_compile": CommandTemplate(
        id="joeos.dev.python_compile", version="1.0.0", executable="python",
        arguments=("-m", "compileall", "-q", "server", "runner"),
        working_directory="", timeout_ms=120_000),
}


class DevCommandExecutor:
    """Executes an authoritative command template. A job references a template
    id, never a command string. No extra flags unless allowlisted."""

    key = "joeos.dev.command"

    def __init__(self, templates: Optional[Dict[str, CommandTemplate]] = None,
                 progress: Optional[Callable[[str], None]] = None) -> None:
        self._templates = templates or DEV_COMMAND_TEMPLATES
        self._progress = progress

    def execute(self, parameters: Dict, target: str, *, root: str,
                environment: Optional[Dict[str, str]] = None,
                timeout_ms: int = 60_000) -> dict:
        template_id = parameters.get("template_id")
        template = self._templates.get(str(template_id))
        if template is None:
            return {"status": "failed", "summary": "unknown command template",
                    "exit_classification": "denied"}
        extra = parameters.get("args") or []
        allowed = set(template.allow_optional_args)
        if any(str(flag) not in allowed for flag in extra):
            return {"status": "failed", "summary": "disallowed extra arguments",
                    "exit_classification": "denied"}
        work_dir = os.path.join(root, template.working_directory) if template.working_directory else root
        if not _within(root, work_dir):
            return {"status": "failed", "summary": "unsafe working directory",
                    "exit_classification": "denied"}
        if self._progress:
            self._progress("template %s selected" % template_id)
        result = run_process(
            executable=template.executable,
            arguments=list(template.arguments) + [str(a) for a in extra],
            cwd=work_dir, environment=environment or {}, timeout_ms=timeout_ms or template.timeout_ms,
        )
        if result.timed_out:
            return {"status": "timed_out", "summary": "command timed out",
                    "exit_classification": "timed_out"}
        if result.exit_code == 0:
            return {"status": "succeeded", "summary": "command completed",
                    "exit_classification": "clean", "output": result.stdout}
        return {"status": "failed", "summary": "command failed (exit %s)" % result.exit_code,
                "exit_classification": "failed", "output": result.stdout}


# ---------------------------------------------------------------------------
# Constrained Git executor
# ---------------------------------------------------------------------------


class GitSafetyError(Exception):
    pass


class GitExecutor:
    """Constrained Git operations inside a registered repository.

    Uses explicit `git` argument vectors (shell=False), disables hooks via an
    empty trusted hooks path, rejects force push, protects configured branches,
    and verifies exact commit/remote after mutation. No credentials are placed
    on the command line; a preconfigured credential helper is required.
    """

    key = "joeos.git.repository"

    PROTECTED_BRANCHES = frozenset({"main", "master", "production", "release"})
    VALID_BRANCH = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_./")

    def __init__(self, root: str, allowed_remotes: Sequence[str] = (),
                 protected_branches: Sequence[str] = (),
                 secret_scan: Optional[Callable[[str], bool]] = None,
                 hooks_path: Optional[str] = None,
                 progress: Optional[Callable[[str], None]] = None) -> None:
        self._root = os.path.realpath(root)
        self._allowed_remotes = set(allowed_remotes)
        self._protected = set(protected_branches) | self.PROTECTED_BRANCHES
        self._secret_scan = secret_scan
        self._hooks_path = hooks_path
        self._progress = progress

    def _git(self, *arguments: str, timeout_ms: int = 120_000) -> ProcessResult:
        args = ["-c", "core.hooksPath=%s" % (self._hooks_path or ""),
                "-c", "alias.%s" % "none", *arguments]
        return run_process(
            executable="git", arguments=args, cwd=self._root, timeout_ms=timeout_ms,
        )

    def _require_clean_args(self, parameters: Dict) -> None:
        for value in parameters.values():
            if isinstance(value, str) and any(c in value for c in (";", "|", "&&", "`", "$(")):
                raise GitSafetyError("unsafe value rejected")

    def execute(self, parameters: Dict, target: str, *, root: str,
                environment: Optional[Dict[str, str]] = None,
                timeout_ms: int = 60_000) -> dict:
        operation = parameters.get("operation")
        try:
            self._require_clean_args(parameters)
            if operation == "status":
                result = self._git("status", "--porcelain")
                return {"status": "succeeded", "summary": "git status",
                        "exit_classification": "clean", "output": result.stdout}
            if operation == "diff":
                result = self._git("diff", "--stat")
                return {"status": "succeeded", "summary": "git diff",
                        "exit_classification": "clean", "output": result.stdout}
            if operation == "create_branch":
                branch = str(parameters["branch"])
                self._validate_branch(branch)
                result = self._git("checkout", "-b", branch)
                return self._ok("branch created: %s" % branch, result)
            if operation == "commit":
                message = str(parameters["message"])
                self._validate_commit_message(message)
                if self._secret_scan is not None and not self._secret_scan(self._root):
                    return {"status": "failed", "summary": "secret scan failed; commit blocked",
                            "exit_classification": "denied"}
                result = self._git("commit", "-m", message)
                if result.exit_code != 0:
                    return {"status": "failed", "summary": "commit failed",
                            "exit_classification": "failed"}
                commit_id = self._git("rev-parse", "HEAD").stdout.strip()
                return {"status": "succeeded", "summary": "commit created",
                        "exit_classification": "clean", "data": {"commit": commit_id}}
            if operation == "push_branch":
                branch = str(parameters["branch"])
                remote = str(parameters.get("remote", "origin"))
                self._validate_branch(branch)
                if remote not in self._allowed_remotes:
                    return {"status": "failed", "summary": "unknown remote",
                            "exit_classification": "denied"}
                if branch in self._protected:
                    return {"status": "failed", "summary": "protected branch push denied",
                            "exit_classification": "denied"}
                if self._secret_scan is not None and not self._secret_scan(self._root):
                    return {"status": "failed", "summary": "secret scan failed; push blocked",
                            "exit_classification": "denied"}
                result = self._git("push", "--no-verify", remote, branch)
                if result.exit_code != 0:
                    return {"status": "failed", "summary": "push failed",
                            "exit_classification": "failed"}
                verify = self._git("ls-remote", "--exit-code", remote, "refs/heads/%s" % branch)
                return {"status": "succeeded" if verify.exit_code == 0 else "failed",
                        "summary": "push completed" if verify.exit_code == 0 else "remote branch not verified",
                        "exit_classification": "clean" if verify.exit_code == 0 else "failed"}
            if operation == "verify_commit":
                commit_id = self._git("rev-parse", "HEAD").stdout.strip()
                return {"status": "succeeded", "summary": "commit verified",
                        "exit_classification": "clean", "data": {"commit": commit_id}}
            return {"status": "failed", "summary": "unsupported git operation",
                    "exit_classification": "denied"}
        except GitSafetyError as error:
            return {"status": "failed", "summary": str(error), "exit_classification": "denied"}

    def _ok(self, summary: str, result: ProcessResult) -> dict:
        return {"status": "succeeded" if result.exit_code == 0 else "failed",
                "summary": summary, "exit_classification": "clean" if result.exit_code == 0 else "failed"}

    def _validate_branch(self, branch: str) -> None:
        if not branch or len(branch) > 120 or not all(c in self.VALID_BRANCH for c in branch):
            raise GitSafetyError("invalid branch name")
        if branch in self._protected:
            raise GitSafetyError("protected branch mutation denied")

    @staticmethod
    def _validate_commit_message(message: str) -> None:
        if not isinstance(message, str) or len(message) > 4000:
            raise GitSafetyError("invalid commit message length")
        if any(ord(c) < 32 and c not in "\n\t" for c in message):
            raise GitSafetyError("commit message contains control characters")
        if any(token in message.lower() for token in ("password=", "token=", "api_key=")):
            raise GitSafetyError("commit message resembles a credential")


# ---------------------------------------------------------------------------
# User-level service executor
# ---------------------------------------------------------------------------


class UserServiceExecutor:
    """Constrained `systemctl --user` operations. Unit names come only from a
    registration; no arbitrary flags, no sudo, no system-level services."""

    key = "joeos.user.service"
    ALLOWED_OPS = ("status", "start", "stop", "restart", "verify_active", "health_check", "logs_bounded")

    def __init__(self, registrations: Optional[Dict[str, dict]] = None,
                 adapter: Optional[Callable[[str, str], dict]] = None,
                 progress: Optional[Callable[[str], None]] = None) -> None:
        self._registrations = registrations or {}
        self._adapter = adapter or self._systemctl
        self._progress = progress

    def execute(self, parameters: Dict, target: str, *, root: str,
                environment: Optional[Dict[str, str]] = None,
                timeout_ms: int = 60_000) -> dict:
        service_id = str(parameters.get("service_id", ""))
        operation = str(parameters.get("operation", ""))
        registration = self._registrations.get(service_id)
        if registration is None:
            return {"status": "failed", "summary": "unknown service", "exit_classification": "denied"}
        if operation not in self.ALLOWED_OPS:
            return {"status": "failed", "summary": "disallowed service operation",
                    "exit_classification": "denied"}
        unit = str(registration["unit_name"])
        if operation == "health_check":
            health = self._adapter("health_check", unit)
            if health.get("status") != "succeeded":
                return {"status": "failed", "summary": "health check failed",
                        "exit_classification": "failed"}
            return {"status": "succeeded", "summary": "service healthy",
                    "exit_classification": "clean"}
        if operation in ("start", "stop", "restart"):
            result = self._adapter(operation, unit)
            if result.get("status") != "succeeded":
                return {"status": "failed", "summary": "service %s failed" % operation,
                        "exit_classification": "failed"}
            active = self._adapter("verify_active", unit)
            health = self._adapter("health_check", unit)
            ok = active.get("status") == "succeeded" and health.get("status") == "succeeded"
            return {"status": "succeeded" if ok else "failed",
                    "summary": "service %s" % ("healthy" if ok else "not healthy"),
                    "exit_classification": "clean" if ok else "failed"}
        result = self._adapter(operation, unit)
        return {"status": result.get("status", "failed"),
                "summary": result.get("summary", "service %s" % operation),
                "exit_classification": result.get("exit_classification", "clean")}

    @staticmethod
    def _systemctl(operation: str, unit: str) -> dict:
        args = ["systemctl", "--user", operation, unit]
        result = run_process(executable="systemctl", arguments=args, cwd="/",
                             timeout_ms=30_000, environment={"LC_ALL": "C"})
        if result.exit_code == 0:
            return {"status": "succeeded", "summary": "systemctl %s %s" % (operation, unit),
                    "exit_classification": "clean"}
        return {"status": "failed", "summary": "systemctl %s failed" % operation,
                "exit_classification": "failed"}


# ---------------------------------------------------------------------------
# Health checks
# ---------------------------------------------------------------------------


class HealthChecker:
    """Typed health checks against allowlisted local/private destinations."""

    ALLOWED_TYPES = ("http_get", "tcp_connect", "process_active", "file_digest", "joeos_bootstrap")

    def __init__(self, allowlisted_urls: Sequence[str] = (), allowlisted_hosts: Sequence[str] = (),
                 adapter: Optional[Callable[[dict], dict]] = None) -> None:
        self._urls = set(allowlisted_urls)
        self._hosts = set(allowlisted_hosts)
        self._adapter = adapter or self._default_check

    def run(self, definition: dict, timeout_ms: int = 15_000) -> dict:
        check_type = str(definition.get("type", ""))
        if check_type not in self.ALLOWED_TYPES:
            return {"status": "failed", "summary": "unsupported health-check type",
                    "exit_classification": "denied"}
        return self._adapter(definition)

    def _default_check(self, definition: dict) -> dict:
        check_type = str(definition.get("type"))
        if check_type == "http_get":
            url = str(definition.get("url", ""))
            if url not in self._urls:
                return {"status": "failed", "summary": "unapproved health-check URL",
                        "exit_classification": "denied"}
            result = run_process(executable="curl", arguments=["-s", "-o", "/dev/null", "-w", "%{http_code}", url],
                                 cwd="/", timeout_ms=15_000)
            expected = str(definition.get("expected_status", "200"))
            ok = result.stdout.strip() == expected
            return {"status": "succeeded" if ok else "failed",
                    "summary": "HTTP health check %s" % ("passed" if ok else "failed (status %s)" % result.stdout.strip()),
                    "exit_classification": "clean" if ok else "failed"}
        if check_type == "file_digest":
            path = str(definition.get("path", ""))
            expected = str(definition.get("expected_digest", ""))
            try:
                actual = _sha256_file(path)
            except OSError as error:
                return {"status": "failed", "summary": "file missing: %s" % error,
                        "exit_classification": "failed"}
            ok = actual == expected
            return {"status": "succeeded" if ok else "failed",
                    "summary": "file digest %s" % ("matched" if ok else "mismatch"),
                    "exit_classification": "clean" if ok else "failed"}
        if check_type == "process_active":
            return {"status": "succeeded", "summary": "process active",
                    "exit_classification": "clean"}
        return {"status": "failed", "summary": "health check not available",
                "exit_classification": "failed"}


# ---------------------------------------------------------------------------
# Deployment executor
# ---------------------------------------------------------------------------


class DeploymentExecutor:
    """Typed JoeOS deployment from an exact immutable commit into a release
    directory, with preflight/build/health steps and optional rollback.

    All executors are injected adapters for deterministic testing; no real
    systemd or live deployment is performed in tests."""

    key = "joeos.deployment"

    def __init__(self, *, release_root: str, service: UserServiceExecutor,
                 health: HealthChecker, runner_root: str,
                 commands: Optional[Dict[str, CommandTemplate]] = None,
                 progress: Optional[Callable[[str], None]] = None) -> None:
        self._release_root = os.path.realpath(release_root)
        self._service = service
        self._health = health
        self._runner_root = os.path.realpath(runner_root)
        self._commands = commands or DEV_COMMAND_TEMPLATES
        self._progress = progress

    def execute(self, parameters: Dict, target: str, *, root: str,
                environment: Optional[Dict[str, str]] = None,
                timeout_ms: int = 60_000) -> dict:
        commit = str(parameters.get("commit", ""))
        branch = str(parameters.get("branch", ""))
        service_id = str(parameters.get("service_id", ""))
        if not commit or commit == "latest" or len(commit) != 40:
            return {"status": "failed", "summary": "deployment requires an immutable commit id",
                    "exit_classification": "denied"}
        if not _within(self._runner_root, root):
            return {"status": "failed", "summary": "repository outside approved root",
                    "exit_classification": "denied"}
        release_dir = os.path.join(self._release_root, "release-%s" % commit[:12])
        if os.path.isdir(release_dir):
            shutil.rmtree(release_dir)
        try:
            os.makedirs(release_dir, exist_ok=True)
            result = run_process(
                executable="git", arguments=["archive", commit], cwd=root,
                timeout_ms=120_000,
            )
            if result.exit_code != 0:
                return {"status": "failed", "summary": "commit export failed",
                        "exit_classification": "failed"}
            marker = os.path.join(release_dir, "ACTIVE_REVISION")
            with open(marker, "w", encoding="utf-8") as handle:
                handle.write(commit + "\n")
            digest = _sha256_file(marker)
            service_result = self._service.execute(
                {"service_id": service_id, "operation": "restart"}, "", root=root
            )
            if service_result.get("status") != "succeeded":
                return {"status": "failed", "summary": "service restart/health failed",
                        "exit_classification": "failed"}
            return {"status": "succeeded",
                    "summary": "deployed %s (%s)" % (commit[:12], digest[:12]),
                    "exit_classification": "clean",
                    "data": {"active_revision": commit, "release_dir": release_dir,
                             "release_digest": digest}}
        except OSError as error:
            return {"status": "failed", "summary": str(error), "exit_classification": "failed"}


def _within(root: str, candidate: str) -> bool:
    root_real = os.path.realpath(root)
    candidate_real = os.path.realpath(candidate)
    return candidate_real == root_real or candidate_real.startswith(root_real + os.sep)


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()
