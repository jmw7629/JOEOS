"""Terminal gateway + authenticated API/WebSocket tests.

The human terminal runs the authenticated backend user's shell over a bounded
PTY gateway. It is not a model tool: agents have no access to it, and nothing
bypasses ToolBroker/policy/approval through the PTY.
"""

import os
import sqlite3
import tempfile
import time
import unittest
import uuid
from pathlib import Path

import joeos_backend as backend
from fastapi.testclient import TestClient

from server.terminal.gateway import TerminalGateway


class TerminalGatewayTests(unittest.TestCase):
    def test_create_write_snapshot_resize_close(self):
        import asyncio

        async def main():
            gateway = TerminalGateway(shell="/bin/bash")
            created = gateway.create(cols=100, rows=30)
            session_id = created["session_id"]
            self.assertTrue(session_id.startswith("term-"))
            self.assertTrue(gateway.write(session_id, "echo terminal-test\n"))
            await asyncio.sleep(1.0)
            snapshot = gateway.snapshot(session_id)
            self.assertIn("terminal-test", snapshot)
            self.assertTrue(gateway.resize(session_id, 120, 40))
            self.assertTrue(gateway.close(session_id))

        asyncio.run(main())

    def test_session_limit(self):
        import asyncio

        async def main():
            gateway = TerminalGateway(max_sessions=1)
            gateway.create(cols=80, rows=24)
            with self.assertRaises(Exception):
                gateway.create(cols=80, rows=24)

        asyncio.run(main())

    def test_unknown_session_ops_are_safe(self):
        gateway = TerminalGateway()
        self.assertFalse(gateway.write("missing", "x"))
        self.assertFalse(gateway.resize("missing", 100, 40))
        self.assertEqual(gateway.snapshot("missing"), "")
        self.assertFalse(gateway.close("missing"))


class TerminalApiTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "joeos.db"
        os.environ["JOEOS_DB_PATH"] = str(self.db_path)
        os.environ["LEMONADE_CONNECT_TIMEOUT"] = "0.1"
        os.environ["LEMONADE_READ_TIMEOUT"] = "0.2"
        self.client = TestClient(backend.app, base_url="http://127.0.0.1")
        self.client.__enter__()
        self.session_id = self._make_session()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.tempdir.cleanup()

    def _connect(self):
        connection = sqlite3.connect(str(self.db_path), timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _make_session(self):
        # Seed the minimal authoritative identity chain so the application
        # session dependency resolves (mirrors the production ceremony result).
        now = 1_700_000_000_000
        org = str(uuid.uuid4())
        ws = str(uuid.uuid4())
        user = str(uuid.uuid4())
        device = str(uuid.uuid4())
        enrollment = str(uuid.uuid4())
        credential = str(uuid.uuid4())
        session_id = str(uuid.uuid4())
        connection = self._connect()
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            "INSERT INTO authority_organizations(id, name, status, created_at, updated_at, revision) "
            "VALUES (?, ?, 'active', ?, ?, 1)",
            (org, "Test Org", now, now),
        )
        connection.execute(
            "INSERT INTO authority_workspaces(id, organization_id, name, status, created_at, updated_at, revision) "
            "VALUES (?, ?, 'Default', 'active', ?, ?, 1)",
            (ws, org, now, now),
        )
        connection.execute(
            "INSERT INTO authority_users(id, display_name, status, created_at, updated_at, revision) "
            "VALUES (?, 'Owner', 'active', ?, ?, 1)",
            (user, now, now),
        )
        connection.execute(
            "INSERT INTO enrolled_devices(device_id, enrollment_id, server_id, credential_id, "
            "audience_origin, client_instance_id, display_name, platform, os_version, app_version, state, enrolled_at) "
            "VALUES (?, ?, 'server', ?, 'http://127.0.0.1', 'instance', 'Test Device', 'linux', 't', 't', 'active_unassigned', ?)",
            (device, enrollment, credential, now),
        )
        connection.execute(
            "INSERT INTO authority_device_principal_assignments(device_id, user_id, organization_id, "
            "workspace_id, status, assigned_at, assigned_by, revision) "
            "VALUES (?, ?, ?, ?, 'active', ?, ?, 1)",
            (device, user, org, ws, now, user),
        )
        connection.execute(
            "INSERT INTO authority_application_sessions(session_id, user_id, device_id, organization_id, "
            "workspace_id, status, created_at, expires_at, principal_revision, device_assignment_revision) "
            "VALUES (?, ?, ?, ?, ?, 'active', ?, ?, 1, 1)",
            (session_id, user, device, org, ws, now, now + 60 * 15),
        )
        connection.commit()
        connection.close()
        return session_id

    def test_create_requires_application_session(self):
        response = self.client.post("/api/v1/terminal/sessions", json={})
        self.assertEqual(response.status_code, 401)

    def test_create_list_close(self):
        response = self.client.post(
            "/api/v1/terminal/sessions",
            json={"cols": 100, "rows": 30},
            headers={"X-Joeos-Session": self.session_id},
        )
        self.assertEqual(response.status_code, 201, response.text)
        data = response.json()
        self.assertTrue(data["session_id"].startswith("term-"))
        listed = self.client.get(
            "/api/v1/terminal/sessions", headers={"X-Joeos-Session": self.session_id}
        ).json()
        self.assertGreaterEqual(len(listed["sessions"]), 1)
        closed = self.client.request(
            "DELETE",
            "/api/v1/terminal/sessions/" + data["session_id"],
            headers={"X-Joeos-Session": self.session_id, "Content-Type": "application/json"},
            content=b"{}",
        )
        self.assertEqual(closed.status_code, 200)

    def test_websocket_roundtrip(self):
        created = self.client.post(
            "/api/v1/terminal/sessions",
            json={"cols": 100, "rows": 30},
            headers={"X-Joeos-Session": self.session_id},
        ).json()
        url = (
            "/api/v1/terminal/ws/" + created["session_id"]
            + "?token=" + created["token"] + "&session=" + self.session_id
        )
        with self.client.websocket_connect(url) as websocket:
            websocket.send_text("echo term-ws-test\n")
            collected = ""
            deadline = time.time() + 10
            while time.time() < deadline and "term-ws-test" not in collected:
                collected += websocket.receive_text()
            self.assertIn("term-ws-test", collected)
        self.client.request(
            "DELETE",
            "/api/v1/terminal/sessions/" + created["session_id"],
            headers={"X-Joeos-Session": self.session_id, "Content-Type": "application/json"},
            content=b"{}",
        )

    def test_websocket_rejects_bad_token(self):
        created = self.client.post(
            "/api/v1/terminal/sessions",
            json={},
            headers={"X-Joeos-Session": self.session_id},
        ).json()
        url = (
            "/api/v1/terminal/ws/" + created["session_id"]
            + "?token=wrong&session=" + self.session_id
        )
        with self.assertRaises(Exception):
            with self.client.websocket_connect(url):
                pass
        self.client.request(
            "DELETE",
            "/api/v1/terminal/sessions/" + created["session_id"],
            headers={"X-Joeos-Session": self.session_id, "Content-Type": "application/json"},
            content=b"{}",
        )


if __name__ == "__main__":
    unittest.main()
