"""Security Policy Registry and Policy Evaluation Engine for the JoeOS
Security Platform.

The policy engine is deny-by-default: an action with no matching allow rule
is denied. Deny rules take precedence over allow rules at equal priority, and
higher-priority rules win. Policies are typed structured rules — never
JavaScript or eval. The engine never trusts a caller-supplied final result.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .models import (
    PolicyDecision,
    PolicyEffect,
    PolicyRequestContext,
    SecurityPolicy,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SecurityError(RuntimeError):
    pass


class PolicyRegistry:
    """Authoritative, versioned security policy store."""

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()

    def upsert(self, policy: SecurityPolicy) -> SecurityPolicy:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO security_policies (
                    policy_id, version, title, description, scope, scope_target, action,
                    resource, effect, priority, conditions, exceptions, authority, owner,
                    created_at, review_time, expiration, enabled, superseded
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(policy_id) DO UPDATE SET
                    version = excluded.version, title = excluded.title,
                    description = excluded.description, scope = excluded.scope,
                    scope_target = excluded.scope_target, action = excluded.action,
                    resource = excluded.resource, effect = excluded.effect,
                    priority = excluded.priority, conditions = excluded.conditions,
                    exceptions = excluded.exceptions, authority = excluded.authority,
                    owner = excluded.owner, review_time = excluded.review_time,
                    expiration = excluded.expiration, enabled = excluded.enabled,
                    superseded = excluded.superseded
                """,
                (
                    policy.policy_id,
                    policy.version,
                    policy.title,
                    policy.description,
                    policy.scope,
                    policy.scope_target,
                    policy.action,
                    policy.resource,
                    policy.effect,
                    policy.priority,
                    json.dumps(policy.conditions),
                    "\n".join(policy.exceptions),
                    policy.authority,
                    policy.owner,
                    policy.created_at or _now(),
                    policy.review_time,
                    policy.expiration,
                    1 if policy.enabled else 0,
                    1 if policy.superseded else 0,
                ),
            )
        return self.get(policy.policy_id)

    def get(self, policy_id: str) -> Optional[SecurityPolicy]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM security_policies WHERE policy_id = ?", (policy_id,)
            ).fetchone()
        return self._row(row) if row else None

    def list(self, *, enabled_only: bool = False) -> Tuple[SecurityPolicy, ...]:
        clause = " WHERE enabled = 1 AND superseded = 0" if enabled_only else ""
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM security_policies" + clause + " ORDER BY priority DESC"
            ).fetchall()
        return tuple(self._row(row) for row in rows)

    def matching(
        self, *, context: PolicyRequestContext
    ) -> Tuple[SecurityPolicy, ...]:
        """Return enabled policies whose scope/action match the request."""
        candidates = []
        for policy in self.list(enabled_only=True):
            if not self._scope_matches(policy, context):
                continue
            if policy.action and policy.action not in {"*", context.action}:
                continue
            if policy.resource and policy.resource not in {"*", context.target}:
                continue
            candidates.append(policy)
        return tuple(candidates)

    @staticmethod
    def _scope_matches(policy: SecurityPolicy, context: PolicyRequestContext) -> bool:
        target_value = {
            "organization": context.subject if context.subject_type == "organization" else "",
            "user": context.subject if context.subject_type == "human_user" else "",
            "host": context.device,
            "workspace": context.workspace,
            "project": context.project,
            "task": context.task,
            "mission": context.mission,
            "workflow": context.workflow,
            "plugin": context.plugin,
            "agent": context.agent,
            "device": context.device,
            "provider": context.provider,
            "identity": context.subject,
            "all": None,
        }.get(policy.scope, None)
        if target_value is None:
            return True
        if not policy.scope_target:
            return bool(target_value)
        return target_value == policy.scope_target

    @staticmethod
    def _row(row: sqlite3.Row) -> SecurityPolicy:
        return SecurityPolicy(
            policy_id=str(row["policy_id"]),
            version=int(row["version"]),
            title=str(row["title"]),
            description=str(row["description"]),
            scope=str(row["scope"]),
            scope_target=str(row["scope_target"]),
            action=str(row["action"]),
            resource=str(row["resource"]),
            effect=str(row["effect"]),
            priority=int(row["priority"]),
            conditions=json.loads(str(row["conditions"])),
            exceptions=tuple(p for p in str(row["exceptions"]).split("\n") if p),
            authority=str(row["authority"]),
            owner=str(row["owner"]),
            created_at=str(row["created_at"]),
            review_time=str(row["review_time"]),
            expiration=str(row["expiration"]),
            enabled=bool(row["enabled"]),
            superseded=bool(row["superseded"]),
        )


# Deterministic precedence: explicit denies dominate at equal priority; higher
# priority wins; if only non-deny rules match, the highest-priority wins.
_PRECEDENCE = {
    "deny": 0,
    "require_approval": 1,
    "require_stronger_authentication": 2,
    "require_local_only": 3,
    "require_redaction": 4,
    "require_review": 5,
    "limit_scope": 6,
    "limit_duration": 7,
    "limit_resources": 8,
    "log": 9,
    "alert": 10,
    "allow": 11,
}


class PolicyEvaluationEngine:
    """Deny-by-default policy evaluation with deterministic precedence."""

    def __init__(self, registry: PolicyRegistry) -> None:
        self._registry = registry
        self._lock = threading.RLock()

    def evaluate(self, context: PolicyRequestContext) -> PolicyDecision:
        matched = self._registry.matching(context=context)
        decision_id = "decision_" + uuid.uuid4().hex[:16]
        if not matched:
            return PolicyDecision(
                decision_id=decision_id,
                effect="deny",
                explanation="No allow rule matched; denied by default.",
                trace_id=context.trace_id,
            )
        # Sort by (priority DESC, precedence rank ASC) so a deny at equal
        # priority wins over an allow.
        ranked = sorted(
            matched,
            key=lambda p: (-p.priority, _PRECEDENCE.get(p.effect, 50)),
        )
        top = ranked[0]
        if top.effect == "deny":
            return PolicyDecision(
                decision_id=decision_id,
                effect="deny",
                matched_rules=tuple(p.policy_id for p in matched),
                denied_rules=(top.policy_id,),
                explanation=top.description or ("Denied by policy %s." % top.policy_id),
                trace_id=context.trace_id,
            )
        required_approval = None
        required_auth = "none"
        for policy in matched:
            if policy.effect == "require_approval":
                required_approval = policy.policy_id
            if policy.effect == "require_stronger_authentication":
                required_auth = "reauthentication"
        return PolicyDecision(
            decision_id=decision_id,
            effect=top.effect,
            matched_rules=tuple(p.policy_id for p in matched),
            explanation=top.description or ("Allowed by policy %s." % top.policy_id),
            required_approval=required_approval,
            required_authentication=required_auth,
            trace_id=context.trace_id,
        )

    def deny_by_default(self, context: PolicyRequestContext) -> bool:
        return self.evaluate(context).effect == "deny"

    def seed_default_policies(self) -> None:
        """Seed conservative defaults: deny-by-default posture is structural,
        plus explicit baseline policies for known high-risk actions."""
        defaults = [
            SecurityPolicy(
                policy_id="policy.deny_arbitrary_shell",
                title="Deny arbitrary shell execution",
                description="No identity, agent, plugin, or workflow may execute arbitrary shell by default.",
                scope="all",
                action="shell_execute",
                effect="deny",
                priority=100,
                authority="joeos_security",
            ),
            SecurityPolicy(
                policy_id="policy.deny_force_push",
                title="Deny force push by default",
                description="Force-push requires an explicit high-risk approval.",
                scope="all",
                action="git_force_push",
                effect="deny",
                priority=100,
                authority="joeos_security",
            ),
            SecurityPolicy(
                policy_id="policy.deny_public_listener",
                title="Deny public inbound listeners",
                description="Binding a public listener requires approval.",
                scope="all",
                action="bind_public_listener",
                effect="deny",
                priority=100,
                authority="joeos_security",
            ),
            SecurityPolicy(
                policy_id="policy.secret_export_approval",
                title="Require approval for secret export",
                description="Exporting secrets requires a strong approval.",
                scope="all",
                action="export_secret",
                effect="require_approval",
                priority=80,
                authority="joeos_security",
            ),
            SecurityPolicy(
                policy_id="policy.workflow_import_disabled",
                title="Imported workflows start disabled",
                description="Imported workflows must remain disabled until review.",
                scope="all",
                action="workflow_import_activate",
                effect="deny",
                priority=90,
                authority="joeos_security",
            ),
        ]
        for policy in defaults:
            if self._registry.get(policy.policy_id) is None:
                self._registry.upsert(policy)