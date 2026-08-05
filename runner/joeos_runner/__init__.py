"""JoeOS private runner package (Phase P3C).

The runner operates as a dedicated unprivileged process, connects outbound to
the authoritative backend over a private network, authenticates with its own
signing key, verifies backend-signed execution jobs, runs only registered
executor adapters with process isolation, and signs progress/results.
"""

from .executors import (
    DeterministicTestExecutor,
    ExecutorAdapter,
    ExecutorResult,
    REGISTERED_EXECUTORS,
    RunnerDiagnosticsExecutor,
    WorkspaceFilesystemExecutor,
    get_executor,
)
from .process import (
    ProcessExecutionError,
    ProcessResult,
    canonicalize_path,
    run_process,
)

__all__ = [
    "DeterministicTestExecutor",
    "ExecutorAdapter",
    "ExecutorResult",
    "ProcessExecutionError",
    "ProcessResult",
    "REGISTERED_EXECUTORS",
    "RunnerDiagnosticsExecutor",
    "WorkspaceFilesystemExecutor",
    "canonicalize_path",
    "get_executor",
    "run_process",
]
