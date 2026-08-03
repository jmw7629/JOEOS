"""JoeOS Security Platform.

A zero-trust, deny-by-default hardening layer enforced in authoritative
services. Provides the Security Policy Registry, Policy Evaluation Engine,
Identity and Scope services, Approval service with exact binding and strength
levels, the authoritative Secret Broker (AES-GCM at rest, rotation,
revocation, detection), Audit Log with hash-chain integrity, Security Events,
Incidents, Lockdown, Emergency Stop, Quarantine, Circuit Breakers, Data
Classification, Privacy Policy Engine, and Threat Model Registry. No security
result is fabricated; UI reflects enforced backend state.

See `docs/architecture/SECURITY_PLATFORM.md` for the design and honest
guarantees.
"""

from .approvals import ApprovalService, arguments_hash, content_hash
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
    ApprovalStrength,
    AuditEvent,
    CircuitBreakerState,
    ConsentRecord,
    DataClass,
    IdentityRecord,
    IncidentRecord,
    LockdownState,
    PolicyDecision,
    PolicyEffect,
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
from .router import router as security_router
from .secrets import SecretBroker
from .service import SecurityService
from .storage import SecurityStorage

# Existing transport and application security boundaries (preserved).
from .http_boundary import BoundaryRejection, HttpRequestBoundary
from .enrollment_guard import EnrollmentRequestGuardMiddleware

__all__ = [
    "ApprovalRequestRecord",
    "ApprovalService",
    "ApprovalStrength",
    "AuditEvent",
    "AuditService",
    "BoundaryRejection",
    "CircuitBreakerRegistry",
    "CircuitBreakerState",
    "ConsentRecord",
    "DataClass",
    "DataClassificationService",
    "EnrollmentRequestGuardMiddleware",
    "GovernanceService",
    "HttpRequestBoundary",
    "IdentityRecord",
    "IdentityRegistry",
    "IncidentRecord",
    "IncidentService",
    "LockdownState",
    "PolicyDecision",
    "PolicyEffect",
    "PolicyEvaluationEngine",
    "PolicyRequestContext",
    "PolicyRegistry",
    "PrivacyPolicyEngine",
    "ScopeGrant",
    "ScopeResolver",
    "SecretBroker",
    "SecretDetection",
    "SecretMetadata",
    "SecurityError",
    "SecurityEvent",
    "SecurityEventService",
    "SecurityOverview",
    "SecurityPolicy",
    "SecurityService",
    "SecurityStorage",
    "ThreatModel",
    "ThreatModelRegistry",
    "arguments_hash",
    "content_hash",
    "security_router",
]