"""Maintenance Coordinator for the JoeOS Self-Maintenance platform.

Runs the real health-check battery against injected providers, applies only
self-hygiene remediations that never touch authority (bounded retention of the
self-maintenance registry's own tables), reconciles evidence-based improvement
proposals, and records every run and log entry with timestamps.

The coordinator never fabricates state and never performs an unsafe action:
any action that changes authority or data semantics (backups, memory expiry,
exiting Safe/Repair Mode) is expressed as an improvement proposal that requires
operator approval before application.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Tuple

from .checks import overall_outcome, run_health_checks
from .improvements import ImprovementRegistry, detect, reconcile
from .models import ImprovementProposal, MaintenanceLogEntry, MaintenanceRun, Remediation

Provider = Callable[[], object]

KEEP_RUNS = 50
KEEP_LOG = 200


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MaintenanceCoordinator:
    def __init__(
        self,
        connection_factory: Callable[[], sqlite3.Connection],
        *,
        event_sink: Optional[Callable[[str, str, str], None]] = None,
        now_provider: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._connection_factory = connection_factory
        self._event_sink = event_sink or (lambda level, source, message: None)
        self._now = now_provider or (lambda: datetime.now(timezone.utc))
        self.registry = ImprovementRegistry(connection_factory)
        self._providers: Dict[str, Provider] = {}
        self._observations: Dict[str, object] = {}

    # ---- provider wiring ----

    def provide(self, name: str, provider: Provider) -> None:
        self._providers[name] = provider

    def register_executor(self, apply_action: str, executor: Callable[[], object]) -> None:
        self.registry.register_executor(apply_action, executor)

    # ---- persistence ----

    def _prepare(self) -> None:
        with self._connection_factory() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS maintenance_runs (
                    run_id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    checks TEXT NOT NULL,
                    remediations TEXT NOT NULL,
                    detail TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS maintenance_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recorded_at TEXT NOT NULL,
                    level TEXT NOT NULL,
                    action TEXT NOT NULL,
                    detail TEXT NOT NULL
                )
                """
            )

    # ---- observations from live state ----

    def _collect_observations(self) -> Dict[str, object]:
        observations: Dict[str, object] = {}
        records = []
        try:
            records = list(self._call("backup_list") or [])
        except Exception:
            pass
        observations["total_backups"] = len(records)
        observations["verified_backups"] = sum(1 for r in records if bool(getattr(r, "verified", False)))
        try:
            observations["memory_due"] = _bounded_int(self._call("memory_due"))
        except Exception:
            observations["memory_due"] = None
        try:
            observations["safe_mode"] = bool(dict(self._call("recovery_state")).get("safe_mode"))
            observations["repair_mode"] = bool(dict(self._call("recovery_state")).get("repair_mode"))
        except Exception:
            observations["safe_mode"] = False
            observations["repair_mode"] = False
        try:
            latest = self._call("telemetry_latest")
            observations["no_telemetry"] = latest is None
        except Exception:
            observations["no_telemetry"] = True
        try:
            writable, _ = self._call("migrations_writable")
            observations["future_schema"] = not writable
        except Exception:
            observations["future_schema"] = False
        self._observations = observations
        return observations

    # ---- the maintenance pass ----

    def run(self) -> MaintenanceRun:
        self._prepare()
        started = self._now().astimezone(timezone.utc).isoformat()
        run_id = str(uuid.uuid4())
        checks = run_health_checks(self._providers)
        outcome, detail = overall_outcome(checks)

        observations = self._collect_observations()
        detected = detect(observations, proposed_at=started)
        existing = self.registry.list()
        merged = reconcile(existing, detected)
        for proposal in merged:
            self.registry.record(proposal)

        remediations = self._self_hygiene()

        finished = self._now().astimezone(timezone.utc).isoformat()
        run = MaintenanceRun(
            run_id=run_id,
            started_at=started,
            finished_at=finished,
            outcome=outcome,
            checks=tuple(checks),
            remediations=tuple(remediations),
            detail=detail,
        )
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO maintenance_runs (run_id, started_at, finished_at, outcome, checks, remediations, detail)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, started, finished, outcome, json.dumps(_run_payload(run)), json.dumps([_remediation_payload(r) for r in remediations]), detail),
            )
        if outcome == "failed":
            self._emit("error", "maintenance", "Maintenance run %s failed: %s" % (run_id, detail))
        elif outcome == "degraded":
            self._emit("warn", "maintenance", "Maintenance run %s degraded: %s" % (run_id, detail))
        else:
            self._emit("info", "maintenance", "Maintenance run %s completed: %s" % (run_id, detail))
        return run

    def run_improvements_pass(self) -> List[ImprovementProposal]:
        """Reconcile proposals without running a full maintenance pass."""
        self._prepare()
        observations = self._collect_observations()
        detected = detect(observations)
        existing = self.registry.list()
        merged = reconcile(existing, detected)
        for proposal in merged:
            self.registry.record(proposal)
        return merged

    # ---- self-hygiene remediations (never touch authority) ----

    def _self_hygiene(self) -> List[Remediation]:
        remediations: List[Remediation] = []
        try:
            with self._connection_factory() as connection:
                connection.execute(
                    "DELETE FROM maintenance_runs WHERE run_id NOT IN (SELECT run_id FROM maintenance_runs ORDER BY started_at DESC LIMIT ?)",
                    (KEEP_RUNS,),
                )
                deleted_runs = connection.execute("SELECT changes()").fetchone()[0]
                connection.execute(
                    "DELETE FROM maintenance_log WHERE id NOT IN (SELECT id FROM maintenance_log ORDER BY id DESC LIMIT ?)",
                    (KEEP_LOG,),
                )
                deleted_log = connection.execute("SELECT changes()").fetchone()[0]
            remediations.append(Remediation("retention.registry", "Bounded self-maintenance registry retention.", "applied"))
            if deleted_runs or deleted_log:
                remediations.append(Remediation("retention.registry", "Pruned %d old run(s) and %d log line(s)." % (deleted_runs, deleted_log), "applied"))
        except sqlite3.Error:
            remediations.append(Remediation("retention.registry", "Registry retention could not be applied.", "failed"))
        return remediations

    # ---- log ----

    def log(self, limit: int = 50) -> List[MaintenanceLogEntry]:
        self._prepare()
        count = max(1, min(200, int(limit)))
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM maintenance_log ORDER BY id DESC LIMIT ?", (count,)
            ).fetchall()
        return [MaintenanceLogEntry(entry_id=int(row["id"]), recorded_at=str(row["recorded_at"]), level=str(row["level"]), action=str(row["action"]), detail=str(row["detail"])) for row in rows]

    def append_log(self, level: str, action: str, detail: str) -> None:
        self._prepare()
        with self._connection_factory() as connection:
            connection.execute(
                "INSERT INTO maintenance_log (recorded_at, level, action, detail) VALUES (?, ?, ?, ?)",
                (_now_iso(), level, action, detail),
            )

    # ---- internals ----

    def _call(self, name: str) -> object:
        provider = self._providers.get(name)
        if provider is None:
            raise KeyError("provider %r is not wired" % name)
        return provider()

    def _emit(self, level: str, source: str, message: str) -> None:
        self.append_log(level, source, message)
        try:
            self._event_sink(level, "selfmaintenance", message)
        except Exception:
            pass


def _bounded_int(value: object) -> int:
    return max(0, int(value))


def _run_payload(run: MaintenanceRun) -> dict:
    return {
        "run_id": run.run_id,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "outcome": run.outcome,
        "detail": run.detail,
    }


def _remediation_payload(item: Remediation) -> dict:
    return {"action": item.action, "detail": item.detail, "state": item.state}