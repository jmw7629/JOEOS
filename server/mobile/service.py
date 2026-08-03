"""MobileService facade: one authoritative entry point into the JoeOS Mobile
Companion and Secure Remote Operations Platform.

Composes the Mobile Client Registry, Host Registry, Pairing Coordinator,
Authentication, Secure Session Manager, Remote Command Gateway, Scoped Remote
API, Offline Action Queue, Handoff Coordinator, Deep-Link Registry, Push
Coordinator, and health/diagnostics. The mobile client is always a client of
authoritative JoeOS services; it never accesses core databases, secrets, or
arbitrary service methods.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .clients import HostRegistry, MobileClientRegistry
from .models import (
    DeepLinkReference,
    HandoffRecord,
    HostRecord,
    MobileClientRecord,
    MobileOverview,
    MobileSession,
    NotificationDelivery,
    OfflineAction,
    PairingSession,
    PushRegistration,
    REMOTE_API_VERSION,
)
from .offline import DeepLinkRegistry, HandoffCoordinator, OfflineActionQueue
from .push import PushCoordinator
from .remote import RemoteCommandGateway, ScopedRemoteAPI
from .security import (
    MobileAuthenticationService,
    MobileSessionManager,
    PairingCoordinator,
)
from .storage import MobileStorage


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MobileService:
    def __init__(
        self,
        data_dir: str,
        *,
        server_version: str = "2.0.0",
        event_sink=None,
        command_executor=None,
        scoped_providers=None,
        push_provider=None,
    ) -> None:
        self.storage = MobileStorage(data_dir)
        self.storage.prepare()
        self._data_dir = Path(data_dir)
        self._event_sink = event_sink or (lambda level, source, message: None)

        self.clients = MobileClientRegistry(self._connection_factory)
        self.hosts = HostRegistry(self._connection_factory, server_version=server_version)
        self.auth = MobileAuthenticationService(self._connection_factory)
        self.pairing = PairingCoordinator(self._connection_factory, self.clients, self.hosts)
        self.sessions = MobileSessionManager(self._connection_factory, self.clients, self.auth)
        self.commands = RemoteCommandGateway(
            connection_factory=self._connection_factory,
            clients=self.clients,
            sessions=self.sessions,
            command_executor=command_executor,
            event_sink=self._event_sink,
        )
        self.remote_api = ScopedRemoteAPI(sessions=self.sessions, clients=self.clients, providers=scoped_providers)
        self.offline = OfflineActionQueue(self._connection_factory, self.clients, self.sessions)
        self.handoffs = HandoffCoordinator(self._connection_factory)
        self.deep_links = DeepLinkRegistry(self._connection_factory)
        self.push = PushCoordinator(self._connection_factory, self.clients, provider_dispatch=push_provider)

    def _connection_factory(self):
        connection = self.storage.connect()
        return _BorrowedConnection(connection)

    def prepare_defaults(self) -> HostRecord:
        return self.hosts.ensure_self(display_name="This JoeOS", endpoint="")

    # ------------------------------------------------------------------
    # Hosts & discovery
    # ------------------------------------------------------------------

    def primary_host(self) -> HostRecord:
        hosts = self.hosts.list()
        return hosts[0] if hosts else self.prepare_defaults()

    def list_hosts(self) -> Tuple[HostRecord, ...]:
        return self.hosts.list()

    def discover_hosts(self, entries: Sequence[dict] = ()) -> Tuple:
        return self.hosts.discover(entries=entries)

    # ------------------------------------------------------------------
    # Clients
    # ------------------------------------------------------------------

    def register_client(self, *, client_id: str, platform: str = "ios", app_version: str = "", installation_identity: str = "", crypto_identity_reference: str = "") -> MobileClientRecord:
        return self.clients.register(
            client_id=client_id,
            platform=platform,
            app_version=app_version,
            installation_identity=installation_identity,
            crypto_identity_reference=crypto_identity_reference,
        )

    def list_clients(self) -> Tuple[MobileClientRecord, ...]:
        return self.clients.list()

    def get_client(self, client_id: str) -> Optional[MobileClientRecord]:
        return self.clients.get(client_id)

    def grant_permission(self, *, client_id: str, permission: str, scope: str = "session", scope_target: str = "") -> None:
        self.clients.grant_permission(client_id=client_id, permission=permission, scope=scope, scope_target=scope_target)

    def revoke_permission(self, *, client_id: str, permission: str, scope_target: str = "") -> None:
        self.clients.revoke_permission(client_id=client_id, permission=permission, scope_target=scope_target)

    def client_permissions(self, client_id: str) -> Tuple[dict, ...]:
        return self.clients.grants_for(client_id)

    # ------------------------------------------------------------------
    # Pairing / auth / sessions
    # ------------------------------------------------------------------

    def begin_pairing(self, *, host_id: str, requested_permissions: Sequence[str] = (), requested_projects: Sequence[str] = ()) -> PairingSession:
        return self.pairing.begin(host_id=host_id, requested_permissions=requested_permissions, requested_projects=requested_projects)

    def confirm_pairing_host(self, *, session_id: str) -> PairingSession:
        return self.pairing.confirm_host(session_id=session_id)

    def confirm_pairing_client(self, *, session_id: str, client_id: str, code: str) -> MobileClientRecord:
        return self.pairing.confirm_client(session_id=session_id, client_id=client_id, code=code)

    def cancel_pairing(self, *, session_id: str) -> None:
        self.pairing.cancel(session_id=session_id)

    def pending_pairings(self) -> Tuple[dict, ...]:
        return self.pairing.list_pending()

    def authenticate(self, *, client_id: str, host_id: str, refresh_token: str, capabilities: Sequence[str] = (), projects: Sequence[str] = ()) -> MobileSession:
        if not self.auth.verify_refresh(client_id=client_id, refresh_token=refresh_token):
            raise ValueError("refresh credential rejected.")
        return self.sessions.create(
            client_id=client_id,
            host_id=host_id,
            capabilities=capabilities,
            projects=projects,
            scopes=(),
        )

    def issue_refresh(self, *, client_id: str) -> str:
        return self.auth.issue_refresh(client_id=client_id)

    def revoke_refresh(self, *, client_id: str) -> None:
        self.auth.revoke_refresh(client_id=client_id)

    def active_sessions(self) -> Tuple[MobileSession, ...]:
        return self.sessions.list_active()

    def session_valid(self, session_id: str) -> bool:
        return self.sessions.is_valid(session_id)

    def renew_session(self, *, session_id: str, client_id: str) -> MobileSession:
        return self.sessions.renew(session_id=session_id, client_id=client_id)

    def revoke_session(self, session_id: str, *, reason: str = "") -> None:
        self.sessions.revoke(session_id, reason=reason)

    # ------------------------------------------------------------------
    # Remote commands / scoped queries
    # ------------------------------------------------------------------

    def execute_command(self, **kwargs) -> dict:
        return self.commands.execute(**kwargs)

    def allowed_commands(self) -> Tuple[str, ...]:
        return self.commands.allowed_commands()

    def scoped_query(self, *, client_id: str, session_id: str, resource: str, scope: dict = None) -> dict:
        return self.remote_api.query(client_id=client_id, session_id=session_id, resource=resource, scope=scope)

    def register_scoped_provider(self, resource: str, provider) -> None:
        self.remote_api.register_provider(resource, provider)

    # ------------------------------------------------------------------
    # Offline / handoff / deep links / push
    # ------------------------------------------------------------------

    def enqueue_offline(self, **kwargs) -> OfflineAction:
        return self.offline.enqueue(**kwargs)

    def offline_actions(self, client_id: str) -> Tuple[OfflineAction, ...]:
        return self.offline.list_for_client(client_id)

    def revalidate_offline(self, *, client_id: str, session_id: str, target_state: Callable[[str, str], Optional[str]]) -> Dict[str, int]:
        return self.offline.revalidate_and_replay(client_id=client_id, session_id=session_id, target_state=target_state)

    def create_handoff(self, **kwargs) -> HandoffRecord:
        return self.handoffs.create(**kwargs)

    def resolve_handoff(self, *, handoff_id: str, accepted: bool, destination_trusted: bool = True) -> HandoffRecord:
        return self.handoffs.resolve(handoff_id=handoff_id, accepted=accepted, destination_trusted=destination_trusted)

    def list_handoffs(self) -> Tuple[HandoffRecord, ...]:
        return self.handoffs.list()

    def issue_deep_link(self, **kwargs) -> str:
        return self.deep_links.issue(**kwargs)

    def resolve_deep_link(self, link_id: str, *, user_identity: str = "user") -> DeepLinkReference:
        return self.deep_links.resolve(link_id, user_identity=user_identity)

    def register_push(self, **kwargs) -> PushRegistration:
        return self.push.register(**kwargs)

    def unregister_push(self, *, client_id: str) -> None:
        self.push.unregister(client_id=client_id)

    def push_registrations(self, client_id: str) -> Tuple[PushRegistration, ...]:
        return self.push.registrations_for(client_id)

    def deliver_notification(self, **kwargs) -> NotificationDelivery:
        return self.push.deliver(**kwargs)

    # ------------------------------------------------------------------
    # Revocation / lost device
    # ------------------------------------------------------------------

    def revoke_client(self, client_id: str, *, reason: str = "") -> MobileClientRecord:
        self.clients.mark_revoked(client_id, reason=reason)
        self.sessions.revoke_for_client(client_id, reason=reason or "client revoked")
        self.auth.revoke_refresh(client_id=client_id)
        self.push.unregister(client_id=client_id)
        return self.clients.get(client_id)

    def mark_lost(self, client_id: str) -> MobileClientRecord:
        self.clients.mark_lost(client_id)
        self.sessions.revoke_for_client(client_id, reason="device marked lost")
        self.auth.revoke_refresh(client_id=client_id)
        self.push.unregister(client_id=client_id, disabled=True)
        return self.clients.get(client_id)

    def revoke_all(self) -> int:
        count = 0
        for client in self.clients.list():
            if client.revocation_state == "active":
                self.revoke_client(client.client_id, reason="administrative revocation")
                count += 1
        return count

    # ------------------------------------------------------------------
    # Overview / health
    # ------------------------------------------------------------------

    def overview(self) -> MobileOverview:
        clients = self.clients.list()
        sessions = self.sessions.list_active()
        pending = self.pairing.list_pending()
        offline = sum(len(self.offline.list_for_client(c.client_id)) for c in clients)
        return MobileOverview(
            paired_clients=sum(1 for c in clients if c.pairing_state == "paired"),
            connected_clients=sum(1 for c in clients if c.authentication_state == "authenticated" and c.revocation_state == "active"),
            active_sessions=len(sessions),
            revoked_clients=sum(1 for c in clients if c.revocation_state == "revoked"),
            pending_pairings=len(pending),
            sync_failures=0,
            stale_offline_actions=offline,
            push_registration_failures=sum(1 for c in clients if c.push_registration_state == "failed"),
            incompatible_versions=0,
            lost_devices=sum(1 for c in clients if "lost" in c.revocation_state),
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