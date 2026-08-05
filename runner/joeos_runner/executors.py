"""Registered executor adapters for the private runner.

Executors are narrowly scoped, typed, and do not accept arbitrary commands.
The deterministic executor exists only for tests/development and never in
production configuration.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Protocol, runtime_checkable

from .process import ProcessExecutionError, canonicalize_path, run_process


@dataclass
class ExecutorResult:
    status: str  # succeeded | failed | cancelled | timed_out
    summary: str
    exit_classification: str = "clean"
    output: str = ""
    artifacts: List[dict] = field(default_factory=list)
    data: Dict[str, object] = field(default_factory=dict)


@runtime_checkable
class ExecutorAdapter(Protocol):
    key: str

    def execute(self, parameters: Dict, target: str, *, root: str,
                environment: Optional[Dict[str, str]] = None,
                timeout_ms: int = 60_000) -> ExecutorResult:
        ...


class RunnerDiagnosticsExecutor:
    """Read-only runner diagnostics. Reports bounded, non-secret summary data."""

    key = "joeos.runner.diagnostics"

    def execute(self, parameters, target, *, root, environment=None, timeout_ms=60000):
        try:
            cpu = os.getloadavg()
        except (OSError, AttributeError):
            cpu = None
        summary = {
            "runner_version": parameters.get("runner_version", "unknown"),
            "protocol_version": parameters.get("protocol_version", 1),
            "load_1m": round(cpu[0], 2) if cpu else None,
            "self_test": parameters.get("self_test", False),
        }
        return ExecutorResult(status="succeeded", summary="runner diagnostics",
                              output=repr(summary), data=summary)


class WorkspaceFilesystemExecutor:
    """Bounded workspace filesystem operations inside an approved root."""

    key = "joeos.workspace.filesystem"

    def execute(self, parameters, target, *, root, environment=None, timeout_ms=60000):
        operation = parameters.get("operation")
        if operation not in {"read_text", "list", "write_atomic", "digest"}:
            return ExecutorResult(status="failed", summary="unsupported operation",
                                  exit_classification="denied")
        try:
            path = canonicalize_path(root, target)
        except ProcessExecutionError as error:
            return ExecutorResult(status="failed", summary=str(error),
                                  exit_classification="denied")
        if operation == "read_text":
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    content = handle.read(256 * 1024)
                return ExecutorResult(status="succeeded", summary="read file",
                                      output=content)
            except OSError as error:
                return ExecutorResult(status="failed", summary=str(error))
        if operation == "list":
            try:
                entries = sorted(os.listdir(path))[:200]
                return ExecutorResult(status="succeeded", summary="listed directory",
                                      data={"entries": entries})
            except OSError as error:
                return ExecutorResult(status="failed", summary=str(error))
        if operation == "digest":
            try:
                digest = hashlib.sha256()
                with open(path, "rb") as handle:
                    for chunk in iter(lambda: handle.read(65536), b""):
                        digest.update(chunk)
                return ExecutorResult(status="succeeded", summary="digest",
                                      data={"sha256": digest.hexdigest()})
            except OSError as error:
                return ExecutorResult(status="failed", summary=str(error))
        if operation == "write_atomic":
            content = parameters.get("content", "")
            if len(content) > 256 * 1024:
                return ExecutorResult(status="failed", summary="content too large")
            tmp = path + ".tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as handle:
                    handle.write(content)
                os.replace(tmp, path)
                return ExecutorResult(status="succeeded", summary="wrote file atomically")
            except OSError as error:
                return ExecutorResult(status="failed", summary=str(error))
        return ExecutorResult(status="failed", summary="unsupported operation")


class DeterministicTestExecutor:
    """Test/development-only deterministic executor.

    Never registered in production configuration. Emits a fixed result; can be
    configured to fail, time out, or leak a simulated secret for tests.
    """

    key = "joeos.test.deterministic"

    def execute(self, parameters, target, *, root, environment=None, timeout_ms=60000):
        mode = parameters.get("mode", "ok")
        if mode == "fail":
            return ExecutorResult(status="failed", summary="deterministic failure",
                                  exit_classification="failed")
        if mode == "timeout":
            import time
            time.sleep(1.0)
            return ExecutorResult(status="timed_out", summary="deterministic timeout")
        if mode == "leak":
            return ExecutorResult(status="succeeded", summary="ok",
                                  output="output contains simulated-secret-value-1234")
        return ExecutorResult(status="succeeded", summary="deterministic ok",
                              output="deterministic output", data={"mode": mode})


REGISTERED_EXECUTORS: Dict[str, ExecutorAdapter] = {
    executor.key: executor
    for executor in (
        RunnerDiagnosticsExecutor(),
        WorkspaceFilesystemExecutor(),
        DeterministicTestExecutor(),
    )
}


def get_executor(key: str) -> Optional[ExecutorAdapter]:
    return REGISTERED_EXECUTORS.get(key)
