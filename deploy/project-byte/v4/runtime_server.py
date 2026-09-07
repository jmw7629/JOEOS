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
BRIDGE_CACHE_LOCK = threading.Lock()
BRIDGE_CACHE = {"at": 0.0, "value": {}}
BRIDGE_CACHE_SECONDS = 10.0
BRIDGE_STALL_SECONDS = 2700
MODEL_STATUS_FRESH_SECONDS = 1800

BRIDGE_SERVICES = {
    "stickdeath": ("stickdeath-opencode-bridge.service", "jmw7629/StickDeath-Infinity-", True),
    "vitros": ("vitros-opencode-bridge.service", "jmw7629/vitros-web-dashboard", True),
    "vitros_verifier": ("vitros-opencode-verifier.service", "jmw7629/vitros-web-dashboard", True),
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


def _latest_bridge_activity(
    repo: str,
    *,
    issue_number: int = 0,
    verifier: bool = False,
):
    state_dir = app.BRIDGE_STATE.get(repo)
    if not state_dir:
        return None

    logs = state_dir / "logs"
    if verifier:
        candidates = [state_dir / "verifier-processed.json"]
        try:
            if logs.is_dir():
                candidates.extend(logs.glob("verify-*.log"))
        except OSError:
            return None
    else:
        candidates = [state_dir / "processed.json"]
        if issue_number:
            candidates.append(logs / f"issue-{issue_number}.log")
            candidates.append(
                Path.home()
                / ".cache"
                / "joeos-opencode-bridge"
                / repo.replace("/", "__")
                / "worktrees"
                / f"issue-{issue_number}"
            )
        else:
            try:
                if logs.is_dir():
                    candidates.extend(logs.glob("issue-*.log"))
            except OSError:
                return None

    try:
        mtimes = [p.stat().st_mtime for p in candidates if p.exists()]
    except OSError:
        return None
    return max(mtimes) if mtimes else None


def _pending_runs(repo: str, current: float):
    try:
        with app.con() as conn:
            records = conn.execute(
                "select status,updated_at,issue_number from runs "
                "where repo=? and status in ('queued','running') order by updated_at",
                (repo,),
            ).fetchall()
    except Exception:
        return None
    if not records:
        return {
            "count": 0,
            "oldest_age_seconds": None,
            "active_updated_at": None,
            "active_issue_number": 0,
        }

    parsed = []
    for record in records:
        try:
            updated = max(0, int(record["updated_at"] or 0))
            issue_number = max(0, int(record["issue_number"] or 0))
        except (KeyError, TypeError, ValueError):
            return None
        parsed.append((updated, issue_number))

    parsed.sort(key=lambda item: item[0])
    active_updated, active_issue = parsed[0]
    return {
        "count": len(parsed),
        "oldest_age_seconds": max(0, int(current - active_updated)) if active_updated else None,
        "active_updated_at": active_updated or None,
        "active_issue_number": active_issue,
    }


def _service_state(service: str):
    env = os.environ.copy()
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    try:
        proc = subprocess.run(
            ["systemctl", "--user", "is-active", service],
            text=True,
            capture_output=True,
            timeout=1,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    text = (proc.stdout or "").strip().lower()
    if proc.returncode == 0 and text == "active":
        return "active"
    if text in {"inactive", "failed", "deactivating"}:
        return "failed"
    return "unknown"


def _evaluate_bridge_health(
    service_state: str,
    pending: dict | None,
    last_activity: float | None,
    current: float,
    *,
    pending_observable: bool = True,
):
    activity_age = max(0, int(current - last_activity)) if last_activity else None
    result = {
        "state": "unknown",
        "service_state": service_state,
        "progress_state": "unknown",
        "pending_count": None if pending is None else int(pending.get("count") or 0),
        "last_activity_age_seconds": activity_age,
        "stall_after_seconds": BRIDGE_STALL_SECONDS,
        "pending_observable": bool(pending_observable),
    }
    if service_state == "failed":
        result["state"] = "failed"
        result["progress_state"] = "inactive"
        return result
    if service_state != "active":
        return result

    if not pending_observable:
        result["state"] = "healthy"
        result["progress_state"] = "idle-or-external"
        return result

    if pending is None:
        result["progress_state"] = "evidence-unavailable"
        return result

    count = int(pending.get("count") or 0)
    if count == 0:
        result["state"] = "healthy"
        result["progress_state"] = "idle"
        return result

    oldest_age = pending.get("oldest_age_seconds")
    active_updated_at = pending.get("active_updated_at")
    result["oldest_pending_age_seconds"] = oldest_age

    if last_activity is None or not active_updated_at or last_activity < float(active_updated_at):
        if oldest_age is not None and oldest_age > BRIDGE_STALL_SECONDS:
            result["state"] = "failed"
            result["progress_state"] = "stalled"
        else:
            result["progress_state"] = "awaiting-evidence"
        return result

    if activity_age is not None and activity_age > BRIDGE_STALL_SECONDS:
        result["state"] = "failed"
        result["progress_state"] = "stalled"
    else:
        result["state"] = "healthy"
        result["progress_state"] = "running"
    return result


def _copy_bridge_health(value: dict) -> dict:
    return {key: dict(item) for key, item in value.items()}


def _bridge_health():
    current = time.time()
    with BRIDGE_CACHE_LOCK:
        cached_at = float(BRIDGE_CACHE.get("at") or 0.0)
        cached = BRIDGE_CACHE.get("value") or {}
        if cached and current - cached_at <= BRIDGE_CACHE_SECONDS:
            return _copy_bridge_health(cached)

        result = {}
        pending_by_repo = {}
        for key, (service, repo, required) in BRIDGE_SERVICES.items():
            # PROJECT_BYTE creates builder runs in its local SQLite database. The
            # independent VITROS verifier queue is GitHub-driven and has no
            # trustworthy PROJECT_BYTE pending-row mapping, so its service state
            # is required while pending verification progress remains explicitly
            # marked as externally observable rather than guessed.
            pending_observable = key != "vitros_verifier"
            if pending_observable and repo not in pending_by_repo:
                pending_by_repo[repo] = _pending_runs(repo, current)
            pending = pending_by_repo.get(repo) if pending_observable else {"count": 0}
            active_issue = int((pending or {}).get("active_issue_number") or 0)
            last_activity = _latest_bridge_activity(
                repo,
                issue_number=active_issue,
                verifier=(key == "vitros_verifier"),
            )
            item = _evaluate_bridge_health(
                _service_state(service),
                pending,
                last_activity,
                current,
                pending_observable=pending_observable,
            )
            item["required"] = bool(required)
            result[key] = item

        BRIDGE_CACHE["at"] = current
        BRIDGE_CACHE["value"] = result
        return _copy_bridge_health(result)


def _model_health():
    try:
        models = [m for m in app.model_rows() if m.get("enabled")]
    except Exception:
        return {
            "state": "unknown",
            "enabled": 0,
            "tested_ok": 0,
            "failed": 0,
            "unknown": 0,
            "freshness_window_seconds": MODEL_STATUS_FRESH_SECONDS,
        }
    current = int(time.time())
    tested_ok = 0
    failed = 0
    unknown = 0
    for model in models:
        status = (model.get("last_status") or "unknown").lower()
        try:
            updated_at = int(model.get("updated_at") or 0)
        except (TypeError, ValueError):
            updated_at = 0
        age = max(0, current - updated_at) if updated_at else None
        fresh = age is not None and age <= MODEL_STATUS_FRESH_SECONDS
        if not fresh or status in {"", "unknown"}:
            unknown += 1
        elif status == "ok":
            tested_ok += 1
        else:
            failed += 1
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
        "freshness_window_seconds": MODEL_STATUS_FRESH_SECONDS,
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
    required_bridges_ok = all(
        item.get("state") == "healthy"
        for item in bridges.values()
        if item.get("required")
    )
    execution_ok = bool(required_bridges_ok)
    chat_ready = models["state"] == "tested_ok"

    warnings = []
    for key, item in bridges.items():
        if not item.get("required") and item.get("state") != "healthy":
            warnings.append(f"{key}-optional-{item.get('state') or 'unknown'}")
    if not chat_ready:
        warnings.append(f"models-{models.get('state') or 'unknown'}")

    operational = bool(core_ok and execution_ok)
    if operational and warnings:
        status = "operational-with-warnings"
    elif operational:
        status = "healthy"
    elif core_ok:
        status = "core-healthy"
    else:
        status = "degraded"

    return {
        "ok": bool(core_ok),
        "operational": operational,
        "status": status,
        "version": 4,
        "ai_ops": bool(execution_ok),
        "ai_chat_ready": bool(chat_ready),
        "warnings": warnings,
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
