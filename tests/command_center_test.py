import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import joeos_backend as backend
from server.command_center.models import OverviewEnvelope, ServicesEnvelope
from server.command_center.router import router
from server.command_center.service import CommandCenterService, worst_state

NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)


class CommandCenterFixture(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "joeos.db"
        backend._prepare_database(self.db_path)
        backend._record_event(self.db_path, "info", "test", "baseline")
        backend._record_event(self.db_path, "warn", "resource-scout", "high utilization")
        backend._record_event(self.db_path, "error", "lemonade", "connection lost")
        self.runtime = {}
        self.service = CommandCenterService(
            lambda: backend._connect(self.db_path),
            lambda: self.runtime,
            version="2.0.0",
            started_at="2026-07-29T11:59:00+00:00",
            sample_interval_seconds=5,
            now_provider=lambda: NOW,
            realtime_ready=lambda: True,
            identity_ready=lambda: True,
            workspace_ready=lambda: True,
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def _insert_metric(self, recorded_at):
        with backend._connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO system_metrics (
                    recorded_at, cpu_percent, ram_percent, gpu_percent, disk_percent,
                    uptime_seconds, cpu_detail, ram_detail, gpu_detail, disk_detail
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    recorded_at.isoformat(),
                    42.0,
                    58.0,
                    10.0,
                    63.0,
                    3600,
                    "8 threads",
                    "8 / 16 GiB",
                    "shared GPU",
                    "250 / 500 GiB",
                ),
            )


class WorstStateTests(unittest.TestCase):
    def test_ranking_is_deterministic(self):
        self.assertEqual(worst_state(["healthy", "degraded"]), "degraded")
        self.assertEqual(worst_state(["healthy", "degraded", "unavailable"]), "unavailable")
        self.assertEqual(worst_state(["unknown", "healthy"]), "unknown")
        self.assertEqual(worst_state(["healthy"]), "healthy")
        self.assertEqual(worst_state([]), "unknown")


class CommandCenterServiceTests(CommandCenterFixture):
    def test_services_report_application_database_events_and_readiness(self):
        services = {item.service_id: item for item in self.service.services().services}

        self.assertEqual(services["joeos.runtime"].state, "healthy")
        self.assertEqual(services["joeos.runtime"].version, "2.0.0")
        self.assertEqual(services["database"].state, "healthy")
        self.assertEqual(services["events.audit"].state, "healthy")
        self.assertEqual(services["realtime.stream"].state, "healthy")
        self.assertEqual(services["identity.enrollment"].state, "healthy")
        self.assertEqual(services["workspace.configuration"].state, "healthy")

    def test_lemonade_is_unavailable_when_runtime_is_offline(self):
        services = {item.service_id: item for item in self.service.services().services}

        self.assertEqual(services["inference.lemonade"].state, "unavailable")
        self.assertFalse(services["inference.lemonade"].available)

    def test_lemonade_is_healthy_when_runtime_reports_online(self):
        self.runtime = {"online": True, "model": "qwen3.5-coder", "version": "1.0"}
        services = {item.service_id: item for item in self.service.services().services}

        self.assertEqual(services["inference.lemonade"].state, "healthy")
        self.assertEqual(services["inference.lemonade"].version, "1.0")

    def test_telemetry_is_stale_when_sample_is_old(self):
        self._insert_metric(NOW - timedelta(seconds=40))
        services = {item.service_id: item for item in self.service.services().services}

        self.assertEqual(services["telemetry.collector"].state, "degraded")
        self.assertIn("stale", services["telemetry.collector"].message.lower())

    def test_telemetry_is_current_when_sample_is_fresh(self):
        self._insert_metric(NOW - timedelta(seconds=2))
        services = {item.service_id: item for item in self.service.services().services}

        self.assertEqual(services["telemetry.collector"].state, "healthy")

    def test_telemetry_is_unavailable_before_first_sample(self):
        services = {item.service_id: item for item in self.service.services().services}

        self.assertEqual(services["telemetry.collector"].state, "unavailable")

    def test_overview_overall_is_worst_of_signals_and_attention_is_real(self):
        self.runtime = {"online": True, "model": "qwen3.5-coder", "loaded_models": ["qwen3.5-coder"]}
        overview = self.service.overview()

        self.assertIsInstance(overview, OverviewEnvelope)
        self.assertEqual(overview.overall, "unavailable")
        self.assertEqual([item.severity for item in overview.attention], ["error", "warn"])
        self.assertEqual(overview.counts.unread_attention, 2)
        self.assertEqual(overview.counts.loaded_models, 1)
        self.assertEqual(overview.runtime.model, "qwen3.5-coder")

    def test_overview_does_not_fabricate_unavailable_capabilities(self):
        overview = self.service.overview()

        self.assertEqual(overview.capabilities.missions, "unavailable")
        self.assertEqual(overview.capabilities.approvals, "unavailable")
        self.assertEqual(overview.capabilities.projects, "unavailable")
        self.assertIsNone(overview.counts.active_missions)
        self.assertIsNone(overview.counts.pending_approvals)
        self.assertIsNone(overview.counts.active_projects)
        self.assertIsNone(overview.next_scheduled_automation)
        self.assertIsNone(overview.counts.loaded_models)

    def test_overview_agents_and_resources_come_from_real_rows(self):
        self._insert_metric(NOW - timedelta(seconds=2))
        overview = self.service.overview()

        self.assertEqual(overview.counts.active_agents, 6)
        self.assertEqual(overview.resources.state, "healthy")
        self.assertEqual(overview.resources.cpu_percent, 42.0)
        self.assertEqual(overview.resources.uptime_seconds, 3600)

    def test_overview_resources_report_unavailable_without_samples(self):
        overview = self.service.overview()

        self.assertEqual(overview.resources.state, "unavailable")
        self.assertIsNone(overview.resources.cpu_percent)


class CommandCenterActivityTests(CommandCenterFixture):
    def test_activity_returns_events_newest_first_with_total(self):
        envelope = self.service.activity(limit=10)

        self.assertEqual([item.event_id for item in envelope.items], [3, 2, 1])
        self.assertEqual(envelope.total_available, 3)
        self.assertIsNone(envelope.next_before)
        self.assertEqual(envelope.filters["limit"], 10)

    def test_activity_filters_by_severity_and_source(self):
        errors = self.service.activity(severity="error")
        lemonade = self.service.activity(source="lemonade")

        self.assertEqual([item.event_id for item in errors.items], [3])
        self.assertEqual([item.event_id for item in lemonade.items], [3])

    def test_activity_paginates_with_before_cursor(self):
        page_one = self.service.activity(limit=2)
        page_two = self.service.activity(limit=2, before=page_one.next_before)

        self.assertEqual([item.event_id for item in page_one.items], [3, 2])
        self.assertEqual(page_one.next_before, 2)
        self.assertEqual([item.event_id for item in page_two.items], [1])
        self.assertEqual(page_two.total_available, 3)


class CommandCenterRouterTests(CommandCenterFixture):
    def setUp(self):
        super().setUp()
        app = FastAPI()
        app.state.command_center_service = self.service
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        super().tearDown()

    def test_endpoints_return_typed_envelopes(self):
        overview = self.client.get("/api/v1/command-center/overview")
        services = self.client.get("/api/v1/command-center/services")
        activity = self.client.get("/api/v1/command-center/activity?severity=warn")

        self.assertEqual(overview.status_code, 200)
        self.assertEqual(overview.json()["schema_version"], 1)
        self.assertEqual(services.status_code, 200)
        self.assertIsInstance(services.json(), dict)
        self.assertEqual(activity.status_code, 200)
        self.assertEqual(len(activity.json()["items"]), 1)

    def test_router_requires_initialized_service(self):
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        response = client.get("/api/v1/command-center/overview")

        self.assertEqual(response.status_code, 503)


class MainApplicationCommandCenterTests(CommandCenterFixture):
    def test_main_lifespan_serves_command_center(self):
        with tempfile.TemporaryDirectory() as temp_name:
            db_path = str(Path(temp_name) / "joeos.db")
            environment = {
                "JOEOS_DB_PATH": db_path,
                "LEMONADE_CONNECT_TIMEOUT": "0.1",
                "LEMONADE_READ_TIMEOUT": "0.2",
            }
            import os
            from unittest.mock import patch

            with patch.dict(os.environ, environment, clear=False):
                with TestClient(backend.app, base_url="http://127.0.0.1") as client:
                    overview = client.get("/api/v1/command-center/overview")
                    services = client.get("/api/v1/command-center/services")

        self.assertEqual(overview.status_code, 200)
        self.assertEqual(services.status_code, 200)
        self.assertEqual(overview.json()["overall"], "unavailable")
        self.assertEqual(len(services.json()["services"]), 12)


if __name__ == "__main__":
    unittest.main()
