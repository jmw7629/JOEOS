"""SecurityService facade: one authoritative entry point into the JoeOS
Security Platform.

Composes the Policy Registry + Evaluation Engine, Identity Registry, Scope
Resolver, Approval Service, Secret Broker, Audit Service, Security Event
Service, Incident Service, Governance (Lockdown/Emergency Stop/Quarantine),
Circuit Breakers, Data Classification, Privacy Policy Engine, and Threat
Model Registry. Security is enforced in authoritative services, never in UI.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .approvals import ApprovalService
from .audit import (
    AuditService,
    CircuitBreakerRegistry,
    GovernanceService,
    IncidentService,
    SecurityEventService,
)
from .classify import DataClassificationService, PrivacyPolicyEngine, ThreatModelRegistry
from .identity import IdentityRegistry, ScopeResolver
from .models import (
    ApprovalRequestRecord,
    AuditEvent,
    CircuitBreakerState,
    ConsentRecord,
    DataClass,
    IdentityRecord,
    IncidentRecord,
    LockdownState,
    PolicyDecision,
    PolicyRequestContext,
    ScopeGrant,
    SecretDetection,
    SecretMetadata,
    SecurityEvent,
    SecurityOverview,
    SecurityPolicy,
    ThreatModel,
)
from .policy import PolicyEvaluationEngine, PolicyRegistry, SecurityError
from .secrets import SecretBroker
from .storage import SecurityStorage


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SecurityService:
    def __init__(self, data_dir: str, *, master_key: bytes, event_sink=None) -> None:
        self.storage = SecurityStorage(data_dir)
        self.storage.prepare()
        self._data_dir = Path(data_dir)
        self._event_sink = event_sink or (lambda level, source, message: None)

        self.policies = PolicyRegistry(self._connection_factory)
        self.policy_engine = PolicyEvaluationEngine(self.policies)
        self.identities = IdentityRegistry(self._connection_factory)
        self.scopes = ScopeResolver(self._connection_factory)
        self.approvals = ApprovalService(self._connection_factory)
        self.secrets = SecretBroker(self._connection_factory, master_key)
        self.audit = AuditService(self._connection_factory)
        self.events = SecurityEventService(self._connection_factory)
        self.incidents = IncidentService(self._connection_factory)
        self.governance = GovernanceService(self._connection_factory)
        self.circuit_breakers = CircuitBreakerRegistry(self._connection_factory)
        self.classifications = DataClassificationService(secrets=self.secrets)
        self.privacy = PrivacyPolicyEngine(self.classifications)
        self.threat_models = ThreatModelRegistry(self._connection_factory)

    def _connection_factory(self):
        connection = self.storage.connect()
        return _BorrowedConnection(connection)

    def prepare_defaults(self) -> None:
        self.policy_engine.seed_default_policies()
        self.threat_models.seed()

    # ------------------------------------------------------------------
    # Policy
    # ------------------------------------------------------------------

    def upsert_policy(self, policy: SecurityPolicy) -> SecurityPolicy:
        return self.policies.upsert(policy)

    def list_policies(self) -> Tuple[SecurityPolicy, ...]:
        return self.policies.list()

    def evaluate(self, context: PolicyRequestContext) -> PolicyDecision:
        return self.policy_engine.evaluate(context)

    # ------------------------------------------------------------------
    # Identity & scope
    # ------------------------------------------------------------------

    def register_identity(self, record: IdentityRecord) -> IdentityRecord:
        return self.identities.register(record)

    def list_identities(self) -> Tuple[IdentityRecord, ...]:
        return self.identities.list()

    def revoke_identity(self, identity_id: str, *, reason: str = "") -> None:
        self.identities.revoke(identity_id, reason=reason)

    def can_impersonate(self, actor_type: str, target_type: str) -> bool:
        return self.identities.can_impersonate(actor_type, target_type)

    def grant_scope(self, grant: ScopeGrant) -> ScopeGrant:
        return self.scopes.grant(grant)

    def revoke_scope(self, grant_id: str) -> None:
        self.scopes.revoke(grant_id)

    def scope_grants_for(self, subject: str) -> Tuple[ScopeGrant, ...]:
        return self.scopes.active_for(subject)

    def scope_granted(self, **kwargs) -> bool:
        return self.scopes.is_granted(**kwargs)

    def resolve_path(self, *, scope_root: str, candidate: str) -> Tuple[bool, str]:
        return self.scopes.resolve_resource_scope(scope_root=scope_root, candidate=candidate)

    # ------------------------------------------------------------------
    # Approvals & consent
    # ------------------------------------------------------------------

    def request_approval(self, **kwargs) -> ApprovalRequestRecord:
        return self.approvals.request(**kwargs)

    def approve(self, **kwargs) -> ApprovalRequestRecord:
        return self.approvals.approve(**kwargs)

    def deny_approval(self, **kwargs) -> ApprovalRequestRecord:
        return self.approvals.deny(**kwargs)

    def verify_approval_exact(self, **kwargs) -> bool:
        return self.approvals.verify_exact(**kwargs)

    def invalidate_approval(self, approval_id: str, *, reason: str = "") -> None:
        self.approvals.invalidate(approval_id, reason=reason)

    def pending_approvals(self) -> Tuple[ApprovalRequestRecord, ...]:
        return self.approvals.pending()

    def approvals_list(self, *, state: Optional[str] = None) -> Tuple[ApprovalRequestRecord, ...]:
        return self.approvals.list(state=state)

    def record_consent(self, **kwargs) -> ConsentRecord:
        return self.approvals.record_consent(**kwargs)

    def withdraw_consent(self, consent_id: str) -> None:
        self.approvals.withdraw_consent(consent_id)

    def consent_active(self, **kwargs) -> bool:
        return self.approvals.consent_active(**kwargs)

    # ------------------------------------------------------------------
    # Secrets
    # ------------------------------------------------------------------

    def create_secret(self, **kwargs) -> SecretMetadata:
        return self.secrets.create(**kwargs)

    def secret_metadata(self, secret_id: str) -> Optional[SecretMetadata]:
        return self.secrets.metadata(secret_id)

    def list_secrets(self) -> Tuple[SecretMetadata, ...]:
        return self.secrets.list()

    def retrieve_secret(self, **kwargs) -> str:
        return self.secrets.retrieve(**kwargs)

    def rotate_secret(self, **kwargs) -> SecretMetadata:
        return self.secrets.rotate(**kwargs)

    def revoke_secret(self, **kwargs) -> SecretMetadata:
        return self.secrets.revoke(**kwargs)

    def revoke_all_secrets(self) -> int:
        return self.secrets.revoke_all()

    def secrets_requiring_rotation(self, **kwargs) -> Tuple[SecretMetadata, ...]:
        return self.secrets.secrets_requiring_rotation(**kwargs)

    def scan_secret_text(self, *, text: str, source: str = "") -> Tuple[SecretDetection, ...]:
        return self.secrets.scan_text(text=text, source=source)

    def secret_detections(self, **kwargs) -> Tuple[SecretDetection, ...]:
        return self.secrets.detections(**kwargs)

    def resolve_secret_detection(self, detection_id: str, *, status: str = "false_positive") -> None:
        self.secrets.resolve_detection(detection_id, status=status)

    # ------------------------------------------------------------------
    # Audit / events / incidents
    # ------------------------------------------------------------------

    def audit_record(self, **kwargs) -> AuditEvent:
        return self.audit.record(AuditEvent(event_id="audit_" + uuid.uuid4().hex[:16], timestamp=_now(), **kwargs))

    def audit_list(self, **kwargs) -> Tuple[AuditEvent, ...]:
        return self.audit.list(**kwargs)

    def audit_verify_integrity(self) -> Tuple[bool, int]:
        return self.audit.verify_integrity()

    def audit_checkpoint(self) -> Tuple[bool, Optional[str]]:
        return self.audit.latest_checkpoint()

    def record_security_event(self, **kwargs) -> SecurityEvent:
        return self.events.record(SecurityEvent(event_id="secevent_" + uuid.uuid4().hex[:16], timestamp=_now(), **kwargs))

    def security_events(self, **kwargs) -> Tuple[SecurityEvent, ...]:
        return self.events.list(**kwargs)

    def resolve_security_event(self, event_id: str, *, status: str = "resolved") -> None:
        self.events.resolve(event_id, status=status)

    def create_incident(self, **kwargs) -> IncidentRecord:
        return self.incidents.create(IncidentRecord(incident_id="incident_" + uuid.uuid4().hex[:16], created_at=_now(), **kwargs))

    def incidents_list(self, **kwargs) -> Tuple[IncidentRecord, ...]:
        return self.incidents.list(**kwargs)

    def incident_update(self, incident_id: str, status: str) -> Optional[IncidentRecord]:
        return self.incidents.update_status(incident_id, status)

    # ------------------------------------------------------------------
    # Governance
    # ------------------------------------------------------------------

    def activate_lockdown(self, **kwargs) -> LockdownState:
        state = self.governance.activate_lockdown(**kwargs)
        self.audit_record(actor="user", action="lockdown_activate", result="allowed", risk="high")
        return state

    def deactivate_lockdown(self, *, reauthenticated: bool = False) -> LockdownState:
        return self.governance.deactivate_lockdown(reauthenticated=reauthenticated)

    def lockdown_state(self) -> LockdownState:
        return self.governance.lockdown()

    def lockdown_active(self) -> bool:
        return self.governance.lockdown_active()

    def emergency_stop(self) -> dict:
        result = self.governance.emergency_stop()
        self.audit_record(actor="user", action="emergency_stop", result="allowed", risk="high")
        return result

    def release_emergency_stop(self) -> None:
        self.governance.release_emergency_stop()

    def quarantine(self, **kwargs) -> dict:
        return self.governance.quarantine(**kwargs)

    # ------------------------------------------------------------------
    # Circuit breakers
    # ------------------------------------------------------------------

    def breaker_failure(self, **kwargs) -> CircuitBreakerState:
        return self.circuit_breakers.record_failure(**kwargs)

    def breaker_success(self, **kwargs) -> CircuitBreakerState:
        return self.circuit_breakers.record_success(**kwargs)

    def breaker_is_open(self, *, target: str) -> bool:
        return self.circuit_breakers.is_open(target=target)

    def breakers(self) -> Tuple[CircuitBreakerState, ...]:
        return self.circuit_breakers.list()

    # ------------------------------------------------------------------
    # Classification / privacy / threat models
    # ------------------------------------------------------------------

    def classify_data(self, **kwargs) -> str:
        return self.classifications.classify(**kwargs)

    def privacy_evaluate(self, **kwargs) -> Tuple:
        return self.privacy.evaluate(**kwargs)

    def privacy_notification_preview(self, **kwargs) -> bool:
        return self.privacy.allow_notification_preview(**kwargs)

    def privacy_semantic_indexing(self, **kwargs) -> bool:
        return self.privacy.allow_semantic_indexing(**kwargs)

    def threat_models_list(self) -> Tuple[ThreatModel, ...]:
        return self.threat_models.list()

    def threat_model_upsert(self, model: ThreatModel) -> ThreatModel:
        return self.threat_models.upsert(model)

    # ------------------------------------------------------------------
    # Overview
    # ------------------------------------------------------------------

    def overview(self) -> SecurityOverview:
        audit_valid, _ = self.audit.verify_integrity()
        return SecurityOverview(
            active_sessions=0,  # populated by integrations
            revoked_devices=0,
            revoked_mobile_clients=0,
            quarantined_plugins=0,
            pending_approvals=len(self.approvals.pending()),
            secrets_requiring_rotation=len(self.secrets.secrets_requiring_rotation()),
            security_events_open=len(self.events.list(status="open")),
            incidents_open=len(self.incidents.list()),
            audit_integrity_verified=audit_valid,
            lockdown_active=self.governance.lockdown_active(),
            public_listeners=0,
            untrusted_projects=0,
            generated_at=_now(),
        )

    def storage_stats(self) -> dict:
        return {"path": self.storage.path(), "size_bytes": self.storage.size_bytes(), "version": 1}

    def backup(self) -> Optional[str]:
        return self.storage.backup_to(str(self._data_dir))


class _BorrowedConnection:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def __enter__(self) -> sqlite3.Connection:
        return self._connection

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self._connection.commit()
        else:
            self._connection.rollback()