"""Phase P3A end-to-end HTTP validation: device-key application authentication,
application session, principal retrieval, and canonical conversations over the
real routers with deny-by-default enforcement."""

import asyncio
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.conversations.repository import SQLiteConversationRepository
from server.conversations.router import router as conversations_router
from server.conversations.service import ConversationService
from server.identity.authority_repository import SQLiteAuthorityRepository
from server.identity.authority_router import router as authority_router
from server.identity.authority_service import AuthorityService
from server.identity.crypto import base64url_encode, encode_p256_public_key
from server.identity.key_protection import PairingKeyProtector
from server.identity.repository import SQLiteDeviceIdentityRepository


class MutableClock:
    def __init__(self):
        self.value = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)

    def __call__(self):
        return int(self.value.timestamp())


class SequenceUUID:
    def __init__(self, start: int = 0):
        self._n = start

    def __call__(self):
        self._n += 1
        return UUID(int=self._n, version=4)


class FakeInferenceResult:
    def __init__(self, reply="authenticated provider reply"):
        self.reply = reply
        self.provider = "test-provider"
        self.model = "test-model"
        self.tokens_used = 5


class AuthorityHTTPFixture(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "e2e.db"
        self.clock = MutableClock()
        self.uuid_source = SequenceUUID()

        def connect():
            connection = sqlite3.connect(str(self.database), timeout=10)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 10000")
            return connection

        self.connect = connect
        device_repository = SQLiteDeviceIdentityRepository(
            connect, PairingKeyProtector(bytes(range(32)))
        )
        device_repository.prepare()
        self.authority = AuthorityService(
            SQLiteAuthorityRepository(connect),
            device_repository,
            now_provider=self.clock,
            uuid_provider=self.uuid_source,
        )
        self.authority.prepare()
        self.bootstrap = self.authority.bootstrap(
            display_name="Owner", organization_name="JoeOS", workspace_name="Default"
        )
        self.device_repository = device_repository
        self.authentication_key = ec.generate_private_key(ec.SECP256R1())
        self.device_id = self._enroll_device(self.authentication_key)
        self._assign_device(self.device_id)

        async def fake_infer(messages):
            return FakeInferenceResult()

        self.conversation_service = ConversationService(
            SQLiteConversationRepository(connect),
            infer=fake_infer,
            availability=lambda: {"available": True, "reason": "", "streaming": False},
            now_provider=self.clock,
            uuid_provider=self.uuid_source,
        )
        self.conversation_service.prepare()

        app = FastAPI()
        app.state.authority_service = self.authority
        app.state.conversation_service = self.conversation_service
        app.include_router(authority_router)
        app.include_router(conversations_router)
        self.client = TestClient(app)

    def tearDown(self):
        self.tempdir.cleanup()

    def _enroll_device(self, key: ec.EllipticCurvePrivateKey) -> UUID:
        device_id = self.uuid_source()
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
                    "http://100.98.25.26:8080",
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
                    encode_p256_public_key(key.public_key()),
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
                    encode_p256_public_key(
                        ec.generate_private_key(ec.SECP256R1()).public_key()
                    ),
                    self.clock(),
                ),
            )
            connection.commit()
        return device_id

    def _assign_device(self, device_id: UUID) -> None:
        roles = self.authority.list_roles()
        self.authority.assign_device(
            device_id=device_id,
            user_id=UUID(self.bootstrap["user_id"]),
            organization_id=UUID(self.bootstrap["organization_id"]),
            workspace_id=UUID(self.bootstrap["workspace_id"]),
            role_ids=[roles[0]["id"]],
            assigned_by=UUID(self.bootstrap["user_id"]),
        )

    def _authenticate(self) -> dict:
        user_id = UUID(self.bootstrap["user_id"])
        challenge = self.client.post(
            "/api/v1/auth/challenge",
            json={"device_id": str(self.device_id), "user_id": str(user_id)},
        )
        self.assertEqual(challenge.status_code, 201, challenge.text)
        body = challenge.json()
        signature = self.authentication_key.sign(
            body["message"].encode("ascii"), ec.ECDSA(hashes.SHA256())
        )
        response = self.client.post(
            "/api/v1/auth/session",
            json={"challenge_id": str(body["challenge_id"]), "signature": base64url_encode(signature)},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()


class EndToEndAuthorityTests(AuthorityHTTPFixture):
    def test_deny_by_default_without_session(self):
        self.assertEqual(self.client.get("/api/v1/principal").status_code, 401)
        self.assertEqual(self.client.get("/api/v1/conversations").status_code, 401)

    def test_browser_request_without_session_is_gated(self):
        browser_headers = {"Origin": "https://joeos.example"}
        self.assertEqual(
            self.client.post(
                "/api/v1/conversations", headers=browser_headers, json={"title": "Gated"}
            ).status_code,
            401,
        )
        self.assertEqual(
            self.client.get("/api/v1/principal", headers=browser_headers).status_code,
            401,
        )

    def test_device_key_authentication_establishes_session(self):
        result = self._authenticate()
        session_id = result["session"]["session_id"]
        principal = self.client.get(
            "/api/v1/principal", headers={"X-JoeOS-Session": session_id}
        )
        self.assertEqual(principal.status_code, 200)
        body = principal.json()
        self.assertEqual(body["user"]["display_name"], "Owner")
        self.assertIn("conversation.write", body["capabilities"])
        self.assertNotIn("shell.execute", body["capabilities"])

    def test_full_conversation_flow_and_resume(self):
        result = self._authenticate()
        session_id = result["session"]["session_id"]
        headers = {"X-JoeOS-Session": session_id}

        created = self.client.post(
            "/api/v1/conversations", headers=headers, json={"title": "P3A"}
        )
        self.assertEqual(created.status_code, 201, created.text)
        conversation_id = created.json()["conversation_id"]

        submitted = self.client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            headers=headers,
            json={"content": "Authenticate and answer"},
        )
        self.assertEqual(submitted.status_code, 202, submitted.text)
        messages = submitted.json()["messages"]
        self.assertEqual(messages[-1]["role"], "assistant")
        self.assertEqual(messages[-1]["status"], "completed")
        self.assertEqual(messages[-1]["content"], "authenticated provider reply")

        reopened = self.client.get(
            f"/api/v1/conversations/{conversation_id}", headers=headers
        )
        self.assertEqual(reopened.status_code, 200)
        self.assertEqual(reopened.json()["conversation_id"], conversation_id)
        self.assertEqual(len(reopened.json()["messages"]), 2)

        retried = self.client.post(
            f"/api/v1/conversations/{conversation_id}/retry", headers=headers
        )
        self.assertEqual(retried.status_code, 200, retried.text)
        assistant = [
            m for m in retried.json()["messages"] if m["role"] == "assistant"
        ]
        self.assertEqual(len(assistant), 2)

    def test_logout_and_refresh_flow(self):
        result = self._authenticate()
        session_id = result["session"]["session_id"]
        headers = {"X-JoeOS-Session": session_id}
        self.assertEqual(self.client.get("/api/v1/principal", headers=headers).status_code, 200)

        # Refresh rotates to a new session and revokes the old refresh credential.
        refreshed = self.client.post(
            "/api/v1/auth/refresh",
            json={"refresh_id": str(result["refresh_id"]), "refresh_token": result["refresh_token"]},
        )
        self.assertEqual(refreshed.status_code, 200, refreshed.text)
        new_session = refreshed.json()["session"]["session_id"]
        self.assertNotEqual(new_session, session_id)
        self.assertEqual(
            self.client.get(
                "/api/v1/principal", headers={"X-JoeOS-Session": new_session}
            ).status_code,
            200,
        )
        # Replaying the old refresh credential must fail (single-use rotation).
        replayed = self.client.post(
            "/api/v1/auth/refresh",
            json={"refresh_id": str(result["refresh_id"]), "refresh_token": result["refresh_token"]},
        )
        self.assertEqual(replayed.status_code, 401)

        # Explicit logout revokes the session and its refresh chain.
        logout = self.client.post("/api/v1/auth/logout", json={"session_id": new_session})
        self.assertEqual(logout.status_code, 204)
        self.assertEqual(
            self.client.get(
                "/api/v1/principal", headers={"X-JoeOS-Session": new_session}
            ).status_code,
            401,
        )
        second_refresh_dead = self.client.post(
            "/api/v1/auth/refresh",
            json={
                "refresh_id": str(refreshed.json()["refresh_id"]),
                "refresh_token": refreshed.json()["refresh_token"],
            },
        )
        self.assertEqual(second_refresh_dead.status_code, 401)

    def test_revocation_immediately_denies_access(self):
        result = self._authenticate()
        session_id = result["session"]["session_id"]
        headers = {"X-JoeOS-Session": session_id}
        self.assertEqual(self.client.get("/api/v1/principal", headers=headers).status_code, 200)

        owner = UUID(self.bootstrap["user_id"])
        self.authority.revoke_device_assignment(self.device_id, owner)

        self.assertEqual(self.client.get("/api/v1/principal", headers=headers).status_code, 401)
        self.assertEqual(self.client.get("/api/v1/conversations", headers=headers).status_code, 401)
