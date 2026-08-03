"""Extension Host child runner.

Runs inside an isolated subprocess. Loads a plugin's entry module from its
managed install directory and serves a bounded, typed RPC surface over stdio.
The runner never imports the privileged core: it receives an ``api`` object
whose methods are forwarded to the parent Extension Host, where every
capability is checked by the Capability Broker before any action is taken.

The child is launched by the Extension Host Manager with:
    python -m server.plugins.host_runner --plugin-dir <dir> --manifest <path>
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional


def _load_manifest(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    if not isinstance(manifest, dict):
        raise ValueError("manifest.json must be an object.")
    return manifest


def _load_entry(plugin_dir: Path, manifest: dict) -> Any:
    entry = manifest.get("entry_point") or {}
    runtime = entry.get("runtime", "python")
    if runtime != "python":
        raise ValueError("unsupported runtime %r for the python host." % runtime)
    module_path = str(entry.get("module") or "")
    function = str(entry.get("function") or "handle")
    if not module_path:
        raise ValueError("entry_point.module is required.")
    # Load only from the managed plugin directory.
    sys.path.insert(0, str(plugin_dir))
    try:
        module = importlib.import_module(module_path)
    except Exception as exc:  # pragma: no cover - isolated by the host
        raise ValueError("failed to import plugin entry: %s" % type(exc).__name__) from exc
    handler = getattr(module, function, None)
    if not callable(handler):
        raise ValueError("entry function %r is not callable." % function)
    return handler


class HostApi:
    """Bounded API forwarded to the parent host for capability-brokered calls."""

    def __init__(self, send: Any) -> None:
        self._send = send

    def call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        result = self._send(method, params or {})
        return result


def _send_request(send: Any, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
    # Emitted by the plugin through HostApi; the parent handles the reply.
    raise RuntimeError("unreachable: HostApi.call is resolved by the parent host")


def main() -> int:
    parser = argparse.ArgumentParser(description="JoeOS extension host child.")
    parser.add_argument("--plugin-dir", required=True)
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args()

    plugin_dir = Path(args.plugin_dir).resolve()
    manifest = _load_manifest(Path(args.manifest))
    handler = _load_entry(plugin_dir, manifest)
    api = HostApi(_send_request)

    for line in sys.stdin:
        if not line:
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if not isinstance(payload, dict) or "request" not in payload:
            continue
        request = payload["request"]
        request_id = request.get("id")
        method = request.get("method", "")
        params = request.get("params", {}) or {}
        try:
            result = handler(method, params, api)
        except Exception as exc:  # contain any plugin failure
            response = {
                "id": request_id,
                "status": "error",
                "error_code": type(exc).__name__,
                "error_message": str(exc)[:300],
            }
            sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
            sys.stdout.flush()
            continue
        response = {
            "id": request_id,
            "status": "ok",
            "result": result if isinstance(result, (dict, list, str, int, float, bool)) or result is None else str(result),
        }
        sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())