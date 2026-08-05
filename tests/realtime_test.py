import asyncio
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import joeos_backend as backend
from server.realtime.models import AuditEventRecord
from server.realtime.repository import SQLiteEventRepository
from server.realtime.router import router
from server.realtime.service import RealtimeService


NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)


def event(event_id, severity="info"):
    return AuditEventRecord(
        event_id=event_id,
        occurred_at=NOW + timedelta(seconds=event_id),
        source="test",
        severity=severity,
        message="event %s" % event_id,
    )


class MemoryRepository:
    def __init__(self, events):
        self.events = list(events)
        self.fetch_limits = []
        self.calls = 0

    def latest_cursor(self):
        self.calls += 1
        return max((item.event_id for item in self.events), default=0)

    def oldest_cursor(self):
        self.calls += 1
        return min((item.event_id for item in self.events), default=0)

    def fetch_after(self, cursor, limit):
        self.calls += 1
        self.fetch_limits.append(limit)
        return [item for item in self.events if item.event_id > cursor][:limit]


class DuplicateRepository(MemoryRepository):
    def fetch_after(self, cursor, limit):
        rows = super().fetch_after(cursor, limit)
        return ([rows[0]] + rows) if rows else []


class OverReturningRepository(MemoryRepository):
    def fetch_after(self, cursor, limit):
        self.fetch_limits.append(limit)
        return [item for item in self.events if item.event_id > cursor]


class RealtimeServiceTests(unittest.TestCase):
    def make_service(self, repository, **kwargs):
        snapshot_provider = kwargs.pop(
            "snapshot_provider", lambda: {"metrics": {"cpu": 23}, "bots": []}
        )
        return RealtimeService(
            repository,
            snapshot_provider=snapshot_provider,
            now_provider=lambda: NOW,
            **kwargs,
        )

    def test_fresh_snapshot_starts_at_latest_cursor(self):
        service = self.make_service(MemoryRepository([event(1), event(2), event(3)]))

        snapshot, cursor = service.initial_snapshot(None)

        self.assertEqual(cursor, 3)
        self.assertEqual(snapshot.cursor, 3)
        self.assertIsNone(snapshot.event_id)
        self.assertEqual(snapshot.event_type, "telemetry.snapshot")
        self.assertEqual(snapshot.payload["resume"]["latest_event_cursor"], 3)
        self.assertEqual(service.events_after(cursor), [])

    def test_resume_is_strict_ordered_deduplicated_and_batch_capped(self):
        repository = DuplicateRepository([event(1), event(2), event(3), event(4)])
        service = self.make_service(repository, batch_size=2)
        snapshot, cursor = service.initial_snapshot(1)

        envelopes = service.events_after(cursor)

        self.assertEqual(snapshot.cursor, 1)
        self.assertEqual([item.event_id for item in envelopes], [2, 3])
        self.assertTrue(all(item.event_id == item.cursor for item in envelopes))
        self.assertEqual(repository.fetch_limits, [2])

    def test_trimmed_resume_reports_history_gap(self):
        service = self.make_service(MemoryRepository([event(8), event(9)]))

        snapshot, _ = service.initial_snapshot(2)

        self.assertTrue(snapshot.payload["resume"]["history_gap"])
        self.assertFalse(snapshot.payload["resume"]["cursor_ahead"])

    def test_service_caps_a_repository_that_over_returns(self):
        repository = OverReturningRepository([event(index) for index in range(1, 8)])
        service = self.make_service(repository, batch_size=3)

        envelopes = service.events_after(0)

        self.assertEqual([item.event_id for item in envelopes], [1, 2, 3])

    def test_heartbeat_is_synthetic_and_retains_cursor(self):
        heartbeat = self.make_service(MemoryRepository([])).heartbeat(14)

        self.assertEqual(heartbeat.event_type, "stream.heartbeat")
        self.assertEqual(heartbeat.cursor, 14)
        self.assertIsNone(heartbeat.event_id)
        self.assertEqual(heartbeat.payload, {"status": "connected"})

    def test_browser_origin_requires_same_host_or_explicit_allowlist(self):
        service = self.make_service(
            MemoryRepository([]), allowed_origins=("https://command.example.com",)
        )

        self.assertTrue(service.origin_allowed(None, "vps.local"))
        self.assertTrue(service.origin_allowed("http://vps.local:8080", "vps.local:8080"))
        self.assertTrue(service.origin_allowed("https://command.example.com", "proxy.internal"))
        self.assertFalse(service.origin_allowed("https://evil.example", "vps.local"))
        self.assertFalse(service.origin_allowed("https://vps.local/path", "vps.local"))
        self.assertFalse(service.origin_allowed("null", "vps.local"))

    def test_payload_limit_is_enforced(self):
        service = self.make_service(
            MemoryRepository([]),
            snapshot_provider=lambda: {"value": "x" * 5_000},
            max_payload_bytes=4_096,
        )

        with self.assertRaises(ValueError):
            service.initial_snapshot(None)


class SQLiteEventRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "events.db"

        def connect():
            connection = sqlite3.connect(str(self.db_path), timeout=10)
            connection.row_factory = sqlite3.Row
            return connection

        self.connect = connect
        with connect() as connection:
            connection.execute(
                """
                CREATE TABLE events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recorded_at TEXT NOT NULL,
                    level TEXT NOT NULL,
                    source TEXT NOT NULL,
                    message TEXT NOT NULL
                )
                """
            )
            for index in range(1, 6):
                connection.execute(
                    "INSERT INTO events(recorded_at, level, source, message) VALUES (?, ?, ?, ?)",
                    ((NOW + timedelta(seconds=index)).isoformat(), "info", "sqlite", "row %s" % index),
                )
        self.repository = SQLiteEventRepository(connect)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_repository_uses_numeric_strict_cursor_and_limit(self):
        self.assertEqual(self.repository.oldest_cursor(), 1)
        self.assertEqual(self.repository.latest_cursor(), 5)
        self.assertEqual(
            [item.event_id for item in self.repository.fetch_after(2, 2)],
            [3, 4],
        )


class RealtimeCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_stops_cleanly_when_cancel_event_is_set(self):
        service = RealtimeService(
            MemoryRepository([]),
            snapshot_provider=lambda: {"metrics": {}, "bots": []},
            poll_seconds=0.05,
            heartbeat_seconds=0.25,
        )
        stop_event = asyncio.Event()
        stream = service.stream(None, stop_event)

        snapshot = await stream.__anext__()
        stop_event.set()

        self.assertEqual(snapshot.event_type, "telemetry.snapshot")
        with self.assertRaises(StopAsyncIteration):
            await stream.__anext__()


class RealtimeWebSocketTests(unittest.TestCase):
    def setUp(self):
        self.repository = MemoryRepository([event(1), event(2), event(3, "warn")])
        service = RealtimeService(
            self.repository,
            snapshot_provider=lambda: {"metrics": {"cpu": 23}, "bots": []},
            poll_seconds=0.05,
            heartbeat_seconds=0.25,
            max_inbound_bytes=256,
        )
        app = FastAPI()
        app.state.realtime_service = service
        app.include_router(router)

        @app.get("/unrelated")
        def unrelated():
            return {"ok": True}

        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()

    def test_websocket_emits_snapshot_then_resumed_audits_without_duplicates(self):
        with self.client.websocket_connect(
            "/ws/events?after=1", headers={"origin": "http://testserver"}
        ) as websocket:
            snapshot = websocket.receive_json()
            second = websocket.receive_json()
            third = websocket.receive_json()

        self.assertEqual(snapshot["event_type"], "telemetry.snapshot")
        self.assertEqual(snapshot["cursor"], 1)
        self.assertEqual([second["event_id"], third["event_id"]], [2, 3])
        self.assertEqual(second["schema_version"], 1)
        self.assertEqual(third["severity"], "warn")

    def test_websocket_emits_bounded_heartbeat_when_idle(self):
        with self.client.websocket_connect(
            "/ws/events?after=3", headers={"origin": "http://testserver"}
        ) as websocket:
            websocket.receive_json()
            heartbeat = websocket.receive_json()

        self.assertEqual(heartbeat["event_type"], "stream.heartbeat")
        self.assertEqual(heartbeat["cursor"], 3)
        self.assertIsNone(heartbeat["event_id"])

    def test_oversized_client_frame_closes_with_1009(self):
        with self.client.websocket_connect(
            "/ws/events", headers={"origin": "http://testserver"}
        ) as websocket:
            websocket.receive_json()
            websocket.send_text("x" * 300)
            close_message = websocket.receive()

        self.assertEqual(close_message["type"], "websocket.close")
        self.assertEqual(close_message["code"], 1009)

    def test_small_client_command_frame_closes_with_1003(self):
        with self.client.websocket_connect(
            "/ws/events", headers={"origin": "http://testserver"}
        ) as websocket:
            websocket.receive_json()
            websocket.send_json({"command": "run"})
            close_message = websocket.receive()

        self.assertEqual(close_message["type"], "websocket.close")
        self.assertEqual(close_message["code"], 1003)

    def test_cross_origin_browser_websocket_is_rejected_before_accept(self):
        with self.assertRaises(WebSocketDisconnect) as caught:
            with self.client.websocket_connect(
                "/ws/events", headers={"origin": "https://evil.example"}
            ):
                pass

        self.assertEqual(caught.exception.code, 1008)

    def test_unrelated_http_route_does_not_start_realtime_polling(self):
        calls_before = self.repository.calls
        response = self.client.get("/unrelated")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.repository.calls, calls_before)


class ExistingEventsContractTests(unittest.TestCase):
    def test_polling_event_fields_are_preserved_and_cursor_is_added(self):
        with tempfile.TemporaryDirectory() as temp_name:
            db_path = Path(temp_name) / "joeos.db"
            backend._prepare_database(db_path)
            backend._record_event(db_path, "warn", "test", "cursor contract")
            request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db_path=db_path)))

            payload = backend.events(request)

        self.assertEqual(payload["cursor"], payload["logs"][0]["event_id"])
        self.assertEqual(payload["logs"][0]["cursor"], payload["logs"][0]["event_id"])
        self.assertIn("recorded_at", payload["logs"][0])
        for preserved in ("id", "time", "level", "source", "message"):
            self.assertIn(preserved, payload["logs"][0])


if __name__ == "__main__":
    unittest.main()
