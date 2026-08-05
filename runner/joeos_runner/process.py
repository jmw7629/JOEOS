"""Safe process execution foundation.

Executors launch only allowlisted executables with a typed argument vector,
`shell=False`, a minimal allowlisted environment, a bounded working directory,
bounded output, and a bounded runtime. Child processes are started in their own
process group so cancellation terminates the whole group. Secret values are
redacted from captured output and never placed on the command line.
"""

from __future__ import annotations

import os
import select
import signal
import subprocess
import time
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

MAX_OUTPUT_BYTES = 1_048_576


class ProcessExecutionError(Exception):
    pass


@dataclass
class ProcessResult:
    exit_code: Optional[int]
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False
    cancelled: bool = False
    signalled: Optional[str] = None

    def redacted(self, secret_values: Sequence[str] = ()) -> "ProcessResult":
        stdout = _redact(self.stdout, secret_values)
        stderr = _redact(self.stderr, secret_values)
        return ProcessResult(
            exit_code=self.exit_code, stdout=stdout, stderr=stderr,
            duration_ms=self.duration_ms, timed_out=self.timed_out,
            cancelled=self.cancelled, signalled=self.signalled,
        )


def _redact(text: str, secrets: Sequence[str]) -> str:
    result = text
    for secret in secrets:
        if secret and len(secret) >= 4:
            result = result.replace(secret, "[REDACTED]")
    return result


def run_process(
    *,
    executable: str,
    arguments: Sequence[str],
    cwd: str,
    environment: Optional[dict] = None,
    timeout_ms: int = 60_000,
    max_output_bytes: int = MAX_OUTPUT_BYTES,
    allowlist_environment: bool = True,
) -> ProcessResult:
    """Runs a typed allowlisted command with process isolation.

    `shell` is always False; `executable` and `arguments` must already be
    validated by an executor. A dedicated process group is created so
    cancellation can terminate children. Reads are select-based so output is
    bounded and timeouts terminate the process group without blocking.
    """
    if not executable or executable.startswith(("/", "~")) or ".." in executable:
        raise ProcessExecutionError("executable must be an allowlisted name, not a path")
    shell_fragment = (";", "&&", "||", "|", "`", "$(", ">", "<")
    for argument in arguments:
        if not isinstance(argument, str) or "\x00" in argument:
            raise ProcessExecutionError("arguments must be NUL-free strings")
        if any(marker in argument for marker in shell_fragment):
            raise ProcessExecutionError("arguments must not contain shell control characters")

    env = None
    if environment is not None:
        base = os.environ if not allowlist_environment else {}
        env = {**base, **{k: str(v) for k, v in environment.items()}}

    start = time.monotonic()
    captured_stdout: List[bytes] = []
    captured_stderr: List[bytes] = []
    stdout_size = 0
    stderr_size = 0
    timed_out = False
    cancelled = False
    output_truncated = False

    try:
        process = subprocess.Popen(
            [executable, *arguments],
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            shell=False,
            close_fds=True,
        )
    except OSError as error:
        raise ProcessExecutionError("could not start %s: %s" % (executable, error)) from error

    try:
        while process.poll() is None:
            if time.monotonic() - start > timeout_ms / 1000.0:
                timed_out = True
                _terminate_group(process)
                break
            readable, _, _ = select.select([process.stdout, process.stderr], [], [], 0.05)
            for stream in readable:
                try:
                    chunk = os.read(stream.fileno(), 65536)
                except (OSError, ValueError):
                    chunk = b""
                if not chunk:
                    continue
                if output_truncated:
                    continue
                if stream is process.stdout:
                    captured_stdout.append(chunk)
                    stdout_size += len(chunk)
                else:
                    captured_stderr.append(chunk)
                    stderr_size += len(chunk)
                if stdout_size > max_output_bytes or stderr_size > max_output_bytes:
                    output_truncated = True
        # Drain any remaining buffered output after the process exits.
        try:
            tail_out, tail_err = process.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            _terminate_group(process)
            tail_out, tail_err = process.communicate(timeout=1)
        if not output_truncated:
            if tail_out:
                captured_stdout.append(tail_out[: max(0, max_output_bytes - stdout_size)])
            if tail_err:
                captured_stderr.append(tail_err[: max(0, max_output_bytes - stderr_size)])
        returncode = process.returncode
    except KeyboardInterrupt:
        _terminate_group(process)
        cancelled = True
        returncode = process.wait()
    finally:
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
        if process.poll() is None:
            _terminate_group(process)

    stdout = _decode(captured_stdout, max_output_bytes)
    stderr = _decode(captured_stderr, max_output_bytes)
    duration_ms = int((time.monotonic() - start) * 1000)
    return ProcessResult(
        exit_code=returncode, stdout=stdout, stderr=stderr, duration_ms=duration_ms,
        timed_out=timed_out, cancelled=cancelled,
        signalled="SIGTERM" if (timed_out or cancelled) else None,
    )


def _terminate_group(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except (OSError, ProcessLookupError):
        try:
            process.terminate()
        except Exception:
            pass


def _decode(chunks: List[bytes], limit: int) -> str:
    combined = b"".join(chunks)[:limit]
    return combined.decode("utf-8", errors="replace")


def canonicalize_path(root: str, candidate: str) -> str:
    """Resolves a candidate path safely inside an approved root."""
    root_real = os.path.realpath(root)
    candidate_real = os.path.realpath(os.path.join(root_real, candidate))
    if candidate_real == root_real:
        return candidate_real
    if not candidate_real.startswith(root_real + os.sep):
        raise ProcessExecutionError("path escapes the approved root")
    if os.path.basename(candidate_real).startswith("."):
        raise ProcessExecutionError("hidden paths are not allowed")
    return candidate_real
