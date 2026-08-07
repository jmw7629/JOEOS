"""Authority service: local owner bootstrap, device assignment, device-key
application authentication, application sessions, and principal lookup.

The backend remains authoritative for identity, permissions, and sessions. No
password, token, or key is stored in source, configuration, or logs. CLI output
never prints authentication tokens or private keys.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from typing import Callable, Dict, List, Optional
from uuid import UUID, uuid4

from server.identity.crypto import verify_p256_signature

from .authority_repository import (
    APPLICATION_AUTHENTICATION_DOMAIN,
    CAPABILITY_RISK_BY_NAME,
    DeviceAssignmentRecord,
    SQLiteAuthorityRepository,
)


class AuthorityError(Exception):
    """Base authority error."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.public_message = message


class AuthorityConflictError(AuthorityError):
    pass


class AuthorityNotFoundError(AuthorityError):
    pass


class AuthorityAuthenticationError(AuthorityError):
    pass


def _now_provider_default() -> int:
    import time

    return int(time.time())


class AuthorityService:
    """Coordinates the authoritative application identity domain."""

    session_lifetime_seconds = 15 * 60
    refresh_lifetime_seconds = 30 * 24 * 60 * 60
    challenge_lifetime_seconds = 120

    owner_standard_capabilities = [
        "conversation.read",
        "conversation.write",
        "conversation.invoke_ai",
        "conversation.cancel",
        "principal.read",
        "diagnostics.read",
        "agent.read",
        "agent.manage",
        "agent.run",
        "tool.read",
        "policy.read",
        "action.read",
        "action.propose",
        "action.cancel",
        "approval.read",
        "approval.decide.low",
        "approval.decide.medium",
    ]

    def __init__(
        self,
        repository: SQLiteAuthorityRepository,
        device_repository: object,
        *,
        now_provider: Callable[[], int] = _now_provider_default,
        uuid_provider: Callable[[], UUID] = uuid4,
    ) -> None:
        self._repository = repository
        self._device_repository = device_repository
        self._now = now_provider
        self._uuid = uuid_provider

    def prepare(self) -> None:
        self._repository.prepare()
        self._repository.seed_capabilities(list(CAPABILITY_RISK_BY_NAME), self._now())
        self._repository.grant_owner_capabilities(self.owner_standard_capabilities, self._now())

    # ------------------------------------------------------------------
    # Local owner bootstrap
    # ------------------------------------------------------------------

    def is_bootstrapped(self) -> bool:
        return self._repository.has_any_installation()

    def bootstrap(
        self,
        *,
        display_name: str,
        organization_name: str,
        workspace_name: str,
    ) -> Dict[str, str]:
        """Local-console-only first-owner bootstrap. Idempotent and refuses a
        second owner installation. Creates no password."""
        if self.is_bootstrapped():
            raise AuthorityConflictError(
                409,
                "installation_already_exists",
                "JoeOS is already bootstrapped. Refusing to create a second owner.",
            )
        now = self._now()
        return self._repository.bootstrap(
            user_id=self._uuid(),
            organization_id=self._uuid(),
            workspace_id=self._uuid(),
            owner_role_id=self._uuid(),
            display_name=display_name.strip(),
            organization_name=organization_name.strip(),
            workspace_name=workspace_name.strip(),
            owner_role_name="joeos.owner",
            capabilities=self.owner_standard_capabilities,
            now=now,
        )

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    def list_users(self) -> List[Dict[str, object]]:
        return [
            {
                "id": record.id,
                "display_name": record.display_name,
                "status": record.status,
                "created_at": record.created_at,
                "updated_at": record.updated_at,
                "revision": record.revision,
            }
            for record in self._repository.list_users()
        ]

    def list_organizations(self) -> List[Dict[str, object]]:
        return [
            {
                "id": record.id,
                "name": record.name,
                "status": record.status,
                "created_at": record.created_at,
            }
            for record in self._repository.list_organizations()
        ]

    def list_workspaces(self) -> List[Dict[str, object]]:
        return [
            {
                "id": record.id,
                "organization_id": record.organization_id,
                "name": record.name,
                "status": record.status,
            }
            for record in self._repository.list_workspaces()
        ]

    def list_roles(self) -> List[Dict[str, object]]:
        return [
            {
                "id": record.id,
                "organization_id": record.organization_id,
                "scope": record.scope,
                "name": record.name,
                "description": record.description,
                "status": record.status,
                "immutable": record.immutable,
            }
            for record in self._repository.list_roles()
        ]

    def list_capabilities(self) -> List[Dict[str, object]]:
        return [
            {
                "name": record.name,
                "description": record.description,
                "risk": record.risk,
                "scope_type": record.scope_type,
                "status": record.status,
            }
            for record in self._repository.list_capabilities()
        ]

    def list_devices(self) -> List[Dict[str, object]]:
        return self._repository.list_devices()

    def list_active_unassigned_devices(self) -> List[Dict[str, object]]:
        return [
            device
            for device in self._repository.list_devices()
            if device["state"] == "active_unassigned"
            and device.get("assignment_status") != "active"
        ]

    # ------------------------------------------------------------------
    # Assignment
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
    ) -> Dict[str, object]:
        try:
            record = self._repository.assign_device(
                device_id=device_id,
                user_id=user_id,
                organization_id=organization_id,
                workspace_id=workspace_id,
                role_ids=role_ids,
                assigned_by=assigned_by,
                now=self._now(),
            )
        except Exception as error:  # noqa: BLE001 - mapped to typed errors
            raise self._map_repository_error(error) from error
        return self._assignment_payload(record)

    def revoke_device_assignment(self, device_id: UUID, assigned_by: UUID) -> bool:
        try:
            return self._repository.revoke_device_assignment(
                device_id, assigned_by, self._now()
            )
        except Exception as error:  # noqa: BLE001
            raise self._map_repository_error(error) from error

    def set_user_status(self, user_id: UUID, status: str) -> bool:
        allowed = {"active", "disabled", "locked", "deleted"}
        if status not in allowed:
            raise AuthorityError(400, "invalid_status", "Unsupported user status.")
        try:
            return self._repository.set_user_status(user_id, status, self._now())
        except Exception as error:  # noqa: BLE001
            raise self._map_repository_error(error) from error

    def revoke_sessions_for_user(self, user_id: UUID) -> int:
        return self._repository.revoke_all_sessions_for_user(user_id, self._now())

    def revoke_sessions_for_device(self, device_id: UUID) -> int:
        return self._repository.revoke_all_sessions_for_device(device_id, self._now())

    # ------------------------------------------------------------------
    # Device-key application authentication (Part 3)
    # ------------------------------------------------------------------

    def create_authentication_challenge(
        self, device_id: UUID, user_id: UUID
    ) -> Dict[str, object]:
        device = self._device_repository.get_device(device_id)
        if device is None:
            raise AuthorityAuthenticationError(
                404, "device_unknown", "The enrolled device does not exist."
            )
        if device.state != "active_unassigned" and not self._is_assigned(device_id):
            raise AuthorityAuthenticationError(
                403, "device_not_active", "The device is not active."
            )
        try:
            record = self._repository.create_authentication_challenge(
                challenge_id=self._uuid(),
                device_id=device_id,
                user_id=user_id,
                organization_id=self._assignment_org(device_id),
                workspace_id=self._assignment_workspace(device_id),
                server_nonce=secrets.token_bytes(32),
                now=self._now(),
            )
        except Exception as error:  # noqa: BLE001
            raise self._map_repository_error(error) from error
        return {
            "challenge_id": record.challenge_id,
            "device_id": record.device_id,
            "user_id": record.user_id,
            "organization_id": record.organization_id,
            "workspace_id": record.workspace_id,
            "server_nonce": base64.urlsafe_b64encode(record.server_nonce)
            .decode("ascii")
            .rstrip("="),
            "expires_at": record.expires_at,
            "message": self._challenge_message(record),
        }

    def solve_authentication_challenge(
        self, challenge_id: UUID, signature_b64url: str
    ) -> Dict[str, object]:
        challenge = self._repository.get_authentication_challenge(challenge_id)
        if challenge is None or challenge.state != "open":
            raise AuthorityAuthenticationError(
                401, "challenge_not_open", "The challenge is not open."
            )
        if challenge.expires_at <= self._now():
            self._repository.revoke_challenge(challenge_id, self._now())
            raise AuthorityAuthenticationError(
                401, "challenge_expired", "The authentication challenge has expired."
            )
        device = self._device_repository.get_device(challenge.device_id)
        if device is None or device.state == "revoked":
            raise AuthorityAuthenticationError(
                403, "device_revoked", "The device is revoked."
            )
        try:
            verify_p256_signature(
                device.auth_public_key,
                self._challenge_message(challenge).encode("ascii"),
                signature_b64url,
            )
        except Exception:  # noqa: BLE001
            self._repository.record_failed_challenge(challenge_id, self._now())
            raise AuthorityAuthenticationError(
                401, "signature_invalid", "The device authentication signature is invalid."
            ) from None
        self._repository.solve_authentication_challenge(challenge_id, self._now())
        session = self._repository.create_session(
            session_id=self._uuid(),
            user_id=challenge.user_id,
            device_id=challenge.device_id,
            organization_id=challenge.organization_id,
            workspace_id=challenge.workspace_id,
            now=self._now(),
        )
        refresh_token, refresh_id = self._issue_refresh(session.session_id)
        return {
            "session": self._session_payload(session),
            "refresh_token": refresh_token,
            "refresh_id": refresh_id,
            "principal": self.principal(session),
        }

    def refresh_session(self, refresh_id: UUID, refresh_token: str) -> Dict[str, object]:
        digest = hashlib.sha256(refresh_token.encode("ascii")).digest()
        try:
            session = self._repository.resolve_refresh_credential(
                refresh_id, digest, self._now()
            )
        except Exception as error:  # noqa: BLE001
            raise self._map_repository_error(error) from error
        if session is None:
            raise AuthorityAuthenticationError(
                401, "refresh_invalid", "The refresh credential is invalid or revoked."
            )
        self._repository.revoke_session(session.session_id, "rotated", self._now())
        new_session = self._repository.create_session(
            session_id=self._uuid(),
            user_id=session.user_id,
            device_id=session.device_id,
            organization_id=session.organization_id,
            workspace_id=session.workspace_id,
            now=self._now(),
        )
        refresh_token, refresh_id = self._issue_refresh(new_session.session_id)
        return {
            "session": self._session_payload(new_session),
            "refresh_token": refresh_token,
            "refresh_id": refresh_id,
            "principal": self.principal(new_session),
        }

    def logout(self, session_id: UUID) -> bool:
        return self._repository.revoke_session(session_id, "logged_out", self._now())

    # ------------------------------------------------------------------
    # Request enforcement
    # ------------------------------------------------------------------

    def validate_session(self, session_id: UUID) -> Optional[Dict[str, object]]:
        session = self._repository.validate_session(session_id, self._now())
        if session is None:
            return None
        return self._session_payload(session)

    def principal_for_session(self, session_id: UUID) -> Optional[Dict[str, object]]:
        """Returns the principal for a live session, or None when the session is
        invalid, expired, revoked, or its user/assignment changed."""
        session = self._repository.validate_session(session_id, self._now())
        if session is None:
            return None
        return self.principal(session)

    def principal(self, session: object) -> Dict[str, object]:
        session_id = getattr(session, "session_id")
        user_id = getattr(session, "user_id")
        organization_id = getattr(session, "organization_id")
        workspace_id = getattr(session, "workspace_id")
        user = self._repository.get_user(user_id)
        organization = next(
            (org for org in self._repository.list_organizations() if org.id == organization_id),
            None,
        )
        workspace = next(
            (ws for ws in self._repository.list_workspaces() if ws.id == workspace_id),
            None,
        )
        roles_and_capabilities = self._repository.principal_roles_and_capabilities(
            user_id, organization_id, workspace_id
        )
        return {
            "session_id": session_id,
            "device_id": getattr(session, "device_id"),
            "user": {
                "id": user_id,
                "display_name": user.display_name if user else None,
                "status": user.status if user else "unknown",
            },
            "organization": {
                "id": organization_id,
                "name": organization.name if organization else None,
            },
            "workspace": {
                "id": workspace_id,
                "name": workspace.name if workspace else None,
            },
            "roles": roles_and_capabilities["roles"],
            "capabilities": roles_and_capabilities["capabilities"],
        }

    def require_capability(
        self, session_id: UUID, capability: str
    ) -> Dict[str, object]:
        session = self._repository.validate_session(session_id, self._now())
        if session is None:
            raise AuthorityAuthenticationError(
                401, "session_invalid", "The application session is invalid or expired."
            )
        principal = self.principal(session)
        if capability not in principal["capabilities"]:
            raise AuthorityAuthenticationError(
                403,
                "capability_denied",
                "This principal is not granted the %s capability." % capability,
            )
        return principal

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_assigned(self, device_id: UUID) -> bool:
        assignment = self._repository.get_device_assignment(device_id)
        return assignment is not None and assignment.status == "active"

    def _assignment_org(self, device_id: UUID) -> UUID:
        assignment = self._repository.get_device_assignment(device_id)
        if assignment is None:
            raise AuthorityAuthenticationError(
                403, "device_not_assigned", "The device is not assigned to a principal."
            )
        return assignment.organization_id

    def _assignment_workspace(self, device_id: UUID) -> UUID:
        assignment = self._repository.get_device_assignment(device_id)
        if assignment is None:
            raise AuthorityAuthenticationError(
                403, "device_not_assigned", "The device is not assigned to a principal."
            )
        return assignment.workspace_id

    def _challenge_message(self, challenge: object) -> str:
        return APPLICATION_AUTHENTICATION_DOMAIN.format(
            challenge_id=challenge.challenge_id,
            device_id=challenge.device_id,
            user_id=challenge.user_id,
            server_nonce=base64.urlsafe_b64encode(challenge.server_nonce)
            .decode("ascii")
            .rstrip("="),
            expires_at=challenge.expires_at,
        )

    def _issue_refresh(self, session_id: UUID) -> tuple:
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode("ascii")).digest()
        refresh_id = self._repository.create_refresh_credential(
            session_id, digest, self._now()
        )
        return token, refresh_id

    @staticmethod
    def _session_payload(session: object) -> Dict[str, object]:
        return {
            "session_id": getattr(session, "session_id"),
            "user_id": getattr(session, "user_id"),
            "device_id": getattr(session, "device_id"),
            "organization_id": getattr(session, "organization_id"),
            "workspace_id": getattr(session, "workspace_id"),
            "status": getattr(session, "status"),
            "created_at": getattr(session, "created_at"),
            "expires_at": getattr(session, "expires_at"),
        }

    @staticmethod
    def _assignment_payload(record: DeviceAssignmentRecord) -> Dict[str, object]:
        return {
            "device_id": record.device_id,
            "user_id": record.user_id,
            "organization_id": record.organization_id,
            "workspace_id": record.workspace_id,
            "status": record.status,
            "assigned_at": record.assigned_at,
            "assigned_by": record.assigned_by,
            "revoked_at": record.revoked_at,
            "revision": record.revision,
        }

    @staticmethod
    def _map_repository_error(error: Exception) -> AuthorityError:
        from .authority_repository import AuthorityConflictError as RepoConflictError
        from .authority_repository import AuthorityNotFoundError as RepoNotFoundError

        if isinstance(error, RepoConflictError):
            return AuthorityConflictError(409, error.code, error.public_message)
        if isinstance(error, RepoNotFoundError):
            return AuthorityNotFoundError(404, error.code, error.public_message)
        if isinstance(error, AuthorityError):
            return error
        return AuthorityError(500, "authority_error", "The identity operation failed.")
