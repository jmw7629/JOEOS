import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.workspace.models import ConfigurationGuideRequest, WorkspaceTheme, WorkspaceUpdate
from server.workspace.router import router
from server.workspace.service import RevisionConflictError, WorkspaceService


class WorkspaceServiceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "workspace.db"
        self.events = []

        def connect():
            connection = sqlite3.connect(str(self.db_path), timeout=10)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            return connection

        self.service = WorkspaceService(
            connect,
            event_sink=lambda level, source, message: self.events.append(
                (level, source, message)
            ),
        )
        self.service.prepare()

    def tearDown(self):
        self.tempdir.cleanup()

    def test_default_workspace_has_versioned_core_and_integration_catalog(self):
        payload = self.service.get_workspace()

        self.assertEqual(payload.catalog_version, 1)
        self.assertEqual(payload.workspace.id, "default")
        self.assertEqual(payload.workspace.name, "Mission Control")
        self.assertEqual(payload.workspace.revision, 1)
        self.assertEqual(payload.workspace.theme.accent_hex, "#31D7FF")
        self.assertEqual(
            [item.order for item in payload.workspace.widgets],
            list(range(len(payload.workspace.widgets))),
        )
        catalog = {item.id: item for item in payload.catalog}
        self.assertEqual(catalog["mission.attention"].state, "ready")
        self.assertEqual(catalog["system.health"].state, "ready")
        self.assertEqual(catalog["calendar.today"].state, "integration_required")
        self.assertEqual(catalog["calendar.today"].integration, "calendar")
        self.assertEqual(catalog["even_reality.g2"].version, 1)

    def test_prepare_is_idempotent_and_preserves_workspace_revision(self):
        current = self.service.get_workspace().workspace
        self.service.update_workspace(
            WorkspaceUpdate(
                revision=current.revision,
                theme=current.theme.model_copy(update={"accent_hex": "#3DE3A4"}),
                widgets=[item.model_dump() for item in current.widgets],
            )
        )

        self.service.prepare()
        restarted = self.service.get_workspace()

        self.assertEqual(restarted.workspace.revision, 2)
        self.assertEqual(restarted.workspace.theme.accent_hex, "#3DE3A4")
        self.assertEqual(len(restarted.catalog), 19)

    def test_update_persists_theme_order_size_visibility_and_revision(self):
        current = self.service.get_workspace().workspace
        widgets = [
            item.model_dump()
            for item in reversed(current.widgets)
        ]
        for position, item in enumerate(widgets):
            item["order"] = position
        widgets[0]["visible"] = False
        widgets[1]["size"] = {"columns": 6, "rows": 3}
        theme = WorkspaceTheme(
            font_scale=1.2,
            font_family="rounded",
            accent_hex="#A879FF",
            text_hex="#F4F7FF",
            canvas_hex="#020711",
            density="compact",
            radius=24,
            glass_opacity=0.6,
        )

        updated = self.service.update_workspace(
            WorkspaceUpdate(
                revision=current.revision,
                name="Executive Mission Control",
                theme=theme,
                widgets=widgets,
            )
        )

        self.assertEqual(updated.workspace.revision, 2)
        self.assertEqual(updated.workspace.name, "Executive Mission Control")
        self.assertEqual(updated.workspace.theme, theme)
        self.assertEqual(updated.workspace.widgets[0].instance_id, widgets[0]["instance_id"])
        self.assertFalse(updated.workspace.widgets[0].visible)
        self.assertEqual(updated.workspace.widgets[1].size.rows, 3)
        self.assertEqual(self.events[-1][0:2], ("success", "workspace"))
        self.assertIn("revision 2", self.events[-1][2])

    def test_stale_revision_is_rejected_without_overwriting_newer_state(self):
        current = self.service.get_workspace().workspace
        body = WorkspaceUpdate(
            revision=current.revision,
            theme=current.theme,
            widgets=[item.model_dump() for item in current.widgets],
        )
        first = self.service.update_workspace(body)

        with self.assertRaises(RevisionConflictError) as caught:
            self.service.update_workspace(body)

        self.assertEqual(caught.exception.current_revision, 2)
        self.assertEqual(self.service.get_workspace().workspace.revision, first.workspace.revision)

    def test_configuration_guide_is_deterministic_and_does_not_apply_proposal(self):
        request = ConfigurationGuideRequest(
            message=(
                "Use a purple accent, larger rounded font, text color #F4F7FF, "
                "background #020711, compact density, add calendar and move Halo health first"
            )
        )
        first = self.service.guide(request).proposal
        second = self.service.guide(request).proposal

        self.assertEqual(first, second)
        self.assertEqual(first.theme.accent_hex, "#A879FF")
        self.assertEqual(first.theme.font_scale, 1.1)
        self.assertEqual(first.theme.font_family, "rounded")
        self.assertEqual(first.theme.text_hex, "#F4F7FF")
        self.assertEqual(first.theme.canvas_hex, "#020711")
        self.assertEqual(first.theme.density, "compact")
        self.assertEqual(first.widgets[0].widget_id, "system.health")
        self.assertTrue(any(item.widget_id == "calendar.today" for item in first.widgets))
        self.assertEqual(first.integration_requirements, ["calendar"])
        self.assertTrue(first.requires_confirmation)
        self.assertIn("never asks", first.secret_notice)
        self.assertEqual(self.service.get_workspace().workspace.revision, 1)

    def test_configuration_guide_ignores_potential_credentials(self):
        marker = "github_pat_" + "A" * 32
        proposal = self.service.guide(
            ConfigurationGuideRequest(message="Set my API key to " + marker)
        ).proposal
        serialized = proposal.model_dump_json()

        self.assertEqual(proposal.changes, [])
        self.assertTrue(proposal.warnings)
        self.assertNotIn(marker, serialized)
        self.assertNotIn("Set my API key", serialized)


class WorkspaceRouterTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        db_path = Path(self.tempdir.name) / "router.db"

        def connect():
            connection = sqlite3.connect(str(db_path), timeout=10)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            return connection

        service = WorkspaceService(connect)
        service.prepare()
        app = FastAPI()
        app.state.workspace_service = service
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.tempdir.cleanup()

    def test_get_put_and_optimistic_conflict_contract(self):
        response = self.client.get("/api/workspace")
        self.assertEqual(response.status_code, 200)
        envelope = response.json()
        self.assertEqual(set(envelope), {"workspace", "catalog", "catalog_version"})

        workspace = envelope["workspace"]
        body = {
            "revision": workspace["revision"],
            "theme": dict(workspace["theme"], font_scale=1.15),
            "widgets": workspace["widgets"],
        }
        saved = self.client.put("/api/workspace", json=body)
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["workspace"]["revision"], 2)
        self.assertEqual(saved.json()["workspace"]["theme"]["font_scale"], 1.15)

        stale = self.client.put("/api/workspace", json=body)
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["detail"]["current_revision"], 2)

    def test_guide_endpoint_returns_typed_proposal(self):
        response = self.client.post(
            "/api/configuration/guide",
            json={"message": "Make the font larger and add the weather widget"},
        )
        self.assertEqual(response.status_code, 200)
        proposal = response.json()["proposal"]
        self.assertEqual(proposal["based_on_revision"], 1)
        self.assertEqual(proposal["theme"]["font_scale"], 1.1)
        self.assertIn("weather_traffic", proposal["integration_requirements"])
        self.assertTrue(proposal["requires_confirmation"])


if __name__ == "__main__":
    unittest.main()
