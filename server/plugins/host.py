"""Extension Host Manager for the JoeOS Plugin Platform.

Spawns and supervises one isolated subprocess per active plugin, enforces
bounded timeouts and cancellation, applies a bounded restart policy, and
escalates repeated crashes to quarantine. The host never loads plugin code
into the privileged core process; all plugin code runs in the child.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from typing import Callable, Dict, Optional

from .host_protocol import RpcProtocolError, encode_request, decode_line
from .models import PluginManifest

HOST_READY_TIMEOUT_SECONDS = 10.0
DEFAULT_RPC_TIMEOUT_SECONDS = 30.0
MAX_RESTART_ATTEMPTS = 3


class HostError(RuntimeError):
    pass


class _ChildHost:
    """One supervised subprocess hosting one plugin."""

    def __init__(
        self,
        *,
        plugin_id: str,
        plugin_dir: str,
        manifest: PluginManifest,
        python: str,
        restart_policy,
        crash_callback: Callable[[str, str], None],
        rpc_timeout: float,
    ) -> None:
        self._plugin_id = plugin_id
        self._plugin_dir = plugin_dir
        self._manifest = manifest
        self._python = python
        self._restart_policy = restart_policy
        self._crash_callback = crash_callback
        self._rpc_timeout = rpc_timeout
        self._process: Optional[subprocess.Popen] = None
        self._lock = threading.RLock()
        self._request_counter = 0
        self._restart_count = 0
        self._trace_id = 0

    def start(self) -> None:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return
            manifest_path = os.path.join(self._plugin_dir, "manifest.json")
            command = [
                self._python,
                "-m",
                "server.plugins.host_runner",
                "--plugin-dir",
                self._plugin_dir,
                "--manifest",
                manifest_path,
            ]
            try:
                self._process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=dict(os.environ, JOEOS_PLUGIN_ISOLATION="1"),
                )
            except OSError as exc:
                raise HostError("could not start extension host: %s" % exc) from exc
            self._restart_count = 0

    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def invoke(self, method: str, params: Optional[dict] = None) -> dict:
        """Send one request and wait for its response with a timeout."""
        with self._lock:
            self._ensure_ready()
            if not self.running():
                self._handle_dead()
                raise HostError("extension host is not running.")
            self._request_counter += 1
            request_id = self._request_counter
            trace_id = "trace-%d" % self._trace_id
            self._trace_id += 1
            payload = encode_request(
                request_id=request_id,
                plugin_id=self._plugin_id,
                method=method,
                params=params or {},
                trace_id=trace_id,
                api_version=self._manifest.api_version,
            )
            try:
                self._process.stdin.write(payload.decode("utf-8"))
                self._process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                self._handle_dead()
                raise HostError("extension host pipe closed: %s" % exc) from exc
            line = self._read_response_with_timeout(request_id)
            if line is None:
                self._handle_dead()
                raise HostError("extension host timed out or exited.")
            kind, message = decode_line(line)
            if kind == "response":
                if message.status == "error":
                    raise HostError(
                        message.error_message or (message.error_code or "extension error")
                    )
                return {"result": message.result, "status": "ok"}

    def _read_response_with_timeout(self, request_id: int) -> Optional[str]:
        import select
        deadline = time.monotonic() + self._rpc_timeout
        fd = self._process.stdout.fileno() if self._process.stdout else -1
        while time.monotonic() < deadline:
            if not self.running():
                return None
            remaining = max(0.0, deadline - time.monotonic())
            try:
                readable, _, _ = select.select([self._process.stdout], [], [], min(0.1, remaining))
            except (OSError, ValueError):
                return None
            if not readable:
                continue
            line = self._process.stdout.readline() if self._process.stdout else None
            if line is None:
                time.sleep(0.01)
                continue
            if line.strip():
                return line
        return None

    def _ensure_ready(self) -> None:
        deadline = time.monotonic() + HOST_READY_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if self.running():
                return
            time.sleep(0.02)
        raise HostError("extension host failed to start.")

    def _handle_dead(self) -> None:
        self._restart_count += 1
        self._crash_callback(self._plugin_id, "extension host exited.")
        if self._restart_count <= MAX_RESTART_ATTEMPTS and self._restart_policy.allow_restart(self._plugin_id):
            try:
                self.start()
            except HostError:
                pass

    def terminate(self) -> None:
        with self._lock:
            if self._process is not None:
                try:
                    self._process.terminate()
                except OSError:
                    pass

    def shutdown(self, timeout: float = 3.0) -> None:
        with self._lock:
            if self._process is None:
                return
            try:
                self._process.terminate()
                try:
                    self._process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=2.0)
            except OSError:
                pass
            self._process = None


class RestartPolicy:
    def __init__(self, max_restarts: int = MAX_RESTART_ATTEMPTS) -> None:
        self._max_restarts = max_restarts
        self._counts: Dict[str, int] = {}

    def allow_restart(self, plugin_id: str) -> bool:
        count = self._counts.get(plugin_id, 0)
        if count >= self._max_restarts:
            return False
        self._counts[plugin_id] = count + 1
        return True

    def reset(self, plugin_id: str) -> None:
        self._counts.pop(plugin_id, None)


class ExtensionHostManager:
    """Supervises extension host subprocesses for all active plugins."""

    def __init__(
        self,
        *,
        python: Optional[str] = None,
        rpc_timeout: float = DEFAULT_RPC_TIMEOUT_SECONDS,
        crash_callback: Optional[Callable[[str, str], None]] = None,
        restart_policy: Optional[RestartPolicy] = None,
    ) -> None:
        self._python = python or sys.executable
        self._rpc_timeout = rpc_timeout
        self._crash_callback = crash_callback or (lambda plugin_id, reason: None)
        self._restart_policy = restart_policy or RestartPolicy()
        self._hosts: Dict[str, _ChildHost] = {}
        self._lock = threading.RLock()

    def ensure_host(
        self,
        *,
        plugin_id: str,
        plugin_dir: str,
        manifest: PluginManifest,
    ) -> _ChildHost:
        with self._lock:
            host = self._hosts.get(plugin_id)
            if host is not None and host.running():
                return host
            host = _ChildHost(
                plugin_id=plugin_id,
                plugin_dir=plugin_dir,
                manifest=manifest,
                python=self._python,
                restart_policy=self._restart_policy,
                crash_callback=self._crash_callback,
                rpc_timeout=self._rpc_timeout,
            )
            host.start()
            self._hosts[plugin_id] = host
            return host

    def invoke(self, *, plugin_id: str, plugin_dir: str, manifest: PluginManifest, method: str, params: Optional[dict] = None) -> dict:
        host = self.ensure_host(plugin_id=plugin_id, plugin_dir=plugin_dir, manifest=manifest)
        return host.invoke(method, params)

    def shutdown(self, plugin_id: str) -> None:
        with self._lock:
            host = self._hosts.pop(plugin_id, None)
        if host is not None:
            host.shutdown()

    def shutdown_all(self) -> None:
        with self._lock:
            hosts = list(self._hosts.values())
            self._hosts.clear()
        for host in hosts:
            host.shutdown()

    def running_plugin_ids(self) -> tuple:
        with self._lock:
            return tuple(
                plugin_id for plugin_id, host in self._hosts.items() if host.running()
            )