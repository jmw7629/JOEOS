"""Security gate for the JoeOS Automation Engine.

Consults the authoritative Security Platform before every workflow action:
the deny-by-default policy engine is evaluated, the decision is audited, and
workflow secrets are mediated through the Security Platform's Secret Broker
(instead of the engine's own availability-only check). This makes the Security
Platform authoritative over privileged workflow actions without duplicating
any identity, approval, or secret system.
"""

from __future__ import annotations

import uuid
from typing import Any, Callable, Dict, Optional

from server.security.models import PolicyRequestContext

SECRET_MEDIUM_RISK_ACTIONS = {
    "joeos.comms.request_send_approval",
    "joeos.notification",
    "joeos.comms.draft",
}

# Safe core automation actions that are already gated by the workflow
# permission system. They are audited as allowed; everything else is
# evaluated against the deny-by-default policy engine.
SAFE_CORE_ACTIONS = {
    "joeos.notification",
    "joeos.comms.notification",
    "joeos.comms.internal_message",
    "joeos.comms.draft",
    "joeos.comms.digest",
    "joeos.transform",
    "joeos.delay",
    "joeos.audit_marker",
    "joeos.git.status",
    "joeos.memory.propose",
}


class AutomationSecurityGate:
    def __init__(
        self,
        *,
        policy_evaluate: Callable[[PolicyRequestContext], Any],
        audit_record: Callable[..., Any],
        secret_broker=None,
        actor_type: str = "workflow",
    ) -> None:
        self._policy_evaluate = policy_evaluate
        self._audit = audit_record
        self._secret_broker = secret_broker
        self._actor_type = actor_type

    def check_action(
        self,
        *,
        workflow_id: str,
        workflow_version: str,
        project: str,
        action: str,
        risk: str,
        target: str = "",
    ) -> None:
        trace_id = "trace_" + uuid.uuid4().hex[:16]
        if action in SAFE_CORE_ACTIONS:
            self._audit(
                actor=workflow_id,
                actor_type=self._actor_type,
                action=action,
                target=target,
                project=project,
                workflow=workflow_id,
                permission_decision="allow",
                policy_version=1,
                result="allowed",
                risk=risk,
                trace_id=trace_id,
            )
            return
        context = PolicyRequestContext(
            subject=workflow_id,
            subject_type=self._actor_type,
            workflow=workflow_id,
            project=project,
            action=action,
            target=target,
            risk=risk,
            trace_id=trace_id,
        )
        decision = self._policy_evaluate(context)
        if decision.effect == "deny":
            self._audit(
                actor=workflow_id,
                actor_type=self._actor_type,
                action=action,
                target=target,
                project=project,
                workflow=workflow_id,
                permission_decision="deny",
                policy_version=decision.policy_version,
                result="denied",
                risk=risk,
                trace_id=trace_id,
            )
            raise PermissionDeniedError(
                "workflow action %s denied by security policy: %s"
                % (action, decision.explanation)
            )
        self._audit(
            actor=workflow_id,
            actor_type=self._actor_type,
            action=action,
            target=target,
            project=project,
            workflow=workflow_id,
            permission_decision=decision.effect,
            policy_version=decision.policy_version,
            result="allowed",
            risk=risk,
            trace_id=trace_id,
        )

    def mediate_secret(
        self,
        *,
        workflow_id: str,
        secret_reference: str,
        purpose: str,
        destination: str = "",
    ) -> Optional[str]:
        """Resolve a workflow secret reference through the authoritative
        Secret Broker when available; returns the value only to the
        in-process privileged action."""
        if self._secret_broker is None:
            return None
        try:
            return self._secret_broker.retrieve(
                secret_id=secret_reference,
                subject=workflow_id,
                purpose=purpose,
                destination=destination,
            )
        except Exception:
            return None


class PermissionDeniedError(RuntimeError):
    pass