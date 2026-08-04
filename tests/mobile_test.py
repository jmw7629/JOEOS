"""Tests for the JoeOS Mobile Companion and Secure Remote Operations Platform.

Covers mobile client identity, host registry, discovery, pairing security,
authentication, sessions, remote command gateway (allowlist/prohibition),
scoped queries, offline queue (safe-only, idempotent, revalidated), conflicts,
handoff, deep links, push privacy, revocation, lost-device mode, and overview.
"""

import tempfile
import unittest
from pathlib import Path

from server.mobile import MobileService, MobileError
from server.mobile.models import MOBILE_PERMISSIONS


class MobileFixture(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.service = MobileService(str(self.root / "mobile"))
        self.service.pairing._generate = lambda: "123456"
        self.host = self.service.prepare_defaults()
        self.client = self.service.register_client(
            client_id="client.iphone", platform="ios", app_version="1.0.0", installation_identity="inst-abc"
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def _pair(self, permissions=("data.view_system_status", "data.view_projects", "action.acknowledge_notification", "action.create_note", "action.approve_low_risk")):
        pair = self.service.begin_pairing(host_id=self.host.host_id, requested_permissions=permissions)
        self.service.confirm_pairing_host(session_id=pair.session_id)
        return self.service.confirm_pairing_client(session_id=pair.session_id, client_id=self.client.client_id, code="123456")

    def _auth(self):
        for p in ("data.view_system_status", "data.view_projects", "action.acknowledge_notification", "action.create_note", "action.approve_low_risk"):
            self.service.grant_permission(client_id=self.client.client_id, permission=p, scope="session")
        refresh = self.service.issue_refresh(client_id=self.client.client_id)
        return self.service.authenticate(
            client_id=self.client.client_id, host_id=self.host.host_id, refresh_token=refresh, capabilities=("command_center",), projects=("demo",)
        )


class ClientIdentityTests(MobileFixture):
    def test_stable_client_identity(self):
        self.assertEqual(self.client.client_id, "client.iphone")
        self.assertEqual(self.client.pairing_state, "unpaired")

    def test_duplicate_client_registration_upserts(self):
        again = self.service.register_client(client_id="client.iphone", platform="ios", app_version="2.0.0")
        self.assertEqual(again.client_id, "client.iphone")


class HostRegistryTests(MobileFixture):
    def test_primary_host_is_trusted(self):
        self.assertEqual(self.host.trusted_state, "trusted")
        self.assertEqual(self.host.paired_state, "paired")

    def test_discovery_is_explicit(self):
        results = self.service.discover_hosts([{"display_name": "Laptop", "connection_path": "https://laptop.local"}])
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].pairing_required)


class PairingTests(MobileFixture):
    def test_pairing_requires_host_confirmation(self):
        pair = self.service.begin_pairing(host_id=self.host.host_id)
        with self.assertRaises(MobileError):
            self.service.confirm_pairing_client(session_id=pair.session_id, client_id=self.client.client_id, code="123456")

    def test_pairing_completes(self):
        paired = self._pair()
        self.assertEqual(paired.pairing_state, "paired")
        self.assertEqual(paired.paired_host, self.host.host_id)

    def test_pairing_wrong_code_rejected(self):
        pair = self.service.begin_pairing(host_id=self.host.host_id)
        self.service.confirm_pairing_host(session_id=pair.session_id)
        with self.assertRaises(MobileError):
            self.service.confirm_pairing_client(session_id=pair.session_id, client_id=self.client.client_id, code="000000")


class AuthenticationTests(MobileFixture):
    def test_auth_requires_valid_refresh(self):
        self._pair()
        with self.assertRaises(ValueError):
            self.service.authenticate(client_id=self.client.client_id, host_id=self.host.host_id, refresh_token="bogus")

    def test_session_created_and_valid(self):
        self._pair()
        session = self._auth()
        self.assertEqual(session.connection_state, "active")
        self.assertTrue(self.service.session_valid(session.session_id))

    def test_revocation_invalidates_session(self):
        self._pair()
        session = self._auth()
        self.service.revoke_client(self.client.client_id, reason="test")
        self.assertFalse(self.service.session_valid(session.session_id))

    def test_revoked_refresh_rejected(self):
        self._pair()
        refresh = self.service.issue_refresh(client_id=self.client.client_id)
        self.service.revoke_refresh(client_id=self.client.client_id)
        with self.assertRaises(ValueError):
            self.service.authenticate(client_id=self.client.client_id, host_id=self.host.host_id, refresh_token=refresh)


class RemoteCommandGatewayTests(MobileFixture):
    def test_allowlisted_command(self):
        self._pair()
        session = self._auth()
        result = self.service.execute_command(client_id=self.client.client_id, session_id=session.session_id, command="view_system_status")
        self.assertEqual(result["command"], "view_system_status")

    def test_prohibited_command_blocked(self):
        self._pair()
        session = self._auth()
        with self.assertRaises(MobileError):
            self.service.execute_command(client_id=self.client.client_id, session_id=session.session_id, command="shell_execute")

    def test_unlisted_command_blocked(self):
        self._pair()
        session = self._auth()
        with self.assertRaises(MobileError):
            self.service.execute_command(client_id=self.client.client_id, session_id=session.session_id, command="sudo_rm")

    def test_missing_permission_blocks(self):
        self._pair()
        # Grant no permissions, create session with none.
        refresh = self.service.issue_refresh(client_id=self.client.client_id)
        session = self.service.authenticate(client_id=self.client.client_id, host_id=self.host.host_id, refresh_token=refresh)
        with self.assertRaises(MobileError):
            self.service.execute_command(client_id=self.client.client_id, session_id=session.session_id, command="view_system_status")

    def test_invalid_session_blocked(self):
        self._pair()
        with self.assertRaises(MobileError):
            self.service.execute_command(client_id=self.client.client_id, session_id="bogus", command="view_system_status")


class ScopedQueryTests(MobileFixture):
    def test_scoped_query(self):
        self._pair()
        session = self._auth()
        self.service.register_scoped_provider("command_center", lambda session, scope: {"status": "healthy"})
        result = self.service.scoped_query(client_id=self.client.client_id, session_id=session.session_id, resource="command_center")
        self.assertEqual(result["status"], "healthy")

    def test_unknown_resource_blocked(self):
        self._pair()
        session = self._auth()
        with self.assertRaises(MobileError):
            self.service.scoped_query(client_id=self.client.client_id, session_id=session.session_id, resource="raw_database")

    def test_all_scoped_resources_available(self):
        # The remote API exposes typed scoped resources, never arbitrary
        # service methods or raw databases.
        self._pair()
        session = self._auth()
        for resource in ("command_center", "projects", "missions", "runtime", "workflows", "communications", "devices", "mobile", "performance"):
            self.service.register_scoped_provider(resource, lambda session, scope, r=resource: {"resource": r})
            result = self.service.scoped_query(client_id=self.client.client_id, session_id=session.session_id, resource=resource)
            self.assertEqual(result["resource"], resource)


class OfflineQueueTests(MobileFixture):
    def test_safe_action_queued(self):
        operation = self.service.enqueue_offline(client_id=self.client.client_id, host_id=self.host.host_id, action="create_note", target="note-1", base_version="v1")
        self.assertEqual(operation.action, "create_note")

    def test_prohibited_action_not_queued(self):
        with self.assertRaises(MobileError):
            self.service.enqueue_offline(client_id=self.client.client_id, host_id=self.host.host_id, action="destructive_approval")
        with self.assertRaises(MobileError):
            self.service.enqueue_offline(client_id=self.client.client_id, host_id=self.host.host_id, action="git_push")

    def test_revalidation_matches_version(self):
        self.service.enqueue_offline(client_id=self.client.client_id, host_id=self.host.host_id, action="create_note", target="note-1", base_version="v1")
        result = self.service.revalidate_offline(client_id=self.client.client_id, session_id="", target_state=lambda t, b: "v1" if t == "note-1" else None)
        self.assertEqual(result["replayed"], 1)

    def test_revalidation_conflict_on_version_mismatch(self):
        self.service.enqueue_offline(client_id=self.client.client_id, host_id=self.host.host_id, action="create_note", target="note-1", base_version="v1")
        result = self.service.revalidate_offline(client_id=self.client.client_id, session_id="", target_state=lambda t, b: "v2" if t == "note-1" else None)
        self.assertEqual(result["conflicted"], 1)

    def test_revalidation_discards_missing_target(self):
        self.service.enqueue_offline(client_id=self.client.client_id, host_id=self.host.host_id, action="create_note", target="note-gone", base_version="v1")
        result = self.service.revalidate_offline(client_id=self.client.client_id, session_id="", target_state=lambda t, b: None)
        self.assertEqual(result["discarded"], 1)


class HandoffTests(MobileFixture):
    def test_handoff_lifecycle(self):
        handoff = self.service.create_handoff(source_surface="mobile", destination_surface="desktop", item_type="mission", item_id="m1")
        self.assertEqual(handoff.state, "created")
        resolved = self.service.resolve_handoff(handoff_id=handoff.handoff_id, accepted=True)
        self.assertEqual(resolved.state, "accepted")

    def test_handoff_requires_trusted_destination(self):
        handoff = self.service.create_handoff(source_surface="mobile", destination_surface="desktop")
        with self.assertRaises(MobileError):
            self.service.resolve_handoff(handoff_id=handoff.handoff_id, accepted=True, destination_trusted=False)


class DeepLinkTests(MobileFixture):
    def test_deep_link_issue_and_resolve(self):
        link = self.service.issue_deep_link(host_id=self.host.host_id, target_type="approval", target_id="apr-1")
        reference = self.service.resolve_deep_link(link)
        self.assertEqual(reference.target_id, "apr-1")

    def test_deep_link_single_use(self):
        link = self.service.issue_deep_link(host_id=self.host.host_id, target_type="approval", target_id="apr-1")
        self.service.resolve_deep_link(link)
        with self.assertRaises(MobileError):
            self.service.resolve_deep_link(link)

    def test_deep_link_denies_arbitrary_target(self):
        with self.assertRaises(MobileError):
            self.service.issue_deep_link(host_id=self.host.host_id, target_type="shell", target_id="x")

    def test_deep_link_user_bound(self):
        link = self.service.issue_deep_link(host_id=self.host.host_id, target_type="approval", target_id="apr-1", user_identity="user")
        with self.assertRaises(MobileError):
            self.service.resolve_deep_link(link, user_identity="other")


class PushTests(MobileFixture):
    def test_push_registration(self):
        registration = self.service.register_push(client_id=self.client.client_id, push_token_reference="token-ref")
        self.assertEqual(registration.health, "healthy")

    def test_push_payload_is_privacy_safe(self):
        delivery = self.service.deliver_notification(client_id=self.client.client_id, category="approval_request", title="", body="")
        self.assertEqual(delivery.privacy_safe_title, "JoeOS needs your attention.")
        # No private details in the payload by default.
        self.assertNotIn("secret", delivery.privacy_safe_body)

    def test_push_to_revoked_client_blocked(self):
        self.service.revoke_client(self.client.client_id, reason="test")
        with self.assertRaises(MobileError):
            self.service.deliver_notification(client_id=self.client.client_id, category="approval_request")


class RevocationTests(MobileFixture):
    def test_revoke_client_terminates_everything(self):
        self._pair()
        session = self._auth()
        self.service.register_push(client_id=self.client.client_id, push_token_reference="t")
        self.service.revoke_client(self.client.client_id, reason="test")
        client = self.service.get_client(self.client.client_id)
        self.assertEqual(client.revocation_state, "revoked")
        self.assertFalse(self.service.session_valid(session.session_id))
        self.assertEqual(len(self.service.active_sessions()), 0)

    def test_mark_lost(self):
        self._pair()
        client = self.service.mark_lost(self.client.client_id)
        self.assertEqual(client.revocation_state, "revoked")
        self.assertEqual(client.push_registration_state, "disabled")


class OverviewTests(MobileFixture):
    def test_overview_real_state(self):
        self._pair()
        overview = self.service.overview()
        self.assertEqual(overview.paired_clients, 1)


class PermissionModelTests(unittest.TestCase):
    def test_permission_catalog_is_granular(self):
        self.assertIn("data.view_code_excerpts", MOBILE_PERMISSIONS)
        self.assertIn("action.approve_low_risk", MOBILE_PERMISSIONS)
        self.assertIn("hardware.camera", MOBILE_PERMISSIONS)

    def test_unknown_permission_rejected(self):
        service = MobileService(str(Path(tempfile.mkdtemp()) / "m"))
        client = service.register_client(client_id="client.x")
        with self.assertRaises(MobileError):
            service.grant_permission(client_id=client.client_id, permission="data.view_everything")


if __name__ == "__main__":
    unittest.main()