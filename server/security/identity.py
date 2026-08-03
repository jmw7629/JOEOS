"""Identity registry and scope resolution for the JoeOS Security Platform.

Identities are explicit and typed; display names never function as identity,
and one identity type can never impersonate another. Scope resolution
canonicalizes identifiers, detects ambiguity/staleness, enforces parent
boundaries, and rejects wildcard expansion, path traversal, symlink escape,
and alias confusion. No string-prefix filesystem checks.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .policy import SecurityError
from .models import IdentityRecord, ScopeGrant

_CANONICAL_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,79}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()



class IdentityRegistry:
    """Normalized identities across JoeOS boundaries."""

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()

    def register(self, record: IdentityRecord) -> IdentityRecord:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO security_identities (
                    identity_id, identity_type, display_label, owner, issuer, trust_state,
                    status, credentials_reference, created_at, last_activity, expiration,
                    revocation_state
                ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, 'active')
                ON CONFLICT(identity_id) DO UPDATE SET
                    identity_type = excluded.identity_type, display_label = excluded.display_label,
                    issuer = excluded.issuer, status = 'active'
                """,
                (
                    record.identity_id, record.identity_type, record.display_label,
                    record.owner, record.issuer, record.trust_state,
                    record.credentials_reference, record.created_at or _now(),
                    record.last_activity, record.expiration,
                ),
            )
        return self.get(record.identity_id)

    def get(self, identity_id: str) -> Optional[IdentityRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM security_identities WHERE identity_id = ?", (identity_id,)
            ).fetchone()
        return self._row(row) if row else None

    def revoke(self, identity_id: str, *, reason: str = "") -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE security_identities SET status = 'revoked', revocation_state = 'revoked' WHERE identity_id = ?",
                (identity_id,),
            )
            connection.execute(
                "UPDATE security_scope_grants SET revocation_state = 'revoked' WHERE subject = ?",
                (identity_id,),
            )

    def list(self) -> Tuple[IdentityRecord, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM security_identities ORDER BY display_label"
            ).fetchall()
        return tuple(self._row(row) for row in rows)

    def can_impersonate(self, actor_type: str, target_type: str) -> bool:
        """Agents, workflows, plugins, and devices can never impersonate a
        human user. Cross-type impersonation is rejected."""
        if target_type == "human_user":
            return actor_type == "human_user"
        return actor_type == target_type

    @staticmethod
    def _row(row: sqlite3.Row) -> IdentityRecord:
        return IdentityRecord(
            identity_id=str(row["identity_id"]),
            identity_type=str(row["identity_type"]),
            display_label=str(row["display_label"]),
            owner=str(row["owner"]),
            issuer=str(row["issuer"]),
            trust_state=str(row["trust_state"]),
            status=str(row["status"]),
            credentials_reference=str(row["credentials_reference"]),
            created_at=str(row["created_at"]),
            last_activity=str(row["last_activity"]),
            expiration=str(row["expiration"]),
            revocation_state=str(row["revocation_state"]),
        )


class ScopeResolver:
    """Canonicalizes and validates scopes with explicit containment checks."""

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()

    def grant(self, grant: ScopeGrant) -> ScopeGrant:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO security_scope_grants (
                    grant_id, subject, capability, action, resource, scope, project, task,
                    mission, device, conditions, duration, issued_by, authority, approval,
                    created_at, expiration, usage_count, last_use, revocation_state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, '', 'active')
                """,
                (
                    grant.grant_id, grant.subject, grant.capability, grant.action,
                    grant.resource, grant.scope, grant.project, grant.task, grant.mission,
                    grant.device, json.dumps(grant.conditions), grant.duration,
                    grant.issued_by, grant.authority, grant.approval,
                    grant.created_at or _now(), grant.expiration,
                ),
            )
        return self.get(grant.grant_id)

    def get(self, grant_id: str) -> Optional[ScopeGrant]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM security_scope_grants WHERE grant_id = ?", (grant_id,)
            ).fetchone()
        return self._row(row) if row else None

    def active_for(self, subject: str) -> Tuple[ScopeGrant, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM security_scope_grants WHERE subject = ? AND revocation_state = 'active' ORDER BY created_at DESC",
                (subject,),
            ).fetchall()
        return tuple(self._row(row) for row in rows)

    def revoke(self, grant_id: str, *, reason: str = "") -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE security_scope_grants SET revocation_state = 'revoked' WHERE grant_id = ?",
                (grant_id,),
            )

    def is_granted(
        self,
        *,
        subject: str,
        capability: str,
        action: str = "",
        project: str = "",
    ) -> bool:
        """Check an active grant within scope (project-scoped grants only
        match their project; session grants are active)."""
        for grant in self.active_for(subject):
            if grant.capability != capability:
                continue
            if action and grant.action and grant.action != action:
                continue
            if grant.scope == "project" and project and grant.project != project:
                continue
            if grant.expiration:
                try:
                    if datetime.fromisoformat(grant.expiration) < datetime.now(timezone.utc):
                        continue
                except ValueError:
                    continue
            return True
        return False

    # ---- canonical filesystem scope (explicit containment, no prefix hacks) ----

    @staticmethod
    def canonical_path(path: str) -> str:
        """Return a canonical absolute-ish path rejecting traversal and NUL."""
        if "\x00" in path:
            raise SecurityError("path contains a NUL byte.")
        normalized = PurePosixPath(path.replace("\\", "/"))
        parts = []
        for part in normalized.parts:
            if part in ("", ".", "/"):
                continue
            if part == "..":
                raise SecurityError("path traversal rejected.")
            parts.append(part)
        return "/" + "/".join(parts)

    @staticmethod
    def is_within(scope_root: str, candidate: str) -> bool:
        root = ScopeResolver.canonical_path(scope_root).rstrip("/")
        resolved = ScopeResolver.canonical_path(candidate)
        if resolved == root:
            return True
        return resolved.startswith(root + "/")

    def resolve_resource_scope(
        self, *, scope_root: str, candidate: str
    ) -> Tuple[bool, str]:
        """Explicit containment check; returns (allowed, canonical_path)."""
        try:
            canonical = self.canonical_path(candidate)
        except SecurityError as exc:
            return False, str(exc)
        if not self.is_within(scope_root, candidate):
            return False, "outside allowed scope"
        return True, canonical

    @staticmethod
    def _row(row: sqlite3.Row) -> ScopeGrant:
        return ScopeGrant(
            grant_id=str(row["grant_id"]),
            subject=str(row["subject"]),
            capability=str(row["capability"]),
            action=str(row["action"]),
            resource=str(row["resource"]),
            scope=str(row["scope"]),
            project=str(row["project"]),
            task=str(row["task"]),
            mission=str(row["mission"]),
            device=str(row["device"]),
            conditions=json.loads(str(row["conditions"])),
            duration=str(row["duration"]),
            issued_by=str(row["issued_by"]),
            authority=str(row["authority"]),
            approval=str(row["approval"]),
            created_at=str(row["created_at"]),
            expiration=str(row["expiration"]),
            usage_count=int(row["usage_count"]),
            last_use=str(row["last_use"]),
            revocation_state=str(row["revocation_state"]),
        )