"""Provider, Account, Identity, and Contact registries for the JoeOS
Communications Platform.

Provider adapters are provider-neutral; accounts store no credentials; agents
can never impersonate user identities; contacts are authoritative and never
merged on name similarity alone.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Callable, Dict, Optional, Sequence, Tuple

from .models import (
    AccountRecord,
    ContactRecord,
    IdentityRecord,
    ProviderCapabilities,
    ProviderRecord,
    VerificationState,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RegistryError(RuntimeError):
    pass


class ProviderRegistry:
    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()

    def register(
        self,
        *,
        provider_id: str,
        display_name: str,
        provider_type: str = "generic",
        capabilities: Optional[ProviderCapabilities] = None,
        authentication: str = "none",
        plugin_source: str = "",
        is_isolated_test: bool = False,
    ) -> ProviderRecord:
        now = _now()
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO comms_providers (
                    provider_id, provider_type, display_name, capabilities, authentication,
                    plugin_source, health_state, privacy, is_isolated_test, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'unknown', 'private', ?, ?)
                ON CONFLICT(provider_id) DO UPDATE SET
                    display_name = excluded.display_name, capabilities = excluded.capabilities,
                    authentication = excluded.authentication, plugin_source = excluded.plugin_source,
                    is_isolated_test = excluded.is_isolated_test
                """,
                (
                    provider_id,
                    provider_type,
                    display_name,
                    (capabilities or ProviderCapabilities()).model_dump_json(),
                    authentication,
                    plugin_source,
                    1 if is_isolated_test else 0,
                    now,
                ),
            )
        return self.get(provider_id)

    def get(self, provider_id: str) -> Optional[ProviderRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM comms_providers WHERE provider_id = ?", (provider_id,)
            ).fetchone()
        return self._row(row) if row else None

    def list(self) -> Tuple[ProviderRecord, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute("SELECT * FROM comms_providers ORDER BY display_name").fetchall()
        return tuple(self._row(row) for row in rows)

    def set_health(self, provider_id: str, health: str) -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE comms_providers SET health_state = ? WHERE provider_id = ?",
                (health, provider_id),
            )

    @staticmethod
    def _row(row: sqlite3.Row) -> ProviderRecord:
        return ProviderRecord(
            provider_id=str(row["provider_id"]),
            provider_type=str(row["provider_type"]),
            display_name=str(row["display_name"]),
            capabilities=ProviderCapabilities.model_validate(json.loads(str(row["capabilities"]))),
            authentication=str(row["authentication"]),
            plugin_source=str(row["plugin_source"]),
            health_state=str(row["health_state"]),
            privacy=str(row["privacy"]),
            is_isolated_test=bool(row["is_isolated_test"]),
        )


class AccountRegistry:
    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()

    def register(
        self,
        *,
        provider_id: str,
        display_label: str,
        identity_id: str = "",
        capabilities: Optional[ProviderCapabilities] = None,
        connection_state: str = "configured",
        sending_permission: bool = False,
        plugin_source: str = "",
    ) -> AccountRecord:
        account_id = "acct_" + uuid.uuid4().hex[:14]
        now = _now()
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO comms_accounts (
                    account_id, provider_id, display_label, identity_id, enabled,
                    connection_state, capabilities, sending_permission, health,
                    plugin_source, removed, created_at
                ) VALUES (?, ?, ?, ?, 0, ?, ?, ?, 'healthy', ?, 0, ?)
                """,
                (
                    account_id,
                    provider_id,
                    display_label,
                    identity_id,
                    connection_state,
                    (capabilities or ProviderCapabilities()).model_dump_json(),
                    1 if sending_permission else 0,
                    plugin_source,
                    now,
                ),
            )
        return self.get(account_id)

    def get(self, account_id: str) -> Optional[AccountRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM comms_accounts WHERE account_id = ?", (account_id,)
            ).fetchone()
        return self._row(row) if row else None

    def set_connection_state(self, account_id: str, state: str, *, health: Optional[str] = None) -> AccountRecord:
        with self._lock, self._connection_factory() as connection:
            if health:
                connection.execute(
                    "UPDATE comms_accounts SET connection_state = ?, health = ?, last_failure = ? WHERE account_id = ?",
                    (state, health, "" if state == "connected" else _now(), account_id),
                )
            else:
                connection.execute(
                    "UPDATE comms_accounts SET connection_state = ? WHERE account_id = ?",
                    (state, account_id),
                )
        return self.get(account_id)

    def set_sync_state(self, account_id: str, *, last_sync: str = "", last_failure: str = "") -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE comms_accounts SET last_sync = ?, last_failure = ? WHERE account_id = ?",
                (last_sync, last_failure, account_id),
            )

    def list(self) -> Tuple[AccountRecord, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute("SELECT * FROM comms_accounts ORDER BY display_label").fetchall()
        return tuple(self._row(row) for row in rows)

    @staticmethod
    def _row(row: sqlite3.Row) -> AccountRecord:
        return AccountRecord(
            account_id=str(row["account_id"]),
            provider_id=str(row["provider_id"]),
            display_label=str(row["display_label"]),
            identity_id=str(row["identity_id"]),
            enabled=bool(row["enabled"]),
            connection_state=str(row["connection_state"]),
            capabilities=ProviderCapabilities.model_validate(json.loads(str(row["capabilities"]))),
            sending_permission=bool(row["sending_permission"]),
            last_sync=str(row["last_sync"]),
            last_failure=str(row["last_failure"]),
            health=str(row["health"]),
            plugin_source=str(row["plugin_source"]),
            removed=bool(row["removed"]),
            created_at=str(row["created_at"]),
        )


class IdentityRegistry:
    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()

    def create(
        self,
        *,
        identity_id: str,
        display_name: str,
        identity_type: str,
        user_owned: bool = False,
        provider: str = "",
        verified_addresses: Tuple[str, ...] = (),
        sending_permission: bool = False,
        default_state: bool = False,
    ) -> IdentityRecord:
        existing = self.get(identity_id)
        if existing is not None:
            return existing
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO comms_identities (
                    identity_id, display_name, identity_type, user_owned, provider,
                    verified_addresses, verification_state, sending_permission,
                    default_state, privacy, created_at, disabled
                ) VALUES (?, ?, ?, ?, ?, ?, 'unverified', ?, ?, 'private', ?, 0)
                """,
                (
                    identity_id,
                    display_name,
                    identity_type,
                    1 if user_owned else 0,
                    provider,
                    "\n".join(verified_addresses),
                    1 if sending_permission else 0,
                    1 if default_state else 0,
                    _now(),
                ),
            )
        return self.get(identity_id)

    def get(self, identity_id: str) -> Optional[IdentityRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM comms_identities WHERE identity_id = ?", (identity_id,)
            ).fetchone()
        return self._row(row) if row else None

    def set_verification(self, identity_id: str, state: VerificationState) -> IdentityRecord:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE comms_identities SET verification_state = ? WHERE identity_id = ?",
                (state, identity_id),
            )
        return self.get(identity_id)

    def can_send_as(self, actor_identity: str, target_identity: str) -> bool:
        """An agent/workflow/plugin identity can never send as a user identity.

        A user may send as themselves; JoeOS core may send as JoeOS. Identity
        aliasing is only allowed when neither side is a user identity and the
        actor holds sending permission.
        """
        actor = self.get(actor_identity)
        target = self.get(target_identity)
        if actor is None or target is None:
            return False
        if actor_identity == target_identity:
            return actor.sending_permission and not actor.disabled
        if target.user_owned:
            return False  # agents/workflows/plugins can never alias the user
        if actor.identity_type in {"agent", "workflow", "plugin"}:
            return False  # no cross-type impersonation
        return bool(actor.sending_permission and target.sending_permission)

    def list(self) -> Tuple[IdentityRecord, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute("SELECT * FROM comms_identities ORDER BY display_name").fetchall()
        return tuple(self._row(row) for row in rows)

    @staticmethod
    def _row(row: sqlite3.Row) -> IdentityRecord:
        return IdentityRecord(
            identity_id=str(row["identity_id"]),
            display_name=str(row["display_name"]),
            identity_type=str(row["identity_type"]),
            user_owned=bool(row["user_owned"]),
            provider=str(row["provider"]),
            account=str(row["account"]),
            verified_addresses=tuple(p for p in str(row["verified_addresses"]).split("\n") if p),
            verified_handles=tuple(p for p in str(row["verified_handles"]).split("\n") if p),
            verification_state=str(row["verification_state"]),
            sending_permission=bool(row["sending_permission"]),
            default_state=bool(row["default_state"]),
            privacy=str(row["privacy"]),
            created_at=str(row["created_at"]),
            disabled=bool(row["disabled"]),
        )


class ContactRegistry:
    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()

    def create(
        self,
        *,
        display_name: str,
        organization: str = "",
        role: str = "",
        addresses: Tuple[str, ...] = (),
        handles: Tuple[str, ...] = (),
        source: str = "",
    ) -> ContactRecord:
        contact_id = "contact_" + uuid.uuid4().hex[:14]
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO comms_contacts (
                    contact_id, display_name, organization, role, addresses, handles,
                    preferred_channel, trust_state, verification_state, source, privacy,
                    last_interaction, deleted, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, '', 'unknown', 'unverified', ?, 'private', '', 0, ?)
                """,
                (
                    contact_id,
                    display_name,
                    organization,
                    role,
                    "\n".join(addresses),
                    "\n".join(handles),
                    source,
                    _now(),
                ),
            )
        return self.get(contact_id)

    def get(self, contact_id: str) -> Optional[ContactRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM comms_contacts WHERE contact_id = ?", (contact_id,)
            ).fetchone()
        return self._row(row) if row else None

    def search(self, query: str) -> Tuple[ContactRecord, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                """
                SELECT * FROM comms_contacts
                WHERE deleted = 0 AND (display_name LIKE ? OR organization LIKE ? OR addresses LIKE ? OR handles LIKE ?)
                ORDER BY display_name LIMIT 20
                """,
                ("%" + query + "%", "%" + query + "%", "%" + query + "%", "%" + query + "%"),
            ).fetchall()
        return tuple(self._row(row) for row in rows)

    def set_verification(self, contact_id: str, state: VerificationState) -> ContactRecord:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE comms_contacts SET verification_state = ? WHERE contact_id = ?",
                (state, contact_id),
            )
        return self.get(contact_id)

    def mark_deleted(self, contact_id: str) -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE comms_contacts SET deleted = 1 WHERE contact_id = ?", (contact_id,)
            )

    def list(self) -> Tuple[ContactRecord, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM comms_contacts WHERE deleted = 0 ORDER BY display_name"
            ).fetchall()
        return tuple(self._row(row) for row in rows)

    @staticmethod
    def _row(row: sqlite3.Row) -> ContactRecord:
        return ContactRecord(
            contact_id=str(row["contact_id"]),
            display_name=str(row["display_name"]),
            organization=str(row["organization"]),
            role=str(row["role"]),
            addresses=tuple(p for p in str(row["addresses"]).split("\n") if p),
            handles=tuple(p for p in str(row["handles"]).split("\n") if p),
            aliases=tuple(p for p in str(row["aliases"]).split("\n") if p),
            preferred_channel=str(row["preferred_channel"]),
            timezone=str(row["timezone"]),
            language=str(row["language"]),
            trust_state=str(row["trust_state"]),
            verification_state=str(row["verification_state"]),
            source=str(row["source"]),
            privacy=str(row["privacy"]),
            last_interaction=str(row["last_interaction"]),
            deleted=bool(row["deleted"]),
            created_at=str(row["created_at"]),
        )


class RecipientResolver:
    """Resolves recipients with ambiguity checks; never invents addresses."""

    def __init__(self, contacts: ContactRegistry, identities: IdentityRegistry) -> None:
        self._contacts = contacts
        self._identities = identities

    def resolve(self, entered: str) -> Tuple:
        """Resolve a recipient entry to candidates.

        Returns (resolved, ambiguous, warnings). Never invents addresses from
        model output; resolution is exact-match only.
        """
        entered = (entered or "").strip()
        if not entered:
            return None, False, ("empty recipient",)
        # Exact identity match.
        identity = self._identities.get(entered)
        if identity is not None and not identity.disabled:
            return self._recipient_for_identity(identity), False, ()
        # Exact contact by id.
        contact = self._contacts.get(entered)
        if contact is not None and not contact.deleted:
            return self._recipient_for_contact(contact), False, ()
        # Address/handle search.
        matches = [c for c in self._contacts.search(entered) if not c.deleted]
        if len(matches) == 1:
            return self._recipient_for_contact(matches[0]), False, ()
        if len(matches) > 1:
            return None, True, ("multiple contacts match; review required",)
        return None, False, ("unverified destination; review required",)

    @staticmethod
    def _recipient_for_identity(identity: IdentityRecord):
        from .models import Recipient
        address = identity.verified_addresses[0] if identity.verified_addresses else identity.identity_id
        return Recipient(
            recipient_id=identity.identity_id,
            display_name=identity.display_name,
            destination=address,
            provider=identity.provider,
            verification=identity.verification_state,
            trust="known" if identity.user_owned else "unknown",
            source="identity",
        )

    @staticmethod
    def _recipient_for_contact(contact: ContactRecord):
        from .models import Recipient
        address = contact.addresses[0] if contact.addresses else contact.contact_id
        return Recipient(
            recipient_id=contact.contact_id,
            display_name=contact.display_name,
            destination=address,
            provider=contact.preferred_channel,
            verification=contact.verification_state,
            trust=contact.trust_state,
            source="contact",
        )