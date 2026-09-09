#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import threading
import time
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

import backend as app

# No account credentials or workspace data are rendered by this public OAuth return page.
SPOTIFY_CALLBACK_HTML = b'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="referrer" content="no-referrer"><title>Returning to PRFKT_PROJECT</title></head><body><p id="message">Returning to your workspace\xe2\x80\xa6</p><script>\ntry {\n const q=new URLSearchParams(location.search);\n const result={code:q.get(\'code\'),state:q.get(\'state\'),error:q.get(\'error\')};\n history.replaceState(null,\'\',\'/spotify-callback\');\n if(typeof result.state===\'string\' && result.state.length<=128 && (result.code===null || result.code.length<=2048) && (result.error===null || result.error.length<=128))sessionStorage.setItem(\'prfkt.spotify.callback\',JSON.stringify(result));\n location.replace(\'/#home\');\n} catch { document.getElementById(\'message\').textContent=\'Allow session storage, then reconnect Spotify from Home.\'; }\n</script></body></html>'

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
        "/api/approvals",
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
BRIDGE_ACTIVITY_SCAN_MAX_ENTRIES = 6000
BRIDGE_ACTIVITY_SKIP_DIRS = frozenset(
    {".git", ".build", ".venv", "__pycache__", "DerivedData", "dist", "node_modules"}
)
MODEL_STATUS_FRESH_SECONDS = 1800
EXECUTION_EVIDENCE_FRESH_SECONDS = 1800
EXECUTION_LOG_READ_BYTES = 32768
EXECUTOR_PROCESS_SCAN_MAX_ENTRIES = 8192
EXECUTOR_CMDLINE_READ_BYTES = 16384
EXECUTOR_MODEL_RE = re.compile(r"^[A-Za-z0-9._:/+\-]{1,120}$")
EXECUTOR_ROOT_NAMES = {
    "stickdeath": "worktrees",
    "vitros": "project-byte-live-builder",
    "vitros_verifier": "verifier-sandboxes",
}

BRIDGE_SERVICES = {
    "stickdeath": ("stickdeath-byte-opencode-bridge.service", "jmw7629/stickdeath-byte", True),
    "vitros": ("vitros-opencode-bridge.service", "jmw7629/vitros-web-dashboard", True),
    "vitros_verifier": ("vitros-opencode-verifier.service", "jmw7629/vitros-web-dashboard", True),
}

EXTERNAL_REVIEW_REPOS = (
    ("stickdeath", "STICKDEATH_BYTE", "jmw7629/stickdeath-byte"),
    ("vitros", "DASH_BYTE / VITROS", "jmw7629/vitros-web-dashboard"),
)
EXTERNAL_REVIEW_CACHE_LOCK = threading.Lock()
EXTERNAL_REVIEW_CACHE = {"repositories": {}, "at": 0.0}
EXTERNAL_REVIEW_CACHE_SECONDS = 30.0
EXTERNAL_REVIEW_STALE_SECONDS = 300.0
EXTERNAL_REVIEW_LIMIT = 12
EXTERNAL_REVIEW_TIMEOUT_SECONDS = 4
EXTERNAL_REVIEW_TITLE_MAX = 180
EXTERNAL_REVIEW_GH = "/usr/bin/gh"
EXTERNAL_REVIEW_DECISIONS = frozenset({"", "APPROVED", "CHANGES_REQUESTED", "REVIEW_REQUIRED"})
EXTERNAL_REVIEW_MERGE_STATES = frozenset({"", "BEHIND", "BLOCKED", "CLEAN", "DIRTY", "DRAFT", "HAS_HOOKS", "UNKNOWN", "UNSTABLE"})
EXTERNAL_REVIEW_CHECK_FAILURES = frozenset({"ACTION_REQUIRED", "CANCELLED", "FAILURE", "STALE", "TIMED_OUT"})

WORKSPACE_PROFILE_DEFAULTS = {
    "schema_version": 1,
    "display_name": "PRFKT_PROJECT",
    "assistant_name": "AI_BYTE",
    "owner_shortcuts": ["Joe", "Mike"],
}


def workspace_profile() -> dict:
    """Read presentation-only configuration; never identity or permissions."""
    defaults = json.loads(json.dumps(WORKSPACE_PROFILE_DEFAULTS))
    try:
        fd = os.open(app.ROOT / "workspace.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return defaults
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("workspace profile must be a regular file")
        raw = stream.read(16385)
    if len(raw) > 16384:
        raise ValueError("workspace profile is too large")
    data = json.loads(raw)
    if not isinstance(data, dict) or set(data) - set(defaults):
        raise ValueError("unsupported workspace profile fields")
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ValueError("unsupported workspace profile version")
    for name in ("display_name", "assistant_name"):
        value = data.get(name, defaults[name])
        if not isinstance(value, str) or not value.strip() or len(value) > 48 or any(ord(c) < 32 for c in value):
            raise ValueError("invalid workspace label")
        defaults[name] = value.strip()
    shortcuts = data.get("owner_shortcuts", defaults["owner_shortcuts"])
    if not isinstance(shortcuts, list) or len(shortcuts) > 6:
        raise ValueError("invalid owner shortcuts")
    if any(not isinstance(v, str) or not v.strip() or len(v) > 48 or any(ord(c) < 32 for c in v) for v in shortcuts):
        raise ValueError("invalid owner shortcut label")
    defaults["owner_shortcuts"] = list(dict.fromkeys(v.strip() for v in shortcuts))
    return defaults


def _external_review_check_summary(rollup) -> dict:
    summary = {"passed": 0, "pending": 0, "failed": 0, "unknown": 0}
    if not isinstance(rollup, list):
        summary["unknown"] = 1
        return summary
    # The repository response is already byte-bounded. Summarize every check so
    # a failure beyond an arbitrary display limit cannot become a green result.
    for raw in rollup:
        if not isinstance(raw, dict):
            summary["unknown"] += 1
            continue
        state = str(raw.get("state") or "").upper()
        status = str(raw.get("status") or "").upper()
        conclusion = str(raw.get("conclusion") or "").upper()
        if state:
            if state == "SUCCESS":
                summary["passed"] += 1
            elif state in {"EXPECTED", "PENDING"}:
                summary["pending"] += 1
            elif state in EXTERNAL_REVIEW_CHECK_FAILURES:
                summary["failed"] += 1
            else:
                summary["unknown"] += 1
        elif status and status != "COMPLETED":
            summary["pending"] += 1
        elif conclusion == "SUCCESS":
            summary["passed"] += 1
        elif conclusion in EXTERNAL_REVIEW_CHECK_FAILURES:
            summary["failed"] += 1
        else:
            summary["unknown"] += 1
    return summary


def _external_review_item(raw, *, key: str, project: str, repo: str, current: float):
    if not isinstance(raw, dict):
        return None
    number = raw.get("number")
    # GitHub emits integer IDs. Do not coerce booleans, strings or floats into
    # trusted pull-request URLs (including non-finite JSON numeric values).
    if type(number) is not int or number <= 0 or number > 2_147_483_647:
        return None
    title = app.sanitize(str(raw.get("title") or "")).strip()
    title = " ".join(title.split())[:EXTERNAL_REVIEW_TITLE_MAX]
    if not title:
        title = f"Pull request #{number}"
    review = str(raw.get("reviewDecision") or "").upper()
    if review not in EXTERNAL_REVIEW_DECISIONS:
        review = ""
    merge = str(raw.get("mergeStateStatus") or "").upper()
    if merge not in EXTERNAL_REVIEW_MERGE_STATES:
        merge = "UNKNOWN"
    updated_at = 0
    try:
        stamp = str(raw.get("updatedAt") or "")
        updated_at = int(datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()) if stamp else 0
    except (TypeError, ValueError, OverflowError):
        updated_at = 0
    age = max(0, int(current - updated_at)) if updated_at else None
    return {
        "source": "external-review",
        "key": key,
        "project": project,
        "repo": repo,
        "number": number,
        "title": title,
        "updated_at": updated_at or None,
        "updated_age_seconds": age,
        "draft": bool(raw.get("isDraft") is True),
        "review_decision": review,
        "merge_state": merge,
        "checks": _external_review_check_summary(raw.get("statusCheckRollup")),
        "url": f"https://github.com/{repo}/pull/{number}",
        "read_only": True,
    }


def _read_external_reviews_repo(key: str, project: str, repo: str, current: float):
    args = [
        EXTERNAL_REVIEW_GH, "pr", "list", "--repo", repo, "--state", "open", "--limit",
        str(EXTERNAL_REVIEW_LIMIT), "--json",
        "number,title,isDraft,updatedAt,reviewDecision,mergeStateStatus,statusCheckRollup",
    ]
    try:
        env = os.environ.copy()
        env["GH_PROMPT_DISABLED"] = "1"
        env["NO_COLOR"] = "1"
        proc = subprocess.run(args, text=True, capture_output=True, timeout=EXTERNAL_REVIEW_TIMEOUT_SECONDS, env=env)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or len(proc.stdout or "") > 256 * 1024:
        return None
    try:
        raw = json.loads(proc.stdout)
    except (ValueError, TypeError, RecursionError):
        return None
    if not isinstance(raw, list) or len(raw) > EXTERNAL_REVIEW_LIMIT:
        return None
    items, numbers = [], set()
    for entry in raw:
        item = _external_review_item(entry, key=key, project=project, repo=repo, current=current)
        if item is None or item["number"] in numbers:
            # Preserve stale evidence through the caller instead of presenting
            # malformed or incomplete output as a fresh empty review queue.
            return None
        numbers.add(item["number"])
        items.append(item)
    return {"state": "fresh", "age_seconds": 0, "items": items, "observed_at": int(current)}


def external_reviews_snapshot() -> dict:
    current = time.time()
    with EXTERNAL_REVIEW_CACHE_LOCK:
        cached = EXTERNAL_REVIEW_CACHE.get("repositories") or {}
        cached_at = float(EXTERNAL_REVIEW_CACHE.get("at") or 0.0)
        if cached and current - cached_at <= EXTERNAL_REVIEW_CACHE_SECONDS:
            repos = {name: dict(value) for name, value in cached.items()}
        else:
            repos = {}
            for key, project, repo in EXTERNAL_REVIEW_REPOS:
                fresh = _read_external_reviews_repo(key, project, repo, current)
                if fresh is not None:
                    repos[key] = fresh
                    continue
                prior = cached.get(key)
                prior_age = max(0, int(current - float((prior or {}).get("observed_at") or 0))) if prior else None
                if prior and prior_age is not None and prior_age <= EXTERNAL_REVIEW_STALE_SECONDS:
                    stale = dict(prior)
                    stale["state"] = "stale"
                    stale["age_seconds"] = prior_age
                    repos[key] = stale
                else:
                    repos[key] = {"state": "unavailable", "age_seconds": None, "items": [], "observed_at": None}
            EXTERNAL_REVIEW_CACHE["repositories"] = {name: dict(value) for name, value in repos.items()}
            EXTERNAL_REVIEW_CACHE["at"] = current
    items = []
    states = {}
    for key, _project, _repo in EXTERNAL_REVIEW_REPOS:
        value = repos.get(key) or {"state": "unavailable", "items": []}
        states[key] = {"state": value.get("state") or "unavailable", "age_seconds": value.get("age_seconds")}
        for item in value.get("items") or []:
            safe = dict(item)
            safe["evidence_state"] = states[key]["state"]
            safe["evidence_age_seconds"] = states[key]["age_seconds"]
            items.append(safe)
    overall = "fresh" if states and all(v["state"] == "fresh" for v in states.values()) else "stale" if any(v["state"] == "stale" for v in states.values()) else "partial" if any(v["state"] == "fresh" for v in states.values()) else "unavailable"
    return {"state": overall, "items": items, "repositories": states, "read_only": True}


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


def _latest_tree_activity(root: Path):
    try:
        latest = root.stat().st_mtime
    except OSError:
        return None, False

    stack = [root]
    seen = 0
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    if seen >= BRIDGE_ACTIVITY_SCAN_MAX_ENTRIES:
                        return latest, False
                    seen += 1
                    try:
                        stat = entry.stat(follow_symlinks=False)
                    except OSError:
                        return latest, False
                    latest = max(latest, stat.st_mtime)
                    try:
                        is_dir = entry.is_dir(follow_symlinks=False)
                    except OSError:
                        return latest, False
                    if is_dir and entry.name not in BRIDGE_ACTIVITY_SKIP_DIRS:
                        stack.append(Path(entry.path))
        except OSError:
            return latest, False
    return latest, True


def _processed_issue_activity(state_file: Path, issue_number: int):
    try:
        data = json.loads(state_file.read_text())
        record = (data.get("processed") or {}).get(str(issue_number)) or {}
        stamp = int(record.get("time") or 0)
        return float(stamp) if stamp > 0 else None
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _latest_bridge_activity(
    repo: str,
    *,
    issue_number: int = 0,
    verifier: bool = False,
):
    state_dir = app.BRIDGE_STATE.get(repo)
    if not state_dir:
        return None, False

    logs = state_dir / "logs"
    candidates: list[float] = []
    evidence_complete = True

    if verifier:
        paths = [state_dir / "verifier-processed.json"]
        try:
            if logs.is_dir():
                paths.extend(logs.glob("verify-*.log"))
        except OSError:
            return None, False
        try:
            candidates.extend(p.stat().st_mtime for p in paths if p.exists())
        except OSError:
            return None, False
    elif issue_number:
        issue_log = logs / f"issue-{issue_number}.log"
        try:
            if issue_log.exists():
                candidates.append(issue_log.stat().st_mtime)
        except OSError:
            return None, False

        issue_state_activity = _processed_issue_activity(
            state_dir / "processed.json", issue_number
        )
        if issue_state_activity is not None:
            candidates.append(issue_state_activity)

        worktree = (
            Path.home()
            / ".cache"
            / "joeos-opencode-bridge"
            / repo.replace("/", "__")
            / "worktrees"
            / f"issue-{issue_number}"
        )
        if worktree.exists():
            tree_activity, tree_complete = _latest_tree_activity(worktree)
            if tree_activity is not None:
                candidates.append(tree_activity)
            evidence_complete = evidence_complete and tree_complete
    else:
        paths = [state_dir / "processed.json"]
        try:
            if logs.is_dir():
                paths.extend(logs.glob("issue-*.log"))
        except OSError:
            return None, False
        try:
            candidates.extend(p.stat().st_mtime for p in paths if p.exists())
        except OSError:
            return None, False

    return (max(candidates) if candidates else None), evidence_complete


def _execution_failure_reason(text: str):
    lower = text.lower()
    if "insufficient balance" in lower or "creditserror" in lower:
        return "provider-balance", "provider balance unavailable"
    if re.search(r"(?:statuscode|status)[^\n]{0,24}429|rate[ -]?limit|too many requests", lower):
        return "provider-rate-limit", "provider rate limit reached"
    if re.search(r"(?:statuscode|status)[^\n]{0,24}40[13]|unauthori[sz]ed|authentication failed|invalid api key", lower):
        return "provider-auth", "provider authentication unavailable"
    if "timeout_type=idle" in lower or ("watchdog_success=false" in lower and "timeout_type=" in lower):
        return "executor-timeout", "executor watchdog timeout"
    if "model not found" in lower or "unknown model" in lower:
        return "model-unavailable", "selected model unavailable"
    classification = re.search(r"(?m)^classification=([A-Za-z0-9_-]{1,60})\s*$", text)
    if classification and classification.group(1):
        return "executor-" + classification.group(1).lower(), "executor reported a classified failure"
    return "execution-failed", "executor exited before completing work"


def _execution_readiness(repo: str, current: float, *, verifier: bool = False):
    result = {
        "state": "unknown",
        "reason_code": "no-evidence",
        "reason": "no recent completed execution evidence",
        "last_execution_age_seconds": None,
        "last_run_ref": "",
        "freshness_window_seconds": EXECUTION_EVIDENCE_FRESH_SECONDS,
    }
    state_dir = app.BRIDGE_STATE.get(repo)
    if not state_dir:
        return result
    logs = state_dir / "logs"
    try:
        entries = []
        with os.scandir(logs) as scan:
            for index, entry in enumerate(scan):
                if index >= BRIDGE_ACTIVITY_SCAN_MAX_ENTRIES:
                    result["reason_code"] = "evidence-incomplete"
                    result["reason"] = "execution evidence scan incomplete"
                    return result
                name = entry.name
                if verifier:
                    match = name.startswith("verify-") and name.endswith(".log")
                else:
                    match = name.startswith("issue-") and name.endswith(".log")
                if not match or not entry.is_file(follow_symlinks=False):
                    continue
                stat = entry.stat(follow_symlinks=False)
                entries.append((stat.st_mtime, Path(entry.path), name[:-4]))
    except OSError:
        result["reason_code"] = "evidence-unavailable"
        result["reason"] = "execution evidence unavailable"
        return result
    if not entries:
        return result
    modified, path, run_ref = max(entries, key=lambda item: item[0])
    age = max(0, int(current - modified))
    result["last_execution_age_seconds"] = age
    result["last_run_ref"] = run_ref
    if age > EXECUTION_EVIDENCE_FRESH_SECONDS:
        result["reason_code"] = "stale-evidence"
        result["reason"] = "no recent completed execution evidence"
        return result
    try:
        with path.open("rb") as stream:
            raw = stream.read(EXECUTION_LOG_READ_BYTES)
        text = raw.decode("utf-8", errors="replace")
    except OSError:
        result["reason_code"] = "evidence-unavailable"
        result["reason"] = "execution evidence unavailable"
        return result
    exit_match = re.search(r"(?m)^exit=(-?\d+)\s*$", text)
    if not exit_match:
        result["reason_code"] = "incomplete-evidence"
        result["reason"] = "latest execution has no completed result yet"
        return result
    if int(exit_match.group(1)) == 0:
        result["state"] = "ready"
        result["reason_code"] = "success"
        result["reason"] = "last execution completed successfully"
        return result
    code, reason = _execution_failure_reason(text)
    result["state"] = "blocked"
    result["reason_code"] = code
    result["reason"] = reason
    return result


def _executor_process_root(repo: str, *, boundary: str, cache_root: Path | None = None):
    root_name = EXECUTOR_ROOT_NAMES.get(boundary)
    if not root_name:
        return None
    base = cache_root or (Path.home() / ".cache" / "joeos-opencode-bridge")
    repo_root = base / repo.replace("/", "__")
    return repo_root / root_name


def _read_proc_uid(status_path: Path):
    try:
        with status_path.open("rb") as stream:
            raw = stream.read(4096)
    except FileNotFoundError:
        return None, True
    except OSError:
        return None, False
    for line in raw.splitlines():
        if line.startswith(b"Uid:"):
            fields = line.split()
            if len(fields) >= 2:
                try:
                    return int(fields[1]), True
                except ValueError:
                    return None, False
    return None, False


def _read_proc_tokens(cmdline_path: Path):
    try:
        with cmdline_path.open("rb") as stream:
            raw = stream.read(EXECUTOR_CMDLINE_READ_BYTES)
    except FileNotFoundError:
        return None, True
    except OSError:
        return None, False
    if not raw:
        return [], True
    pieces = raw.split(b"\0")
    # A truncated final token can contain an arbitrarily long prompt. Discard it.
    if raw[-1:] != b"\0":
        pieces = pieces[:-1]
    tokens = []
    for piece in pieces[:64]:
        if not piece or len(piece) > 512:
            continue
        try:
            tokens.append(piece.decode("utf-8", errors="strict"))
        except UnicodeDecodeError:
            continue
    return tokens, True


def _process_elapsed_seconds(pid_root: Path, proc_root: Path):
    try:
        stat = (pid_root / "stat").read_text(errors="strict")
        fields = stat[stat.rfind(")") + 2 :].split()
        started_ticks = int(fields[19])
        uptime = float((proc_root / "uptime").read_text().split()[0])
        hz = os.sysconf("SC_CLK_TCK")
        return max(0, int(uptime - (started_ticks / hz)))
    except (OSError, ValueError, IndexError):
        return None


def _active_executor(
    repo: str,
    *,
    boundary: str,
    proc_root: Path | None = None,
    cache_root: Path | None = None,
):
    result = {
        "running": False,
        "count": 0,
        "active_run_ref": "",
        "active_issue_number": 0,
        "active_model": "",
        "elapsed_seconds": None,
        "evidence_complete": True,
    }
    proc = proc_root or Path("/proc")
    expected = _executor_process_root(repo, boundary=boundary, cache_root=cache_root)
    if expected is None:
        result["evidence_complete"] = False
        return result
    verifier = boundary == "vitros_verifier"
    try:
        expected = expected.resolve(strict=True)
    except FileNotFoundError:
        return result
    except OSError:
        result["evidence_complete"] = False
        return result
    try:
        with os.scandir(proc) as scan:
            entries = [entry for entry in scan if entry.name.isdigit()]
    except OSError:
        result["evidence_complete"] = False
        return result
    if len(entries) > EXECUTOR_PROCESS_SCAN_MAX_ENTRIES:
        result["evidence_complete"] = False
        entries = entries[:EXECUTOR_PROCESS_SCAN_MAX_ENTRIES]
    matches = []
    uid = os.getuid()
    for entry in sorted(entries, key=lambda item: int(item.name)):
        pid_root = Path(entry.path)
        process_uid, uid_complete = _read_proc_uid(pid_root / "status")
        if not uid_complete:
            result["evidence_complete"] = False
            continue
        if process_uid is None or process_uid != uid:
            continue
        tokens, cmd_complete = _read_proc_tokens(pid_root / "cmdline")
        if not cmd_complete:
            result["evidence_complete"] = False
            continue
        if not tokens or Path(tokens[0]).name != "opencode" or "run" not in tokens[:6]:
            continue
        run_dir = ""
        model = ""
        for index, token in enumerate(tokens):
            if token == "--dir" and index + 1 < len(tokens):
                run_dir = tokens[index + 1]
            elif token.startswith("--dir="):
                run_dir = token.split("=", 1)[1]
            elif token == "--model" and index + 1 < len(tokens):
                model = tokens[index + 1]
            elif token.startswith("--model="):
                model = token.split("=", 1)[1]
        if not run_dir:
            continue
        try:
            candidate = Path(run_dir).resolve(strict=True)
        except OSError:
            continue
        if candidate.parent != expected:
            continue
        if verifier:
            match = re.fullmatch(r"verify-(\d+)-([0-9A-Fa-f]{7,64})", candidate.name)
        else:
            match = re.fullmatch(r"issue-(\d+)", candidate.name)
        if not match:
            continue
        if model and not EXECUTOR_MODEL_RE.fullmatch(model):
            model = ""
        matches.append(
            {
                "pid": int(entry.name),
                "active_run_ref": candidate.name,
                "active_issue_number": int(match.group(1)),
                "active_model": model,
                "elapsed_seconds": _process_elapsed_seconds(pid_root, proc),
            }
        )
    if matches:
        first = min(matches, key=lambda item: item["pid"])
        result.update(
            running=True,
            count=len(matches),
            active_run_ref=first["active_run_ref"],
            active_issue_number=first["active_issue_number"],
            active_model=first["active_model"],
            elapsed_seconds=first["elapsed_seconds"],
        )
    return result


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
    activity_evidence_complete: bool = True,
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
        "progress_evidence_complete": bool(activity_evidence_complete),
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

    evidence_fresh = (
        last_activity is not None
        and active_updated_at
        and last_activity >= float(active_updated_at)
        and activity_age is not None
        and activity_age <= BRIDGE_STALL_SECONDS
    )
    if evidence_fresh:
        result["state"] = "healthy"
        result["progress_state"] = "running"
        return result

    if not activity_evidence_complete:
        result["progress_state"] = "evidence-incomplete"
        return result

    if oldest_age is not None and oldest_age > BRIDGE_STALL_SECONDS:
        result["state"] = "failed"
        result["progress_state"] = "stalled"
    else:
        result["progress_state"] = "awaiting-evidence"
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
            executor = _active_executor(repo, boundary=key)
            active_issue = int((pending or {}).get("active_issue_number") or executor.get("active_issue_number") or 0)
            last_activity, activity_evidence_complete = _latest_bridge_activity(
                repo,
                issue_number=active_issue,
                verifier=(key == "vitros_verifier"),
            )
            service_state = _service_state(service)
            item = _evaluate_bridge_health(
                service_state,
                pending,
                last_activity,
                current,
                pending_observable=pending_observable,
                activity_evidence_complete=activity_evidence_complete,
            )
            if service_state == "active" and executor.get("running"):
                item["state"] = "healthy"
                item["progress_state"] = "running"
            elif (
                service_state == "active"
                and not executor.get("evidence_complete")
                and item.get("progress_state") in {"idle", "idle-or-external"}
            ):
                item["state"] = "unknown"
                item["progress_state"] = "evidence-incomplete"
            external_running = bool(executor.get("running"))
            item["executor_evidence_complete"] = bool(executor.get("evidence_complete"))
            item["active_executor_count"] = int(executor.get("count") or 0)
            item["active_run_ref"] = executor.get("active_run_ref") or ""
            # Provider/model strings from external process command lines are intentionally not exposed.
            item["active_model"] = ""
            item["execution_source"] = (
                "external-bridge" if external_running
                else "project-byte" if int(item.get("pending_count") or 0) > 0
                else ""
            )
            item["external_running"] = external_running
            item["external_executor_count"] = int(executor.get("count") or 0)
            item["external_run_ref"] = executor.get("active_run_ref") or ""
            item["external_issue_number"] = int(executor.get("active_issue_number") or 0)
            item["external_elapsed_seconds"] = executor.get("elapsed_seconds")
            item["external_activity_age_seconds"] = item.get("last_activity_age_seconds")
            item["external_evidence_complete"] = bool(executor.get("evidence_complete"))
            execution = _execution_readiness(
                repo, current, verifier=(key == "vitros_verifier")
            )
            item["execution_state"] = execution["state"]
            item["execution_reason_code"] = execution["reason_code"]
            item["execution_reason"] = execution["reason"]
            item["last_execution_age_seconds"] = execution["last_execution_age_seconds"]
            item["last_run_ref"] = execution["last_run_ref"]
            item["execution_freshness_seconds"] = execution["freshness_window_seconds"]
            # Current executor presence and historical completion evidence are separate facts.
            # A positive allowlisted executor match proves activity even when the broader
            # process scan is partial; scan completeness only limits absence conclusions.
            if external_running:
                item["execution_state"] = "running"
                item["execution_reason_code"] = "active-executor"
                item["execution_reason"] = "active external executor observed; completion pending"
            hold = app.execution_hold(repo)
            item["owner_paused"] = bool(hold and hold["reason_code"] == "owner-paused")
            if hold:
                # An owner hold blocks dispatch, even when a past run succeeded.
                # Keep observed activity visible; never claim a running child stopped.
                item["execution_state"] = "blocked"
                item["execution_reason_code"] = hold["reason_code"]
                item["execution_reason"] = hold["reason"]
                if item["owner_paused"]:
                    item["state"] = "failed" if external_running else "paused"
                    item["progress_state"] = "running-despite-owner-hold" if external_running else "paused-by-owner"
                else:
                    item["state"] = "unknown"
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
    required_execution_ready = all(
        item.get("execution_state") == "ready"
        for item in bridges.values()
        if item.get("required")
    )
    execution_ok = bool(required_bridges_ok and required_execution_ready)
    chat_ready = models["state"] == "tested_ok"

    warnings = []
    for key, item in bridges.items():
        if not item.get("required") and item.get("state") != "healthy":
            warnings.append(f"{key}-optional-{item.get('state') or 'unknown'}")
        if item.get("required") and item.get("execution_state") != "ready":
            warnings.append(f"{key}-owner-paused" if item.get("owner_paused") else f"{key}-execution-{item.get('execution_state') or 'unknown'}")
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


# Native observation adapter. No arbitrary paths, hooks, shell execution or writes.
OBS_REPOS = (("stickdeath", "STICKDEATH_BYTE", "jmw7629/stickdeath-byte"), ("vitros", "DASH_BYTE / VITROS", "jmw7629/vitros-web-dashboard"))
OBS_CACHE = {"at": 0.0, "value": None}
OBS_OWNER_CACHE = {"at": 0.0, "value": None}
OBS_LOCK = threading.Lock()
OBS_LOG_BYTES = 262144
OBS_LOG_LIMIT = 6
OBS_SCAN_LIMIT = 1000
OBS_EVENT_LIMIT = 320
OBS_EVIDENCE_FIELD_CHARS = 8192
OBS_EVIDENCE_EVENT_CHARS = 24576
OBS_LOG_RE = re.compile(r"^(?:issue-\d+|verify-\d+-[a-f0-9]{6,64})\.log$")
OBS_SECRET_KEY_RE = re.compile(r"(?i)(?:password|passwd|secret|api.?key|token|authorization|cookie|credential|private.?key|access.?key|connection.?string)")
OBS_HIDDEN_KINDS = frozenset({"analysis", "reasoning", "thinking", "chain_of_thought", "reasoning_text", "redacted_thinking"})


def _obs_id(*parts):
    import hashlib
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:20]


def _obs_label(value, maximum=100):
    text = _obs_redact_text(str(value or ""))
    return re.sub(r"[\x00-\x1f\x7f]", " ", text)[:maximum]


def _obs_redact_text(value, known_secrets=()):
    # Inspect only already-read evidence. Never discover secrets from other files or the environment.
    text = value
    for secret in sorted(known_secrets, key=len, reverse=True):
        text = text.replace(secret, "[redacted]")
    text = re.sub(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", "[redacted private key]", text)
    text = re.sub(r"(?i)(?:sk-|gh[pousr]_|github_pat_|pb_|xox[baprs]-)[A-Za-z0-9_-]{8,}", "[redacted]", text)
    text = re.sub(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b", "[redacted]", text)
    text = re.sub(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b", "[redacted]", text)
    text = re.sub(r"(?i)\b(?:Bearer|Basic)\s+[A-Za-z0-9_+/=.:-]+", "[redacted authorization]", text)
    text = re.sub(r"(?im)\b(?:set-cookie|cookie|authorization|proxy-authorization)\s*:\s*[^\r\n]+", "[redacted header]", text)
    text = re.sub(r"(?i)([a-z][a-z0-9+.-]*://)[^\s/@:]+:[^\s/@]+@", r"\1[redacted]@", text)
    # Consume complete shell words, including quoted/escaped segments, before display truncation.
    shell_word = r'''(?:"(?:\\[^\r\n]|[^"\\\r\n])*"|'[^'\r\n]*'|\\[^\r\n]|[^\s;&|<>\\'"])+'''
    credential_option = r'''(?<![\w.-])(?:(?:"--(?i:cookie|user|proxy-user)"|'--(?i:cookie|user|proxy-user)'|--(?i:cookie|user|proxy-user))(?:\s*=\s*|\s+)|(?:"-[buU]"|'-[buU]'|-[buU])(?:\s*=\s*|\s+|(?=[^\s])))'''
    text = re.sub(credential_option+shell_word, "[redacted credential option]", text)
    # Quoted JSON/shell assignments, environment assignments, query values and command flags.
    text = re.sub(r'''(?ix)(?<![\w.-])(?:["']?[\w.-]*(?:password|passwd|secret|api[_-]?key|access[_-]?key|token|credential|private[_-]?key|cookie|authorization)[\w.-]*["']?\s*[=:]\s*|--(?:password|passwd|secret|api[_-]?key|token)\s+)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s&,;]+)''', "[redacted credential]", text)
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)


def _obs_known_secrets(value, depth=0):
    if depth > 12:
        return set()
    found = set()
    if isinstance(value, dict):
        for key, item in list(value.items())[:120]:
            if OBS_SECRET_KEY_RE.search(str(key)) and isinstance(item, str) and len(item) >= 4:
                found.add(item)
            else:
                found.update(_obs_known_secrets(item, depth+1))
    elif isinstance(value, list):
        for item in value[:120]:
            found.update(_obs_known_secrets(item, depth+1))
    return found


def _obs_evidence_value(value, source, budget, known_secrets):
    if value is None:
        return None
    structured = not isinstance(value, str)
    original = json.dumps(value, ensure_ascii=False, default=str) if structured else value
    clipped = False
    def clean(item, depth=0):
        nonlocal clipped
        if depth > 12:
            clipped = True
            return "[depth limit]"
        if isinstance(item, dict):
            if (any(str(item.get(field, "")).lower() in OBS_HIDDEN_KINDS for field in ("type", "channel", "phase"))
                    or item.get("hidden") is True or item.get("visibility") in {"hidden", "internal"}):
                return "[not visible]"
            clipped |= len(item) > 120
            return {_obs_redact_text(str(key), known_secrets):
                    ("[not visible]" if str(key).lower() in OBS_HIDDEN_KINDS or key == "encrypted_content" else
                     "[redacted]" if OBS_SECRET_KEY_RE.search(str(key)) else clean(child, depth+1))
                    for key, child in list(item.items())[:120]}
        if isinstance(item, list):
            clipped |= len(item) > 120
            return [clean(child, depth+1) for child in item[:120]
                    if not isinstance(child, dict) or str(child.get("type", "")).lower() not in OBS_HIDDEN_KINDS]
        return _obs_redact_text(item, known_secrets) if isinstance(item, str) else item
    cleaned = clean(value)
    result = json.dumps(cleaned, ensure_ascii=False, indent=2, default=str) if structured else cleaned
    limit = max(0, min(OBS_EVIDENCE_FIELD_CHARS, budget[0]))
    budget[0] -= min(len(result), limit)
    return {"text": result[:limit], "format": "json" if structured else "text", "source": source,
            "recorded_chars": len(original), "truncated": bool(clipped or len(result) > limit),
            "redacted": cleaned != value}


def _obs_visible_text(raw, part, depth=0):
    if depth > 1:
        return None, ""
    for container in (raw, part):
        if (any(str(container.get(field, "")).lower() in OBS_HIDDEN_KINDS for field in ("type", "channel", "phase"))
                or container.get("hidden") is True or container.get("visibility") in {"hidden", "internal"}
                or container.get("role") not in (None, "", "user", "assistant", "tool")):
            return None, ""
    for container, source in ((part, "part"), (raw, "event")):
        if isinstance(container.get("text"), str):
            return container["text"], source+".text"
        content = container.get("content")
        if isinstance(content, str):
            return content, source+".content"
        if isinstance(content, list):
            texts = [item["text"] for item in content if isinstance(item, dict)
                     and item.get("type") in {"text", "input_text", "output_text"} and isinstance(item.get("text"), str)
                     and not any(str(item.get(field, "")).lower() in OBS_HIDDEN_KINDS for field in ("channel", "phase"))
                     and item.get("hidden") is not True and item.get("visibility") not in {"hidden", "internal"}]
            if texts:
                return "\n".join(texts), source+".content[text]"
    message = raw.get("message")
    if isinstance(message, dict):
        value, source = _obs_visible_text(message, {}, depth+1)
        return value, source.replace("event", "event.message", 1)
    return None, ""


def _obs_evidence(raw, part, state, inputs, kind, role):
    known = _obs_known_secrets(raw)
    budget = [OBS_EVIDENCE_EVENT_CHARS]
    text, text_source = _obs_visible_text(raw, part) if kind in {"text", "message", "prompt", "user_message", "assistant_message"} else (None, "")
    prompt = inputs.get("prompt") if isinstance(inputs, dict) else None
    prompt_source = "part.state.input.prompt"
    if kind == "prompt":
        prompt, prompt_source = raw.get("prompt", text), "event.prompt" if "prompt" in raw else text_source
    elif role == "user" and text is not None:
        prompt, prompt_source = text, text_source
    return {"prompt": _obs_evidence_value(prompt, prompt_source, budget, known),
            "arguments": _obs_evidence_value(state.get("input"), "part.state.input", budget, known) if kind == "tool_use" else None,
            "response": _obs_evidence_value(state.get("output"), "part.state.output", budget, known) if kind == "tool_use" else None,
            "message": _obs_evidence_value(text, text_source, budget, known) if role != "user" and kind != "prompt" else None,
            "error": _obs_evidence_value(state.get("error", raw.get("error")), "part.state.error" if "error" in state else "event.error", budget, known)}


def _obs_summary(event):
    kind, tool, status = event["kind"], event["tool"], event["status"]
    if kind == "tool_use":
        description = f"{tool or 'Tool'}: {status}."
    else:
        description = {"step_start": "Model step started.", "step_finish": "Model step finished.",
                       "compaction": "Context compaction was recorded.", "error": "An error was recorded.",
                       "prompt": "User prompt was recorded.", "user_message": "User message was recorded.",
                       "assistant_message": "Assistant update was recorded."}.get(kind, "Visible message was recorded.")
    evidence = event.get("evidence") or {}
    for field in ("message", "prompt", "error"):
        if evidence.get(field) and evidence[field]["text"]:
            return description+" "+_obs_label(evidence[field]["text"], 220)
    if kind == "tool_use" and evidence.get("arguments"):
        try:
            args = json.loads(evidence["arguments"]["text"])
            if isinstance(args, dict):
                value = next((args[key] for key in ("description", "command", "filePath", "path", "query", "url") if isinstance(args.get(key), str)), None)
                if value:
                    return description+" "+_obs_label(value, 220)
        except (TypeError, ValueError):
            pass
    return description


def _obs_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if 0 <= value <= 1e15 else None


def _obs_time(value):
    number = _obs_number(value)
    return number / 1000 if number is not None and number > 1e11 else number


def _obs_event(raw, trace_id, ordinal, include_evidence=False):
    part = raw.get("part") if isinstance(raw.get("part"), dict) else {}
    kind = str(raw.get("type") or part.get("type") or "")
    if kind not in {"tool_use", "step_start", "step_finish", "text", "message", "prompt", "user_message", "assistant_message", "error", "compaction"}:
        return None
    message = raw.get("message") if isinstance(raw.get("message"), dict) else {}
    for container in (raw, part, message):
        if (str(container.get("type", "")).lower() in OBS_HIDDEN_KINDS
                or str(container.get("channel", "")).lower() in OBS_HIDDEN_KINDS
                or str(container.get("phase", "")).lower() in OBS_HIDDEN_KINDS
                or container.get("hidden") is True or container.get("visibility") in {"hidden", "internal"}):
            return None
    role = str(raw.get("role") or part.get("role") or message.get("role") or "")
    if role and role not in {"user", "assistant", "tool"}:
        return None
    if not role and kind in {"text", "assistant_message"}:
        role = "assistant"
    elif kind in {"user_message", "prompt"}:
        role = "user"
    state = part.get("state") if isinstance(part.get("state"), dict) else {}
    timing = state.get("time") if isinstance(state.get("time"), dict) else part.get("time") if isinstance(part.get("time"), dict) else {}
    recorded_start = _obs_time(timing.get("start"))
    start = recorded_start if recorded_start is not None else _obs_time(raw.get("timestamp"))
    end = _obs_time(timing.get("end"))
    status = state.get("status") if state.get("status") in {"pending", "running", "completed", "error"} else "recorded"
    tool = _obs_label(part.get("tool"), 60) if kind == "tool_use" else kind.replace("_", " ")
    inputs = state.get("input") if isinstance(state.get("input"), dict) else {}
    metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
    tokens = part.get("tokens") if isinstance(part.get("tokens"), dict) else {}
    measured = {k: _obs_number(tokens.get(k)) for k in ("input", "output", "reasoning", "total")}
    cache_tokens = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
    measured.update({"cache_read": _obs_number(cache_tokens.get("read")), "cache_write": _obs_number(cache_tokens.get("write"))})
    sid = _obs_id(trace_id, raw.get("sessionID") or part.get("sessionID") or "default")
    child = metadata.get("sessionId") or metadata.get("sessionID")
    event_id = _obs_id(trace_id, part.get("id") or part.get("callID") or json.dumps(raw, sort_keys=True))
    event = {"id": event_id, "session": sid, "kind": kind, "tool": tool, "status": status, "message_role": role or None,
            "time": start, "end": end, "duration_ms": round((end-recorded_start)*1000, 2) if recorded_start is not None and end is not None and end >= recorded_start else None,
            "tokens": measured, "cost": _obs_number(part.get("cost")),
            "argument_fields": [_obs_label(k, 50) for k in list(inputs)[:20]],
            "response_chars": len(state["output"]) if isinstance(state.get("output"), str) else None,
            "error_recorded": bool(state.get("error")) or kind == "error",
            "delegate": _obs_label(inputs.get("subagent_type"), 60) if tool == "task" else "",
            "child_session": _obs_id(trace_id, child) if isinstance(child, str) and child else "",
            "evidence": _obs_evidence(raw, part, state, inputs, kind, role) if include_evidence else None,
            "payload_policy": "Owner evidence: recorded visible content, with credential redaction and explicit length limits; hidden reasoning is excluded." if include_evidence else "Owner access is required for recorded prompts, arguments, responses and visible messages."}
    event["summary"] = _obs_summary(event)
    return event


def _obs_parse_log(path, repo, project, record=None, include_evidence=False):
    import stat
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("not a regular log")
        offset = max(0, info.st_size - OBS_LOG_BYTES)
        handle.seek(offset)
        data = handle.read(OBS_LOG_BYTES)
    lines = data.splitlines()
    if offset and lines:
        lines = lines[1:]
    trace_id = _obs_id(repo, path.name)
    events, seen, skipped = [], {}, 0
    for ordinal, line in enumerate(lines):
        if len(line) > 131072:
            skipped += 1
            continue
        try:
            raw = json.loads(line)
            event = _obs_event(raw, trace_id, ordinal, include_evidence) if isinstance(raw, dict) else None
        except (ValueError, TypeError, RecursionError, OverflowError):
            if line.lstrip().startswith(b"{"):
                skipped += 1
            continue
        if event:
            if event["id"] in seen:
                events[seen[event["id"]]] = event
            else:
                seen[event["id"]] = len(events); events.append(event)
    clipped = len(events) > OBS_EVENT_LIMIT
    events = events[-OBS_EVENT_LIMIT:]
    stamps = [e["time"] for e in events if e["time"] is not None]
    ends = [e["end"] if e["end"] is not None else e["time"] for e in events if e["end"] is not None or e["time"] is not None]
    total_values = [e["tokens"]["total"] for e in events if e["kind"] == "step_finish" and e["tokens"]["total"] is not None]
    tools = [e for e in events if e["kind"] == "tool_use"]
    status = str((record or {}).get("status") or "historical")
    if status not in {"pr-created", "opencode-failed", "bridge-error", "no-changes", "diff-check-failed", "historical"}:
        status = "historical"
    return {"id": trace_id, "label": path.stem, "project": project, "repo": repo,
            "role": "verifier" if path.name.startswith("verify-") else "builder", "status": status,
            "updated_at": info.st_mtime, "started_at": min(stamps) if stamps else None,
            "ended_at": max(ends) if ends else None, "tokens": sum(total_values) if total_values else None,
            "tool_count": len(tools), "event_count": len(events), "events": events,
            "partial": bool(offset or clipped or skipped), "skipped_records": skipped,
            "source": "bridge-log", "owner": "", "task_id": ""}


def _obs_processed(state_dir):
    path = state_dir / "processed.json"
    if path.is_symlink():
        return {}
    try:
        import stat
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                return {}
            data = handle.read(1048577)
        if len(data) > 1048576:
            return {}
        raw = json.loads(data)
        return raw.get("processed", {}) if isinstance(raw, dict) else {}
    except (OSError, ValueError, RecursionError):
        return {}


def _obs_build_snapshot(include_evidence=False):
    traces, sources = [], []
    for key, project, repo in OBS_REPOS:
        root = app.BRIDGE_STATE.get(repo)
        source = {"project": project, "repo": repo, "state": "unavailable", "files_scanned": 0, "partial": False}
        sources.append(source)
        if not root:
            continue
        root = Path(root); logs = root / "logs"
        if root.is_symlink() or logs.is_symlink():
            continue
        records = _obs_processed(root)
        candidates = []
        try:
            with os.scandir(logs) as listing:
                for count, entry in enumerate(listing):
                    if count >= OBS_SCAN_LIMIT:
                        source["partial"] = True; break
                    if OBS_LOG_RE.fullmatch(entry.name) and entry.is_file(follow_symlinks=False):
                        candidates.append((entry.stat(follow_symlinks=False).st_mtime, Path(entry.path)))
            candidates.sort(reverse=True)
            source["files_scanned"] = len(candidates)
            source["partial"] |= len(candidates) > OBS_LOG_LIMIT
            for _, path in candidates[:OBS_LOG_LIMIT]:
                number = path.stem.split("-")[1]
                record = records.get(number) if path.name.startswith("issue-") else None
                traces.append(_obs_parse_log(path, repo, project, record if isinstance(record, dict) else None, include_evidence))
            source["state"] = "available"
        except (OSError, ValueError, TypeError, RecursionError) as exc:
            source["state"] = "unavailable"; source["error_type"] = type(exc).__name__
    try:
        work = {t["id"]: t for t in app.task_rows()}
        for run in app.run_rows():
            match = next((t for t in traces if t["repo"] == run["repo"] and t["label"] == "issue-"+str(run["issue_number"])), None)
            if match:
                task = work.get(run["task_id"], {})
                match["owner"] = _obs_label(task.get("owner"), 80)
                match["task_id"] = _obs_label(run["task_id"], 100)
                match["project"] = _obs_label(run["project"], 100)
    except Exception:
        sources.append({"project": "Workspace associations", "state": "unavailable", "partial": True})
    traces.sort(key=lambda item: item["updated_at"], reverse=True)
    return {"version": 1, "observed_at": int(time.time()), "traces": traces, "sources": sources,
            "state": "available" if all(s["state"] == "available" for s in sources) else "partial",
            "limits": {"logs_per_repository": OBS_LOG_LIMIT, "bytes_per_log": OBS_LOG_BYTES, "events_per_log": OBS_EVENT_LIMIT,
                       "evidence_chars_per_field": OBS_EVIDENCE_FIELD_CHARS, "evidence_chars_per_event": OBS_EVIDENCE_EVENT_CHARS},
            "capabilities": {"read_only": True, "execution_permissions": False, "token_metrics": "recorded counts only; totals are not inferred",
                             "owner_evidence": include_evidence, "hidden_reasoning": False,
                             "payloads": "owner redacted recorded content" if include_evidence else "metadata only"}}


def observatory_snapshot(actor=None):
    include_evidence = bool(isinstance(actor, dict) and actor.get("role") == "owner" and actor.get("level", 0) >= 4)
    cache = OBS_OWNER_CACHE if include_evidence else OBS_CACHE
    with OBS_LOCK:
        if cache["value"] is None or time.monotonic()-cache["at"] > 15:
            cache["value"] = _obs_build_snapshot(include_evidence); cache["at"] = time.monotonic()
        return cache["value"]


def permission_gateway():
    # Lazy loading keeps the optional runner boundary out of ordinary page/health reads.
    import importlib.util
    spec = importlib.util.spec_from_file_location("project_byte_permissions", Path(__file__).with_name("execution_permissions.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Gateway(app.ROOT, hold=app.execution_hold), module


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

    def log_message(self, fmt, *args):
        if urlparse(self.path).path == '/spotify-callback':
            return  # OAuth authorization codes must never enter request logs.
        return super().log_message(fmt, *args)

    def _spotify_callback(self, head=False):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(SPOTIFY_CALLBACK_HTML)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        if not head:
            self.wfile.write(SPOTIFY_CALLBACK_HTML)

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        if path == '/spotify-callback':
            return self._spotify_callback()
        if path == "/api/execution-permissions":
            if not self.need(4):
                return
            return self._execution_permissions()
        if path == "/api/workspace-profile":
            try:
                return self.sendj({"profile": workspace_profile()})
            except (OSError, ValueError, TypeError, RecursionError):
                return self.sendj({"error": "Workspace presentation configuration is unavailable"}, 503)
        if path == "/healthz":
            return self.sendj(health_snapshot())
        if path == "/api/observatory":
            actor = self.need(2)
            if not actor:
                return
            return self.sendj(observatory_snapshot(actor))
        if path == "/api/external-reviews":
            if not self.need(1):
                return
            return self.sendj(external_reviews_snapshot())
        if path in PUBLIC_FILES:
            return self._public_file(path)
        if path.startswith("/uploads/"):
            return self._attachment_file(path)
        if path in GET_API_PATHS or RUN_TERMINAL_RE.fullmatch(path):
            return app.H.do_GET(self)
        return self._not_found()

    def _execution_permissions(self, decision=None, actor=None):
        try:
            gateway, module = permission_gateway()
            try:
                result = gateway.snapshot() if decision is None else gateway.decide(decision, actor)
                return self.sendj(result)
            except module.PermissionError as error:
                return self.sendj({"error": str(error), "state": "unavailable", "requests": [],
                                   "history": getattr(error, "history", [])}, error.status)
        except Exception:
            # No configuration, credentials, tool output, or SQLite paths in errors.
            return self.sendj({"error": "Execution permissions are unavailable", "state": "unavailable", "requests": []}, 503)

    def do_POST(self):
        if unquote(urlparse(self.path).path) != "/api/execution-permissions/decision":
            return app.H.do_POST(self)
        actor = self.need(4)
        if not actor:
            return
        # The existing explicit access-key header is required; no cookie credentials.
        lengths = self.headers.get_all("Content-Length", [])
        origin = self.headers.get("Origin")
        if (self.path != "/api/execution-permissions/decision" or self.headers.get("Transfer-Encoding")
                or len(lengths) != 1 or not lengths[0].isdigit() or not 0 < int(lengths[0]) <= 4096
                or self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json"
                or (origin and (urlparse(origin).scheme not in ("http", "https")
                                or urlparse(origin).netloc != self.headers.get("Host")))):
            return self.sendj({"error": "Invalid execution permission request"}, 400)
        try:
            # Reject duplicate keys and nonfinite values before runner or audit I/O.
            import importlib.util
            spec = importlib.util.spec_from_file_location("project_byte_permissions", Path(__file__).with_name("execution_permissions.py"))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            decision = module.decode(self.rfile.read(int(lengths[0])))
        except Exception:
            return self.sendj({"error": "Invalid execution permission request"}, 400)
        if not isinstance(decision, dict):
            return self.sendj({"error": "Invalid execution permission request"}, 400)
        return self._execution_permissions(decision, actor)

    def do_HEAD(self):
        path = unquote(urlparse(self.path).path)
        if path == "/spotify-callback":
            return self._spotify_callback(head=True)
        if path in PUBLIC_FILES:
            return self._public_file(path, head=True)
        if path.startswith("/uploads/"):
            return self._attachment_file(path, head=True)
        return self._not_found(head=True)


from ai_runtime import install as install_ai_connections

SafeHandler = install_ai_connections(app, SafeHandler, PUBLIC_FILES)

from codex_tasks_runtime import install as install_codex_workspace

SafeHandler = install_codex_workspace(app, SafeHandler, PUBLIC_FILES)


if __name__ == "__main__":
    app.init_db()
    _sync_once()
    threading.Thread(target=truthful_sync_loop, daemon=True).start()
    host = os.getenv("KANBAN_HOST", "127.0.0.1")
    port = int(os.getenv("KANBAN_PORT", "8094"))
    print(f"PROJECT_BYTE v4 listening on http://{host}:{port}")
    ThreadingHTTPServer((host, port), SafeHandler).serve_forever()
