"""Audit log with integrity, security events, incidents, lockdown, emergency
stop, quarantine, and circuit breakers for the JoeOS Security Platform.

The audit log is append-mostly with a hash chain: each event stores the hash
of the previous event, giving application-level tamper evidence. The actual
guarantee is stated honestly (accidental-modification resistance and tamper
evidence, not immutability against a local root).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .policy import SecurityError
from .models import (
    AuditEvent,
    CircuitBreakerState,
    IncidentRecord,
    LockdownState,
    SecurityEvent,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()



class AuditService:
    """Hash-chained audit log (application-level tamper evidence)."""

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()

    def record(self, event: AuditEvent) -> AuditEvent:
        with self._lock, self._connection_factory() as connection:
            row = connection.execute(
                "SELECT event_id, integrity_hash, sequence FROM security_audit ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            previous_hash = str(row["integrity_hash"]) if row else ""
            sequence = (int(row["sequence"]) if row else 0) + 1
            integrity = self._chain_hash(
                previous_hash, sequence, event.actor, event.action, event.target,
                event.result, event.permission_decision, event.trace_id,
            )
            event = AuditEvent(**{**event.model_dump(), "integrity_hash": integrity, "previous_hash": previous_hash, "timestamp": event.timestamp or _now()})
            connection.execute(
                """
                INSERT INTO security_audit (
                    event_id, timestamp, actor, actor_type, session, device, action, target,
                    project, task, mission, plugin, workflow, provider, permission_decision,
                    approval, policy_version, result, risk, source, trace_id, integrity_hash,
                    previous_hash, sequence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id, event.timestamp, event.actor, event.actor_type, event.session,
                    event.device, event.action, event.target, event.project, event.task,
                    event.mission, event.plugin, event.workflow, event.provider,
                    event.permission_decision, event.approval, event.policy_version, event.result,
                    event.risk, event.source, event.trace_id, integrity, previous_hash, sequence,
                ),
            )
        return event

    def list(self, *, limit: int = 100, actor: Optional[str] = None, action: Optional[str] = None) -> Tuple[AuditEvent, ...]:
        clauses: List[str] = []
        params: List[object] = []
        if actor:
            clauses.append("actor = ?")
            params.append(actor)
        if action:
            clauses.append("action = ?")
            params.append(action)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(max(1, min(500, int(limit))))
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM security_audit" + where + " ORDER BY sequence DESC LIMIT ?", params
            ).fetchall()
        return tuple(self._row(row) for row in rows)

    def verify_integrity(self) -> Tuple[bool, int]:
        """Verify the hash chain from the first event; returns (valid, count)."""
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM security_audit ORDER BY sequence ASC"
            ).fetchall()
        expected_previous = ""
        for row in rows:
            computed = self._chain_hash(
                expected_previous,
                int(row["sequence"]),
                str(row["actor"]),
                str(row["action"]),
                str(row["target"]),
                str(row["result"]),
                str(row["permission_decision"]),
                str(row["trace_id"]),
            )
            if computed != str(row["integrity_hash"]):
                return False, len(rows)
            expected_previous = computed
        return True, len(rows)

    def latest_checkpoint(self) -> Tuple[bool, Optional[str]]:
        valid, count = self.verify_integrity()
        if count == 0:
            return valid, None
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT integrity_hash FROM security_audit ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
        return valid, str(row["integrity_hash"]) if row else None

    @staticmethod
    def _chain_hash(previous_hash: str, sequence: int, actor: str, action: str, target: str, result: str, permission_decision: str, trace_id: str) -> str:
        payload = "|".join([previous_hash, str(sequence), actor, action, target, result, permission_decision, trace_id])
        return hashlib.sha256(("joeos-audit-chain-v1\0" + payload).encode()).hexdigest()

    @staticmethod
    def _row(row: sqlite3.Row) -> AuditEvent:
        return AuditEvent(
            event_id=str(row["event_id"]),
            timestamp=str(row["timestamp"]),
            actor=str(row["actor"]),
            actor_type=str(row["actor_type"]),
            session=str(row["session"]),
            device=str(row["device"]),
            action=str(row["action"]),
            target=str(row["target"]),
            project=str(row["project"]),
            task=str(row["task"]),
            mission=str(row["mission"]),
            plugin=str(row["plugin"]),
            workflow=str(row["workflow"]),
            provider=str(row["provider"]),
            permission_decision=str(row["permission_decision"]),
            approval=str(row["approval"]),
            policy_version=int(row["policy_version"]),
            result=str(row["result"]),
            risk=str(row["risk"]),
            source=str(row["source"]),
            trace_id=str(row["trace_id"]),
            integrity_hash=str(row["integrity_hash"]),
            previous_hash=str(row["previous_hash"]),
        )


class SecurityEventService:
    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()

    def record(self, event: SecurityEvent) -> SecurityEvent:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO security_events (
                    event_id, category, severity, confidence, evidence, affected_identity,
                    affected_project, affected_service, timestamp, recommended_action, status, trace_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
                """,
                (
                    event.event_id, event.category, event.severity, event.confidence,
                    event.evidence[:500], event.affected_identity, event.affected_project,
                    event.affected_service, event.timestamp or _now(),
                    event.recommended_action[:240], event.trace_id,
                ),
            )
        return event

    def list(self, *, status: str = "open", category: Optional[str] = None, limit: int = 50) -> Tuple[SecurityEvent, ...]:
        clauses: List[str] = ["status = ?"]
        params: List[object] = [status]
        if category:
            clauses.append("category = ?")
            params.append(category)
        params.append(max(1, min(200, int(limit))))
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM security_events WHERE " + " AND ".join(clauses) + " ORDER BY timestamp DESC LIMIT ?",
                params,
            ).fetchall()
        return tuple(
            SecurityEvent(
                event_id=str(row["event_id"]),
                category=str(row["category"]),
                severity=str(row["severity"]),
                confidence=str(row["confidence"]),
                evidence=str(row["evidence"]),
                affected_identity=str(row["affected_identity"]),
                affected_project=str(row["affected_project"]),
                affected_service=str(row["affected_service"]),
                timestamp=str(row["timestamp"]),
                recommended_action=str(row["recommended_action"]),
                status=str(row["status"]),
                trace_id=str(row["trace_id"]),
            )
            for row in rows
        )

    def resolve(self, event_id: str, *, status: str = "resolved") -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE security_events SET status = ? WHERE event_id = ?", (status, event_id)
            )


class IncidentService:
    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()

    def create(self, incident: IncidentRecord) -> IncidentRecord:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO security_incidents (
                    incident_id, title, severity, status, detection_source, affected_assets,
                    affected_identities, affected_secrets, timeline, evidence, containment,
                    eradication, recovery, residual_risk, owner, created_at, resolved_at
                ) VALUES (?, ?, ?, 'new', ?, ?, ?, ?, ?, ?, '', '', '', ?, ?, ?, '')
                """,
                (
                    incident.incident_id, incident.title, incident.severity,
                    incident.detection_source,
                    "\n".join(incident.affected_assets),
                    "\n".join(incident.affected_identities),
                    "\n".join(incident.affected_secrets),
                    json.dumps([dict(item) for item in incident.timeline]),
                    incident.evidence[:1000],
                    incident.residual_risk,
                    incident.owner,
                    incident.created_at or _now(),
                ),
            )
        return self.get(incident.incident_id)

    def get(self, incident_id: str) -> Optional[IncidentRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM security_incidents WHERE incident_id = ?", (incident_id,)
            ).fetchone()
        return self._row(row) if row else None

    def list(self, *, status: Optional[str] = None, limit: int = 50) -> Tuple[IncidentRecord, ...]:
        clause = " WHERE status = ?" if status else ""
        params: list = [status] if status else []
        params.append(max(1, min(200, int(limit))))
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM security_incidents" + clause + " ORDER BY created_at DESC LIMIT ?", params
            ).fetchall()
        return tuple(self._row(row) for row in rows)

    def update_status(self, incident_id: str, status: str) -> Optional[IncidentRecord]:
        with self._lock, self._connection_factory() as connection:
            resolved_at = _now() if status in {"resolved", "false_positive"} else ""
            connection.execute(
                "UPDATE security_incidents SET status = ?, resolved_at = ? WHERE incident_id = ?",
                (status, resolved_at, incident_id),
            )
        return self.get(incident_id)

    @staticmethod
    def _row(row: sqlite3.Row) -> IncidentRecord:
        return IncidentRecord(
            incident_id=str(row["incident_id"]),
            title=str(row["title"]),
            severity=str(row["severity"]),
            status=str(row["status"]),
            detection_source=str(row["detection_source"]),
            affected_assets=tuple(p for p in str(row["affected_assets"]).split("\n") if p),
            affected_identities=tuple(p for p in str(row["affected_identities"]).split("\n") if p),
            affected_secrets=tuple(p for p in str(row["affected_secrets"]).split("\n") if p),
            timeline=tuple(dict(item) for item in json.loads(str(row["timeline"]))),
            evidence=str(row["evidence"]),
            containment=str(row["containment"]),
            eradication=str(row["eradication"]),
            recovery=str(row["recovery"]),
            residual_risk=str(row["residual_risk"]),
            owner=str(row["owner"]),
            created_at=str(row["created_at"]),
            resolved_at=str(row["resolved_at"]),
        )


class GovernanceService:
    """Lockdown, Emergency Stop, and Quarantine."""

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection], cancellation_handlers=None) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()
        self._lockdown = LockdownState()
        self._emergency_stop = False
        self._cancellation_handlers = list(cancellation_handlers or [])

    # ---- lockdown ----

    def activate_lockdown(self, *, activated_by: str = "user", reason: str = "") -> LockdownState:
        self._lockdown = LockdownState(
            active=True,
            activated_by=activated_by,
            activated_at=_now(),
            reason=reason,
            restrictions=(
                "disable_third_party_plugins",
                "pause_workflows",
                "pause_agents",
                "block_cloud_providers",
                "block_external_communication",
                "block_remote_clients",
                "block_wearable_clients",
                "stop_public_listeners",
            ),
        )
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "INSERT INTO security_lockdown (id, payload) VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET payload = excluded.payload",
                (self._lockdown.model_dump_json(),),
            )
        return self._lockdown

    def deactivate_lockdown(self, *, reauthenticated: bool = False) -> LockdownState:
        if not reauthenticated:
            raise SecurityError("exiting Lockdown requires strong authentication.")
        self._lockdown = LockdownState(active=False)
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "INSERT INTO security_lockdown (id, payload) VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET payload = excluded.payload",
                (self._lockdown.model_dump_json(),),
            )
        return self._lockdown

    def lockdown(self) -> LockdownState:
        with self._connection_factory() as connection:
            row = connection.execute("SELECT payload FROM security_lockdown WHERE id = 1").fetchone()
        if row:
            payload = json.loads(str(row["payload"]))

            def _norm(value):
                if isinstance(value, list):
                    return tuple(_norm(item) for item in value)
                if isinstance(value, dict):
                    return {key: _norm(item) for key, item in value.items()}
                return value

            return LockdownState.model_validate(_norm(payload))
        return self._lockdown

    def lockdown_active(self) -> bool:
        return self.lockdown().active

    # ---- emergency stop ----

    def register_cancellation_handler(self, handler: Callable[[], int]) -> None:
        self._cancellation_handlers.append(handler)

    def emergency_stop(self) -> dict:
        """Stop/cancel active autonomous work. Reports actual cancelled
        counts and any incomplete cancellation honestly; never auto-restarts."""
        self._emergency_stop = True
        cancelled: Dict[str, int] = {"agents": 0, "workflows": 0, "tools": 0, "terminals": 0, "plugin_jobs": 0}
        incomplete: List[str] = []
        for handler in list(self._cancellation_handlers):
            try:
                result = handler()
                if isinstance(result, dict):
                    for key, value in result.items():
                        cancelled[key] = cancelled.get(key, 0) + int(value or 0)
                    if result.get("incomplete"):
                        incomplete.extend(result["incomplete"])
            except Exception as exc:  # isolation: one handler failure must not block others
                incomplete.append("handler failed: %s" % type(exc).__name__)
        return {
            "stopped": True,
            "cancelled": cancelled,
            "incomplete": incomplete,
            "automatic_restart": False,
        }

    def emergency_stop_active(self) -> bool:
        return self._emergency_stop

    def release_emergency_stop(self) -> None:
        self._emergency_stop = False

    # ---- quarantine ----

    def quarantine(self, *, kind: str, subject: str, reason: str) -> dict:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "INSERT INTO security_activity (event_id, kind, message, level, recorded_at) VALUES (?, ?, ?, 'warn', ?)",
                ("secact_" + uuid.uuid4().hex[:16], "quarantine",
                 "Quarantined %s %s: %s" % (kind, subject, reason[:200]), _now()),
            )
        return {"kind": kind, "subject": subject, "quarantined": True, "reason": reason}


class CircuitBreakerRegistry:
    """Per-target circuit breakers to prevent retry storms."""

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()

    def record_failure(self, *, target: str, error: str = "", threshold: int = 5) -> CircuitBreakerState:
        breaker_id = "breaker_" + target
        with self._lock, self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM security_circuit_breakers WHERE breaker_id = ?", (breaker_id,)
            ).fetchone()
            failures = (int(row["failures"]) if row else 0) + 1
            state = "open" if failures >= threshold else "closed"
            connection.execute(
                """
                INSERT INTO security_circuit_breakers (breaker_id, target, state, failures, opened_at, retry_after, last_error)
                VALUES (?, ?, ?, ?, ?, '', ?)
                ON CONFLICT(breaker_id) DO UPDATE SET
                    failures = excluded.failures, state = excluded.state, last_error = excluded.last_error
                """,
                (breaker_id, target, state, failures, _now() if state == "open" else "", error[:200]),
            )
        return self.get(breaker_id)

    def record_success(self, *, target: str) -> CircuitBreakerState:
        breaker_id = "breaker_" + target
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO security_circuit_breakers (breaker_id, target, state, failures, opened_at, retry_after, last_error)
                VALUES (?, ?, 'closed', 0, '', '', '')
                ON CONFLICT(breaker_id) DO UPDATE SET state = 'closed', failures = 0, last_error = ''
                """,
                (breaker_id, target),
            )
        return self.get(breaker_id)

    def is_open(self, *, target: str) -> bool:
        breaker = self.get("breaker_" + target)
        return bool(breaker and breaker.state == "open")

    def get(self, breaker_id: str) -> Optional[CircuitBreakerState]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM security_circuit_breakers WHERE breaker_id = ?", (breaker_id,)
            ).fetchone()
        if row is None:
            return None
        return CircuitBreakerState(
            breaker_id=str(row["breaker_id"]),
            target=str(row["target"]),
            state=str(row["state"]),
            failures=int(row["failures"]),
            opened_at=str(row["opened_at"]),
            retry_after=str(row["retry_after"]),
            last_error=str(row["last_error"]),
        )

    def list(self) -> Tuple[CircuitBreakerState, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM security_circuit_breakers ORDER BY target"
            ).fetchall()
        return tuple(
            CircuitBreakerState(
                breaker_id=str(row["breaker_id"]),
                target=str(row["target"]),
                state=str(row["state"]),
                failures=int(row["failures"]),
                opened_at=str(row["opened_at"]),
                retry_after=str(row["retry_after"]),
                last_error=str(row["last_error"]),
            )
            for row in rows
        )