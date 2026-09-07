#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

import backend as app

PUBLIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/home.js": ("home.js", "application/javascript; charset=utf-8"),
    "/home-inspector.js": ("home-inspector.js", "application/javascript; charset=utf-8"),
    "/health-runtime.js": ("health-runtime.js", "application/javascript; charset=utf-8"),
}

GET_API_PATHS = frozenset(
    {
        "/api/session",
        "/api/tasks",
        "/api/projects",
        "/api/intelligence",
        "/api/activity",
        "/api/models",
        "/api/agents",
        "/api/runs",
        "/api/settings",
        "/api/notifications",
        "/api/team",
        "/api/memory",
        "/api/chat",
        "/api/help",
    }
)
RUN_TERMINAL_RE = re.compile(r"^/api/runs/[^/]+/terminal$")
SYNC_LOCK = threading.Lock()
SYNC_STATE = {"last_attempt": 0, "last_ok": 0, "last_error": ""}

BRIDGE_SERVICES = {
    "stickdeath": ("stickdeath-opencode-bridge.service", "jmw7629/StickDeath-Infinity-"),
    "vitros": ("vitros-opencode-bridge.service", "jmw7629/vitros-web-dashboard"),
    "vitros_verifier": ("vitros-opencode-verifier.service", "jmw7629/vitros-web-dashboard"),
}


def _sync_once():
    attempt = int(time.time())
    with SYNC_LOCK:
        SYNC_STATE["last_attempt"] = attempt
    try:
        app.sync_runs_once()
        app.sweep_notifications()
    except Exception as exc:
        with SYNC_LOCK:
            SYNC_STATE["last_error"] = type(exc).__name__
        return False
    with SYNC_LOCK:
        SYNC_STATE["last_ok"] = int(time.time())
        SYNC_STATE["last_error"] = ""
    return True


def truthful_sync_loop():
    while True:
        _sync_once()
        time.sleep(10)


def _sqlite_health():
    try:
        with app.con() as conn:
            conn.execute("create temp table if not exists project_byte_health_probe(value integer)")
            conn.execute("delete from project_byte_health_probe")
            conn.execute("insert into project_byte_health_probe(value) values(1)")
            value = conn.execute("select value from project_byte_health_probe").fetchone()[0]
        if value != 1:
            raise RuntimeError("probe mismatch")
        return {"state": "healthy"}
    except Exception as exc:
        return {"state": "failed", "error_type": type(exc).__name__}


def _latest_bridge_activity(repo: str):
    state_dir = app.BRIDGE_STATE.get(repo)
    if not state_dir:
        return None
    candidates = [state_dir / "processed.json"]
    logs = state_dir / "logs"
    try:
        if logs.is_dir():
            candidates.extend(logs.glob("issue-*.log"))
        mtimes = [p.stat().st_mtime for p in candidates if p.exists() and p.is_file()]
    except OSError:
        return None
    return max(mtimes) if mtimes else None


def _service_state(service: str):
    env = os.environ.copy()
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    try:
        proc = subprocess.run(
            ["systemctl", "--user", "is-active", service],
            text=True,
            capture_output=True,
            timeout=2,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    text = (proc.stdout or "").strip().lower()
    if proc.returncode == 0 and text == "active":
        return "healthy"
    if text in {"inactive", "failed", "deactivating"}:
        return "failed"
    return "unknown"


def _bridge_health():
    current = time.time()
    result = {}
    for key, (service, repo) in BRIDGE_SERVICES.items():
        last_activity = _latest_bridge_activity(repo)
        result[key] = {
            "state": _service_state(service),
            "last_activity_age_seconds": max(0, int(current - last_activity)) if last_activity else None,
        }
    return result


def _model_health():
    try:
        models = [m for m in app.model_rows() if m.get("enabled")]
    except Exception:
        return {"state": "unknown", "enabled": 0, "tested_ok": 0, "failed": 0, "unknown": 0}
    tested_ok = sum((m.get("last_status") or "").lower() == "ok" for m in models)
    unknown = sum((m.get("last_status") or "unknown").lower() in {"", "unknown"} for m in models)
    failed = max(0, len(models) - tested_ok - unknown)
    if failed:
        state = "failed"
    elif models and tested_ok == len(models):
        state = "tested_ok"
    else:
        state = "unknown"
    return {
        "state": state,
        "enabled": len(models),
        "tested_ok": tested_ok,
        "failed": failed,
        "unknown": unknown,
    }


def health_snapshot():
    current = int(time.time())
    database = _sqlite_health()
    with SYNC_LOCK:
        sync = dict(SYNC_STATE)
    last_ok = int(sync.get("last_ok") or 0)
    age = max(0, current - last_ok) if last_ok else None
    if last_ok and age <= 30 and not sync.get("last_error"):
        sync_state = "healthy"
    elif sync.get("last_error") or (age is not None and age > 30):
        sync_state = "failed"
    else:
        sync_state = "unknown"
    sync_component = {
        "state": sync_state,
        "last_attempt": int(sync.get("last_attempt") or 0),
        "last_ok": last_ok,
        "last_ok_age_seconds": age,
        "last_error_type": sync.get("last_error") or "",
    }
    bridges = _bridge_health()
    models = _model_health()
    core_ok = database["state"] == "healthy" and sync_state == "healthy"
    execution_ok = (
        bridges["stickdeath"]["state"] == "healthy"
        and bridges["vitros"]["state"] == "healthy"
        and models["state"] == "tested_ok"
    )
    operational = bool(core_ok and execution_ok)
    return {
        "ok": bool(core_ok),
        "operational": operational,
        "status": "healthy" if operational else "core-healthy" if core_ok else "degraded",
        "version": 4,
        "ai_ops": bool(execution_ok),
        "settings": database["state"] == "healthy",
        "notifications": sync_state == "healthy",
        "components": {
            "sqlite": database,
            "sync": sync_component,
            "bridges": bridges,
            "models": models,
        },
    }


class SafeHandler(app.H):
    """Production HTTP boundary with evidence-backed health."""

    def _not_found(self, *, head: bool = False):
        if not head:
            return self.sendj({"error": "not found"}, 404)
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()

    def _send_file(self, path: Path, content_type: str, *, head: bool = False):
        try:
            resolved = path.resolve(strict=True)
            stat = resolved.stat()
            if not resolved.is_file():
                return self._not_found(head=head)
        except (FileNotFoundError, OSError):
            return self._not_found(head=head)

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(stat.st_size))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.end_headers()
        if head:
            return
        with resolved.open("rb") as stream:
            while True:
                chunk = stream.read(64 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def _public_file(self, path: str, *, head: bool = False):
        item = PUBLIC_FILES.get(path)
        if not item:
            return self._not_found(head=head)
        name, content_type = item
        candidate = (app.ROOT / name).resolve()
        try:
            if candidate.parent != app.ROOT.resolve():
                return self._not_found(head=head)
        except OSError:
            return self._not_found(head=head)
        return self._send_file(candidate, content_type, head=head)

    def _attachment_file(self, path: str, *, head: bool = False):
        stored = path[len("/uploads/") :]
        if (
            not stored
            or "/" in stored
            or "\\" in stored
            or Path(stored).name != stored
            or len(stored) > 180
        ):
            return self._not_found(head=head)

        try:
            with app.con() as conn:
                record = conn.execute(
                    "select stored_name,content_type from attachments where stored_name=? limit 1",
                    (stored,),
                ).fetchone()
        except Exception:
            return self._not_found(head=head)
        if not record:
            return self._not_found(head=head)

        uploads_root = app.UPLOADS.resolve()
        candidate = (app.UPLOADS / stored).resolve()
        if candidate.parent != uploads_root:
            return self._not_found(head=head)
        content_type = record["content_type"] or "application/octet-stream"
        return self._send_file(candidate, content_type, head=head)

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        if path == "/healthz":
            return self.sendj(health_snapshot())
        if path in PUBLIC_FILES:
            return self._public_file(path)
        if path.startswith("/uploads/"):
            return self._attachment_file(path)
        if path in GET_API_PATHS or RUN_TERMINAL_RE.fullmatch(path):
            return app.H.do_GET(self)
        return self._not_found()

    def do_HEAD(self):
        path = unquote(urlparse(self.path).path)
        if path in PUBLIC_FILES:
            return self._public_file(path, head=True)
        if path.startswith("/uploads/"):
            return self._attachment_file(path, head=True)
        return self._not_found(head=True)


if __name__ == "__main__":
    app.init_db()
    _sync_once()
    threading.Thread(target=truthful_sync_loop, daemon=True).start()
    host = os.getenv("KANBAN_HOST", "127.0.0.1")
    port = int(os.getenv("KANBAN_PORT", "8094"))
    print(f"PROJECT_BYTE v4 listening on http://{host}:{port}")
    ThreadingHTTPServer((host, port), SafeHandler).serve_forever()
