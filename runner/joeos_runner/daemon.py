"""Long-lived runner daemon.

Connects outbound to the authoritative backend over a private endpoint,
authenticates with the enrolled runner key, maintains heartbeat, polls
compatible signed job leases, executes registered executor adapters, streams
bounded progress, signs terminal results, and records a bounded local journal.
Reconnects with bounded exponential backoff and shuts down cleanly on signals.
"""

from __future__ import annotations

import hashlib
import logging
import random
import signal
import threading
import time
import uuid as _uuid
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Protocol

from .configuration import RunnerConfiguration
from .journal import ExecutionJournal
from .process import ProcessExecutionError
from .secrets import RunnerLocalSecretProvider

LOGGER = logging.getLogger("joeos.runner")


class RunnerTransport(Protocol):
    """Backend transport used by the daemon (test-injectable)."""

    def request_connection(self, runner_id: str, key_identifier: str, public_key: str) -> Dict:
        ...

    def authenticate(self, challenge: Dict, signature_b64url: str) -> Dict:
        ...

    def heartbeat(self, credential: str) -> bool:
        ...

    def lease(self, credential: str) -> Dict:
        ...

    def acknowledge(self, credential: str, job: Dict, signature_b64url: str) -> bool:
        ...

    def start(self, credential: str, job: Dict) -> bool:
        ...

    def progress(self, credential: str, job: Dict, text: str) -> bool:
        ...

    def complete(self, credential: str, job: Dict, signature_b64url: str, result: Dict) -> Dict:
        ...

    def rotate(self, credential: str) -> str:
        ...


class RunnerSigner(Protocol):
    def public_key(self) -> str:
        ...

    def key_identifier(self) -> str:
        ...

    def sign(self, message: str) -> str:
        ...


class RunnerDaemon:
    """The continuously operating runner service loop."""

    RESULT_DOMAIN = "JOEOS-EXECUTION-RESULT-V1"
    CONNECTION_DOMAIN = "JOEOS-RUNNER-CONNECTION-V1"

    def __init__(
        self,
        config: RunnerConfiguration,
        signer: RunnerSigner,
        transport: RunnerTransport,
        journal: ExecutionJournal,
        *,
        secret_provider: Optional[RunnerLocalSecretProvider] = None,
        executor_resolver: Optional[Callable[[str], object]] = None,
        event_sink: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._config = config
        self._signer = signer
        self._transport = transport
        self._journal = journal
        self._secret_provider = secret_provider or RunnerLocalSecretProvider()
        self._executor_resolver = executor_resolver or (lambda key: None)
        self._event_sink = event_sink
        self._stop = threading.Event()
        self._credential: Optional[str] = None
        self._paused = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, run_once: bool = False) -> int:
        if self._event_sink:
            self._event_sink("runner.daemon_started")
        self._register_signal_handlers()
        attempt = 0
        while not self._stop.is_set():
            try:
                self._connect_and_run(run_once)
                return 0
            except Exception as error:  # noqa: BLE001
                LOGGER.warning("runner connection failed: %s", error)
                if run_once:
                    return 2
                attempt = self._sleep_backoff(attempt)
        if self._event_sink:
            self._event_sink("runner.daemon_stopped")
        return 0

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------
    # Connection + main loop
    # ------------------------------------------------------------------

    def _connect_and_run(self, run_once: bool) -> None:
        challenge = self._transport.request_connection(
            self._config.runner_id, self._signer.key_identifier(), self._signer.public_key()
        )
        message = self.CONNECTION_DOMAIN + "\0" + str(challenge["challenge_id"]) + "\0" + str(challenge["nonce"])
        authenticated = self._transport.authenticate(
            challenge, self._signer.sign(message)
        )
        self._credential = authenticated["connection_credential"]
        if self._event_sink:
            self._event_sink("runner.connection_authenticated")
        last_heartbeat = time.monotonic()
        while not self._stop.is_set():
            if self._paused:
                time.sleep(0.5)
                continue
            if time.monotonic() - last_heartbeat > self._config.heartbeat_interval_ms / 1000.0:
                self._transport.heartbeat(self._credential)
                last_heartbeat = time.monotonic()
            lease = self._transport.lease(self._credential)
            job = lease.get("job") if lease else None
            if job is None:
                if run_once:
                    return
                time.sleep(0.5)
                continue
            self._run_job(job)
            if run_once:
                return

    def _run_job(self, job: Dict) -> None:
        job_id = str(job["id"])
        generation = int(job.get("lease_generation", 0))
        executor_key = str(job.get("executor_key", job.get("executor", "")))
        executor = self._executor_resolver(executor_key)
        if executor is None:
            self._journal.append(job_id=job_id, lease_generation=generation, state="failed",
                                 executor=executor_key, result_metadata="unknown executor")
            return
        ack_message = self.RESULT_DOMAIN + "\0" + job_id + "\0acknowledge"
        try:
            self._transport.acknowledge(self._credential, job, self._signer.sign(ack_message))
            self._journal.append(job_id=job_id, lease_generation=generation, state="acknowledged",
                                 executor=executor_key)
            self._transport.start(self._credential, job)
            self._journal.append(job_id=job_id, lease_generation=generation, state="running",
                                 executor=executor_key)
            result = self._dispatch(executor, job, executor_key)
        except Exception as error:  # noqa: BLE001
            result = {"status": "failed", "summary": str(error)[:240],
                      "exit_classification": "failed"}
        status = result.get("status", "failed")
        terminal = status if status in ("succeeded", "failed", "cancelled", "timed_out") else "failed"
        message = self.RESULT_DOMAIN + "\0" + job_id + "\0" + terminal
        try:
            self._transport.complete(
                self._credential, job, self._signer.sign(message), result
            )
        except Exception as error:  # noqa: BLE001
            LOGGER.warning("result submission failed: %s", error)
        self._journal.append(job_id=job_id, lease_generation=generation, state=terminal,
                             executor=executor_key,
                             result_metadata=str(result.get("summary", ""))[:200])

    def _dispatch(self, executor: object, job: Dict, executor_key: str) -> Dict:
        parameters = dict(job.get("parameters") or {})
        target = str(job.get("target", ""))
        root = "/"
        # Inject a runner-local secret into a protected temp file when the job
        # references one and the executor is allowed (executor-specific).
        secret_name = parameters.pop("_secret_reference", None)
        secret_path = None
        secret_values = []
        if secret_name:
            try:
                resolved = self._secret_provider.resolve(secret_name)
                secret_values = [resolved.value]
                secret_path = self._secret_provider.write_temporary(
                    resolved.name, resolved.value, self._config.work_root
                )
                parameters["_secret_file"] = secret_path
            except Exception:  # noqa: BLE001
                return {"status": "failed", "summary": "secret resolution failed",
                        "exit_classification": "denied"}
        try:
            if hasattr(executor, "execute"):
                try:
                    outcome = executor.execute(parameters, target, root=root,
                                               timeout_ms=self._config.max_job_runtime_ms)
                except TypeError:
                    outcome = executor.execute(parameters, target, root=root)
            else:
                outcome = {"status": "failed", "summary": "invalid executor",
                           "exit_classification": "denied"}
            if not isinstance(outcome, dict):
                outcome = {
                    "status": getattr(outcome, "status", "succeeded"),
                    "summary": getattr(outcome, "summary", "executor returned"),
                    "exit_classification": getattr(outcome, "exit_classification", "clean"),
                    "output": getattr(outcome, "output", ""),
                }
            if secret_values:
                for field in ("output",):
                    if isinstance(outcome.get(field), str):
                        outcome[field] = RunnerLocalSecretProvider.redact(outcome[field], secret_values)
                    if RunnerLocalSecretProvider.scan_for_leakage(
                            json_text(outcome), secret_values):
                        outcome["status"] = "quarantined"
                        outcome["summary"] = "secret leakage suspected; result quarantined"
            return outcome
        finally:
            if secret_path:
                try:
                    import os
                    os.remove(secret_path)
                except OSError:
                    pass

    def _sleep_backoff(self, attempt: int) -> int:
        base = min(
            self._config.reconnect_max_ms,
            self._config.reconnect_initial_ms * (2 ** attempt),
        )
        jitter = random.randint(0, self._config.reconnect_jitter_ms)
        delay = base + jitter
        if self._event_sink:
            self._event_sink("runner.reconnecting")
        self._stop.wait(delay / 1000.0)
        return attempt + 1

    def _register_signal_handlers(self) -> None:
        def handle(signum, frame):
            self.stop()

        signal.signal(signal.SIGTERM, handle)
        signal.signal(signal.SIGINT, handle)


def json_text(value) -> str:
    import json
    try:
        return json.dumps(value, default=str)
    except Exception:  # noqa: BLE001
        return str(value)
