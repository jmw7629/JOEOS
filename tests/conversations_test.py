"""Phase P3A conversation tests: canonical identity, submit with real provider,
retry without corruption, server-side cancellation, and resume after restart."""

import asyncio
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

from server.conversations.repository import SQLiteConversationRepository
from server.conversations.service import (
    ConversationCapabilityError,
    ConversationForbiddenError,
    ConversationService,
)


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
    def __init__(self, reply, provider="test-provider", model="test-model", tokens_used=7):
        self.reply = reply
        self.provider = provider
        self.model = model
        self.tokens_used = tokens_used


def principal(*, user_id=UUID("11111111-2222-4333-8444-555555555555"),
              device_id=UUID("22222222-3333-4444-8555-666666666666"),
              workspace_id=UUID("33333333-4444-4555-8666-777777777777"),
              capabilities=None):
    capabilities = capabilities or [
        "conversation.read",
        "conversation.write",
        "conversation.invoke_ai",
        "conversation.cancel",
    ]
    return {
        "session_id": UUID("44444444-5555-4666-8777-888888888888"),
        "device_id": device_id,
        "user": {"id": user_id, "display_name": "Owner"},
        "organization": {"id": UUID("55555555-6666-4777-8888-999999999999")},
        "workspace": {"id": workspace_id, "name": "Default Workspace"},
        "roles": ["joeos.owner"],
        "capabilities": capabilities,
    }


class ConversationFixture(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "conversations.db"
        self.clock = MutableClock()
        self.uuid_source = SequenceUUID()

        def connect():
            connection = sqlite3.connect(str(self.database), timeout=10)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 10000")
            return connection

        self.connect = connect
        self.infer_calls = []
        self.availability = {"available": True, "reason": "", "streaming": False}

        async def fake_infer(messages):
            self.infer_calls.append(messages)
            return FakeInferenceResult(reply="hello from the provider")

        self.fake_infer = fake_infer

    def tearDown(self):
        self.tempdir.cleanup()

    def make_service(self):
        service = ConversationService(
            SQLiteConversationRepository(self.connect),
            infer=self.fake_infer,
            availability=lambda: self.availability,
            now_provider=self.clock,
            uuid_provider=self.uuid_source,
        )
        service.prepare()
        return service


class ConversationTests(ConversationFixture):
    async def test_create_and_reopen_preserves_canonical_id(self):
        service = self.make_service()
        conversation = service.create_conversation(principal(), "Launch plan")
        conversation_id = conversation["conversation_id"]

        # "Restart" the backend: a fresh service over the same database.
        reopened = self.make_service()
        same = reopened.get_conversation(principal(), conversation_id)
        self.assertEqual(same["conversation_id"], conversation_id)
        self.assertEqual(same["title"], "Launch plan")

    async def test_submit_produces_user_and_completed_assistant_messages(self):
        service = self.make_service()
        conversation = service.create_conversation(principal(), "Q&A")
        result = await service.submit_message(
            principal(), conversation["conversation_id"], "What is JoeOS?"
        )
        messages = result["messages"]
        roles = [message["role"] for message in messages]
        self.assertEqual(roles, ["user", "assistant"])
        assistant = messages[-1]
        self.assertEqual(assistant["status"], "completed")
        self.assertEqual(assistant["content"], "hello from the provider")
        self.assertEqual(assistant["provider"], "test-provider")
        self.assertEqual(assistant["model"], "test-model")
        self.assertEqual(len(self.infer_calls), 1)

    async def test_provider_unavailable_is_reported_honestly(self):
        self.availability = {"available": False, "reason": "Lemonade Server is offline.", "streaming": False}
        service = self.make_service()
        conversation = service.create_conversation(principal(), "Offline")
        result = await service.submit_message(
            principal(), conversation["conversation_id"], "ping"
        )
        assistant = result["messages"][-1]
        self.assertEqual(assistant["status"], "failed")
        self.assertIn("Lemonade Server is offline", assistant["error_detail"])
        self.assertEqual(assistant["content"], "")

    async def test_retry_does_not_corrupt_history(self):
        service = self.make_service()
        conversation = service.create_conversation(principal(), "Retry")
        first = await service.submit_message(
            principal(), conversation["conversation_id"], "Build my app"
        )
        retried = await service.retry_last_message(principal(), conversation["conversation_id"])
        messages = retried["messages"]
        # user message appears once; two assistant messages (one per attempt)
        user_messages = [m for m in messages if m["role"] == "user"]
        assistant_messages = [m for m in messages if m["role"] == "assistant"]
        self.assertEqual(len(user_messages), 1)
        self.assertEqual(user_messages[0]["content"], "Build my app")
        self.assertEqual(len(assistant_messages), 2)
        self.assertTrue(all(m["status"] == "completed" for m in assistant_messages))
        self.assertEqual(first["messages"][0]["content"], "Build my app")

    async def test_cancel_stops_active_generation(self):
        async def slow_infer(messages):
            try:
                while True:
                    await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                raise

        self.fake_infer = slow_infer
        service = self.make_service()
        conversation = service.create_conversation(principal(), "Cancel")
        principal_value = principal()

        submit_task = asyncio.create_task(
            service.submit_message(principal_value, conversation["conversation_id"], "go")
        )
        await asyncio.sleep(0.05)
        # The run id is the assistant message id; find it via the pending message.
        runs = service._runs
        run_id = next(iter(runs.keys()))
        self.assertTrue(service.cancel_run(principal_value, run_id))
        result = await submit_task
        messages = result["messages"]
        assistant = [m for m in messages if m["role"] == "assistant"][0]
        self.assertEqual(assistant["status"], "cancelled")

    async def test_conversation_is_scoped_to_principal(self):
        service = self.make_service()
        conversation = service.create_conversation(principal(), "Private")
        other_workspace = principal(
            workspace_id=UUID("99999999-8888-4777-8666-555555555555")
        )
        with self.assertRaises(ConversationForbiddenError):
            service.get_conversation(other_workspace, conversation["conversation_id"])

    async def test_capability_gate_denies_by_default(self):
        service = self.make_service()
        restricted = principal(capabilities=["conversation.read"])
        with self.assertRaises(ConversationCapabilityError):
            service.create_conversation(restricted, "Blocked")
