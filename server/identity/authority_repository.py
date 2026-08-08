"""Authoritative application identity domain (Phase P3A).

Versioned, migration-safe SQLite records for users, organizations, workspaces,
memberships, roles, capabilities, principal role assignments, device principal
assignments, application sessions, session refresh credentials, authentication
challenges, and authentication events.

The existing enrolled-device public keys and enrollment identities remain
immutable. A device principal assignment references an existing enrolled device
and never rewrites its cryptographic identity.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional
from uuid import UUID

USER_STATUSES = ("active", "disabled", "locked", "deleted")
ORG_STATUSES = ("active", "disabled", "deleted")
WORKSPACE_STATUSES = ("active", "disabled", "deleted")
ROLE_STATUSES = ("active", "disabled", "deleted")
CAPABILITY_STATUSES = ("active", "disabled")
RISK_LEVELS = ("standard", "privileged", "critical")
SCOPE_TYPES = ("system", "organization", "workspace")
ASSIGNMENT_STATUSES = ("active", "revoked")
SESSION_STATUSES = ("active", "expired", "revoked", "logged_out")
CHALLENGE_STATUSES = ("open", "solved", "expired", "revoked")

SYSTEM_OWNER_ROLE_NAME = "joeos.owner"
SYSTEM_OPERATOR_ROLE_NAME = "joeos.operator"

# The exact message an enrolled device signs with its device-authentication key
# to prove possession and establish an application session.
APPLICATION_AUTHENTICATION_DOMAIN = (
    "JOEOS-APPLICATION-AUTH-V1\0"
    "{challenge_id}\0{device_id}\0{user_id}\0{server_nonce}\0{expires_at}"
)


@dataclass(frozen=True)
class AuthorityUserRecord:
    id: UUID
    display_name: str
    status: str
    created_at: int
    updated_at: int
    revision: int


@dataclass(frozen=True)
class AuthorityOrganizationRecord:
    id: UUID
    name: str
    status: str
    created_at: int
    updated_at: int
    revision: int


@dataclass(frozen=True)
class AuthorityWorkspaceRecord:
    id: UUID
    organization_id: UUID
    name: str
    status: str
    created_at: int
    updated_at: int
    revision: int


@dataclass(frozen=True)
class AuthorityRoleRecord:
    id: UUID
    organization_id: Optional[UUID]
    scope: str
    name: str
    description: str
    status: str
    immutable: bool
    created_at: int
    updated_at: int
    revision: int


@dataclass(frozen=True)
class AuthorityCapabilityRecord:
    name: str
    description: str
    risk: str
    scope_type: str
    status: str


@dataclass(frozen=True)
class DeviceAssignmentRecord:
    device_id: UUID
    user_id: UUID
    organization_id: UUID
    workspace_id: UUID
    status: str
    assigned_at: int
    assigned_by: UUID
    revoked_at: Optional[int]
    revision: int


@dataclass(frozen=True)
class ApplicationSessionRecord:
    session_id: UUID
    user_id: UUID
    device_id: UUID
    organization_id: UUID
    workspace_id: UUID
    status: str
    created_at: int
    expires_at: int
    revoked_at: Optional[int]
    principal_revision: int
    device_assignment_revision: int


@dataclass(frozen=True)
class AuthenticationChallengeRecord:
    challenge_id: UUID
    device_id: UUID
    user_id: UUID
    organization_id: UUID
    workspace_id: UUID
    server_nonce: bytes
    created_at: int
    expires_at: int
    state: str
    failed_attempts: int
    solved_at: Optional[int]


class AuthorityConflictError(Exception):
    """Raised when an authority operation conflicts with existing state."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message


class AuthorityNotFoundError(Exception):
    """Raised when a referenced authority record does not exist."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message


class SQLiteAuthorityRepository:
    """SQLite persistence for the authoritative JoeOS application identity."""

    default_session_lifetime_seconds = 15 * 60
    default_refresh_lifetime_seconds = 30 * 24 * 60 * 60
    maximum_challenge_failures = 5
    challenge_lifetime_seconds = 120
    refresh_token_bytes = 32
    nonce_bytes = 32

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory

    def prepare(self) -> None:
        with self._connection_factory() as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS authority_users (
                    id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL CHECK(length(display_name) BETWEEN 1 AND 120),
                    status TEXT NOT NULL CHECK(status IN ('active','disabled','locked','deleted')),
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    revision INTEGER NOT NULL CHECK(revision >= 1)
                );

                CREATE TABLE IF NOT EXISTS authority_organizations (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 160),
                    status TEXT NOT NULL CHECK(status IN ('active','disabled','deleted')),
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    revision INTEGER NOT NULL CHECK(revision >= 1)
                );

                CREATE TABLE IF NOT EXISTS authority_workspaces (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT NOT NULL
                        REFERENCES authority_organizations(id),
                    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 160),
                    status TEXT NOT NULL CHECK(status IN ('active','disabled','deleted')),
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    revision INTEGER NOT NULL CHECK(revision >= 1)
                );

                CREATE TABLE IF NOT EXISTS authority_roles (
                    id TEXT PRIMARY KEY,
                    organization_id TEXT
                        REFERENCES authority_organizations(id),
                    scope TEXT NOT NULL CHECK(scope IN ('system','organization','workspace')),
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL CHECK(status IN ('active','disabled','deleted')),
                    immutable INTEGER NOT NULL DEFAULT 0 CHECK(immutable IN (0,1)),
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    revision INTEGER NOT NULL CHECK(revision >= 1),
                    UNIQUE(scope, name)
                );

                CREATE TABLE IF NOT EXISTS authority_capabilities (
                    name TEXT PRIMARY KEY,
                    description TEXT NOT NULL DEFAULT '',
                    risk TEXT NOT NULL CHECK(risk IN ('standard','privileged','critical')),
                    scope_type TEXT NOT NULL CHECK(scope_type IN ('system','organization','workspace')),
                    status TEXT NOT NULL CHECK(status IN ('active','disabled'))
                );

                CREATE TABLE IF NOT EXISTS authority_role_capabilities (
                    role_id TEXT NOT NULL REFERENCES authority_roles(id),
                    capability_name TEXT NOT NULL
                        REFERENCES authority_capabilities(name),
                    PRIMARY KEY(role_id, capability_name)
                );

                CREATE TABLE IF NOT EXISTS authority_principal_role_assignments (
                    user_id TEXT NOT NULL REFERENCES authority_users(id),
                    role_id TEXT NOT NULL REFERENCES authority_roles(id),
                    organization_id TEXT NOT NULL REFERENCES authority_organizations(id),
                    workspace_id TEXT NOT NULL REFERENCES authority_workspaces(id),
                    status TEXT NOT NULL CHECK(status IN ('active','revoked')),
                    assigned_at INTEGER NOT NULL,
                    assigned_by TEXT NOT NULL,
                    revoked_at INTEGER,
                    revision INTEGER NOT NULL CHECK(revision >= 1),
                    PRIMARY KEY(user_id, role_id, organization_id, workspace_id)
                );

                CREATE TABLE IF NOT EXISTS authority_device_principal_assignments (
                    device_id TEXT PRIMARY KEY
                        REFERENCES enrolled_devices(device_id),
                    user_id TEXT NOT NULL REFERENCES authority_users(id),
                    organization_id TEXT NOT NULL REFERENCES authority_organizations(id),
                    workspace_id TEXT NOT NULL REFERENCES authority_workspaces(id),
                    status TEXT NOT NULL CHECK(status IN ('active','revoked')),
                    assigned_at INTEGER NOT NULL,
                    assigned_by TEXT NOT NULL,
                    revoked_at INTEGER,
                    revision INTEGER NOT NULL CHECK(revision >= 1)
                );

                CREATE TABLE IF NOT EXISTS authority_application_sessions (
                    session_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES authority_users(id),
                    device_id TEXT NOT NULL REFERENCES enrolled_devices(device_id),
                    organization_id TEXT NOT NULL REFERENCES authority_organizations(id),
                    workspace_id TEXT NOT NULL REFERENCES authority_workspaces(id),
                    status TEXT NOT NULL CHECK(status IN ('active','expired','revoked','logged_out')),
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL CHECK(expires_at > created_at),
                    revoked_at INTEGER,
                    principal_revision INTEGER NOT NULL,
                    device_assignment_revision INTEGER NOT NULL,
                    last_seen_at INTEGER
                );

                CREATE INDEX IF NOT EXISTS idx_authority_sessions_device
                ON authority_application_sessions(device_id, status);

                CREATE INDEX IF NOT EXISTS idx_authority_sessions_user
                ON authority_application_sessions(user_id, status);

                CREATE TABLE IF NOT EXISTS authority_session_refresh_credentials (
                    refresh_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL
                        REFERENCES authority_application_sessions(session_id),
                    credential_digest BLOB NOT NULL CHECK(length(credential_digest) = 32),
                    created_at INTEGER NOT NULL,
                    revoked_at INTEGER
                );

                CREATE TABLE IF NOT EXISTS authority_authentication_challenges (
                    challenge_id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL REFERENCES enrolled_devices(device_id),
                    user_id TEXT NOT NULL REFERENCES authority_users(id),
                    organization_id TEXT NOT NULL REFERENCES authority_organizations(id),
                    workspace_id TEXT NOT NULL REFERENCES authority_workspaces(id),
                    server_nonce BLOB NOT NULL CHECK(length(server_nonce) = 32),
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL CHECK(expires_at > created_at),
                    state TEXT NOT NULL CHECK(state IN ('open','solved','expired','revoked')),
                    failed_attempts INTEGER NOT NULL DEFAULT 0 CHECK(failed_attempts >= 0),
                    solved_at INTEGER
                );

                CREATE INDEX IF NOT EXISTS idx_authority_challenges_device_state
                ON authority_authentication_challenges(device_id, state, expires_at);

                CREATE TABLE IF NOT EXISTS authority_authentication_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    actor_id TEXT,
                    device_id TEXT,
                    session_id TEXT,
                    occurred_at INTEGER NOT NULL,
                    detail TEXT NOT NULL DEFAULT '' CHECK(length(detail) <= 240)
                );

                CREATE TRIGGER IF NOT EXISTS trg_authority_events_no_update
                BEFORE UPDATE ON authority_authentication_events
                BEGIN
                    SELECT RAISE(ABORT, 'authority authentication events are append-only');
                END;

                CREATE TRIGGER IF NOT EXISTS trg_authority_events_no_delete
                BEFORE DELETE ON authority_authentication_events
                BEGIN
                    SELECT RAISE(ABORT, 'authority authentication events are append-only');
                END;
                """
            )
            connection.commit()

    # ------------------------------------------------------------------
    # Bootstrap
    # ------------------------------------------------------------------

    def has_any_installation(self) -> bool:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT 1 FROM authority_organizations LIMIT 1"
            ).fetchone()
        return row is not None

    def bootstrap(
        self,
        *,
        user_id: UUID,
        organization_id: UUID,
        workspace_id: UUID,
        owner_role_id: UUID,
        display_name: str,
        organization_name: str,
        workspace_name: str,
        owner_role_name: str,
        capabilities: List[str],
        now: int,
    ) -> Dict[str, str]:
        """Creates the first installation. Idempotent: refuses a second owner."""
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT id FROM authority_organizations LIMIT 1"
                ).fetchone()
                if existing is not None:
                    connection.rollback()
                    raise AuthorityConflictError(
                        "installation_already_exists",
                        "JoeOS is already bootstrapped. Refusing to create a second owner.",
                    )
                connection.execute(
                    """
                    INSERT INTO authority_organizations(
                        id, name, status, created_at, updated_at, revision
                    ) VALUES (?, ?, 'active', ?, ?, 1)
                    """,
                    (str(organization_id), organization_name, now, now),
                )
                connection.execute(
                    """
                    INSERT INTO authority_workspaces(
                        id, organization_id, name, status, created_at, updated_at, revision
                    ) VALUES (?, ?, ?, 'active', ?, ?, 1)
                    """,
                    (str(workspace_id), str(organization_id), workspace_name, now, now),
                )
                connection.execute(
                    """
                    INSERT INTO authority_users(
                        id, display_name, status, created_at, updated_at, revision
                    ) VALUES (?, ?, 'active', ?, ?, 1)
                    """,
                    (str(user_id), display_name, now, now),
                )
                connection.execute(
                    """
                    INSERT INTO authority_roles(
                        id, organization_id, scope, name, description, status,
                        immutable, created_at, updated_at, revision
                    ) VALUES (?, ?, 'organization', ?, 'Local owner of this JoeOS installation',
                              'active', 1, ?, ?, 1)
                    """,
                    (
                        str(owner_role_id),
                        str(organization_id),
                        owner_role_name,
                        now,
                        now,
                    ),
                )
                for capability in capabilities:
                    self._ensure_capability_row(connection, capability, now)
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO authority_role_capabilities(role_id, capability_name)
                        VALUES (?, ?)
                        """,
                        (str(owner_role_id), capability),
                    )
                connection.execute(
                    """
                    INSERT INTO authority_principal_role_assignments(
                        user_id, role_id, organization_id, workspace_id, status,
                        assigned_at, assigned_by, revision
                    ) VALUES (?, ?, ?, ?, 'active', ?, ?, 1)
                    """,
                    (
                        str(user_id),
                        str(owner_role_id),
                        str(organization_id),
                        str(workspace_id),
                        now,
                        str(user_id),
                    ),
                )
                self._auth_event(
                    connection,
                    "authority.bootstrap",
                    now,
                    actor_id=user_id,
                    detail="local owner installation created",
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return {
            "user_id": str(user_id),
            "organization_id": str(organization_id),
            "workspace_id": str(workspace_id),
            "owner_role_id": str(owner_role_id),
        }

    def seed_capabilities(self, capabilities: List[str], now: int) -> None:
        """Idempotently ensures the minimal capability catalog exists."""
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                for capability in capabilities:
                    self._ensure_capability_row(connection, capability, now)
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    def list_users(self) -> List[AuthorityUserRecord]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM authority_users ORDER BY created_at"
            ).fetchall()
        return [self._user(row) for row in rows]

    def get_user(self, user_id: UUID) -> Optional[AuthorityUserRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM authority_users WHERE id = ?", (str(user_id),)
            ).fetchone()
        return self._user(row) if row is not None else None

    def list_organizations(self) -> List[AuthorityOrganizationRecord]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM authority_organizations ORDER BY created_at"
            ).fetchall()
        return [self._organization(row) for row in rows]

    def list_workspaces(self) -> List[AuthorityWorkspaceRecord]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM authority_workspaces ORDER BY created_at"
            ).fetchall()
        return [self._workspace(row) for row in rows]

    def list_roles(self) -> List[AuthorityRoleRecord]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM authority_roles ORDER BY created_at"
            ).fetchall()
        return [self._role(row) for row in rows]

    def list_capabilities(self) -> List[AuthorityCapabilityRecord]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM authority_capabilities ORDER BY name"
            ).fetchall()
        return [self._capability(row) for row in rows]

    def list_devices(self) -> List[Dict[str, object]]:
        """Enrolled devices with their current assignment state."""
        with self._connection_factory() as connection:
            rows = connection.execute(
                """
                SELECT device.device_id, device.display_name, device.platform,
                       device.state, device.enrolled_at,
                       assignment.user_id, assignment.organization_id,
                       assignment.workspace_id, assignment.status AS assignment_status
                FROM enrolled_devices AS device
                LEFT JOIN authority_device_principal_assignments AS assignment
                  ON assignment.device_id = device.device_id
                ORDER BY device.enrolled_at DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # User status
    # ------------------------------------------------------------------

    def set_user_status(self, user_id: UUID, status: str, now: int) -> bool:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT revision FROM authority_users WHERE id = ? AND status != 'deleted'",
                    (str(user_id),),
                ).fetchone()
                if row is None:
                    connection.rollback()
                    return False
                revision = int(row["revision"]) + 1
                cursor = connection.execute(
                    """
                    UPDATE authority_users
                    SET status = ?, updated_at = ?, revision = ?
                    WHERE id = ? AND status != 'deleted'
                    """,
                    (status, now, revision, str(user_id)),
                )
                revoked = cursor.rowcount != 1
                if status != "active":
                    self._revoke_sessions_for_user(connection, user_id, now, "user status: %s" % status)
                self._auth_event(
                    connection,
                    "authority.user_status",
                    now,
                    actor_id=user_id,
                    device_id=None,
                    detail="user status set to %s" % status,
                )
                connection.commit()
                return not revoked
            except Exception:
                connection.rollback()
                raise

    # ------------------------------------------------------------------
    # Device assignment
    # ------------------------------------------------------------------

    def assign_device(
        self,
        *,
        device_id: UUID,
        user_id: UUID,
        organization_id: UUID,
        workspace_id: UUID,
        role_ids: List[UUID],
        assigned_by: UUID,
        now: int,
    ) -> DeviceAssignmentRecord:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                device = connection.execute(
                    """
                    SELECT state FROM enrolled_devices WHERE device_id = ?
                    """,
                    (str(device_id),),
                ).fetchone()
                if device is None:
                    connection.rollback()
                    raise AuthorityNotFoundError(
                        "device_unknown", "The enrolled device does not exist."
                    )
                if device["state"] != "active_unassigned":
                    connection.rollback()
                    raise AuthorityConflictError(
                        "device_not_assignable",
                        "The device is revoked, expired, or otherwise not active_unassigned.",
                    )
                user = connection.execute(
                    "SELECT status FROM authority_users WHERE id = ?",
                    (str(user_id),),
                ).fetchone()
                if user is None or user["status"] != "active":
                    connection.rollback()
                    raise AuthorityConflictError(
                        "user_not_active", "The principal user is not active."
                    )
                self._require_organization(connection, organization_id)
                self._require_workspace(connection, organization_id, workspace_id)
                existing = connection.execute(
                    """
                    SELECT status, revision FROM authority_device_principal_assignments
                    WHERE device_id = ?
                    """,
                    (str(device_id),),
                ).fetchone()
                if existing is not None and existing["status"] == "active":
                    connection.rollback()
                    raise AuthorityConflictError(
                        "device_already_assigned",
                        "The device is already assigned to a principal.",
                    )
                for role_id in role_ids:
                    self._require_role(connection, role_id, organization_id)
                revision = (int(existing["revision"]) + 1) if existing is not None else 1
                connection.execute(
                    """
                    INSERT INTO authority_device_principal_assignments(
                        device_id, user_id, organization_id, workspace_id, status,
                        assigned_at, assigned_by, revoked_at, revision
                    ) VALUES (?, ?, ?, ?, 'active', ?, ?, NULL, ?)
                    ON CONFLICT(device_id) DO UPDATE SET
                        user_id = excluded.user_id,
                        organization_id = excluded.organization_id,
                        workspace_id = excluded.workspace_id,
                        status = 'active',
                        assigned_at = excluded.assigned_at,
                        assigned_by = excluded.assigned_by,
                        revoked_at = NULL,
                        revision = excluded.revision
                    """,
                    (
                        str(device_id),
                        str(user_id),
                        str(organization_id),
                        str(workspace_id),
                        now,
                        str(assigned_by),
                        revision,
                    ),
                )
                for role_id in role_ids:
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO authority_principal_role_assignments(
                            user_id, role_id, organization_id, workspace_id, status,
                            assigned_at, assigned_by, revision
                        ) VALUES (?, ?, ?, ?, 'active', ?, ?, 1)
                        """,
                        (
                            str(user_id),
                            str(role_id),
                            str(organization_id),
                            str(workspace_id),
                            now,
                            str(assigned_by),
                        ),
                    )
                self._auth_event(
                    connection,
                    "authority.device_assigned",
                    now,
                    actor_id=assigned_by,
                    device_id=device_id,
                    detail="assigned to %s" % user_id,
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.get_device_assignment(device_id)  # type: ignore[return-value]

    def revoke_device_assignment(self, device_id: UUID, assigned_by: UUID, now: int) -> bool:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    """
                    UPDATE authority_device_principal_assignments
                    SET status = 'revoked', revoked_at = ?, revision = revision + 1
                    WHERE device_id = ? AND status = 'active'
                    """,
                    (now, str(device_id)),
                )
                if cursor.rowcount != 1:
                    connection.rollback()
                    return False
                self._revoke_sessions_for_device(connection, device_id, now, "device assignment revoked")
                self._auth_event(
                    connection,
                    "authority.device_assignment_revoked",
                    now,
                    actor_id=assigned_by,
                    device_id=device_id,
                )
                connection.commit()
                return True
            except Exception:
                connection.rollback()
                raise

    def get_device_assignment(self, device_id: UUID) -> Optional[DeviceAssignmentRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                """
                SELECT * FROM authority_device_principal_assignments WHERE device_id = ?
                """,
                (str(device_id),),
            ).fetchone()
        return self._device_assignment(row) if row is not None else None

    # ------------------------------------------------------------------
    # Authentication challenges (Part 3)
    # ------------------------------------------------------------------

    def create_authentication_challenge(
        self,
        *,
        challenge_id: UUID,
        device_id: UUID,
        user_id: UUID,
        organization_id: UUID,
        workspace_id: UUID,
        server_nonce: bytes,
        now: int,
    ) -> AuthenticationChallengeRecord:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                assignment = connection.execute(
                    """
                    SELECT * FROM authority_device_principal_assignments
                    WHERE device_id = ? AND status = 'active'
                    """,
                    (str(device_id),),
                ).fetchone()
                if assignment is None:
                    connection.rollback()
                    raise AuthorityConflictError(
                        "device_not_assigned",
                        "The device is not assigned to a principal.",
                    )
                if assignment["user_id"] != str(user_id):
                    connection.rollback()
                    raise AuthorityConflictError(
                        "principal_mismatch",
                        "The device is not assigned to the stated principal.",
                    )
                user = connection.execute(
                    "SELECT status FROM authority_users WHERE id = ?",
                    (str(user_id),),
                ).fetchone()
                if user is None or user["status"] != "active":
                    connection.rollback()
                    raise AuthorityConflictError(
                        "user_not_active", "The principal user is not active."
                    )
                connection.execute(
                    """
                    UPDATE authority_authentication_challenges
                    SET state = 'expired'
                    WHERE device_id = ? AND state = 'open'
                    """,
                    (str(device_id),),
                )
                connection.execute(
                    """
                    INSERT INTO authority_authentication_challenges(
                        challenge_id, device_id, user_id, organization_id, workspace_id,
                        server_nonce, created_at, expires_at, state, failed_attempts
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', 0)
                    """,
                    (
                        str(challenge_id),
                        str(device_id),
                        str(user_id),
                        str(organization_id),
                        str(workspace_id),
                        server_nonce,
                        now,
                        now + self.challenge_lifetime_seconds,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        record = self.get_authentication_challenge(challenge_id)
        assert record is not None
        return record

    def get_authentication_challenge(
        self, challenge_id: UUID
    ) -> Optional[AuthenticationChallengeRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                """
                SELECT * FROM authority_authentication_challenges
                WHERE challenge_id = ?
                """,
                (str(challenge_id),),
            ).fetchone()
        return self._challenge(row) if row is not None else None

    def solve_authentication_challenge(
        self,
        challenge_id: UUID,
        now: int,
    ) -> bool:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    """
                    UPDATE authority_authentication_challenges
                    SET state = 'solved', solved_at = ?
                    WHERE challenge_id = ? AND state = 'open' AND expires_at > ?
                    """,
                    (now, str(challenge_id), now),
                )
                connection.commit()
                return cursor.rowcount == 1
            except Exception:
                connection.rollback()
                raise

    def record_failed_challenge(self, challenge_id: UUID, now: int) -> bool:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    """
                    SELECT failed_attempts FROM authority_authentication_challenges
                    WHERE challenge_id = ? AND state = 'open'
                    """,
                    (str(challenge_id),),
                ).fetchone()
                if row is None:
                    connection.rollback()
                    return False
                failures = int(row["failed_attempts"]) + 1
                state = (
                    "revoked"
                    if failures >= self.maximum_challenge_failures
                    else "open"
                )
                connection.execute(
                    """
                    UPDATE authority_authentication_challenges
                    SET failed_attempts = ?, state = ?
                    WHERE challenge_id = ? AND state = 'open'
                    """,
                    (failures, state, str(challenge_id)),
                )
                connection.commit()
                return True
            except Exception:
                connection.rollback()
                raise

    def revoke_challenge(self, challenge_id: UUID, now: int) -> bool:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    """
                    UPDATE authority_authentication_challenges
                    SET state = 'revoked'
                    WHERE challenge_id = ? AND state = 'open'
                    """,
                    (str(challenge_id),),
                )
                connection.commit()
                return cursor.rowcount == 1
            except Exception:
                connection.rollback()
                raise

    # ------------------------------------------------------------------
    # Application sessions
    # ------------------------------------------------------------------

    def create_session(
        self,
        *,
        session_id: UUID,
        user_id: UUID,
        device_id: UUID,
        organization_id: UUID,
        workspace_id: UUID,
        now: int,
        lifetime_seconds: int = default_session_lifetime_seconds,
    ) -> ApplicationSessionRecord:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                principal = connection.execute(
                    """
                    SELECT revision FROM authority_users WHERE id = ? AND status = 'active'
                    """,
                    (str(user_id),),
                ).fetchone()
                if principal is None:
                    connection.rollback()
                    raise AuthorityConflictError(
                        "user_not_active", "The principal user is not active."
                    )
                assignment = connection.execute(
                    """
                    SELECT revision FROM authority_device_principal_assignments
                    WHERE device_id = ? AND user_id = ? AND status = 'active'
                    """,
                    (str(device_id), str(user_id)),
                ).fetchone()
                if assignment is None:
                    connection.rollback()
                    raise AuthorityConflictError(
                        "device_not_assigned",
                        "The device is not assigned to this principal.",
                    )
                connection.execute(
                    """
                    INSERT INTO authority_application_sessions(
                        session_id, user_id, device_id, organization_id, workspace_id,
                        status, created_at, expires_at, principal_revision,
                        device_assignment_revision
                    ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)
                    """,
                    (
                        str(session_id),
                        str(user_id),
                        str(device_id),
                        str(organization_id),
                        str(workspace_id),
                        now,
                        now + lifetime_seconds,
                        int(principal["revision"]),
                        int(assignment["revision"]),
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.get_session(session_id)  # type: ignore[return-value]

    def get_session(self, session_id: UUID) -> Optional[ApplicationSessionRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM authority_application_sessions WHERE session_id = ?",
                (str(session_id),),
            ).fetchone()
        return self._session(row) if row is not None else None

    def validate_session(self, session_id: UUID, now: int) -> Optional[ApplicationSessionRecord]:
        """Returns the session only if it is active, unexpired, and its user and
        device assignment remain valid at the recorded revisions."""
        with self._connection_factory() as connection:
            row = connection.execute(
                """
                SELECT session.*
                FROM authority_application_sessions AS session
                WHERE session.session_id = ? AND session.status = 'active'
                """,
                (str(session_id),),
            ).fetchone()
        if row is None:
            return None
        session = self._session(row)
        if session.expires_at <= now:
            self.revoke_session(session_id, "expired", now)
            return None
        user = self.get_user(session.user_id)
        assignment = self.get_device_assignment(session.device_id)
        if (
            user is None
            or user.status != "active"
            or user.revision != session.principal_revision
        ):
            self.revoke_session(session_id, "principal_changed", now)
            return None
        if (
            assignment is None
            or assignment.status != "active"
            or assignment.revision != session.device_assignment_revision
        ):
            self.revoke_session(session_id, "assignment_changed", now)
            return None
        with self._connection_factory() as connection:
            connection.execute(
                """
                UPDATE authority_application_sessions
                SET last_seen_at = ? WHERE session_id = ? AND status = 'active'
                """,
                (now, str(session_id)),
            )
        return session

    def revoke_session(self, session_id: UUID, reason: str, now: int) -> bool:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    """
                    UPDATE authority_application_sessions
                    SET status = 'revoked', revoked_at = ?
                    WHERE session_id = ? AND status = 'active'
                    """,
                    (now, str(session_id)),
                )
                connection.execute(
                    """
                    UPDATE authority_session_refresh_credentials
                    SET revoked_at = ?
                    WHERE session_id = ? AND revoked_at IS NULL
                    """,
                    (now, str(session_id)),
                )
                self._auth_event(
                    connection,
                    "authority.session_revoked",
                    now,
                    session_id=session_id,
                    detail=reason[:200],
                )
                connection.commit()
                return cursor.rowcount == 1
            except Exception:
                connection.rollback()
                raise

    def revoke_all_sessions_for_user(self, user_id: UUID, now: int) -> int:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                count = self._revoke_sessions_for_user(connection, user_id, now, "operator revoke")
                connection.commit()
                return count
            except Exception:
                connection.rollback()
                raise

    def revoke_all_sessions_for_device(self, device_id: UUID, now: int) -> int:
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                count = self._revoke_sessions_for_device(
                    connection, device_id, now, "operator revoke"
                )
                connection.commit()
                return count
            except Exception:
                connection.rollback()
                raise

    # ------------------------------------------------------------------
    # Refresh credentials
    # ------------------------------------------------------------------

    def create_refresh_credential(
        self, session_id: UUID, credential_digest: bytes, now: int
    ) -> UUID:
        refresh_id = UUID(bytes=secrets.token_bytes(16))
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO authority_session_refresh_credentials(
                        refresh_id, session_id, credential_digest, created_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (str(refresh_id), str(session_id), credential_digest, now),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return refresh_id

    def resolve_refresh_credential(
        self, refresh_id: UUID, credential_digest: bytes, now: int
    ) -> Optional[ApplicationSessionRecord]:
        """Single-use refresh: validates the digest, revokes the credential, and
        returns the bound session if it is still active."""
        with self._connection_factory() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    """
                    SELECT session_id FROM authority_session_refresh_credentials
                    WHERE refresh_id = ? AND revoked_at IS NULL
                    """,
                    (str(refresh_id),),
                ).fetchone()
                if row is None:
                    connection.rollback()
                    return None
                session_id = UUID(str(row["session_id"]))
                connection.execute(
                    """
                    UPDATE authority_session_refresh_credentials
                    SET revoked_at = ?
                    WHERE refresh_id = ? AND revoked_at IS NULL
                    """,
                    (now, str(refresh_id)),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.validate_session(session_id, now)

    # ------------------------------------------------------------------
    # Principal
    # ------------------------------------------------------------------

    def principal_roles_and_capabilities(
        self,
        user_id: UUID,
        organization_id: UUID,
        workspace_id: UUID,
    ) -> Dict[str, List[str]]:
        with self._connection_factory() as connection:
            roles = connection.execute(
                """
                SELECT role.name AS role_name, capability.name AS capability_name
                FROM authority_principal_role_assignments AS assignment
                JOIN authority_roles AS role ON role.id = assignment.role_id
                LEFT JOIN authority_role_capabilities AS rc ON rc.role_id = role.id
                LEFT JOIN authority_capabilities AS capability ON capability.name = rc.capability_name
                WHERE assignment.user_id = ? AND assignment.organization_id = ?
                  AND assignment.workspace_id = ? AND assignment.status = 'active'
                  AND role.status = 'active'
                ORDER BY role.name, capability.name
                """,
                (str(user_id), str(organization_id), str(workspace_id)),
            ).fetchall()
        role_names: List[str] = []
        capabilities: List[str] = []
        for row in roles:
            role_name = str(row["role_name"])
            if role_name not in role_names:
                role_names.append(role_name)
            capability_name = row["capability_name"]
            if capability_name:
                capabilities.append(str(capability_name))
        capabilities = list(dict.fromkeys(capabilities))
        return {"roles": role_names, "capabilities": capabilities}

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def recent_auth_events(self, limit: int = 50) -> List[Dict[str, object]]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                """
                SELECT * FROM authority_authentication_events
                ORDER BY event_id DESC LIMIT ?
                """,
                (max(1, min(limit, 200)),),
            ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _ensure_capability_row(
        connection: sqlite3.Connection, name: str, now: int
    ) -> None:
        risk, scope_type = CAPABILITY_RISK_BY_NAME.get(name, ("standard", "organization"))
        connection.execute(
            """
            INSERT OR IGNORE INTO authority_capabilities(
                name, description, risk, scope_type, status
            ) VALUES (?, '', ?, ?, 'active')
            """,
            (name, risk, scope_type),
        )

    @staticmethod
    def _require_organization(connection: sqlite3.Connection, organization_id: UUID) -> None:
        row = connection.execute(
            "SELECT status FROM authority_organizations WHERE id = ?",
            (str(organization_id),),
        ).fetchone()
        if row is None or row["status"] != "active":
            raise AuthorityNotFoundError(
                "organization_not_found", "The organization does not exist."
            )

    @staticmethod
    def _require_workspace(
        connection: sqlite3.Connection, organization_id: UUID, workspace_id: UUID
    ) -> None:
        row = connection.execute(
            """
            SELECT status FROM authority_workspaces
            WHERE id = ? AND organization_id = ?
            """,
            (str(workspace_id), str(organization_id)),
        ).fetchone()
        if row is None or row["status"] != "active":
            raise AuthorityNotFoundError(
                "workspace_not_found", "The workspace does not exist."
            )

    @staticmethod
    def _require_role(
        connection: sqlite3.Connection, role_id: UUID, organization_id: UUID
    ) -> None:
        row = connection.execute(
            "SELECT status FROM authority_roles WHERE id = ? AND organization_id = ?",
            (str(role_id), str(organization_id)),
        ).fetchone()
        if row is None or row["status"] != "active":
            raise AuthorityNotFoundError(
                "role_not_found", "The role does not exist."
            )

    @staticmethod
    def _revoke_sessions_for_user(
        connection: sqlite3.Connection, user_id: UUID, now: int, reason: str
    ) -> int:
        cursor = connection.execute(
            """
            UPDATE authority_application_sessions
            SET status = 'revoked', revoked_at = ?
            WHERE user_id = ? AND status = 'active'
            """,
            (now, str(user_id)),
        )
        connection.execute(
            """
            UPDATE authority_session_refresh_credentials
            SET revoked_at = ?
            WHERE revoked_at IS NULL AND session_id IN (
                SELECT session_id FROM authority_application_sessions WHERE user_id = ?
            )
            """,
            (now, str(user_id)),
        )
        return max(0, cursor.rowcount)

    @staticmethod
    def _revoke_sessions_for_device(
        connection: sqlite3.Connection, device_id: UUID, now: int, reason: str
    ) -> int:
        cursor = connection.execute(
            """
            UPDATE authority_application_sessions
            SET status = 'revoked', revoked_at = ?
            WHERE device_id = ? AND status = 'active'
            """,
            (now, str(device_id)),
        )
        connection.execute(
            """
            UPDATE authority_session_refresh_credentials
            SET revoked_at = ?
            WHERE revoked_at IS NULL AND session_id IN (
                SELECT session_id FROM authority_application_sessions WHERE device_id = ?
            )
            """,
            (now, str(device_id)),
        )
        return max(0, cursor.rowcount)

    @staticmethod
    def _auth_event(
        connection: sqlite3.Connection,
        event_type: str,
        occurred_at: int,
        *,
        actor_id: Optional[UUID] = None,
        device_id: Optional[UUID] = None,
        session_id: Optional[UUID] = None,
        detail: str = "",
    ) -> None:
        connection.execute(
            """
            INSERT INTO authority_authentication_events(
                event_type, actor_id, device_id, session_id, occurred_at, detail
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event_type,
                str(actor_id) if actor_id else None,
                str(device_id) if device_id else None,
                str(session_id) if session_id else None,
                occurred_at,
                detail[:240],
            ),
        )

    # ------------------------------------------------------------------
    # Row mappers
    # ------------------------------------------------------------------

    @staticmethod
    def _user(row: sqlite3.Row) -> AuthorityUserRecord:
        return AuthorityUserRecord(
            id=UUID(str(row["id"])),
            display_name=str(row["display_name"]),
            status=str(row["status"]),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
            revision=int(row["revision"]),
        )

    @staticmethod
    def _organization(row: sqlite3.Row) -> AuthorityOrganizationRecord:
        return AuthorityOrganizationRecord(
            id=UUID(str(row["id"])),
            name=str(row["name"]),
            status=str(row["status"]),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
            revision=int(row["revision"]),
        )

    @staticmethod
    def _workspace(row: sqlite3.Row) -> AuthorityWorkspaceRecord:
        return AuthorityWorkspaceRecord(
            id=UUID(str(row["id"])),
            organization_id=UUID(str(row["organization_id"])),
            name=str(row["name"]),
            status=str(row["status"]),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
            revision=int(row["revision"]),
        )

    @staticmethod
    def _role(row: sqlite3.Row) -> AuthorityRoleRecord:
        return AuthorityRoleRecord(
            id=UUID(str(row["id"])),
            organization_id=UUID(str(row["organization_id"])) if row["organization_id"] else None,
            scope=str(row["scope"]),
            name=str(row["name"]),
            description=str(row["description"]),
            status=str(row["status"]),
            immutable=bool(row["immutable"]),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
            revision=int(row["revision"]),
        )

    @staticmethod
    def _capability(row: sqlite3.Row) -> AuthorityCapabilityRecord:
        return AuthorityCapabilityRecord(
            name=str(row["name"]),
            description=str(row["description"]),
            risk=str(row["risk"]),
            scope_type=str(row["scope_type"]),
            status=str(row["status"]),
        )

    @staticmethod
    def _device_assignment(row: sqlite3.Row) -> DeviceAssignmentRecord:
        return DeviceAssignmentRecord(
            device_id=UUID(str(row["device_id"])),
            user_id=UUID(str(row["user_id"])),
            organization_id=UUID(str(row["organization_id"])),
            workspace_id=UUID(str(row["workspace_id"])),
            status=str(row["status"]),
            assigned_at=int(row["assigned_at"]),
            assigned_by=UUID(str(row["assigned_by"])),
            revoked_at=int(row["revoked_at"]) if row["revoked_at"] is not None else None,
            revision=int(row["revision"]),
        )

    @staticmethod
    def _session(row: sqlite3.Row) -> ApplicationSessionRecord:
        return ApplicationSessionRecord(
            session_id=UUID(str(row["session_id"])),
            user_id=UUID(str(row["user_id"])),
            device_id=UUID(str(row["device_id"])),
            organization_id=UUID(str(row["organization_id"])),
            workspace_id=UUID(str(row["workspace_id"])),
            status=str(row["status"]),
            created_at=int(row["created_at"]),
            expires_at=int(row["expires_at"]),
            revoked_at=int(row["revoked_at"]) if row["revoked_at"] is not None else None,
            principal_revision=int(row["principal_revision"]),
            device_assignment_revision=int(row["device_assignment_revision"]),
        )

    @staticmethod
    def _challenge(row: sqlite3.Row) -> AuthenticationChallengeRecord:
        return AuthenticationChallengeRecord(
            challenge_id=UUID(str(row["challenge_id"])),
            device_id=UUID(str(row["device_id"])),
            user_id=UUID(str(row["user_id"])),
            organization_id=UUID(str(row["organization_id"])),
            workspace_id=UUID(str(row["workspace_id"])),
            server_nonce=bytes(row["server_nonce"]),
            created_at=int(row["created_at"]),
            expires_at=int(row["expires_at"]),
            state=str(row["state"]),
            failed_attempts=int(row["failed_attempts"]),
            solved_at=int(row["solved_at"]) if row["solved_at"] is not None else None,
        )


def sha256_digest(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


CAPABILITY_RISK_BY_NAME: Dict[str, tuple] = {
    # standard, non-privileged application capabilities
    "conversation.read": ("standard", "workspace"),
    "conversation.write": ("standard", "workspace"),
    "conversation.invoke_ai": ("standard", "workspace"),
    "conversation.cancel": ("standard", "workspace"),
    "principal.read": ("standard", "workspace"),
    "diagnostics.read": ("standard", "workspace"),
    # engineering campaign read surfaces are standard, non-privileged
    "engineering.campaign.read": ("standard", "workspace"),
    "engineering.package.read": ("standard", "workspace"),
    # privileged / critical capabilities are NOT granted by default
    "approval.sign": ("privileged", "workspace"),
    "repository.write": ("privileged", "workspace"),
    "deployment.execute": ("critical", "organization"),
    "secret.access": ("critical", "organization"),
    "shell.execute": ("critical", "organization"),
    "git.mutate": ("privileged", "workspace"),
    "external.send": ("critical", "organization"),
    # engineering campaign orchestration is privileged; blocker resolution is
    # the only critical campaign capability and requires explicit grant
    "engineering.campaign.manage": ("privileged", "workspace"),
    "engineering.campaign.start": ("privileged", "workspace"),
    "engineering.campaign.pause": ("privileged", "workspace"),
    "engineering.campaign.cancel": ("privileged", "workspace"),
    "engineering.package.manage": ("privileged", "workspace"),
    "engineering.blocker.resolve": ("critical", "workspace"),
}
