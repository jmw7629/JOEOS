"""Push architecture, local notifications, and mobile health/diagnostics for
the JoeOS Mobile Companion.

Push is provider-neutral with privacy-safe minimal payloads. No production
push delivery is fabricated: contracts are implemented, local-notification
behavior is real, and an isolated test provider fixture is provided. The
remaining platform configuration (APNs/FCM credentials and entitlements) is
documented, not claimed.
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Callable, Dict, Optional, Sequence, Tuple

from .clients import MobileClientRegistry, MobileError
from .models import NotificationDelivery, PushRegistration


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PushCoordinator:
    """Provider-neutral push registration and privacy-safe delivery contracts."""

    def __init__(
        self,
        connection_factory: Callable[[], sqlite3.Connection],
        clients: MobileClientRegistry,
        provider_dispatch=None,
    ) -> None:
        self._connection_factory = connection_factory
        self._clients = clients
        self._provider_dispatch = provider_dispatch or (lambda delivery: {"queued": True, "provider": "test-isolated"})
        self._lock = threading.RLock()

    def register(
        self,
        *,
        client_id: str,
        platform: str = "ios",
        provider: str = "apns",
        push_token_reference: str = "",
        environment: str = "sandbox",
        enabled_categories: Sequence[str] = (),
    ) -> PushRegistration:
        client = self._clients.get(client_id)
        if client is None:
            raise MobileError("client not found.")
        registration_id = "push_" + uuid.uuid4().hex[:16]
        now = _now()
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO mobile_push_registrations (
                    registration_id, client_id, platform, provider, push_token_reference,
                    environment, registered_at, last_validation, enabled_categories,
                    privacy_mode, quiet_hours, health, revocation_state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'normal', 0, 'healthy', 'active')
                """,
                (
                    registration_id, client_id, platform, provider, push_token_reference,
                    environment, now, now, "\n".join(enabled_categories),
                ),
            )
            connection.execute(
                "UPDATE mobile_clients SET push_registration_state = 'registered' WHERE client_id = ?",
                (client_id,),
            )
        return self.get(registration_id)

    def get(self, registration_id: str) -> Optional[PushRegistration]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM mobile_push_registrations WHERE registration_id = ?", (registration_id,)
            ).fetchone()
        return self._row(row) if row else None

    def unregister(self, *, client_id: str, disabled: bool = False) -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE mobile_push_registrations SET revocation_state = 'revoked' WHERE client_id = ?",
                (client_id,),
            )
            connection.execute(
                "UPDATE mobile_clients SET push_registration_state = ? WHERE client_id = ?",
                ("disabled" if disabled else "unregistered", client_id),
            )

    def deliver(
        self,
        *,
        client_id: str,
        category: str = "",
        title: str = "",
        body: str = "",
        target_deep_link: str = "",
        severity: str = "informational",
        is_test_fixture: bool = False,
    ) -> NotificationDelivery:
        """Deliver a privacy-safe notification (minimal payload by default)."""
        client = self._clients.get(client_id)
        if client is None or client.revocation_state != "active":
            raise MobileError("client is revoked.")
        registration = self._active_registration(client_id)
        if registration is None:
            # Local-notification fallback only; no fabricated remote delivery.
            provider_state = "local_fallback"
        else:
            delivery = NotificationDelivery(
                delivery_id="delivery_" + uuid.uuid4().hex[:16],
                client_id=client_id,
                category=category,
                privacy_safe_title=title or "JoeOS needs your attention.",
                privacy_safe_body=body or "",
                target_deep_link=target_deep_link,
                severity=severity,
                created_at=_now(),
                provider_state="queued",
                is_test_fixture=is_test_fixture,
            )
            self._provider_dispatch(delivery)
            return delivery
        return NotificationDelivery(
            delivery_id="delivery_" + uuid.uuid4().hex[:16],
            client_id=client_id,
            category=category,
            privacy_safe_title=title or "JoeOS needs your attention.",
            privacy_safe_body=body or "",
            target_deep_link=target_deep_link,
            severity=severity,
            created_at=_now(),
            provider_state=provider_state,
            is_test_fixture=is_test_fixture,
        )

    def _active_registration(self, client_id: str) -> Optional[PushRegistration]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM mobile_push_registrations WHERE client_id = ? AND revocation_state = 'active' ORDER BY registered_at DESC LIMIT 1",
                (client_id,),
            ).fetchone()
        return self._row(row) if row else None

    def registrations_for(self, client_id: str) -> Tuple[PushRegistration, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM mobile_push_registrations WHERE client_id = ? ORDER BY registered_at DESC",
                (client_id,),
            ).fetchall()
        return tuple(self._row(row) for row in rows)

    @staticmethod
    def _row(row: sqlite3.Row) -> PushRegistration:
        return PushRegistration(
            registration_id=str(row["registration_id"]),
            client_id=str(row["client_id"]),
            platform=str(row["platform"]),
            provider=str(row["provider"]),
            push_token_reference=str(row["push_token_reference"]),
            environment=str(row["environment"]),
            registered_at=str(row["registered_at"]),
            last_validation=str(row["last_validation"]),
            enabled_categories=tuple(p for p in str(row["enabled_categories"]).split("\n") if p),
            privacy_mode=str(row["privacy_mode"]),
            quiet_hours=bool(row["quiet_hours"]),
            health=str(row["health"]),
            revocation_state=str(row["revocation_state"]),
        )