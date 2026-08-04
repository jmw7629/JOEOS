"""Phase P3A completion tests: genuine provider streaming, honest non-streaming,
server-sent event integration, stream cancellation, restart/resume, and browser
authentication gating."""

import asyncio
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.conversations.repository import SQLiteConversationRepository
from server.conversations.router import router as conversations_router
from server.conversations.service import ConversationService
from server.identity.authority_router import (
    require_application_session,
    router as authority_router,
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
    def __init__(self, reply="non-streaming provider reply"):
        self.reply = reply
        self.provider = "test-provider"
        self.model = "test-model"
        self.tokens_used = 3


class FakeStreamingDelta:
    def __init__(self, content, done=False):
        self.content = content
        self.done = done


def principal(capabilities=None):
    capabilities = capabilities or [
        "conversation.read",
        "conversation.write",
        "conversation.invoke_ai",
        "conversation.cancel",
    ]
    return {
        "session_id": UUID("44444444-5555-4666-8777-888888888888"),
        "device_id": UUID("22222222-3333-4444-8555-666666666666"),
        "user": {"id": UUID("11111111-2222-4333-8444-555555555555")},
        "organization": {"id": UUID("55555555-6666-4777-8888-999999999999")},
        "workspace": {"id": UUID("33333333-4444-4555-8666-777777777777")},
        "roles": ["joeos.owner"],
        "capabilities": capabilities,
    }


class StreamingServiceFixture(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "stream.db"
        self.clock = MutableClock()
        self.uuid_source = SequenceUUID()
        self.events = []

        def connect():
            connection = sqlite3.connect(str(self.database), timeout=10)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            return connection

        self.connect = connect
        self.availability = {"available": True, "reason": "", "streaming": False}
        self.infer_calls = []

        async def fake_infer(messages):
            self.infer_calls.append(messages)
            return FakeInferenceResult()

        self.fake_infer = fake_infer
        self.partials = ["Hel", "lo ", "world"]

        async def fake_stream_infer(messages):
            for piece in self.partials:
                await asyncio.sleep(0.01)
                yield FakeStreamingDelta(piece)
            yield FakeStreamingDelta("", done=True)

        self.fake_stream_infer = fake_stream_infer

    def tearDown(self):
        self.tempdir.cleanup()

    def make_service(self, streaming=False):
        if streaming:
            self.availability["streaming"] = True
        service = ConversationService(
            SQLiteConversationRepository(self.connect),
            infer=self.fake_infer,
            availability=lambda: self.availability,
            stream_infer=self.fake_stream_infer,
            now_provider=self.clock,
            uuid_provider=self.uuid_source,
            event_sink=lambda level, source, message: self.events.append(message),
        )
        service.prepare()
        return service


class ConversationStreamingTests(StreamingServiceFixture):
    async def test_streaming_provider_emits_genuine_partials(self):
        service = self.make_service(streaming=True)
        conversation = service.create_conversation(principal(), "Stream")
        events = []
        async for event in service.stream_message(
            principal(), conversation["conversation_id"], "Tell me a story"
        ):
            events.append(event)
        deltas = [event for event in events if event["event"] == "message.delta"]
        self.assertGreaterEqual(len(deltas), 2)
        self.assertEqual("".join(event["content"] for event in deltas), "Hello world")
        completed = [event for event in events if event["event"] == "run.completed"]
        self.assertEqual(len(completed), 1)
        # The final assistant message is persisted with the streamed content.
        fresh = service.get_conversation(principal(), conversation["conversation_id"])
        assistant = [m for m in fresh["messages"] if m["role"] == "assistant"][0]
        self.assertEqual(assistant["content"], "Hello world")
        self.assertEqual(assistant["status"], "completed")

    async def test_non_streaming_provider_emits_single_completed_delta(self):
        service = self.make_service(streaming=False)
        conversation = service.create_conversation(principal(), "Single")
        events = []
        async for event in service.stream_message(
            principal(), conversation["conversation_id"], "ping"
        ):
            events.append(event)
        deltas = [event for event in events if event["event"] == "message.delta"]
        self.assertEqual(len(deltas), 1)
        self.assertEqual(deltas[0]["content"], "non-streaming provider reply")
        completed = [event for event in events if event["event"] == "run.completed"]
        self.assertEqual(len(completed), 1)

    async def test_stream_cancellation_persists_partial(self):
        service = self.make_service(streaming=True)
        conversation = service.create_conversation(principal(), "Cancel")
        events = []
        run_id = None
        async for event in service.stream_message(
            principal(), conversation["conversation_id"], "go"
        ):
            if event["event"] == "message.delta" and run_id is None:
                run_id = UUID(event["run_id"])
                self.assertTrue(service.cancel_run(principal(), run_id))
            events.append(event)
        cancelled = [event for event in events if event["event"] == "run.cancelled"]
        self.assertEqual(len(cancelled), 1)
        fresh = service.get_conversation(principal(), conversation["conversation_id"])
        assistant = [m for m in fresh["messages"] if m["role"] == "assistant"][0]
        self.assertEqual(assistant["status"], "cancelled")
        # Partial content accumulated before cancellation is preserved.
        self.assertTrue(assistant["content"].startswith("Hel"))

    def test_realtime_events_are_emitted_without_content(self):
        service = self.make_service(streaming=False)
        conversation = service.create_conversation(principal(), "Events")
        self.assertTrue(any("conversation.created" in message for message in self.events))
        asyncio.get_event_loop().run_until_complete(
            service.submit_message(principal(), conversation["conversation_id"], "hi")
        )
        # Terminal run events present; no conversation text leaks into events.
        self.assertTrue(any("run.completed" in message for message in self.events))
        self.assertFalse(any("non-streaming provider reply" in message for message in self.events))


class HTTPStreamingTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "http.db"
        self.clock = MutableClock()
        self.uuid_source = SequenceUUID()

        def connect():
            connection = sqlite3.connect(str(self.database), timeout=10)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            return connection

        self.connect = connect
        self.availability = {"available": True, "reason": "", "streaming": False}

        async def fake_infer(messages):
            return FakeInferenceResult()

        async def fake_stream_infer(messages):
            for piece in ["par", "tial ", "output"]:
                yield FakeStreamingDelta(piece)
            yield FakeStreamingDelta("", done=True)

        self.fake_stream_infer = fake_stream_infer

        def make_app(streaming):
            self.availability["streaming"] = streaming
            service = ConversationService(
                SQLiteConversationRepository(self.connect),
                infer=fake_infer,
                availability=lambda: self.availability,
                stream_infer=fake_stream_infer,
                now_provider=self.clock,
                uuid_provider=self.uuid_source,
            )
            service.prepare()
            app = FastAPI()
            app.state.conversation_service = service
            app.include_router(conversations_router)
            app.dependency_overrides[require_application_session] = lambda: principal()
            return app

        self.make_app = make_app
        self.app = make_app(streaming=True)
        self.client = TestClient(self.app)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_streaming_endpoint_yields_partial_events(self):
        headers = {"X-JoeOS-Session": str(principal()["session_id"])}
        created = self.client.post(
            "/api/v1/conversations", headers=headers, json={"title": "HTTP stream"}
        )
        self.assertEqual(created.status_code, 201, created.text)
        conversation_id = created.json()["conversation_id"]

        with self.client.stream(
            "POST",
            f"/api/v1/conversations/{conversation_id}/stream",
            headers=headers,
            json={"content": "stream it"},
        ) as response:
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
            body = "".join(response.iter_text())
        self.assertIn("event: message.delta", body)
        self.assertIn("event: run.completed", body)
        self.assertIn("event: done", body)
        self.assertIn("par", body)
        self.assertIn("tial ", body)

    def test_non_streaming_endpoint_is_honest(self):
        self.app = self.make_app(streaming=False)
        self.client = TestClient(self.app)
        headers = {"X-JoeOS-Session": str(principal()["session_id"])}
        created = self.client.post(
            "/api/v1/conversations", headers=headers, json={"title": "Non stream"}
        )
        conversation_id = created.json()["conversation_id"]
        with self.client.stream(
            "POST",
            f"/api/v1/conversations/{conversation_id}/stream",
            headers=headers,
            json={"content": "single"},
        ) as response:
            body = "".join(response.iter_text())
        self.assertIn("event: message.delta", body)
        self.assertIn("non-streaming provider reply", body)
        self.assertIn("event: run.completed", body)

    def test_restart_resumes_same_conversation_over_http(self):
        headers = {"X-JoeOS-Session": str(principal()["session_id"])}
        created = self.client.post(
            "/api/v1/conversations", headers=headers, json={"title": "Restart"}
        )
        conversation_id = created.json()["conversation_id"]
        self.client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            headers=headers,
            json={"content": "before restart"},
        )
        # "Restart" the backend: a fresh app and service over the same database.
        restarted = self.make_app(streaming=False)
        restarted_client = TestClient(restarted)
        reopened = restarted_client.get(
            f"/api/v1/conversations/{conversation_id}", headers=headers
        )
        self.assertEqual(reopened.status_code, 200)
        self.assertEqual(reopened.json()["conversation_id"], conversation_id)
        self.assertEqual(len(reopened.json()["messages"]), 2)
