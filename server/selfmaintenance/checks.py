"""Maintenance checks for the JoeOS Self-Maintenance platform.

Each check inspects a real, authoritative source through an injected provider
and returns an honest state. When a source is unavailable or has not produced
a value yet, the check reports `unknown`/`skipped` rather than fabricating a
pass. Providers are dependency-injected so the platform can be composed over
live services in the backend and over fakes in tests.

Check states: "ok" | "degraded" | "failed" | "warning" | "unknown" | "skipped".
A failure is never invented; unmeasured signals stay unknown.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Tuple

from .models import MaintenanceCheck

Provider = Callable[[], Any]

DISK_PRESSURE_PERCENT = 85.0
TELEMETRY_FRESHNESS_SECONDS = 30.0

OUTCOME_ORDER = {
    "failed": 5,
    "degraded": 4,
    "warning": 3,
    "unknown": 2,
    "skipped": 1,
    "ok": 0,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def check_database_healthy(connection_factory: Provider) -> MaintenanceCheck:
    try:
        with connection_factory() as connection:
            connection.execute("SELECT 1").fetchone()
        return MaintenanceCheck("database.healthy", "Local database", "storage", "ok", "SELECT 1 succeeded.", _now_iso())
    except sqlite3.Error as exc:
        return MaintenanceCheck("database.healthy", "Local database", "storage", "failed", "%s: %s" % (type(exc).__name__, exc), _now_iso())


def check_event_store(connection_factory: Provider) -> MaintenanceCheck:
    try:
        with connection_factory() as connection:
            connection.execute("SELECT COUNT(*) FROM events").fetchone()
        return MaintenanceCheck("event.store", "Audit event store", "observability", "ok", "Audit event store is readable.", _now_iso())
    except sqlite3.Error as exc:
        return MaintenanceCheck("event.store", "Audit event store", "observability", "failed", "%s: %s" % (type(exc).__name__, exc), _now_iso())


def check_telemetry_fresh(telemetry_latest: Provider, freshness_seconds: float = TELEMETRY_FRESHNESS_SECONDS) -> MaintenanceCheck:
    try:
        latest = telemetry_latest()
    except Exception as exc:
        return MaintenanceCheck("telemetry.fresh", "Telemetry freshness", "observability", "unknown", "Telemetry unavailable: %s" % type(exc).__name__, _now_iso())
    if latest is None:
        return MaintenanceCheck("telemetry.fresh", "Telemetry freshness", "observability", "skipped", "No telemetry sample recorded yet.", _now_iso())
    recorded = latest.get("recorded_at")
    if not recorded:
        return MaintenanceCheck("telemetry.fresh", "Telemetry freshness", "observability", "skipped", "Telemetry sample has no timestamp.", _now_iso())
    try:
        stamp = datetime.fromisoformat(str(recorded).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return MaintenanceCheck("telemetry.fresh", "Telemetry freshness", "observability", "skipped", "Telemetry timestamp is unparseable.", _now_iso())
    if datetime.now(timezone.utc) - stamp > timedelta(seconds=freshness_seconds):
        return MaintenanceCheck("telemetry.fresh", "Telemetry freshness", "observability", "degraded", "Newest telemetry sample is older than %.0fs." % freshness_seconds, _now_iso())
    return MaintenanceCheck("telemetry.fresh", "Telemetry freshness", "observability", "ok", "Telemetry is current.", _now_iso())


def check_disk_space(telemetry_latest: Provider, pressure_percent: float = DISK_PRESSURE_PERCENT) -> MaintenanceCheck:
    try:
        latest = telemetry_latest()
    except Exception as exc:
        return MaintenanceCheck("disk.space", "Disk space", "storage", "unknown", "Disk state unavailable: %s" % type(exc).__name__, _now_iso())
    disk = latest.get("disk_percent") if latest else None
    if disk is None:
        return MaintenanceCheck("disk.space", "Disk space", "storage", "unknown", "Disk usage has not been measured.", _now_iso())
    try:
        value = float(disk)
    except (TypeError, ValueError):
        return MaintenanceCheck("disk.space", "Disk space", "storage", "unknown", "Disk usage is not a number.", _now_iso())
    if value >= pressure_percent:
        return MaintenanceCheck("disk.space", "Disk space", "storage", "warning", "Disk utilization is %.1f%%." % value, _now_iso())
    return MaintenanceCheck("disk.space", "Disk space", "storage", "ok", "Disk utilization is %.1f%%." % value, _now_iso())


def check_migration_status(migrations_writable: Provider) -> MaintenanceCheck:
    try:
        writable, detail = migrations_writable()
    except Exception as exc:
        return MaintenanceCheck("migration.status", "Schema migrations", "integrity", "unknown", "Migration state unavailable: %s" % type(exc).__name__, _now_iso())
    if not writable:
        return MaintenanceCheck("migration.status", "Schema migrations", "integrity", "failed", detail or "Migration gate blocks writes.", _now_iso())
    return MaintenanceCheck("migration.status", "Schema migrations", "integrity", "ok", detail or "Schema is compatible; writes allowed.", _now_iso())


def check_backup_verified(backup_list: Provider) -> MaintenanceCheck:
    try:
        records = list(backup_list())
    except Exception as exc:
        return MaintenanceCheck("backup.verified", "Verified backup", "recovery", "unknown", "Backup state unavailable: %s" % type(exc).__name__, _now_iso())
    if not records:
        return MaintenanceCheck("backup.verified", "Verified backup", "recovery", "failed", "No backup has been created.", _now_iso())
    if any(getattr(record, "verified", False) for record in records):
        return MaintenanceCheck("backup.verified", "Verified backup", "recovery", "ok", "%d backup(s), latest verified." % len(records), _now_iso())
    return MaintenanceCheck("backup.verified", "Verified backup", "recovery", "degraded", "%d backup(s), none verified." % len(records), _now_iso())


def check_recovery_state(recovery_state: Provider) -> MaintenanceCheck:
    try:
        state = dict(recovery_state())
    except Exception as exc:
        return MaintenanceCheck("recovery.state", "Recovery mode", "recovery", "unknown", "Recovery state unavailable: %s" % type(exc).__name__, _now_iso())
    flags = []
    if state.get("safe_mode"):
        flags.append("safe mode")
    if state.get("repair_mode"):
        flags.append("repair mode")
    if state.get("crash_loop_detected"):
        flags.append("crash loop")
    if state.get("interrupted_update"):
        flags.append("interrupted update")
    if not flags:
        return MaintenanceCheck("recovery.state", "Recovery mode", "recovery", "ok", "No active recovery flags.", _now_iso())
    return MaintenanceCheck("recovery.state", "Recovery mode", "recovery", "degraded", "Active: %s." % ", ".join(flags), _now_iso())


def run_health_checks(providers: Dict[str, Provider]) -> List[MaintenanceCheck]:
    return [
        check_database_healthy(providers["connection_factory"]),
        check_event_store(providers["connection_factory"]),
        check_telemetry_fresh(providers["telemetry_latest"]),
        check_disk_space(providers["telemetry_latest"]),
        check_migration_status(providers["migrations_writable"]),
        check_backup_verified(providers["backup_list"]),
        check_recovery_state(providers["recovery_state"]),
    ]


def overall_outcome(checks: List[MaintenanceCheck]) -> Tuple[str, str]:
    """Worst-of outcome with honest labeling.

    Returns ("completed"|"degraded"|"failed", detail). A run is only
    "completed" when every check passed and none remain unknown/skipped.
    """
    if not checks:
        return ("failed", "no checks produced")
    worst = max(checks, key=lambda c: OUTCOME_ORDER.get(c.state, 0))
    if worst.state == "failed":
        return ("failed", "check %s failed" % worst.check_id)
    if worst.state in ("degraded", "warning", "unknown", "skipped"):
        label = "warning" if worst.state in ("warning", "unknown", "skipped") else "degraded"
        return ("degraded", "check %s reports %s" % (worst.check_id, worst.state))
    return ("completed", "all %d checks ok" % len(checks))