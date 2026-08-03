from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import socket
import sqlite3
import time
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from server.agents import AgentsService, agents_router
from server.api.bootstrap import (
    BootstrapService,
    SQLiteServerIdentityRepository,
    bootstrap_router,
)
from server.automation import AutomationService, automation_router
from server.command_center import CommandCenterService, command_center_router
from server.communications import CommunicationsService, communications_router
from server.engineering import EngineeringService, engineering_router
from server.intelligence import IntelligenceService, intelligence_router
from server.memory import MemoryService, memory_router
from server.mobile import MobileService, mobile_router
from server.identity import (
    DeviceEnrollmentService,
    PairingKeyProtector,
    SQLiteDeviceIdentityRepository,
    device_enrollment_router,
    load_or_create_identity_master_key,
)
from server.plugins import PluginService, plugins_router
from server.realtime import RealtimeService, SQLiteEventRepository, realtime_router
from server.wearables import WearableService, wearables_router
from server.security import (
    EnrollmentRequestGuardMiddleware,
    HttpRequestBoundary,
    SecurityService,
    security_router,
)
from server.workspace import WorkspaceService, workspace_router

try:
    import psutil
except ImportError:  # The launcher installs psutil; this keeps diagnostics importable.
    psutil = None


LOGGER = logging.getLogger("joeos.backend")
BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = BASE_DIR / "data" / "joeos.db"
INDEX_PATH = BASE_DIR / "index.html"
MANIFEST_PATH = BASE_DIR / "manifest.webmanifest"
SERVICE_WORKER_PATH = BASE_DIR / "sw.js"
ICON_PATH = BASE_DIR / "joeos-icon.svg"
SDK_PATH = BASE_DIR / "packages" / "sdk" / "src" / "index.js"
JOEOS_VERSION = "2.0.0"
SAMPLE_INTERVAL_SECONDS = 5
MAX_METRIC_ROWS = 720
MAX_EVENT_ROWS = 240


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bounded_number(value: Any, minimum: float = 0, maximum: float = 100) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return minimum
    return max(minimum, min(maximum, parsed))


def _nonnegative_integer(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _environment_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _environment_integer(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _database_path() -> Path:
    configured = os.getenv("JOEOS_DB_PATH", "").strip()
    return Path(configured).expanduser().resolve() if configured else DEFAULT_DB_PATH


def _connect(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(db_path), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


def _prepare_database(db_path: Path) -> None:
    created_parent = not db_path.parent.exists()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if created_parent:
        with suppress(OSError):
            db_path.parent.chmod(0o700)
    with _connect(db_path) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS system_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recorded_at TEXT NOT NULL,
                cpu_percent REAL NOT NULL,
                ram_percent REAL NOT NULL,
                gpu_percent REAL,
                disk_percent REAL NOT NULL,
                uptime_seconds INTEGER NOT NULL,
                cpu_detail TEXT NOT NULL,
                ram_detail TEXT NOT NULL,
                gpu_detail TEXT NOT NULL,
                disk_detail TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_metrics_recorded_at
            ON system_metrics(recorded_at DESC);

            CREATE TABLE IF NOT EXISTS bots (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                role TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL CHECK(status IN ('running', 'stopped')),
                tasks_completed INTEGER NOT NULL DEFAULT 0,
                queued_tasks INTEGER NOT NULL DEFAULT 0,
                success_rate REAL NOT NULL DEFAULT 100,
                activity TEXT NOT NULL DEFAULT '',
                icon TEXT NOT NULL DEFAULT 'fa-robot',
                agent_type TEXT NOT NULL DEFAULT 'profile',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recorded_at TEXT NOT NULL,
                level TEXT NOT NULL,
                source TEXT NOT NULL,
                message TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_events_recorded_at
            ON events(recorded_at DESC);
            """
        )
        _seed_bots(connection)
    with suppress(OSError):
        db_path.chmod(0o600)


def _seed_bots(connection: sqlite3.Connection) -> None:
    now = _utc_now()
    rows = [
        (
            "lemonade-copilot",
            "Lemonade Copilot",
            "Private Local Inference",
            "Routes JoeOS chat to the model loaded in Lemonade Server. No prompt leaves the Halo.",
            "running",
            0,
            0,
            100.0,
            "Waiting for Lemonade health check",
            "fa-lemon",
            "lemonade",
        ),
        (
            "codex-local",
            "Codex Local",
            "Approval-Gated Coding Profile",
            "Tracks the local Codex workspace profile. Shell and file execution remain outside browser chat.",
            "running",
            0,
            0,
            100.0,
            "Local profile enabled",
            "fa-code",
            "codex",
        ),
        (
            "claude-local",
            "Claude Code Local",
            "Approval-Gated Coding Profile",
            "Tracks the Claude Code profile connected to Lemonade's local Anthropic-compatible API.",
            "running",
            0,
            0,
            100.0,
            "Local profile enabled",
            "fa-terminal",
            "claude",
        ),
        (
            "resource-scout",
            "Resource Scout",
            "Halo Telemetry",
            "Samples CPU, unified memory, GPU, storage, and uptime every five seconds.",
            "running",
            0,
            0,
            100.0,
            "Collecting Halo telemetry",
            "fa-chart-line",
            "monitor",
        ),
        (
            "runtime-sentinel",
            "Runtime Sentinel",
            "Lemonade Health",
            "Monitors Lemonade availability, loaded models, and inference performance.",
            "running",
            0,
            0,
            100.0,
            "Checking local inference runtime",
            "fa-shield-halved",
            "monitor",
        ),
        (
            "event-sentry",
            "Event Sentry",
            "Local Audit Stream",
            "Records JoeOS state transitions and operator actions in the private SQLite event log.",
            "running",
            0,
            0,
            100.0,
            "Following JoeOS events",
            "fa-wave-square",
            "monitor",
        ),
    ]
    connection.executemany(
        """
        INSERT OR IGNORE INTO bots (
            id, name, role, description, status, tasks_completed, queued_tasks,
            success_rate, activity, icon, agent_type, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [row + (now, now) for row in rows],
    )


def _record_event(db_path: Path, level: str, source: str, message: str) -> None:
    safe_level = level if level in {"info", "success", "warn", "error"} else "info"
    with _connect(db_path) as connection:
        connection.execute(
            "INSERT INTO events(recorded_at, level, source, message) VALUES (?, ?, ?, ?)",
            (_utc_now(), safe_level, source[:80], message[:500]),
        )
        connection.execute(
            """
            DELETE FROM events
            WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT ?)
            """,
            (MAX_EVENT_ROWS,),
        )


def _lemonade_api_base() -> str:
    raw = os.getenv("LEMONADE_BASE_URL", "http://127.0.0.1:13305/api/v1").strip()
    raw = raw.rstrip("/")
    if re.search(r"/(?:api/)?v1$", raw):
        return raw
    return raw + "/api/v1"


def _lemonade_headers() -> Dict[str, str]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    api_key = os.getenv("LEMONADE_API_KEY", "").strip()
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    return headers


def _text_models(payload: Any) -> List[Dict[str, Any]]:
    rows = payload.get("data", []) if isinstance(payload, dict) else []
    excluded_labels = {
        "image",
        "embedding",
        "reranking",
        "speech",
        "transcription",
        "audio",
        "tts",
        "3d",
    }
    excluded_recipes = {
        "sd-cpp",
        "whispercpp",
        "kokoro",
        "moonshine",
        "onnxruntime",
        "trellis",
    }
    models = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        labels = {str(item).lower() for item in row.get("labels", []) if item}
        recipe = str(row.get("recipe", "")).lower()
        if labels.intersection(excluded_labels) or recipe in excluded_recipes:
            continue
        if row.get("downloaded") is False:
            continue
        models.append(row)
    return models


def _choose_model(models: List[Dict[str, Any]], loaded_model: Any = None) -> Optional[str]:
    configured = os.getenv("LEMONADE_MODEL", "").strip()
    if configured:
        return configured
    if isinstance(loaded_model, str) and loaded_model.strip():
        return loaded_model.strip()
    if not models:
        return None

    def score(model: Dict[str, Any]) -> float:
        name = str(model.get("id", "")).lower()
        labels = {str(item).lower() for item in model.get("labels", []) if item}
        value = 50 if model.get("suggested") else 0
        value += 40 if "coder" in name or "coding" in labels else 0
        value += 25 if "qwen3.5" in name or "gpt-oss" in name else 0
        value += 10 if "reasoning" in labels or "instruct" in name else 0
        value += min(float(model.get("size") or 0), 40) / 10
        return value

    return str(max(models, key=score)["id"])


async def _lemonade_get(app: FastAPI, path: str) -> Dict[str, Any]:
    response = await app.state.http.get(
        _lemonade_api_base() + "/" + path.lstrip("/"),
        headers=_lemonade_headers(),
    )
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, dict) else {}


async def _refresh_runtime(app: FastAPI) -> Dict[str, Any]:
    previous = getattr(app.state, "runtime", {})
    results = await asyncio.gather(
        _lemonade_get(app, "health"),
        _lemonade_get(app, "system-stats"),
        _lemonade_get(app, "stats"),
        _lemonade_get(app, "models"),
        return_exceptions=True,
    )
    health, system_stats, inference_stats, models_payload = results
    if isinstance(health, Exception):
        runtime = {
            "online": False,
            "status": "offline",
            "version": None,
            "model": None,
            "loaded_models": [],
            "available_models": [],
            "gpu_percent": None,
            "vram_gb": None,
            "npu_percent": None,
            "tokens_per_second": None,
            "time_to_first_token": None,
            "message": "Lemonade Server is not reachable on the Halo loopback interface.",
        }
    else:
        system_stats = system_stats if isinstance(system_stats, dict) else {}
        inference_stats = inference_stats if isinstance(inference_stats, dict) else {}
        models_payload = models_payload if isinstance(models_payload, dict) else {}
        models = _text_models(models_payload)
        loaded_rows = health.get("all_models_loaded", [])
        loaded_names = [
            str(row.get("model_name"))
            for row in loaded_rows
            if isinstance(row, dict) and row.get("model_name")
        ]
        model = _choose_model(models, health.get("model_loaded"))
        runtime = {
            "online": True,
            "status": str(health.get("status") or "ok"),
            "version": health.get("version"),
            "model": model,
            "loaded_models": loaded_names,
            "available_models": [str(row["id"]) for row in models],
            "gpu_percent": system_stats.get("gpu_percent"),
            "vram_gb": system_stats.get("vram_gb"),
            "npu_percent": system_stats.get("npu_percent"),
            "tokens_per_second": inference_stats.get("tokens_per_second"),
            "time_to_first_token": inference_stats.get("time_to_first_token"),
            "message": "Private local inference is ready.",
        }

    app.state.runtime = runtime
    was_online = previous.get("online") if previous else None
    if was_online is not None and was_online != runtime["online"]:
        level = "success" if runtime["online"] else "error"
        message = "Lemonade Server connected." if runtime["online"] else "Lemonade Server connection lost."
        await asyncio.to_thread(_record_event, app.state.db_path, level, "lemonade", message)
    await asyncio.to_thread(_sync_runtime_bots, app.state.db_path, runtime)
    return runtime


def _sync_runtime_bots(db_path: Path, runtime: Dict[str, Any]) -> None:
    now = _utc_now()
    lemonade_activity = (
        "Online · " + str(runtime.get("model") or "no text model selected")
        if runtime.get("online")
        else "Offline · check Lemonade Server"
    )
    cli_states = {
        "codex-local": "Codex CLI detected · approval-gated" if shutil.which("codex") else "Profile enabled · Codex CLI not detected",
        "claude-local": "Claude CLI detected · approval-gated" if shutil.which("claude") else "Profile enabled · Claude CLI not detected",
    }
    with _connect(db_path) as connection:
        connection.execute(
            "UPDATE bots SET activity = ?, updated_at = ? WHERE id = 'lemonade-copilot' AND status = 'running'",
            (lemonade_activity, now),
        )
        for bot_id, activity in cli_states.items():
            connection.execute(
                "UPDATE bots SET activity = ?, updated_at = ? WHERE id = ? AND status = 'running'",
                (activity, now, bot_id),
            )


def _temperature_celsius() -> float:
    if psutil is None or not hasattr(psutil, "sensors_temperatures"):
        return 0.0
    try:
        groups = psutil.sensors_temperatures(fahrenheit=False) or {}
        readings = [
            float(item.current)
            for items in groups.values()
            for item in items
            if item.current is not None and 0 < float(item.current) < 130
        ]
        return round(max(readings), 1) if readings else 0.0
    except (AttributeError, OSError, ValueError):
        return 0.0


def _fallback_uptime() -> int:
    proc_uptime = Path("/proc/uptime")
    if proc_uptime.exists():
        try:
            return max(0, int(float(proc_uptime.read_text(encoding="utf-8").split()[0])))
        except (OSError, ValueError, IndexError):
            pass
    return max(0, int(time.monotonic()))


def _host_sample(runtime: Dict[str, Any]) -> Dict[str, Any]:
    if psutil is not None:
        cpu = _bounded_number(psutil.cpu_percent(interval=0.12))
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage(str(Path.home().anchor or "/"))
        uptime = max(0, int(time.time() - psutil.boot_time()))
        cpu_count = psutil.cpu_count(logical=True) or os.cpu_count() or 1
        cpu_detail = "%s threads · local Halo" % cpu_count
        ram_detail = "%.1f / %.1f GiB unified memory" % (
            memory.used / (1024 ** 3),
            memory.total / (1024 ** 3),
        )
        disk_detail = "%.1f / %.1f GiB used" % (
            disk.used / (1024 ** 3),
            disk.total / (1024 ** 3),
        )
        ram_percent = _bounded_number(memory.percent)
        disk_percent = _bounded_number(disk.percent)
    else:
        cpu_count = os.cpu_count() or 1
        try:
            cpu = _bounded_number(os.getloadavg()[0] / cpu_count * 100)
        except (AttributeError, OSError):
            cpu = 0.0
        disk = shutil.disk_usage(str(Path.home().anchor or "/"))
        ram_percent = 0.0
        disk_percent = _bounded_number(disk.used / max(1, disk.total) * 100)
        uptime = _fallback_uptime()
        cpu_detail = "%s threads · install psutil for precise sampling" % cpu_count
        ram_detail = "Install psutil for unified-memory telemetry"
        disk_detail = "%.1f / %.1f GiB used" % (
            disk.used / (1024 ** 3),
            disk.total / (1024 ** 3),
        )

    gpu = runtime.get("gpu_percent")
    gpu_percent = _bounded_number(gpu) if gpu is not None else 0.0
    vram = runtime.get("vram_gb")
    gpu_detail = (
        "%.1f GiB shared GPU memory · Lemonade" % float(vram)
        if vram is not None
        else "Waiting for Lemonade GPU telemetry"
    )
    return {
        "recorded_at": _utc_now(),
        "cpu_percent": round(cpu, 1),
        "ram_percent": round(ram_percent, 1),
        "gpu_percent": round(gpu_percent, 1),
        "disk_percent": round(disk_percent, 1),
        "uptime_seconds": uptime,
        "cpu_detail": cpu_detail,
        "ram_detail": ram_detail,
        "gpu_detail": gpu_detail,
        "disk_detail": disk_detail,
        "temperature": _temperature_celsius(),
    }


def _record_metric(db_path: Path, runtime: Dict[str, Any]) -> Dict[str, Any]:
    sample = _host_sample(runtime)
    with _connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO system_metrics (
                recorded_at, cpu_percent, ram_percent, gpu_percent, disk_percent,
                uptime_seconds, cpu_detail, ram_detail, gpu_detail, disk_detail
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sample["recorded_at"],
                sample["cpu_percent"],
                sample["ram_percent"],
                sample["gpu_percent"],
                sample["disk_percent"],
                sample["uptime_seconds"],
                sample["cpu_detail"],
                sample["ram_detail"],
                sample["gpu_detail"],
                sample["disk_detail"],
            ),
        )
        connection.execute(
            """
            DELETE FROM system_metrics
            WHERE id NOT IN (SELECT id FROM system_metrics ORDER BY id DESC LIMIT ?)
            """,
            (MAX_METRIC_ROWS,),
        )
        connection.execute(
            """
            UPDATE bots
            SET tasks_completed = tasks_completed + 1,
                activity = 'Telemetry cycle completed',
                updated_at = ?
            WHERE id = 'resource-scout' AND status = 'running'
            """,
            (sample["recorded_at"],),
        )
    return sample


async def _collector_loop(app: FastAPI) -> None:
    cycle = 0
    while True:
        try:
            await asyncio.to_thread(app.state.device_enrollment_service.expire_pending)
            runtime = await _refresh_runtime(app)
            sample = await asyncio.to_thread(_record_metric, app.state.db_path, runtime)
            cycle += 1
            if cycle % 12 == 0:
                await asyncio.to_thread(
                    _record_event,
                    app.state.db_path,
                    "info",
                    "telemetry",
                    "Halo telemetry captured · CPU %.1f%% · RAM %.1f%% · GPU %.1f%%"
                    % (sample["cpu_percent"], sample["ram_percent"], sample["gpu_percent"]),
                )
            for key, label in (("cpu_percent", "CPU"), ("ram_percent", "memory"), ("disk_percent", "disk")):
                active = sample[key] >= 85
                previous = app.state.thresholds.get(key, False)
                if active and not previous:
                    await asyncio.to_thread(
                        _record_event,
                        app.state.db_path,
                        "warn",
                        "resource-scout",
                        "%s utilization crossed 85%%." % label,
                    )
                app.state.thresholds[key] = active
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # Keep monitoring alive without leaking payloads.
            LOGGER.warning("Collector cycle failed: %s", type(exc).__name__)
        await asyncio.sleep(SAMPLE_INTERVAL_SECONDS)


async def _identity_maintenance_loop(app: FastAPI) -> None:
    while True:
        try:
            await asyncio.to_thread(app.state.device_enrollment_service.expire_pending)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOGGER.warning("Identity maintenance failed: %s", type(exc).__name__)
        await asyncio.sleep(5)


def _wire_mobile_scopes(app: FastAPI) -> None:
    """Register scoped remote queries that read real authoritative state."""
    mobile = app.state.mobile_service

    def _command_center(session, scope):
        service = getattr(app.state, "command_center_service", None)
        if service is None:
            return {"available": False, "reason": "command center unavailable"}
        overview = service.overview()
        return {
            "available": True,
            "overall": overview.overall,
            "services": [s.service_id for s in overview.services],
            "runtime_online": bool((app.state.runtime or {}).get("online")),
        }

    def _projects(session, scope):
        service = getattr(app.state, "engineering_service", None)
        if service is None:
            return {"projects": []}
        projects = service.projects.list_projects()
        return {"projects": [{"id": p.project_id, "name": p.name, "trust": p.trust} for p in projects]}

    def _missions(session, scope):
        service = getattr(app.state, "agents_service", None)
        if service is None:
            return {"missions": []}
        missions = service.missions.missions(limit=16)
        return {"missions": [{"mission_id": m.mission_id, "title": m.title, "status": m.status} for m in missions]}

    def _runtime(session, scope):
        return {"runtime": app.state.runtime or {}, "model": (app.state.runtime or {}).get("model")}

    def _workflows(session, scope):
        service = getattr(app.state, "automation_service", None)
        if service is None:
            return {"workflows": [], "runs": []}
        workflows = service.list_workflows()
        runs = service.list_runs(limit=10)
        return {
            "workflows": [{"workflow_id": w.workflow_id, "name": w.name, "enabled": w.enabled, "health": w.health_state} for w in workflows],
            "runs": [{"run_id": r.run_id, "state": r.state, "workflow_id": r.workflow_id} for r in runs],
        }

    def _communications(session, scope):
        service = getattr(app.state, "communications_service", None)
        if service is None:
            return {"inbox": [], "unread": 0}
        notifications = service.list_notifications(limit=20)
        return {
            "inbox": [{"notification_id": n.notification_id, "source": n.source, "category": n.category, "title": n.title, "severity": n.severity} for n in notifications],
            "unread": service.unread_notifications(),
        }

    def _devices(session, scope):
        service = getattr(app.state, "wearables_service", None)
        if service is None:
            return {"devices": []}
        devices = service.list_devices()
        return {
            "devices": [
                {"device_id": d.device_id, "display_name": d.display_name, "device_type": d.device_type, "connection_state": d.connection_state}
                for d in devices
                if d.revocation_state == "active"
            ]
        }

    def _mobile(session, scope):
        return {"overview": mobile.overview().model_dump()}

    mobile.register_scoped_provider("command_center", _command_center)
    mobile.register_scoped_provider("projects", _projects)
    mobile.register_scoped_provider("missions", _missions)
    mobile.register_scoped_provider("runtime", _runtime)
    mobile.register_scoped_provider("workflows", _workflows)
    mobile.register_scoped_provider("communications", _communications)
    mobile.register_scoped_provider("devices", _devices)
    mobile.register_scoped_provider("mobile", _mobile)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db_path = _database_path()
    _prepare_database(db_path)
    app.state.db_path = db_path
    app.state.started_at = _utc_now()
    app.state.runtime = {}
    app.state.http_boundary = HttpRequestBoundary.from_environment()
    server_identity_repository = SQLiteServerIdentityRepository(lambda: _connect(db_path))
    pairing_key_protector = PairingKeyProtector(
        load_or_create_identity_master_key(db_path)
    )
    app.state.bootstrap_service = BootstrapService(
        repository=server_identity_repository,
        server_version=JOEOS_VERSION,
    )
    app.state.bootstrap_service.prepare()
    app.state.device_enrollment_service = DeviceEnrollmentService(
        repository=SQLiteDeviceIdentityRepository(
            lambda: _connect(db_path),
            pairing_key_protector,
        ),
        server_id_provider=server_identity_repository.get_or_create_server_id,
        allowed_https_hosts=os.getenv("JOEOS_ALLOWED_HOSTS", "").split(","),
        event_sink=lambda level, source, message: _record_event(
            db_path, level, source, message
        ),
    )
    app.state.device_enrollment_service.prepare()
    app.state.workspace_service = WorkspaceService(
        connection_factory=lambda: _connect(db_path),
        event_sink=lambda level, source, message: _record_event(
            db_path, level, source, message
        ),
    )
    app.state.workspace_service.prepare()
    allowed_origins = tuple(
        value.strip()
        for value in os.getenv("JOEOS_ALLOWED_ORIGINS", "").split(",")
        if value.strip()
    )
    app.state.realtime_service = RealtimeService(
        repository=SQLiteEventRepository(lambda: _connect(db_path)),
        snapshot_provider=lambda: {
            "metrics": _metric_payload(db_path, app.state.runtime),
            "bots": _bots_payload(db_path),
            "events": _events_payload(db_path)["summary"],
        },
        allowed_origins=allowed_origins,
        batch_size=_environment_integer("JOEOS_WS_BATCH_SIZE", 40),
        poll_seconds=_environment_float("JOEOS_WS_POLL_SECONDS", 0.5),
        heartbeat_seconds=_environment_float("JOEOS_WS_HEARTBEAT_SECONDS", 15.0),
        max_payload_bytes=_environment_integer("JOEOS_WS_MAX_PAYLOAD_BYTES", 262_144),
        max_inbound_bytes=_environment_integer("JOEOS_WS_MAX_INBOUND_BYTES", 4_096),
    )
    app.state.command_center_service = CommandCenterService(
        connection_factory=lambda: _connect(db_path),
        runtime_provider=lambda: app.state.runtime,
        version=JOEOS_VERSION,
        started_at=app.state.started_at,
        sample_interval_seconds=SAMPLE_INTERVAL_SECONDS,
        realtime_ready=lambda: True,
        identity_ready=lambda: True,
        workspace_ready=lambda: True,
        plugins_ready=lambda: getattr(app.state, "plugins_service", None) is not None,
        automation_ready=lambda: getattr(app.state, "automation_service", None) is not None,
        communications_ready=lambda: getattr(app.state, "communications_service", None) is not None,
        wearables_ready=lambda: getattr(app.state, "wearables_service", None) is not None,
        mobile_ready=lambda: getattr(app.state, "mobile_service", None) is not None,
        security_ready=lambda: getattr(app.state, "security_service", None) is not None,
    )
    app.state.engineering_service = EngineeringService(
        connection_factory=lambda: _connect(db_path),
        event_sink=lambda level, source, message: _record_event(
            db_path, level, source, message
        ),
    )
    app.state.intelligence_service = IntelligenceService(
        project_service=app.state.engineering_service.projects,
        data_dir=str(db_path.parent / "intelligence"),
    )
    app.state.memory_service = MemoryService(data_dir=str(db_path.parent / "memory"))
    app.state.agents_service = AgentsService(data_dir=str(db_path.parent / "agents"))
    app.state.plugins_service = PluginService(
        str(db_path.parent / "plugins"),
        master_key=load_or_create_identity_master_key(db_path),
        joeos_version=JOEOS_VERSION,
        first_party_publishers=["joeos"],
    )
    app.state.communications_service = CommunicationsService(
        str(db_path.parent / "communications"),
        event_sink=lambda level, source, message: _record_event(
            db_path, level, source, message
        ),
    )
    app.state.communications_service.prepare_defaults()
    app.state.automation_service = AutomationService(
        str(db_path.parent / "automation"),
        master_key=load_or_create_identity_master_key(db_path),
        joeos_version=JOEOS_VERSION,
        event_sink=lambda level, source, message: _record_event(
            db_path, level, source, message
        ),
        communications=app.state.communications_service,
    )
    app.state.wearables_service = WearableService(
        str(db_path.parent / "wearables"),
        event_sink=lambda level, source, message: _record_event(
            db_path, level, source, message
        ),
    )
    app.state.mobile_service = MobileService(
        str(db_path.parent / "mobile"),
        server_version=JOEOS_VERSION,
        event_sink=lambda level, source, message: _record_event(
            db_path, level, source, message
        ),
    )
    app.state.mobile_service.prepare_defaults()
    _wire_mobile_scopes(app)
    app.state.security_service = SecurityService(
        str(db_path.parent / "security"),
        master_key=load_or_create_identity_master_key(db_path),
        event_sink=lambda level, source, message: _record_event(
            db_path, level, source, message
        ),
    )
    app.state.security_service.prepare_defaults()
    app.state.thresholds = {}
    timeout = httpx.Timeout(
        connect=float(os.getenv("LEMONADE_CONNECT_TIMEOUT", "3")),
        read=float(os.getenv("LEMONADE_READ_TIMEOUT", "180")),
        write=30.0,
        pool=5.0,
    )
    app.state.http = httpx.AsyncClient(timeout=timeout)
    await _refresh_runtime(app)
    await asyncio.to_thread(_record_metric, db_path, app.state.runtime)
    await asyncio.to_thread(_record_event, db_path, "success", "joeos", "JoeOS local command center started.")
    collector = asyncio.create_task(_collector_loop(app), name="joeos-collector")
    identity_maintenance = asyncio.create_task(
        _identity_maintenance_loop(app),
        name="joeos-identity-maintenance",
    )
    try:
        yield
    finally:
        collector.cancel()
        identity_maintenance.cancel()
        with suppress(asyncio.CancelledError):
            await collector
        with suppress(asyncio.CancelledError):
            await identity_maintenance
        await app.state.http.aclose()
        plugins_service = getattr(app.state, "plugins_service", None)
        if plugins_service is not None:
            plugins_service.shutdown()


app = FastAPI(
    title="JoeOS Local Command Center",
    version=JOEOS_VERSION,
    docs_url="/docs" if os.getenv("ENABLE_API_DOCS", "false").lower() == "true" else None,
    redoc_url=None,
    lifespan=lifespan,
)
app.add_middleware(EnrollmentRequestGuardMiddleware)
app.state.http_boundary = HttpRequestBoundary.from_environment()
app.include_router(bootstrap_router)
app.include_router(device_enrollment_router)
app.include_router(workspace_router)
app.include_router(realtime_router)
app.include_router(command_center_router)
app.include_router(engineering_router)
app.include_router(intelligence_router)
app.include_router(memory_router)
app.include_router(agents_router)
app.include_router(plugins_router)
app.include_router(automation_router)
app.include_router(communications_router)
app.include_router(wearables_router)
app.include_router(mobile_router)
app.include_router(security_router)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    boundary = request.app.state.http_boundary
    request_id = boundary.request_id(request.headers.get("x-request-id"))
    request.state.request_id = request_id
    rejection = boundary.authorize(
        method=request.method,
        path=request.url.path,
        host_header=request.headers.get("host"),
        origin_header=request.headers.get("origin"),
        sec_fetch_site=request.headers.get("sec-fetch-site"),
        content_type=request.headers.get("content-type"),
    )
    if rejection:
        response = JSONResponse(
            status_code=rejection.status_code,
            content={
                "error": {
                    "code": rejection.code,
                    "message": rejection.message,
                    "request_id": request_id,
                }
            },
        )
    else:
        response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), geolocation=(), microphone=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; "
        "script-src 'self' 'unsafe-inline' https://unpkg.com; "
        "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; "
        "font-src 'self' data: https://cdnjs.cloudflare.com; "
        "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    )
    if rejection or request.url.path.startswith("/api/") or request.url.path == "/healthz":
        response.headers["Cache-Control"] = "no-store"
    return response


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    context: Dict[str, Any] = Field(default_factory=dict)


class BotStatusRequest(BaseModel):
    status: Literal["running", "stopped"]


class BotCreateRequest(BaseModel):
    id: Optional[str] = Field(default=None, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    role: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    status: Literal["running", "stopped"] = "running"
    tasks: int = Field(default=0, ge=0)
    queued: int = Field(default=0, ge=0)
    success: float = Field(default=100.0, ge=0, le=100)
    activity: str = Field(default="Local profile created", max_length=240)
    icon: str = Field(default="fa-robot", max_length=80)


def _metric_payload(db_path: Path, runtime: Dict[str, Any]) -> Dict[str, Any]:
    with _connect(db_path) as connection:
        rows = connection.execute(
            "SELECT * FROM system_metrics ORDER BY id DESC LIMIT 14"
        ).fetchall()
    if not rows:
        return {"updated_at": None, "uptime_seconds": 0, "metrics": [], "runtime": runtime, "nodes": []}

    latest = rows[0]
    previous = rows[1] if len(rows) > 1 else latest
    chronological = list(reversed(rows))
    definitions = (
        ("cpu", "CPU Load", "cpu_percent", "cpu_detail", "fa-microchip"),
        ("ram", "Unified Memory", "ram_percent", "ram_detail", "fa-memory"),
        ("gpu", "Radeon GPU", "gpu_percent", "gpu_detail", "fa-bolt"),
        ("disk", "Storage", "disk_percent", "disk_detail", "fa-hard-drive"),
    )
    metrics = []
    for metric_id, label, value_column, detail_column, icon in definitions:
        metrics.append(
            {
                "id": metric_id,
                "label": label,
                "value": _bounded_number(latest[value_column]),
                "previous": _bounded_number(previous[value_column]),
                "unit": "%",
                "icon": icon,
                "detail": latest[detail_column],
                "history": [_bounded_number(row[value_column]) for row in chronological],
            }
        )

    cpu = round(_bounded_number(latest["cpu_percent"]))
    disk = round(_bounded_number(latest["disk_percent"]))
    online = bool(runtime.get("online"))
    node = {
        "id": "halo-local",
        "name": socket.gethostname() or "joeos-halo",
        "region": "Private local fabric",
        "role": "Ryzen AI Max+ 395 · Lemonade",
        "ip": "Loopback inference",
        "status": "healthy" if online else "degraded",
        "temp": round(_temperature_celsius()),
        "latency": round(float(runtime.get("time_to_first_token") or 0) * 1000),
        "cpu": cpu,
        "disk": disk,
    }
    return {
        "updated_at": latest["recorded_at"],
        "uptime_seconds": _nonnegative_integer(latest["uptime_seconds"]),
        "metrics": metrics,
        "runtime": runtime,
        "nodes": [node],
    }


def _normalize_bot(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "role": row["role"],
        "description": row["description"],
        "status": "running" if row["status"] == "running" else "stopped",
        "tasks": _nonnegative_integer(row["tasks_completed"]),
        "queued": _nonnegative_integer(row["queued_tasks"]),
        "success": _bounded_number(row["success_rate"]),
        "activity": row["activity"],
        "icon": row["icon"],
        "agent_type": row["agent_type"],
        "updated_at": row["updated_at"],
    }


def _bots_payload(db_path: Path) -> Dict[str, Any]:
    with _connect(db_path) as connection:
        rows = connection.execute("SELECT * FROM bots ORDER BY name").fetchall()
    bots = [_normalize_bot(row) for row in rows]
    updated_at = max((bot["updated_at"] for bot in bots), default=None)
    return {"updated_at": updated_at, "bots": bots}


def _events_payload(db_path: Path) -> Dict[str, Any]:
    with _connect(db_path) as connection:
        rows = connection.execute("SELECT * FROM events ORDER BY id DESC LIMIT 40").fetchall()
        active_profiles = connection.execute(
            "SELECT COUNT(*) FROM bots WHERE status = 'running'"
        ).fetchone()[0]
    logs = []
    for row in rows:
        try:
            stamp = datetime.fromisoformat(str(row["recorded_at"]).replace("Z", "+00:00"))
            display_time = stamp.astimezone().strftime("%H:%M:%S")
        except (TypeError, ValueError):
            display_time = "--:--:--"
        logs.append(
            {
                "id": "event-%s" % row["id"],
                "event_id": int(row["id"]),
                "cursor": int(row["id"]),
                "recorded_at": row["recorded_at"],
                "time": display_time,
                "level": row["level"],
                "source": row["source"],
                "message": row["message"],
            }
        )
    warnings = sum(1 for row in rows if row["level"] == "warn")
    errors = sum(1 for row in rows if row["level"] == "error")
    successes = sum(1 for row in rows if row["level"] == "success")
    return {
        "cursor": int(rows[0]["id"]) if rows else 0,
        "recorded_at": rows[0]["recorded_at"] if rows else None,
        "logs": logs,
        "summary": {
            "events": len(rows),
            "warnings": warnings,
            "errors": errors,
            "successes": successes,
            "active_profiles": active_profiles,
        },
    }


@app.get("/", include_in_schema=False)
def frontend() -> FileResponse:
    return FileResponse(INDEX_PATH, media_type="text/html")


@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest() -> FileResponse:
    return FileResponse(MANIFEST_PATH, media_type="application/manifest+json")


@app.get("/sw.js", include_in_schema=False)
def service_worker() -> FileResponse:
    return FileResponse(SERVICE_WORKER_PATH, media_type="application/javascript")


@app.get("/joeos-icon.svg", include_in_schema=False)
def app_icon() -> FileResponse:
    return FileResponse(ICON_PATH, media_type="image/svg+xml")


@app.get("/sdk/index.js", include_in_schema=False)
def browser_sdk() -> FileResponse:
    return FileResponse(SDK_PATH, media_type="application/javascript")


@app.get("/healthz")
def healthz(request: Request) -> Dict[str, Any]:
    runtime = request.app.state.runtime
    return {
        "status": "ok",
        "lemonade": "online" if runtime.get("online") else "offline",
        "model": runtime.get("model"),
    }


@app.get("/api/status")
async def runtime_status(request: Request) -> Dict[str, Any]:
    return await _refresh_runtime(request.app)


@app.get("/api/metrics")
def metrics(request: Request) -> Dict[str, Any]:
    return _metric_payload(request.app.state.db_path, request.app.state.runtime)


@app.get("/api/bots")
def bots(request: Request) -> Dict[str, Any]:
    return _bots_payload(request.app.state.db_path)


@app.get("/api/events")
def events(request: Request) -> Dict[str, Any]:
    return _events_payload(request.app.state.db_path)


@app.patch("/api/bots/{bot_id}")
def update_bot(bot_id: str, payload: BotStatusRequest, request: Request) -> Dict[str, Any]:
    now = _utc_now()
    activity = "Local profile enabled" if payload.status == "running" else "Local profile paused"
    with _connect(request.app.state.db_path) as connection:
        cursor = connection.execute(
            "UPDATE bots SET status = ?, activity = ?, updated_at = ? WHERE id = ?",
            (payload.status, activity, now, bot_id),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Agent profile not found.")
        row = connection.execute("SELECT * FROM bots WHERE id = ?", (bot_id,)).fetchone()
    _record_event(
        request.app.state.db_path,
        "success" if payload.status == "running" else "info",
        "bot-fleet",
        "%s profile %s." % (row["name"], "enabled" if payload.status == "running" else "paused"),
    )
    return {"bot": _normalize_bot(row)}


@app.post("/api/bots", status_code=status.HTTP_201_CREATED)
def create_bot(payload: BotCreateRequest, request: Request) -> Dict[str, Any]:
    slug = re.sub(r"[^a-z0-9]+", "-", payload.name.lower()).strip("-") or "agent"
    bot_id = payload.id or "%s-%s" % (slug, int(time.time()))
    now = _utc_now()
    values = (
        bot_id,
        payload.name.strip(),
        payload.role.strip(),
        payload.description.strip(),
        payload.status,
        payload.tasks,
        payload.queued,
        payload.success,
        payload.activity,
        payload.icon,
        "profile",
        now,
        now,
    )
    try:
        with _connect(request.app.state.db_path) as connection:
            connection.execute(
                """
                INSERT INTO bots (
                    id, name, role, description, status, tasks_completed, queued_tasks,
                    success_rate, activity, icon, agent_type, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            row = connection.execute("SELECT * FROM bots WHERE id = ?", (bot_id,)).fetchone()
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="An agent profile with that ID already exists.") from exc
    _record_event(request.app.state.db_path, "success", "bot-fleet", "%s profile created." % payload.name.strip())
    return {"bot": _normalize_bot(row)}


def _chat_history(context: Dict[str, Any]) -> List[Dict[str, str]]:
    history = context.get("history", []) if isinstance(context, dict) else []
    cleaned = []
    for item in history[-10:] if isinstance(history, list) else []:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("text") or item.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            continue
        cleaned.append({"role": role, "content": content[:4000]})
    return cleaned


async def _chat_with_lemonade(
    http: httpx.AsyncClient,
    message: str,
    context: Dict[str, Any],
    runtime: Dict[str, Any],
) -> Dict[str, Any]:
    if not runtime.get("online"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Lemonade Server is offline. Start Lemonade on the Halo, then retry.",
        )
    model = runtime.get("model")
    if not model:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Lemonade is online, but no downloaded text model is available.",
        )
    active_section = str(context.get("active_section") or "dashboard")[:40]
    system_prompt = (
        "You are JoeOS, a private local command-center copilot running on an AMD Ryzen AI Max+ 395 Halo. "
        "Be concise, practical, and honest. The current UI section is %s. "
        "You may analyze information and suggest steps, but do not claim that you executed shell commands, "
        "changed files, deployed code, or controlled hardware. Browser chat is read-only unless a separate "
        "approval-gated runner is explicitly connected. Never request secrets."
    ) % active_section
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(_chat_history(context))
    messages.append({"role": "user", "content": message})
    try:
        response = await http.post(
            _lemonade_api_base() + "/chat/completions",
            headers=_lemonade_headers(),
            json={
                "model": model,
                "messages": messages,
                "stream": False,
                "temperature": 0.25,
                "max_tokens": 1200,
            },
        )
        response.raise_for_status()
        data = response.json()
        choices = data.get("choices", []) if isinstance(data, dict) else []
        reply = choices[0].get("message", {}).get("content") if choices else None
        if not isinstance(reply, str) or not reply.strip():
            raise ValueError("Lemonade response did not contain assistant text")
        return {"reply": reply.strip(), "status": "completed", "model": model}
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="The local model timed out while generating a reply.") from exc
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        detail = "Lemonade rejected the request. Verify the selected model and local API key."
        raise HTTPException(status_code=502 if code < 500 else 503, detail=detail) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="Lemonade could not complete the local request.") from exc


@app.post("/api/chat")
async def chat(payload: ChatRequest, request: Request) -> Dict[str, Any]:
    with _connect(request.app.state.db_path) as connection:
        profile = connection.execute(
            "SELECT status FROM bots WHERE id = 'lemonade-copilot'"
        ).fetchone()
    if profile is not None and profile["status"] != "running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The Lemonade Copilot profile is paused. Start it in Bot Fleet, then retry.",
        )
    runtime = await _refresh_runtime(request.app)
    result = await _chat_with_lemonade(
        request.app.state.http,
        payload.message.strip(),
        payload.context,
        runtime,
    )
    now = _utc_now()
    with _connect(request.app.state.db_path) as connection:
        connection.execute(
            """
            UPDATE bots
            SET tasks_completed = tasks_completed + 1,
                activity = ?,
                updated_at = ?
            WHERE id = 'lemonade-copilot'
            """,
            ("Completed local inference · %s" % result["model"], now),
        )
    _record_event(
        request.app.state.db_path,
        "success",
        "lemonade",
        "Local assistant completion finished with %s." % result["model"],
    )
    return result
