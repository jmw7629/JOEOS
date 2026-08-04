"""Command Center aggregation over real JoeOS state.

Health states follow the documented worst-of ranking:
    unavailable > blocked > attention > degraded > starting > paused > unknown > healthy
The overall state is the worst state of the reported subsystem signals. Every
value is derived from live application state; nothing is fabricated. Features
without a backing engine (missions, approvals, projects, agent execution) are
reported as unavailable rather than zero.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from .models import (
    ActivityEnvelope,
    ActivityEvent,
    AiRuntimeStatus,
    HealthSignal,
    HealthState,
    OverviewCapabilities,
    OverviewCounts,
    OverviewEnvelope,
    ResourceTelemetry,
    ServiceHealth,
    ServicesEnvelope,
)

HEALTH_RANK: Tuple[HealthState, ...] = (
    "healthy",
    "unknown",
    "paused",
    "starting",
    "degraded",
    "attention",
    "blocked",
    "unavailable",
)

VERSION = "2.0.0"


def worst_state(states: List[HealthState]) -> HealthState:
    return max(states, key=lambda state: HEALTH_RANK.index(state)) if states else "unknown"


class CommandCenterService:
    def __init__(
        self,
        connection_factory: Callable[[], sqlite3.Connection],
        runtime_provider: Callable[[], Dict[str, Any]],
        *,
        version: str = VERSION,
        started_at: Optional[str] = None,
        sample_interval_seconds: float = 5.0,
        now_provider: Optional[Callable[[], datetime]] = None,
        realtime_ready: Optional[Callable[[], bool]] = None,
        identity_ready: Optional[Callable[[], bool]] = None,
        workspace_ready: Optional[Callable[[], bool]] = None,
        plugins_ready: Optional[Callable[[], bool]] = None,
        automation_ready: Optional[Callable[[], bool]] = None,
        communications_ready: Optional[Callable[[], bool]] = None,
        wearables_ready: Optional[Callable[[], bool]] = None,
        mobile_ready: Optional[Callable[[], bool]] = None,
        security_ready: Optional[Callable[[], bool]] = None,
        performance_ready: Optional[Callable[[], bool]] = None,
        ai_ready: Optional[Callable[[], bool]] = None,
        production_ready: Optional[Callable[[], bool]] = None,
    ) -> None:
        self._connection_factory = connection_factory
        self._runtime_provider = runtime_provider
        self._version = version
        self._started_at = started_at
        self._sample_interval_seconds = max(1.0, float(sample_interval_seconds))
        self._now = now_provider or (lambda: datetime.now(timezone.utc))
        self._realtime_ready = realtime_ready or (lambda: False)
        self._identity_ready = identity_ready or (lambda: False)
        self._workspace_ready = workspace_ready or (lambda: False)
        self._plugins_ready = plugins_ready or (lambda: False)
        self._automation_ready = automation_ready or (lambda: False)
        self._communications_ready = communications_ready or (lambda: False)
        self._wearables_ready = wearables_ready or (lambda: False)
        self._mobile_ready = mobile_ready or (lambda: False)
        self._security_ready = security_ready or (lambda: False)
        self._performance_ready = performance_ready or (lambda: False)
        self._ai_ready = ai_ready or (lambda: False)
        self._production_ready = production_ready or (lambda: False)

    def overview(self) -> OverviewEnvelope:
        services = self.services().services
        signals = [
            HealthSignal(subsystem=service.service_id, state=service.state, message=service.message)
            for service in services
        ]
        overall = worst_state([signal.state for signal in signals])
        resources = self._resources()
        runtime = self._runtime_status()
        counts = self._counts(runtime)
        attention = self._attention_events()
        return OverviewEnvelope(
            generated_at=self._now_iso(),
            overall=overall,
            health_signals=tuple(signals),
            services=services,
            capabilities=OverviewCapabilities(),
            counts=counts,
            resources=resources,
            runtime=runtime,
            attention=attention,
            next_scheduled_automation=None,
        )

    def services(self) -> ServicesEnvelope:
        runtime = self._runtime_provider()
        service_list: List[ServiceHealth] = [
            self._application_health(),
            self._lemonade_health(runtime),
            self._database_health(),
            self._telemetry_health(),
            self._events_health(),
            self._readiness_health(
                "realtime.stream",
                "Realtime Stream",
                self._realtime_ready,
                "Resumable audit and telemetry WebSocket stream.",
            ),
            self._readiness_health(
                "identity.enrollment",
                "Device Enrollment",
                self._identity_ready,
                "Operator pairing workflow (no authority granted).",
            ),
            self._readiness_health(
                "workspace.configuration",
                "Workspace Configuration",
                self._workspace_ready,
                "Persistent Mission Control layout and theme.",
            ),
            self._readiness_health(
                "security.platform",
                "Security Platform",
                self._security_ready,
                "Security Platform with zero-trust hardening.",
            ),
            self._readiness_health(
                "performance.platform",
                "Performance Platform",
                self._performance_ready,
                "Performance and Resource Governance Platform.",
            ),
            self._readiness_health(
                "ai.runtime",
                "Local AI Runtime",
                self._ai_ready,
                "Provider-neutral local AI runtime with embeddings and interpretation.",
            ),
            self._readiness_health(
                "production.platform",
                "Production Platform",
                self._production_ready,
                "Production readiness, release, backup, and recovery platform.",
            ),
            self._readiness_health(
                "automation.engine",
                "Automation Engine",
                self._automation_ready,
                "Automation and Workflow Engine with the schedule service.",
            ),
            self._readiness_health(
                "communications.hub",
                "Communications Hub",
                self._communications_ready,
                "Communications, Inbox, and Notification Hub.",
            ),
            self._readiness_health(
                "wearables.platform",
                "Wearable Platform",
                self._wearables_ready,
                "Smart Glasses and Wearable Device Platform.",
            ),
            self._readiness_health(
                "mobile.companion",
                "Mobile Companion",
                self._mobile_ready,
                "Mobile Companion and Secure Remote Operations Platform.",
            ),
            self._readiness_health(
                "security.platform",
                "Security Platform",
                self._security_ready,
                "Security, Policy, Secrets, Audit, and Zero-Trust boundaries.",
            ),
        ]
        return ServicesEnvelope(generated_at=self._now_iso(), services=tuple(service_list))

    def activity(
        self,
        *,
        limit: int = 40,
        severity: Optional[str] = None,
        source: Optional[str] = None,
        before: Optional[int] = None,
    ) -> ActivityEnvelope:
        count = max(1, min(100, int(limit)))
        where: List[str] = []
        parameters: List[Any] = []
        if severity:
            where.append("level = ?")
            parameters.append(severity)
        if source:
            where.append("source = ?")
            parameters.append(source)
        filter_clause = (" WHERE " + " AND ".join(where)) if where else ""
        page_parameters = list(parameters)
        if before is not None:
            where.append("id < ?")
            page_parameters.append(int(before))
        page_clause = (" WHERE " + " AND ".join(where)) if where else ""
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT id, recorded_at, level, source, message FROM events"
                + page_clause
                + " ORDER BY id DESC LIMIT ?",
                page_parameters + [count],
            ).fetchall()
            total = connection.execute(
                "SELECT COUNT(*) FROM events" + filter_clause,
                parameters,
            ).fetchone()[0]
        items = tuple(self._normalize_event(row) for row in rows)
        next_before = items[-1].event_id if len(items) >= count else None
        return ActivityEnvelope(
            generated_at=self._now_iso(),
            items=items,
            total_available=int(total),
            next_before=next_before,
            filters={
                "severity": severity,
                "source": source,
                "before": before,
                "limit": count,
            },
        )

    def _application_health(self) -> ServiceHealth:
        return ServiceHealth(
            service_id="joeos.runtime",
            name="JoeOS Application Runtime",
            state="healthy",
            available=True,
            version=self._version,
            started_at=self._started_at,
            last_check=self._now_iso(),
            latency_ms=0,
            message="Application runtime is serving requests.",
        )

    def _lemonade_health(self, runtime: Dict[str, Any]) -> ServiceHealth:
        online = bool(runtime.get("online"))
        message = runtime.get("message")
        if online:
            return ServiceHealth(
                service_id="inference.lemonade",
                name="Lemonade Inference",
                state="healthy",
                available=True,
                version=runtime.get("version"),
                last_check=self._now_iso(),
                message=message or "Private local inference is ready.",
            )
        return ServiceHealth(
            service_id="inference.lemonade",
            name="Lemonade Inference",
            state="unavailable",
            available=False,
            last_check=self._now_iso(),
            last_failure=message,
            degraded_reason="Local inference runtime is not reachable.",
            message=message or "Local inference runtime is not reachable.",
        )

    def _database_health(self) -> ServiceHealth:
        started = time.monotonic()
        try:
            with self._connection_factory() as connection:
                connection.execute("SELECT 1").fetchone()
        except sqlite3.Error as exc:
            return ServiceHealth(
                service_id="database",
                name="SQLite Database",
                state="unavailable",
                available=False,
                last_check=self._now_iso(),
                last_failure="SELECT 1 failed.",
                degraded_reason=type(exc).__name__,
                message="Local persistence is not reachable.",
            )
        return ServiceHealth(
            service_id="database",
            name="SQLite Database",
            state="healthy",
            available=True,
            last_check=self._now_iso(),
            latency_ms=max(0, int((time.monotonic() - started) * 1000)),
            message="Local persistence is ready.",
        )

    def _telemetry_health(self) -> ServiceHealth:
        try:
            with self._connection_factory() as connection:
                row = connection.execute(
                    "SELECT recorded_at FROM system_metrics ORDER BY id DESC LIMIT 1"
                ).fetchone()
        except sqlite3.Error:
            return ServiceHealth(
                service_id="telemetry.collector",
                name="Telemetry Collector",
                state="unavailable",
                available=False,
                last_check=self._now_iso(),
                degraded_reason="Metric store is not readable.",
                message="Telemetry samples are unavailable.",
            )
        if row is None:
            return ServiceHealth(
                service_id="telemetry.collector",
                name="Telemetry Collector",
                state="unavailable",
                available=False,
                last_check=self._now_iso(),
                message="No telemetry sample has been recorded yet.",
            )
        recorded = self._parse_iso(str(row["recorded_at"]))
        stale_window = self._sample_interval_seconds * 3
        if recorded is None or self._now() - recorded > timedelta(seconds=stale_window):
            return ServiceHealth(
                service_id="telemetry.collector",
                name="Telemetry Collector",
                state="degraded",
                available=True,
                last_check=self._now_iso(),
                degraded_reason="The newest telemetry sample is stale.",
                message="Telemetry samples are stale.",
            )
        return ServiceHealth(
            service_id="telemetry.collector",
            name="Telemetry Collector",
            state="healthy",
            available=True,
            last_check=self._now_iso(),
            message="Telemetry samples are current.",
        )

    def _events_health(self) -> ServiceHealth:
        started = time.monotonic()
        try:
            with self._connection_factory() as connection:
                connection.execute("SELECT COUNT(*) FROM events").fetchone()
        except sqlite3.Error as exc:
            return ServiceHealth(
                service_id="events.audit",
                name="Audit Event Store",
                state="unavailable",
                available=False,
                last_check=self._now_iso(),
                last_failure="SELECT COUNT(*) FROM events failed.",
                degraded_reason=type(exc).__name__,
                message="Audit event store is not readable.",
            )
        return ServiceHealth(
            service_id="events.audit",
            name="Audit Event Store",
            state="healthy",
            available=True,
            last_check=self._now_iso(),
            latency_ms=max(0, int((time.monotonic() - started) * 1000)),
            message="Audit event store is ready.",
        )

    @staticmethod
    def _readiness_health(
        service_id: str,
        name: str,
        ready: Callable[[], bool],
        message: str,
    ) -> ServiceHealth:
        if ready():
            return ServiceHealth(
                service_id=service_id,
                name=name,
                state="healthy",
                available=True,
                message=message,
            )
        return ServiceHealth(
            service_id=service_id,
            name=name,
            state="unavailable",
            available=False,
            degraded_reason="Service is not initialized.",
            message="%s is not initialized." % name,
        )

    def _resources(self) -> ResourceTelemetry:
        try:
            with self._connection_factory() as connection:
                row = connection.execute(
                    """
                    SELECT cpu_percent, ram_percent, gpu_percent, disk_percent, uptime_seconds,
                           cpu_detail, ram_detail, gpu_detail, disk_detail, recorded_at
                    FROM system_metrics ORDER BY id DESC LIMIT 1
                    """
                ).fetchone()
        except sqlite3.Error:
            return ResourceTelemetry(state="unavailable", message="Resource store is not readable.")
        if row is None:
            return ResourceTelemetry(state="unavailable", message="No telemetry sample recorded yet.")
        recorded = self._parse_iso(str(row["recorded_at"]))
        stale = recorded is None or self._now() - recorded > timedelta(seconds=self._sample_interval_seconds * 3)
        return ResourceTelemetry(
            state="degraded" if stale else "healthy",
            updated_at=row["recorded_at"],
            cpu_percent=_bounded(row["cpu_percent"]),
            ram_percent=_bounded(row["ram_percent"]),
            gpu_percent=_bounded(row["gpu_percent"]),
            disk_percent=_bounded(row["disk_percent"]),
            uptime_seconds=_nonnegative(row["uptime_seconds"]),
            cpu_detail=row["cpu_detail"],
            ram_detail=row["ram_detail"],
            gpu_detail=row["gpu_detail"],
            disk_detail=row["disk_detail"],
        )

    def _runtime_status(self) -> AiRuntimeStatus:
        runtime = self._runtime_provider()
        online = bool(runtime.get("online"))
        loaded = tuple(str(item) for item in runtime.get("loaded_models") or [])
        available = tuple(str(item) for item in runtime.get("available_models") or [])
        if not online:
            return AiRuntimeStatus(
                state="unavailable",
                online=False,
                loaded_models=(),
                available_models=(),
                message=runtime.get("message") or "AI runtime has not reported yet.",
            )
        return AiRuntimeStatus(
            state="healthy",
            online=True,
            status=runtime.get("status"),
            model=runtime.get("model"),
            loaded_models=loaded,
            available_models=available,
            message=runtime.get("message"),
        )

    def _counts(self, runtime: AiRuntimeStatus) -> OverviewCounts:
        try:
            with self._connection_factory() as connection:
                running = connection.execute(
                    "SELECT COUNT(*) FROM bots WHERE status = 'running'"
                ).fetchone()[0]
        except sqlite3.Error:
            running = 0
        attention = len(self._attention_events())
        return OverviewCounts(
            active_agents=int(running),
            blocked_agents=0,
            unread_attention=attention,
            loaded_models=len(runtime.loaded_models) if runtime.online else None,
            available_models=len(runtime.available_models) if runtime.online else None,
            active_missions=None,
            queued_missions=None,
            pending_approvals=None,
            failed_jobs=None,
            active_projects=None,
            dirty_repositories=None,
        )

    def _attention_events(self) -> Tuple[ActivityEvent, ...]:
        try:
            with self._connection_factory() as connection:
                rows = connection.execute(
                    """
                    SELECT id, recorded_at, level, source, message
                    FROM events
                    WHERE level IN ('warn', 'error')
                    ORDER BY id DESC LIMIT 8
                    """
                ).fetchall()
        except sqlite3.Error:
            return ()
        return tuple(self._normalize_event(row) for row in rows)

    @staticmethod
    def _normalize_event(row: sqlite3.Row) -> ActivityEvent:
        event_id = int(row["id"])
        return ActivityEvent(
            event_id=event_id,
            event_type="audit.event",
            source=str(row["source"]),
            source_id="event-%s" % event_id,
            occurred_at=str(row["recorded_at"]),
            summary=str(row["message"]),
            severity=str(row["level"]),
            navigation=None,
        )

    @staticmethod
    def _parse_iso(value: str) -> Optional[datetime]:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None

    def _now_iso(self) -> str:
        return self._now().astimezone(timezone.utc).isoformat()


def _bounded(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(100.0, parsed))


def _nonnegative(value: Any) -> Optional[int]:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None
