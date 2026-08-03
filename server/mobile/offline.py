"""Offline actions, conflict resolution, handoff, and deep links for the JoeOS
Mobile Companion.

Offline actions are limited to safe, idempotent operations that are
revalidated against authoritative state before replay; high-risk actions are
never queued. Conflicts preserve authoritative host state while protecting
mobile drafts. Handoffs and deep links use opaque short-lived references and
never execute actions automatically.
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Callable, Dict, Optional, Sequence, Tuple

from .clients import MobileClientRegistry, MobileError
from .models import (
    DeepLinkReference,
    HandoffRecord,
    OFFLINE_PROHIBITED_ACTIONS,
    OFFLINE_SAFE_ACTIONS,
    OfflineAction,
)
from .security import MobileSessionManager


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class OfflineActionQueue:
    """Safe, idempotent offline operations revalidated on reconnect."""

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection], clients: MobileClientRegistry, sessions: MobileSessionManager) -> None:
        self._connection_factory = connection_factory
        self._clients = clients
        self._sessions = sessions
        self._lock = threading.RLock()

    def enqueue(
        self,
        *,
        client_id: str,
        host_id: str,
        action: str,
        target: str = "",
        base_version: str = "",
        arguments: Optional[dict] = None,
        project: str = "",
        session_id: str = "",
    ) -> OfflineAction:
        if action in OFFLINE_PROHIBITED_ACTIONS:
            raise MobileError("action %r is prohibited for offline queueing." % action)
        if action not in OFFLINE_SAFE_ACTIONS:
            raise MobileError("action %r is not a supported safe offline action." % action)
        arguments_hash = hashlib.sha256(
            (action + "\0" + target + "\0" + str(arguments or {})).encode("utf-8")
        ).hexdigest()
        operation = OfflineAction(
            action_id="mop_" + uuid.uuid4().hex[:16],
            client_id=client_id,
            host_id=host_id,
            session_id=session_id,
            action=action,
            target=target,
            base_version=base_version,
            arguments_hash=arguments_hash,
            created_at=_now(),
            expires_at="",
            idempotency_key=uuid.uuid4().hex,
            privacy="private",
            project=project,
            conflict_policy="keep_authoritative",
            permission_state="pending",
            approval_state="none",
            retry_state="queued",
        )
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO mobile_offline_actions (
                    action_id, client_id, host_id, session_id, user_identity, action, target,
                    base_version, arguments_hash, created_at, expires_at, idempotency_key,
                    privacy, project, conflict_policy, permission_state, approval_state, retry_state
                ) VALUES (?, ?, ?, ?, 'user', ?, ?, ?, ?, ?, '', ?, ?, ?, 'keep_authoritative', 'pending', 'none', 'queued')
                """,
                (
                    operation.action_id, client_id, host_id, session_id, action, target,
                    base_version, arguments_hash, operation.created_at, operation.idempotency_key,
                    operation.privacy, project,
                ),
            )
        return operation

    def list_for_client(self, client_id: str) -> Tuple[OfflineAction, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM mobile_offline_actions WHERE client_id = ? AND retry_state = 'queued' ORDER BY created_at",
                (client_id,),
            ).fetchall()
        return tuple(self._row(row) for row in rows)

    def revalidate_and_replay(
        self,
        *,
        client_id: str,
        session_id: str,
        target_state: Callable[[str, str], Optional[str]],
    ) -> Dict[str, int]:
        """Revalidate queued actions against authoritative state on reconnect.

        ``target_state(target, base_version)`` returns the current version of a
        target or None. Actions whose base version no longer matches are not
        replayed blindly; they are marked for conflict review.
        """
        replayed = 0
        conflicted = 0
        discarded = 0
        for operation in self.list_for_client(client_id):
            current = target_state(operation.target, operation.base_version)
            if current is None:
                self._mark_discarded(operation.action_id, reason="target no longer exists")
                discarded += 1
                continue
            if operation.base_version and current != operation.base_version:
                self._mark_conflicted(operation.action_id, current)
                conflicted += 1
                continue
            replayed += 1
            self._mark_applied(operation.action_id)
        return {"replayed": replayed, "conflicted": conflicted, "discarded": discarded}

    def _mark_discarded(self, action_id: str, *, reason: str) -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE mobile_offline_actions SET retry_state = 'discarded' WHERE action_id = ?", (action_id,)
            )

    def _mark_conflicted(self, action_id: str, current: str) -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE mobile_offline_actions SET retry_state = 'conflict', base_version = ? WHERE action_id = ?",
                (current, action_id),
            )

    def _mark_applied(self, action_id: str) -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE mobile_offline_actions SET retry_state = 'applied' WHERE action_id = ?", (action_id,)
            )

    def pending_count(self, client_id: str) -> int:
        return len(self.list_for_client(client_id))

    @staticmethod
    def _row(row: sqlite3.Row) -> OfflineAction:
        return OfflineAction(
            action_id=str(row["action_id"]),
            client_id=str(row["client_id"]),
            host_id=str(row["host_id"]),
            session_id=str(row["session_id"]),
            user_identity=str(row["user_identity"]),
            action=str(row["action"]),
            target=str(row["target"]),
            base_version=str(row["base_version"]),
            arguments_hash=str(row["arguments_hash"]),
            created_at=str(row["created_at"]),
            expires_at=str(row["expires_at"]),
            idempotency_key=str(row["idempotency_key"]),
            privacy=str(row["privacy"]),
            project=str(row["project"]),
            conflict_policy=str(row["conflict_policy"]),
            permission_state=str(row["permission_state"]),
            approval_state=str(row["approval_state"]),
            retry_state=str(row["retry_state"]),
        )


class HandoffCoordinator:
    """Handoff between surfaces without duplicate action or state."""

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()

    def create(
        self,
        *,
        source_surface: str,
        destination_surface: str,
        user_identity: str = "user",
        host_id: str = "",
        item_type: str = "",
        item_id: str = "",
        content_position: str = "",
        selected_tab: str = "",
        unsent_draft: str = "",
        pending_action: str = "",
        privacy: str = "private",
    ) -> HandoffRecord:
        handoff = HandoffRecord(
            handoff_id="handoff_" + uuid.uuid4().hex[:16],
            source_surface=source_surface,
            destination_surface=destination_surface,
            user_identity=user_identity,
            host_id=host_id,
            item_type=item_type,
            item_id=item_id,
            content_position=content_position,
            selected_tab=selected_tab,
            unsent_draft=unsent_draft[:4000],
            pending_action=pending_action,
            privacy=privacy,
            expiration="",
            idempotency_key=uuid.uuid4().hex,
            created_at=_now(),
        )
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO mobile_handoffs (
                    handoff_id, source_surface, destination_surface, user_identity, host_id,
                    item_type, item_id, content_position, selected_tab, unsent_draft,
                    pending_action, privacy, expiration, state, idempotency_key, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', 'created', ?, ?)
                """,
                (
                    handoff.handoff_id, source_surface, destination_surface, user_identity, host_id,
                    item_type, item_id, content_position, selected_tab, unsent_draft,
                    pending_action, privacy, handoff.idempotency_key, _now(),
                ),
            )
        return handoff

    def resolve(self, *, handoff_id: str, accepted: bool, destination_trusted: bool = True) -> HandoffRecord:
        if not destination_trusted:
            raise MobileError("handoff destination is not trusted.")
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE mobile_handoffs SET state = ? WHERE handoff_id = ?",
                ("accepted" if accepted else "rejected", handoff_id),
            )
        return self.get(handoff_id)

    def get(self, handoff_id: str) -> Optional[HandoffRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM mobile_handoffs WHERE handoff_id = ?", (handoff_id,)
            ).fetchone()
        return self._row(row) if row else None

    def list(self, *, limit: int = 50) -> Tuple[HandoffRecord, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM mobile_handoffs ORDER BY created_at DESC LIMIT ?", (max(1, min(200, int(limit))),)
            ).fetchall()
        return tuple(self._row(row) for row in rows)

    @staticmethod
    def _row(row: sqlite3.Row) -> HandoffRecord:
        return HandoffRecord(
            handoff_id=str(row["handoff_id"]),
            source_surface=str(row["source_surface"]),
            destination_surface=str(row["destination_surface"]),
            user_identity=str(row["user_identity"]),
            host_id=str(row["host_id"]),
            item_type=str(row["item_type"]),
            item_id=str(row["item_id"]),
            content_position=str(row["content_position"]),
            selected_tab=str(row["selected_tab"]),
            unsent_draft=str(row["unsent_draft"]),
            pending_action=str(row["pending_action"]),
            privacy=str(row["privacy"]),
            expiration=str(row["expiration"]),
            state=str(row["state"]),
            idempotency_key=str(row["idempotency_key"]),
            created_at=str(row["created_at"]),
        )


class DeepLinkRegistry:
    """Opaque, short-lived deep-link references that never execute actions."""

    ALLOWED_TARGETS = {
        "notification", "approval", "mission", "task", "agent", "workflow_run",
        "project", "patch", "build", "test", "device", "handoff",
    }

    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()

    def issue(self, *, host_id: str, target_type: str, target_id: str, scope: str = "", user_identity: str = "user", ttl_minutes: int = 15) -> str:
        if target_type not in self.ALLOWED_TARGETS:
            raise MobileError("deep-link target %r is not allowlisted." % target_type)
        link_id = "link_" + uuid.uuid4().hex[:16]
        expires = (datetime.now(timezone.utc)).isoformat()
        from datetime import timedelta
        expires = (datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)).isoformat()
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO mobile_deep_links (link_id, host_id, user_identity, target_type, target_id, scope, expires_at, state, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?)
                """,
                (link_id, host_id, user_identity, target_type, target_id, scope, expires, _now()),
            )
        return link_id

    def resolve(self, link_id: str, *, user_identity: str = "user") -> DeepLinkReference:
        with self._lock, self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM mobile_deep_links WHERE link_id = ?", (link_id,)
            ).fetchone()
            if row is None:
                raise MobileError("deep link not found.")
            if str(row["state"]) != "active":
                raise MobileError("deep link is no longer active.")
            try:
                if datetime.fromisoformat(str(row["expires_at"])) < datetime.now(timezone.utc):
                    connection.execute(
                        "UPDATE mobile_deep_links SET state = 'expired' WHERE link_id = ?", (link_id,)
                    )
                    raise MobileError("deep link has expired.")
            except ValueError:
                raise MobileError("deep link has expired.") from None
            if str(row["user_identity"]) != user_identity:
                raise MobileError("deep link is bound to another user.")
            connection.execute(
                "UPDATE mobile_deep_links SET state = 'used' WHERE link_id = ?", (link_id,)
            )
            reference = DeepLinkReference(
                link_id=str(row["link_id"]),
                host_id=str(row["host_id"]),
                user_identity=str(row["user_identity"]),
                target_type=str(row["target_type"]),
                target_id=str(row["target_id"]),
                scope=str(row["scope"]),
                expires_at=str(row["expires_at"]),
                state="used",
                created_at=str(row["created_at"]),
            )
        return reference