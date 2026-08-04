"""SelfMaintenanceService — the authoritative Self-Maintenance and Continuous
Improvement facade.

Composes the MaintenanceCoordinator (real health checks, safe self-hygiene,
evidence-based improvement proposals) over live JoeOS services. Every value is
derived from real state: the Production platform (backups, migrations,
recovery), the Memory platform (expiry hygiene), and the local telemetry and
event stores. Nothing is fabricated, and no improvement is ever self-applied.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .maintenance import MaintenanceCoordinator
from .models import MaintenanceCheck, MaintenanceRun, ImprovementProposal

Provider = Callable[[], object]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SelfMaintenanceService:
    def __init__(
        self,
        data_dir: str,
        *,
        main_connection_factory: Callable[[], sqlite3.Connection],
        event_sink: Optional[Callable[[str, str, str], None]] = None,
        governance_blocked: Optional[Callable[[], Tuple[bool, str]]] = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        db_path = self.data_dir / "selfmaintenance.db"

        def connect() -> sqlite3.Connection:
            connection = sqlite3.connect(str(db_path), timeout=10)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 10000")
            return connection

        self._db_path = db_path
        self._main_connection_factory = main_connection_factory
        self._governance_blocked = governance_blocked or (lambda: (False, ""))
        self.coordinator = MaintenanceCoordinator(connect, event_sink=event_sink)
        self._providers: Dict[str, Provider] = {}
        self._wire_default_providers()

    def _wire_default_providers(self) -> None:
        self.coordinator.provide("connection_factory", self._main_connection_factory)
        self.coordinator.provide("telemetry_latest", self._telemetry_latest)
        self.coordinator.provide("backup_list", lambda: [])
        self.coordinator.provide("migrations_writable", lambda: (True, "no migration coordinator wired"))
        self.coordinator.provide("recovery_state", lambda: {})
        self.coordinator.provide("memory_due", lambda: 0)

    # ---- external wiring hooks (called by the backend) ----

    def set_provider(self, name: str, provider: Provider) -> None:
        self.coordinator.provide(name, provider)

    def register_executor(self, apply_action: str, executor: Callable[[], object]) -> None:
        self.coordinator.register_executor(apply_action, executor)

    def governance_blocked(self) -> Tuple[bool, str]:
        return self._governance_blocked()

    # ---- read paths ----

    def overview(self) -> Dict[str, Any]:
        checks = self.checks()
        from .checks import overall_outcome

        outcome, detail = overall_outcome(checks)
        proposals = self.proposals()
        last_run = self.last_run()
        return {
            "generated_at": _now_iso(),
            "health": {"state": outcome, "detail": detail},
            "checks": [_check_payload(check) for check in checks],
            "proposals": [_proposal_payload(proposal) for proposal in proposals],
            "last_run": last_run,
            "log": [_log_payload(entry) for entry in self.log(limit=12)],
        }

    def checks(self) -> List[MaintenanceCheck]:
        from .checks import run_health_checks

        return run_health_checks(self.coordinator._providers)

    def proposals(self) -> List[ImprovementProposal]:
        self.coordinator.run_improvements_pass()
        return self.coordinator.registry.list()

    def last_run(self) -> Optional[Dict[str, Any]]:
        self.coordinator._prepare()
        with self._db_connect() as connection:
            row = connection.execute(
                "SELECT run_id, started_at, finished_at, outcome, detail FROM maintenance_runs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return {
            "run_id": row["run_id"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "outcome": row["outcome"],
            "detail": row["detail"],
        }

    def log(self, limit: int = 50) -> list:
        return self.coordinator.log(limit=limit)

    def run_maintenance(self) -> Dict[str, Any]:
        run = self.coordinator.run()
        return _run_payload(run)

    def apply_improvement(self, improvement_id: str) -> Tuple[bool, str]:
        ok, detail = self.coordinator.registry.apply(improvement_id)
        if ok:
            self.coordinator.append_log("info", "improvement", "Applied %s." % improvement_id)
        else:
            self.coordinator.append_log("warn", "improvement", "Apply failed for %s: %s" % (improvement_id, detail))
        return (ok, detail)

    # ---- internals ----

    def _db_connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self._db_path), timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _telemetry_latest(self) -> Optional[Dict[str, Any]]:
        try:
            with self._main_connection_factory() as connection:
                row = connection.execute(
                    "SELECT disk_percent, recorded_at FROM system_metrics ORDER BY id DESC LIMIT 1"
                ).fetchone()
        except sqlite3.Error:
            return None
        if row is None:
            return None
        return {"disk_percent": row["disk_percent"], "recorded_at": str(row["recorded_at"])}


def _check_payload(check: MaintenanceCheck) -> Dict[str, Any]:
    return {
        "check_id": check.check_id,
        "name": check.name,
        "category": check.category,
        "state": check.state,
        "detail": check.detail,
        "measured_at": check.measured_at,
    }


def _proposal_payload(proposal: ImprovementProposal) -> Dict[str, Any]:
    return {
        "improvement_id": proposal.improvement_id,
        "title": proposal.title,
        "category": proposal.category,
        "evidence": list(proposal.evidence),
        "priority": proposal.priority,
        "state": proposal.state,
        "apply_action": proposal.apply_action,
        "detail": proposal.detail,
        "proposed_at": proposal.proposed_at,
        "resolved_at": proposal.resolved_at,
    }


def _run_payload(run: MaintenanceRun) -> Dict[str, Any]:
    return {
        "run_id": run.run_id,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "outcome": run.outcome,
        "detail": run.detail,
        "checks": [_check_payload(check) for check in run.checks],
        "remediations": [
            {"action": item.action, "detail": item.detail, "state": item.state}
            for item in run.remediations
        ],
    }


def _log_payload(entry) -> Dict[str, Any]:
    return {
        "entry_id": entry.entry_id,
        "recorded_at": entry.recorded_at,
        "level": entry.level,
        "action": entry.action,
        "detail": entry.detail,
    }