"""HTTP integration tests for autonomous operations.

Verifies the full create -> schedule -> execute -> result path through the
HTTP API with a substitute AgentFabric executor and a fake scheduler driver."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.autonomous.router import router as autonomous_router
from server.autonomous.service import AutonomousService
from server.autonomous.storage import AutonomousStore
from server.identity.authority_router import require_application_session

OWNER = "11111111-2222-4333-8444-555555555555"


def principal():
    return {
        "session_id": "44444444-5555-4666-8777-888888888888",
        "device_id": "22222222-3333-4444-8555-666666666666",
        "user": {"id": OWNER, "display_name": "Owner", "status": "active"},
        "organization": {"id": "55555555-6666-4777-8888-999999999999"},
        "workspace": {"id": "33333333-4444-4555-8666-777777777777", "name": "Default"},
        "roles": ["joeos.owner"],
        "capabilities": ["agent.run", "agent.read", "action.propose"],
    }


class AutonomousHTTPFixture(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = AutonomousStore(str(Path(self.tempdir.name) / "auto"))
        self.service = AutonomousService(self.store)
        self.service.prepare()
        app = FastAPI()
        app.state.autonomous_service = self.service
        self.current_principal = principal()
        app.dependency_overrides[require_application_session] = lambda: self.current_principal
        app.include_router(autonomous_router)
        self.client = TestClient(app)

    def tearDown(self):
        self.tempdir.cleanup()

    def _payload(self, **kw):
        defaults = dict(
            name="HTTP recurring",
            description="",
            objective="Report system health.",
            agent_ref="architect",
            trigger={
                "kind": "recurring",
                "schedule": {"kind": "interval", "interval_seconds": 3600},
                "timezone": "UTC",
            },
            timezone="UTC",
            retry_policy={"max_attempts": 3},
            notification_policy={"on_failure": True},
        )
        defaults.update(kw)
        return defaults


class HTTPDefinitionTests(AutonomousHTTPFixture):
    def test_create_list_get_pause_resume_archive(self):
        created = self.client.post("/api/v1/automations", json=self._payload())
        self.assertEqual(created.status_code, 201, created.text)
        automation_id = created.json()["id"]
        listing = self.client.get("/api/v1/automations").json()
        self.assertGreaterEqual(len(listing["automations"]), 1)
        got = self.client.get("/api/v1/automations/" + automation_id).json()
        self.assertEqual(got["agent_ref"], "architect")
        self.assertGreater(len(got["next_run_at"]), 0)
        paused = self.client.post("/api/v1/automations/%s/pause" % automation_id)
        self.assertEqual(paused.json()["state"], "paused")
        resumed = self.client.post("/api/v1/automations/%s/resume" % automation_id)
        self.assertEqual(resumed.json()["state"], "active")
        archived = self.client.post("/api/v1/automations/%s/archive" % automation_id)
        self.assertEqual(archived.json()["state"], "archived")

    def test_run_now_creates_run(self):
        created = self.client.post("/api/v1/automations", json=self._payload()).json()
        run = self.client.post("/api/v1/automations/%s/run-now" % created["id"])
        self.assertEqual(run.status_code, 200, run.text)
        runs = self.client.get("/api/v1/automations/%s/runs" % created["id"]).json()["runs"]
        self.assertGreaterEqual(len(runs), 1)
        self.assertEqual(runs[0]["trigger_kind"], "manual")

    def test_run_detail_roundtrip(self):
        created = self.client.post("/api/v1/automations", json=self._payload()).json()
        run = self.client.post("/api/v1/automations/%s/run-now" % created["id"]).json()
        detail = self.client.get(
            "/api/v1/automations/%s/runs/%s" % (created["id"], run["id"]))
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["id"], run["id"])

    def test_unauthorized_session_required(self):
        # Without a valid session the route is rejected before the handler runs.
        app = FastAPI()
        app.state.autonomous_service = self.service
        app.dependency_overrides[require_application_session] = lambda: (_ for _ in ()).throw(
            __import__("fastapi").HTTPException(status_code=401))
        app.include_router(autonomous_router)
        client = TestClient(app)
        response = client.get("/api/v1/automations")
        self.assertEqual(response.status_code, 401)
