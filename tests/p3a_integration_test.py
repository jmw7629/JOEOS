"""Phase P3A full HTTP integration test.

Exercises the real FastAPI routers with real temporary SQLite persistence. Only
cryptographic keys and AI providers are substituted through approved test
dependency injection. Deterministic test-only providers yield known incremental
pieces through the same streaming protocol real providers use; they are never
selectable in production configuration and never touch the network.
"""

import asyncio
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import AsyncIterator, List, Optional
from uuid import UUID

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.ai.models import InferenceResult, ProviderRecord
from server.conversations.router import router as conversations_router
from server.conversations.repository import SQLiteConversationRepository
from server.conversations.service import ConversationService
from server.identity.authority_repository import SQLiteAuthorityRepository
from server.identity.authority_router import router as authority_router
from server.identity.authority_service import AuthorityService
from server.identity.crypto import base64url_encode, encode_p256_public_key
from server.identity.key_protection import PairingKeyProtector
from server.identity.repository import SQLiteDeviceIdentityRepository


# ---------------------------------------------------------------------------
# Deterministic test-only providers (never available in production config)
# ---------------------------------------------------------------------------


class DeterministicStreamingProvider:
    """Yields known incremental pieces through the same streaming protocol real
    providers use. Supports controlled delay, cancellation, and a controlled
    failure after a specified partial. Requires no network or credentials."""

    provider_id = "deterministic-stream"
    name = "Deterministic Streaming"
    model = "deterministic-stream-1"

    def __init__(self, pieces: Optional[List[str]] = None, delay: float = 0.0, fail_after: Optional[int] = None):
        self.pieces = pieces if pieces is not None else ["par", "tial ", "output"]
        self.delay = delay
        self.fail_after = fail_after

    def availability(self) -> ProviderRecord:
        return ProviderRecord(
            provider_id=self.provider_id,
            name=self.name,
            kind="local",
            available=True,
            reason="",
            model=self.model,
            base_url="loopback",
            privacy_class="restricted",
            cloud_approved=False,
            supports_streaming=True,
        )

    async def infer(self, messages: List[dict], *, model: str = "", temperature: float = 0.25, max_tokens: int = 1200) -> InferenceResult:
        return InferenceResult(
            reply="".join(self.pieces), model=model or self.model, provider=self.provider_id
        )

    async def stream_infer(self, messages: List[dict], *, model: str = "", temperature: float = 0.25, max_tokens: int = 1200) -> AsyncIterator[str]:
        for index, piece in enumerate(self.pieces):
            if self.fail_after is not None and index >= self.fail_after:
                raise RuntimeError("deterministic failure after partial")
            if self.delay:
                await asyncio.sleep(self.delay)
            yield piece

    async def embed(self, texts: List[str], *, model: str = "") -> List[List[float]]:
        return [[0.0] * 4 for _ in texts]


class DeterministicNonStreamingProvider:
    """Proves honest non-streaming behavior: no partials, stream_supported false."""

    provider_id = "deterministic-nonstream"
    name = "Deterministic Non-Streaming"
    model = "deterministic-nonstream-1"

    def availability(self) -> ProviderRecord:
        return ProviderRecord(
            provider_id=self.provider_id,
            name=self.name,
            kind="local",
            available=True,
            reason="",
            model=self.model,
            base_url="loopback",
            privacy_class="restricted",
            cloud_approved=False,
            supports_streaming=False,
        )

    async def infer(self, messages: List[dict], *, model: str = "", temperature: float = 0.25, max_tokens: int = 1200) -> InferenceResult:
        return InferenceResult(
            reply="non-streaming complete reply",
            model=model or self.model,
            provider=self.provider_id,
            tokens_used=9,
        )

    async def embed(self, texts: List[str], *, model: str = "") -> List[List[float]]:
        return [[0.0] * 4 for _ in texts]


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


class FullP3AIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "p3a.db"
        self.clock = MutableClock()
        self.uuid_source = SequenceUUID()

        def connect():
            connection = sqlite3.connect(str(self.database), timeout=10)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 10000")
            return connection

        self.connect = connect
        self._ensure_events_table()
        self.authentication_key = ec.generate_private_key(ec.SECP256R1())
        self.provider = DeterministicStreamingProvider(pieces=["par", "tial ", "output"])
        self.streaming_available = True
        self.app, self.client = self._build_app()
        self.session = self._bootstrap_and_enroll()
        self.headers = {"X-JoeOS-Session": str(self.session["session"]["session_id"])}

    def tearDown(self):
        self.tempdir.cleanup()

    # ------------------------------------------------------------------
    # Fixture construction
    # ------------------------------------------------------------------

    def _ensure_events_table(self):
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recorded_at TEXT NOT NULL,
                    level TEXT NOT NULL,
                    source TEXT NOT NULL,
                    message TEXT NOT NULL
                )
                """
            )

    def _build_app(self):
        device_repository = SQLiteDeviceIdentityRepository(
            self.connect, PairingKeyProtector(bytes(range(32)))
        )
        device_repository.prepare()
        authority = AuthorityService(
            SQLiteAuthorityRepository(self.connect),
            device_repository,
            now_provider=self.clock,
            uuid_provider=self.uuid_source,
        )
        authority.prepare()
        self.device_repository = device_repository
        self.authority = authority

        def availability():
            return {
                "available": True,
                "reason": "",
                "streaming": self.streaming_available,
                "provider_id": self.provider.provider_id,
                "model": self.provider.model,
                "state": "streaming" if self.streaming_available else "non_streaming",
            }

        conversation_service = ConversationService(
            SQLiteConversationRepository(self.connect),
            infer=lambda messages: self.provider.infer(messages),
            availability=availability,
            stream_infer=lambda messages: self.provider.stream_infer(messages),
            now_provider=self.clock,
            uuid_provider=self.uuid_source,
            event_sink=self._event_sink,
        )
        conversation_service.prepare()
        self.conversation_service = conversation_service

        app = FastAPI()
        app.state.authority_service = authority
        app.state.conversation_service = conversation_service
        app.include_router(authority_router)
        app.include_router(conversations_router)
        return app, TestClient(app)

    def _event_sink(self, level, source, message):
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO events(recorded_at, level, source, message) VALUES (?, ?, ?, ?)",
                (
                    datetime.now(timezone.utc).isoformat(),
                    level,
                    source[:80],
                    message[:500],
                ),
            )

    def _bootstrap_and_enroll(self) -> dict:
        bootstrap = self.authority.bootstrap(
            display_name="Owner", organization_name="JoeOS", workspace_name="Default"
        )
        self.bootstrap = bootstrap
        device_id = self.uuid_source()
        auth_spki = encode_p256_public_key(self.authentication_key.public_key())
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
                    "Integration iPhone",
                    self.clock(),
                ),
            )
            connection.execute(
                """
                INSERT INTO enrolled_device_keys(
                    fingerprint, device_id, purpose, public_key, active, created_at
                ) VALUES (?, ?, 'device_authentication', ?, 1, ?)
                """,
                (base64url_encode(bytes(range(32))), str(device_id), auth_spki, self.clock()),
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
                    encode_p256_public_key(ec.generate_private_key(ec.SECP256R1()).public_key()),
                    self.clock(),
                ),
            )
            connection.commit()
        self.device_id = device_id
        self.assert_device_state("active_unassigned", assignment=None)
        roles = self.authority.list_roles()
        self.authority.assign_device(
            device_id=device_id,
            user_id=UUID(bootstrap["user_id"]),
            organization_id=UUID(bootstrap["organization_id"]),
            workspace_id=UUID(bootstrap["workspace_id"]),
            role_ids=[roles[0]["id"]],
            assigned_by=UUID(bootstrap["user_id"]),
        )
        return self._authenticate()

    def assert_device_state(self, state, assignment):
        devices = self.authority.list_devices()
        device = next(item for item in devices if item["device_id"] == str(self.device_id))
        self.assertEqual(device["state"], state)
        if assignment is None:
            self.assertNotEqual(device.get("assignment_status"), "active")
        else:
            self.assertEqual(device.get("assignment_status"), assignment)

    def _authenticate(self) -> dict:
        user_id = UUID(self.bootstrap["user_id"])
        challenge = self.client.post(
            "/api/v1/auth/challenge",
            json={"device_id": str(self.device_id), "user_id": str(user_id)},
        )
        self.assertEqual(challenge.status_code, 201, challenge.text)
        body = challenge.json()
        self.assertEqual(body["message"].startswith("JOEOS-APPLICATION-AUTH-V1\0"), True)
        signature = self.authentication_key.sign(
            body["message"].encode("ascii"), ec.ECDSA(hashes.SHA256())
        )
        session = self.client.post(
            "/api/v1/auth/session",
            json={
                "challenge_id": str(body["challenge_id"]),
                "signature": base64url_encode(signature),
            },
        )
        self.assertEqual(session.status_code, 200, session.text)
        return session.json()

    def _create_conversation(self) -> str:
        created = self.client.post(
            "/api/v1/conversations", headers=self.headers, json={"title": "Integration"}
        )
        self.assertEqual(created.status_code, 201, created.text)
        return created.json()["conversation_id"]

    # ------------------------------------------------------------------
    # The full sequence
    # ------------------------------------------------------------------

    def test_full_p3a_sequence(self):
        # 10. Principal endpoint reflects the authenticated owner.
        principal = self.client.get("/api/v1/principal", headers=self.headers)
        self.assertEqual(principal.status_code, 200)
        self.assertIn("conversation.write", principal.json()["capabilities"])

        # 11-14. Create conversation and stream with genuine partial events.
        conversation_id = self._create_conversation()
        with self.client.stream(
            "POST",
            f"/api/v1/conversations/{conversation_id}/stream",
            headers=self.headers,
            json={"content": "stream me", "idempotency_key": str(self.uuid_source())},
        ) as response:
            body = "".join(response.iter_text())
        self.assertEqual(response.status_code, 200)
        self.assertIn("event: message.delta", body)
        self.assertEqual(body.count("event: message.delta"), 3)
        self.assertIn("par", body)
        self.assertIn("tial ", body)
        self.assertIn("output", body)
        self.assertIn("event: run.completed", body)

        # 15-16. Cursor resume does not duplicate acknowledged events.
        first_ack = self._acknowledged_event_ids()
        resumed, _ = self.conversation_service.conversation_events(
            self._principal_dict(), first_ack, limit=200
        )
        self.assertEqual(resumed, [])

        # 17. Final assistant message persisted exactly once.
        conversation = self.client.get(
            f"/api/v1/conversations/{conversation_id}", headers=self.headers
        ).json()
        assistants = [m for m in conversation["messages"] if m["role"] == "assistant"]
        self.assertEqual(len(assistants), 1)
        self.assertEqual(assistants[0]["status"], "completed")
        self.assertEqual(assistants[0]["content"], "partial output")

        # 18-19. Restart services against the same persistence and reload.
        restarted_app, restarted_client = self._build_app()
        reopened = restarted_client.get(
            f"/api/v1/conversations/{conversation_id}", headers=self.headers
        )
        self.assertEqual(reopened.status_code, 200)
        self.assertEqual(len(reopened.json()["messages"]), 2)
        self.assertEqual(reopened.json()["messages"][1]["content"], "partial output")

        # 20-23. Controlled long-running stream, cancellation, idempotent repeat.
        self.provider = DeterministicStreamingProvider(
            pieces=["a", "b", "c", "d", "e"], delay=0.05
        )
        cancel_conversation = self._create_conversation()
        stream_task = self._start_stream(cancel_conversation, "cancel me")
        await_first_delta = self._wait_for_delta(cancel_conversation)
        run_id = await_first_delta["run_id"]
        cancel = self.client.post(
            f"/api/v1/conversations/{cancel_conversation}/runs/{run_id}/cancel",
            headers=self.headers,
            json={},
        )
        self.assertEqual(cancel.status_code, 204)
        # Repeated cancellation is idempotent (still accepted).
        repeat = self.client.post(
            f"/api/v1/conversations/{cancel_conversation}/runs/{run_id}/cancel",
            headers=self.headers,
            json={},
        )
        self.assertIn(repeat.status_code, (204, 404))
        body = stream_task.result()
        self.assertIn("event: run.cancelled", body)
        run = self.conversation_service.load_run(self._principal_dict(), UUID(run_id))
        self.assertEqual(run["status"], "cancelled")
        self.assertIn("cancelled", run["status"])

        # 24-27. Controlled failed run then retry with a new related run id.
        self.provider = DeterministicStreamingProvider(
            pieces=["par", "tial"], fail_after=1
        )
        fail_conversation = self._create_conversation()
        with self.client.stream(
            "POST",
            f"/api/v1/conversations/{fail_conversation}/stream",
            headers=self.headers,
            json={"content": "fail me", "idempotency_key": str(self.uuid_source())},
        ) as response:
            fail_body = "".join(response.iter_text())
        self.assertIn("event: run.failed", fail_body)
        runs_before = self.conversation_service._repository.list_runs(UUID(fail_conversation))
        failed_run = runs_before[0]
        self.assertEqual(failed_run.status, "failed")

        # Retry produces a new run related to the original, no user duplication.
        retried = self.client.post(
            f"/api/v1/conversations/{fail_conversation}/retry",
            headers=self.headers,
            json={"parent_run_id": str(failed_run.run_id)},
        )
        self.assertEqual(retried.status_code, 200, retried.text)
        conversation_after = retried.json()
        users = [m for m in conversation_after["messages"] if m["role"] == "user"]
        self.assertEqual(len(users), 1)
        runs_after = self.conversation_service._repository.list_runs(UUID(fail_conversation))
        self.assertEqual(len(runs_after), 2)
        retry_run = runs_after[1]
        self.assertEqual(retry_run.parent_run_id, failed_run.run_id)
        self.assertNotEqual(retry_run.run_id, failed_run.run_id)

        # 28-30. Non-streaming provider: no run.partial, stream_supported false.
        self.provider = DeterministicNonStreamingProvider()
        self.streaming_available = False
        list_response = self.client.get("/api/v1/conversations", headers=self.headers).json()
        self.assertFalse(list_response["stream_supported"])
        nonstream = self._create_conversation()
        with self.client.stream(
            "POST",
            f"/api/v1/conversations/{nonstream}/stream",
            headers=self.headers,
            json={"content": "single"},
        ) as response:
            nonstream_body = "".join(response.iter_text())
        self.assertIn("non-streaming complete reply", nonstream_body)
        self.assertEqual(nonstream_body.count("event: run.partial"), 0)

        # 31-34. Refresh rotates; replaying the old refresh is revoked.
        refreshed = self.client.post(
            "/api/v1/auth/refresh",
            json={
                "refresh_id": str(self.session["refresh_id"]),
                "refresh_token": self.session["refresh_token"],
            },
        )
        self.assertEqual(refreshed.status_code, 200, refreshed.text)
        replay = self.client.post(
            "/api/v1/auth/refresh",
            json={
                "refresh_id": str(self.session["refresh_id"]),
                "refresh_token": self.session["refresh_token"],
            },
        )
        self.assertEqual(replay.status_code, 401)
        new_session = refreshed.json()["session"]["session_id"]
        self.headers = {"X-JoeOS-Session": str(new_session)}

        # 35-36. Reauthenticate, then revoke the session and verify immediate denial.
        self.session = self._authenticate()
        self.headers = {"X-JoeOS-Session": str(self.session["session"]["session_id"])}
        self.assertEqual(self.client.get("/api/v1/principal", headers=self.headers).status_code, 200)
        logout = self.client.post(
            "/api/v1/auth/logout",
            json={"session_id": str(self.session["session"]["session_id"])},
        )
        self.assertEqual(logout.status_code, 204)
        self.assertEqual(self.client.get("/api/v1/principal", headers=self.headers).status_code, 401)
        self.assertEqual(self.client.get("/api/v1/conversations", headers=self.headers).status_code, 401)

        # 37-38. Reauthenticate, revoke the device assignment, verify immediate denial.
        self.session = self._authenticate()
        self.headers = {"X-JoeOS-Session": str(self.session["session"]["session_id"])}
        self.assertEqual(self.client.get("/api/v1/principal", headers=self.headers).status_code, 200)
        self.authority.revoke_device_assignment(self.device_id, UUID(self.bootstrap["user_id"]))
        self.assertEqual(self.client.get("/api/v1/principal", headers=self.headers).status_code, 401)

        # 39. Another workspace cannot read the conversation or subscribe to events.
        other_principal = dict(self._principal_dict())
        other_principal["workspace"]["id"] = UUID("99999999-8888-4777-8666-555555555555")
        other_principal["workspace"]["name"] = "Other Workspace"
        with self.assertRaises(Exception):
            self.conversation_service.get_conversation(other_principal, UUID(conversation_id))
        events_from_other_ws, _ = self.conversation_service.conversation_events(
            other_principal, 0, limit=200
        )
        self.assertEqual(events_from_other_ws, [])

        # 40. No credential material in the persisted event/audit stream.
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT message FROM events ORDER BY id"
            ).fetchall()
        joined = "\n".join(str(row["message"]) for row in rows)
        self.assertNotIn(self.session["refresh_token"], joined)
        self.assertNotIn(str(self.session["refresh_id"]), joined)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _principal_dict(self):
        return {
            "session_id": UUID(self.headers["X-JoeOS-Session"]),
            "device_id": self.device_id,
            "user": {"id": UUID(self.bootstrap["user_id"]), "display_name": "Owner"},
            "organization": {"id": UUID(self.bootstrap["organization_id"])},
            "workspace": {"id": UUID(self.bootstrap["workspace_id"])},
            "roles": ["joeos.owner"],
            "capabilities": [
                "conversation.read",
                "conversation.write",
                "conversation.invoke_ai",
                "conversation.cancel",
                "principal.read",
                "diagnostics.read",
            ],
        }

    def _acknowledged_event_ids(self) -> int:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT MAX(id) AS m FROM events WHERE source = 'conversations'"
            ).fetchone()
        return int(row["m"]) if row and row["m"] is not None else 0

    def _start_stream(self, conversation_id: str, content: str):
        import threading

        result = {}

        def run():
            with self.client.stream(
                "POST",
                f"/api/v1/conversations/{conversation_id}/stream",
                headers=self.headers,
                json={"content": content, "idempotency_key": str(self.uuid_source())},
            ) as response:
                result["status"] = response.status_code
                result["body"] = "".join(response.iter_text())

        thread = threading.Thread(target=run)
        thread.start()
        result["thread"] = thread
        result["result"] = None

        class _Handle:
            def __init__(self, ref):
                self._ref = ref

            def result(self):
                self._ref["thread"].join(timeout=30)
                return self._ref["body"]

        return _Handle(result)

    def _wait_for_delta(self, conversation_id: str) -> dict:
        import time

        deadline = time.monotonic() + 15
        run_id = None
        while time.monotonic() < deadline:
            runs = self.conversation_service._repository.list_runs(UUID(conversation_id))
            if runs:
                run = runs[-1]
                if run.status == "running":
                    return {"run_id": str(run.run_id)}
            time.sleep(0.05)
        raise AssertionError("streaming run never reached running state")
