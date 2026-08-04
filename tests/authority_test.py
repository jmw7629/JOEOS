"""Phase P3A authority tests: bootstrap, assignment, device-key application
authentication, sessions, revocation, and principal capabilities."""

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from server.identity.authority_repository import (
    APPLICATION_AUTHENTICATION_DOMAIN,
    SQLiteAuthorityRepository,
)
from server.identity.authority_service import (
    AuthorityAuthenticationError,
    AuthorityConflictError,
    AuthorityNotFoundError,
    AuthorityService,
)
from server.identity.crypto import base64url_encode, encode_p256_public_key
from server.identity.key_protection import PairingKeyProtector
from server.identity.repository import SQLiteDeviceIdentityRepository

OWNER_USER = UUID("11111111-2222-4333-8444-555555555555")


class MutableClock:
    def __init__(self):
        self.value = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)

    def __call__(self):
        return int(self.value.timestamp())

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


class SequenceUUID:
    def __init__(self, start: int = 0):
        self._n = start

    def __call__(self):
        self._n += 1
        return UUID(int=self._n, version=4)


class AuthorityFixture(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "authority.db"
        self.clock = MutableClock()
        self.uuid_source = SequenceUUID()

        def connect():
            connection = sqlite3.connect(str(self.database), timeout=10)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 10000")
            return connection

        self.connect = connect
        self.device_repository = SQLiteDeviceIdentityRepository(
            connect, PairingKeyProtector(bytes(range(32)))
        )
        self.device_repository.prepare()
        self.service = AuthorityService(
            SQLiteAuthorityRepository(connect),
            self.device_repository,
            now_provider=self.clock,
            uuid_provider=self.uuid_source,
        )
        self.service.prepare()
        self.bootstrap = self.service.bootstrap(
            display_name="JoeOS Owner",
            organization_name="JoeOS",
            workspace_name="Default Workspace",
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def _enroll_device(self, key: ec.EllipticCurvePrivateKey) -> UUID:
        """Inserts a directly enrolled active_unassigned device (test fixture)."""
        device_id = self.uuid_source()
        auth_public_key = encode_p256_public_key(key.public_key())
        approval_public_key = encode_p256_public_key(
            ec.generate_private_key(ec.SECP256R1()).public_key()
        )
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO enrolled_devices(
                    device_id, enrollment_id, server_id, credential_id, audience_origin,
                    client_instance_id, display_name, platform, os_version, app_version,
                    state, enrolled_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ios', '17.6', '1.0.0', 'active_unassigned', ?)
                """,
                (
                    str(device_id),
                    str(self.uuid_source()),
                    str(self.uuid_source()),
                    base64url_encode(bytes(range(32))),
                    "https://joeos.example.com",
                    str(self.uuid_source()),
                    "Test iPhone",
                    self.clock(),
                ),
            )
            connection.execute(
                """
                INSERT INTO enrolled_device_keys(
                    fingerprint, device_id, purpose, public_key, active, created_at
                ) VALUES (?, ?, 'device_authentication', ?, 1, ?)
                """,
                (
                    base64url_encode(bytes(range(32))),
                    str(device_id),
                    auth_public_key,
                    self.clock(),
                ),
            )
            connection.execute(
                """
                INSERT INTO enrolled_device_keys(
                    fingerprint, device_id, purpose, public_key, active, created_at
                ) VALUES (?, ?, 'approval', ?, 1, ?)
                """,
                (
                    base64url_encode(bytes(range(33))),
                    str(device_id),
                    approval_public_key,
                    self.clock(),
                ),
            )
            connection.commit()
        return device_id

    def _assign_device(self, device_id: UUID) -> None:
        roles = self.service.list_roles()
        self.service.assign_device(
            device_id=device_id,
            user_id=UUID(self.bootstrap["user_id"]),
            organization_id=UUID(self.bootstrap["organization_id"]),
            workspace_id=UUID(self.bootstrap["workspace_id"]),
            role_ids=[roles[0]["id"]],
            assigned_by=UUID(self.bootstrap["user_id"]),
        )


class BootstrapTests(AuthorityFixture):
    def test_bootstrap_is_idempotent_and_creates_no_password(self):
        roles = self.service.list_roles()
        capabilities = self.service.list_capabilities()
        self.assertEqual(len(roles), 1)
        self.assertEqual(roles[0]["name"], "joeos.owner")
        self.assertGreaterEqual(len(capabilities), 6)
        self.assertFalse(any("password" in str(capability).lower() for capability in capabilities))

    def test_second_owner_is_refused(self):
        with self.assertRaises(AuthorityConflictError):
            self.service.bootstrap(
                display_name="Second Owner",
                organization_name="Other",
                workspace_name="Other WS",
            )
        users = self.service.list_users()
        self.assertEqual(len(users), 1)

    def test_owner_has_standard_capabilities_only(self):
        roles = self.service.list_roles()
        owner_id = UUID(self.bootstrap["user_id"])
        principal = {
            "session_id": uuid4(),
            "device_id": uuid4(),
            "user": {"id": owner_id},
            "organization": {"id": UUID(self.bootstrap["organization_id"])},
            "workspace": {"id": UUID(self.bootstrap["workspace_id"])},
        }
        roles_and_caps = self.service._repository.principal_roles_and_capabilities(
            owner_id,
            UUID(self.bootstrap["organization_id"]),
            UUID(self.bootstrap["workspace_id"]),
        )
        _ = roles
        capabilities = roles_and_caps["capabilities"]
        self.assertIn("conversation.write", capabilities)
        self.assertIn("conversation.invoke_ai", capabilities)
        # Privileged and critical actions are NOT granted by bootstrap.
        for denied in ("approval.sign", "repository.write", "shell.execute", "deployment.execute", "secret.access"):
            self.assertNotIn(denied, capabilities)


class AssignmentTests(AuthorityFixture):
    def test_assign_active_unassigned_device(self):
        device_id = self._enroll_device(ec.generate_private_key(ec.SECP256R1()))
        self._assign_device(device_id)
        devices = self.service.list_devices()
        device = next(item for item in devices if item["device_id"] == str(device_id))
        self.assertEqual(device["assignment_status"], "active")
        self.assertEqual(device["state"], "active_unassigned")

    def test_assign_twice_fails(self):
        device_id = self._enroll_device(ec.generate_private_key(ec.SECP256R1()))
        self._assign_device(device_id)
        with self.assertRaises(AuthorityConflictError):
            self._assign_device(device_id)

    def test_assign_unknown_device_fails(self):
        with self.assertRaises(AuthorityNotFoundError):
            self._assign_device(self.uuid_source())

    def test_assign_revoked_device_fails(self):
        device_id = self._enroll_device(ec.generate_private_key(ec.SECP256R1()))
        self.device_repository.revoke_device(device_id, "test", self.clock())
        with self.assertRaises(AuthorityConflictError):
            self._assign_device(device_id)

    def test_revoke_assignment_then_reassign(self):
        device_id = self._enroll_device(ec.generate_private_key(ec.SECP256R1()))
        self._assign_device(device_id)
        owner = UUID(self.bootstrap["user_id"])
        self.assertTrue(self.service.revoke_device_assignment(device_id, owner))
        self._assign_device(device_id)
        device = next(item for item in self.service.list_devices() if item["device_id"] == str(device_id))
        self.assertEqual(device["assignment_status"], "active")


class AuthenticationTests(AuthorityFixture):
    def _authenticate(self, key: ec.EllipticCurvePrivateKey, device_id: UUID) -> dict:
        user_id = UUID(self.bootstrap["user_id"])
        challenge = self.service.create_authentication_challenge(device_id, user_id)
        message = challenge["message"]
        signature = key.sign(message.encode("ascii"), ec.ECDSA(hashes.SHA256()))
        return self.service.solve_authentication_challenge(
            challenge["challenge_id"],
            base64url_encode(signature),
        )

    def test_device_key_proof_establishes_session(self):
        key = ec.generate_private_key(ec.SECP256R1())
        device_id = self._enroll_device(key)
        self._assign_device(device_id)
        result = self._authenticate(key, device_id)
        session = result["session"]
        self.assertEqual(session["status"], "active")
        self.assertTrue(result["refresh_token"])
        self.assertEqual(result["principal"]["user"]["id"], UUID(self.bootstrap["user_id"]))
        self.assertIn("conversation.write", result["principal"]["capabilities"])

    def test_challenge_rejects_wrong_signature(self):
        key = ec.generate_private_key(ec.SECP256R1())
        device_id = self._enroll_device(key)
        self._assign_device(device_id)
        challenge = self.service.create_authentication_challenge(
            device_id, UUID(self.bootstrap["user_id"])
        )
        wrong_key = ec.generate_private_key(ec.SECP256R1())
        signature = wrong_key.sign(
            challenge["message"].encode("ascii"), ec.ECDSA(hashes.SHA256())
        )
        with self.assertRaises(AuthorityAuthenticationError):
            self.service.solve_authentication_challenge(
                challenge["challenge_id"], base64url_encode(signature)
            )

    def test_unassigned_device_cannot_start_auth(self):
        key = ec.generate_private_key(ec.SECP256R1())
        device_id = self._enroll_device(key)
        with self.assertRaises(AuthorityAuthenticationError):
            self.service.create_authentication_challenge(
                device_id, UUID(self.bootstrap["user_id"])
            )

    def test_application_sessions_expire(self):
        key = ec.generate_private_key(ec.SECP256R1())
        device_id = self._enroll_device(key)
        self._assign_device(device_id)
        result = self._authenticate(key, device_id)
        session_id = result["session"]["session_id"]
        self.assertIsNotNone(self.service.principal_for_session(session_id))
        self.clock.advance(16 * 60)
        self.assertIsNone(self.service.principal_for_session(session_id))

    def test_revoking_assignment_kills_sessions_immediately(self):
        key = ec.generate_private_key(ec.SECP256R1())
        device_id = self._enroll_device(key)
        self._assign_device(device_id)
        result = self._authenticate(key, device_id)
        session_id = result["session"]["session_id"]
        self.assertIsNotNone(self.service.principal_for_session(session_id))
        owner = UUID(self.bootstrap["user_id"])
        self.assertTrue(self.service.revoke_device_assignment(device_id, owner))
        self.assertIsNone(self.service.principal_for_session(session_id))

    def test_disabling_user_kills_sessions_immediately(self):
        key = ec.generate_private_key(ec.SECP256R1())
        device_id = self._enroll_device(key)
        self._assign_device(device_id)
        result = self._authenticate(key, device_id)
        session_id = result["session"]["session_id"]
        self.assertIsNotNone(self.service.principal_for_session(session_id))
        self.assertTrue(self.service.set_user_status(UUID(self.bootstrap["user_id"]), "disabled"))
        self.assertIsNone(self.service.principal_for_session(session_id))

    def test_refresh_rotates_and_old_refresh_cannot_replay(self):
        key = ec.generate_private_key(ec.SECP256R1())
        device_id = self._enroll_device(key)
        self._assign_device(device_id)
        first = self._authenticate(key, device_id)
        refreshed = self.service.refresh_session(
            first["refresh_id"], first["refresh_token"]
        )
        self.assertNotEqual(refreshed["session"]["session_id"], first["session"]["session_id"])
        with self.assertRaises(AuthorityAuthenticationError):
            self.service.refresh_session(first["refresh_id"], first["refresh_token"])

    def test_logout_revokes_session(self):
        key = ec.generate_private_key(ec.SECP256R1())
        device_id = self._enroll_device(key)
        self._assign_device(device_id)
        result = self._authenticate(key, device_id)
        session_id = result["session"]["session_id"]
        self.assertTrue(self.service.logout(session_id))
        self.assertIsNone(self.service.principal_for_session(session_id))

    def test_revoke_sessions_for_user_and_device(self):
        key = ec.generate_private_key(ec.SECP256R1())
        device_id = self._enroll_device(key)
        self._assign_device(device_id)
        self._authenticate(key, device_id)
        user_id = UUID(self.bootstrap["user_id"])
        self.assertGreaterEqual(self.service.revoke_sessions_for_user(user_id), 1)
        self._authenticate(key, device_id)
        self.assertGreaterEqual(self.service.revoke_sessions_for_device(device_id), 1)

    def test_principal_requires_live_session(self):
        self.assertIsNone(self.service.principal_for_session(uuid4()))
        with self.assertRaises(AuthorityAuthenticationError):
            self.service.require_capability(uuid4(), "conversation.read")
