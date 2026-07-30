import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

import joeos_backend as backend
from server.api.bootstrap.models import BootstrapDocument, RouteDescriptor, ServerIdentity
from server.api.bootstrap.repository import SQLiteServerIdentityRepository
from server.api.bootstrap.router import router
from server.api.bootstrap.service import BootstrapService


FIXED_ID = UUID("12345678-1234-4abc-8def-1234567890ab")
OTHER_ID = UUID("87654321-4321-4cba-8fed-ba0987654321")
NOW = datetime(2026, 7, 29, 15, 30, tzinfo=timezone.utc)


class BootstrapFixture(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "bootstrap.db"

        def connect():
            connection = sqlite3.connect(str(self.db_path), timeout=10)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            return connection

        self.connect = connect
        self.repository = SQLiteServerIdentityRepository(
            connect,
            uuid_provider=lambda: FIXED_ID,
        )
        self.service = BootstrapService(
            self.repository,
            server_version="2.0.0",
            now_provider=lambda: NOW,
        )
        self.service.prepare()

    def tearDown(self):
        self.tempdir.cleanup()


class SQLiteServerIdentityTests(BootstrapFixture):
    def test_server_uuid_is_stable_across_repository_instances_and_prepare_calls(self):
        first = self.repository.get_or_create_server_id()
        second_repository = SQLiteServerIdentityRepository(
            self.connect,
            uuid_provider=lambda: OTHER_ID,
        )
        second_repository.prepare()
        second = second_repository.get_or_create_server_id()

        self.assertEqual(first, FIXED_ID)
        self.assertEqual(second, FIXED_ID)
        with self.connect() as connection:
            count = connection.execute("SELECT COUNT(*) FROM server_identity").fetchone()[0]
        self.assertEqual(count, 1)

    def test_non_v4_uuid_provider_is_rejected_without_persisting_identity(self):
        invalid_path = Path(self.tempdir.name) / "invalid-identity.db"

        def connect_invalid():
            connection = sqlite3.connect(str(invalid_path), timeout=10)
            connection.row_factory = sqlite3.Row
            return connection

        repository = SQLiteServerIdentityRepository(
            connect_invalid,
            uuid_provider=lambda: UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8"),
        )
        repository.prepare()

        with self.assertRaises(TypeError):
            repository.get_or_create_server_id()
        with connect_invalid() as connection:
            count = connection.execute("SELECT COUNT(*) FROM server_identity").fetchone()[0]
        self.assertEqual(count, 0)

    def test_bootstrap_table_does_not_change_existing_joeos_tables(self):
        existing_path = Path(self.tempdir.name) / "existing.db"
        backend._prepare_database(existing_path)
        backend._record_event(existing_path, "info", "test", "preserve me")

        repository = SQLiteServerIdentityRepository(
            lambda: backend._connect(existing_path),
            uuid_provider=lambda: FIXED_ID,
        )
        repository.prepare()
        server_id = repository.get_or_create_server_id()

        with backend._connect(existing_path) as connection:
            event = connection.execute("SELECT message FROM events").fetchone()[0]
            bot_count = connection.execute("SELECT COUNT(*) FROM bots").fetchone()[0]
        self.assertEqual(server_id, FIXED_ID)
        self.assertEqual(event, "preserve me")
        self.assertGreater(bot_count, 0)


class BootstrapContractTests(BootstrapFixture):
    def test_contract_is_strict_versioned_relative_and_non_secret(self):
        sentinel = "DO-NOT-EXPOSE-BOOTSTRAP-SECRET"
        with patch.dict(
            os.environ,
            {
                "LEMONADE_API_KEY": sentinel,
                "LEMONADE_BASE_URL": "http://127.0.0.1:13305/api/v1",
                "JOEOS_HOST": "100.64.0.1",
            },
            clear=False,
        ):
            document = self.service.discover()
        payload = document.model_dump(mode="json")
        serialized = document.model_dump_json()

        self.assertEqual(
            set(payload),
            {
                "schema_version",
                "generated_at",
                "server",
                "security",
                "device_enrollment",
                "capabilities",
                "routes",
            },
        )
        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["generated_at"], "2026-07-29T15:30:00Z")
        self.assertEqual(payload["server"]["server_id"], str(FIXED_ID))
        self.assertEqual(payload["server"]["server_version"], "2.0.0")
        self.assertNotIn(sentinel, serialized)
        self.assertNotIn("127.0.0.1", serialized)
        self.assertNotIn("100.64.0.1", serialized)
        self.assertNotIn("LEMONADE_API_KEY", serialized)
        self.assertTrue(all(route["path"].startswith("/") for route in payload["routes"]))
        self.assertTrue(all("://" not in route["path"] for route in payload["routes"]))

    def test_security_posture_is_explicitly_not_public_or_enrolled(self):
        security = self.service.discover().security

        self.assertEqual(security.ownership_model, "single_owner")
        self.assertEqual(security.network_boundary, "operator_managed_private_tailnet")
        self.assertEqual(security.application_authentication, "unavailable")
        self.assertEqual(security.device_enrollment, "operator_pairing_v1")
        self.assertEqual(security.role_based_access, "unavailable")
        self.assertEqual(security.privileged_actions, "unavailable")
        self.assertFalse(security.public_internet_ready)
        self.assertFalse(security.secrets_returned)
        self.assertIn("not public-internet ready", security.warning)

    def test_device_enrollment_profile_is_exact_local_console_only_and_non_authorizing(self):
        document = self.service.discover()
        profile = document.device_enrollment
        capabilities = {capability.id: capability for capability in document.capabilities}
        routes = {route.id: route for route in document.routes}

        self.assertEqual(profile.protocol, "joeos-device-enrollment-v1")
        self.assertEqual(profile.offer_authority, "local_console_only")
        self.assertEqual(profile.pairing_secret_bytes, 32)
        self.assertEqual(profile.offer_ttl_seconds, 300)
        self.assertEqual(profile.challenge_ttl_seconds, 120)
        self.assertEqual(profile.key_algorithm, "ES256")
        self.assertEqual(profile.public_key_format, "spki_der_base64url")
        self.assertEqual(profile.signature_format, "x962_der_base64url")
        self.assertEqual(
            profile.required_key_purposes,
            ("device_authentication", "approval"),
        )
        self.assertEqual(profile.activation_state, "active_unassigned")
        self.assertFalse(profile.grants_authority)
        enrollment = capabilities["identity.device_enrollment"]
        self.assertEqual(enrollment.status, "available")
        self.assertEqual(enrollment.access, "enrollment")
        self.assertEqual(
            enrollment.route_ids,
            ("device-enrollment.challenge", "device-enrollment.complete"),
        )
        self.assertEqual(routes["device-enrollment.challenge"].methods, ("POST",))
        self.assertEqual(routes["device-enrollment.complete"].methods, ("POST",))
        self.assertTrue(all(routes[route_id].access == "enrollment" for route_id in enrollment.route_ids))

    def test_curated_routes_do_not_advertise_mutation_or_execution_as_privileged(self):
        document = self.service.discover()
        routes = {route.id: route for route in document.routes}

        self.assertEqual(routes["bootstrap.discovery"].methods, ("GET",))
        self.assertEqual(routes["events.stream"].methods, ("WEBSOCKET",))
        self.assertEqual(routes["workspace.configure"].access, "configuration")
        self.assertEqual(routes["assistant.local_analysis"].access, "local_analysis")
        self.assertNotIn("/api/bots/{bot_id}", {route.path for route in document.routes})
        serialized = document.model_dump_json().lower()
        self.assertNotIn("shell command", serialized)
        self.assertNotIn("download route", serialized)
        self.assertNotIn("deployment route", serialized)

    def test_capability_route_references_resolve_and_privileged_features_are_unavailable(self):
        document = self.service.discover()
        route_ids = {route.id for route in document.routes}
        capabilities = {capability.id: capability for capability in document.capabilities}

        for capability in document.capabilities:
            self.assertTrue(set(capability.route_ids).issubset(route_ids))
        for capability_id in (
            "identity.authentication",
            "authorization.roles",
            "approvals.privileged_actions",
            "agents.execution",
            "secrets.management",
        ):
            self.assertEqual(capabilities[capability_id].status, "unavailable")
            self.assertEqual(capabilities[capability_id].route_ids, ())

    def test_strict_models_reject_extra_fields_and_invalid_route_protocol(self):
        with self.assertRaises(ValidationError):
            ServerIdentity(
                server_id=FIXED_ID,
                server_version="2.0.0",
                hostname="must-not-be-accepted",
            )
        with self.assertRaises(ValidationError):
            RouteDescriptor(
                id="bad.websocket",
                path="/ws/bad",
                protocol="websocket",
                methods=("GET",),
                access="stream",
                description="Invalid transport contract.",
            )
        with self.assertRaises(ValidationError):
            RouteDescriptor(
                id="bad.network-path",
                path="//attacker.example/bootstrap",
                protocol="http",
                methods=("GET",),
                access="read_only",
                description="Invalid network-path reference.",
            )

        mismatched = self.service.discover().model_dump()
        mismatched["capabilities"][0]["access"] = "configuration"
        with self.assertRaises(ValidationError):
            BootstrapDocument.model_validate(mismatched)


class BootstrapRouterTests(BootstrapFixture):
    def setUp(self):
        super().setUp()
        app = FastAPI()
        app.state.bootstrap_service = self.service
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        super().tearDown()

    def test_get_route_returns_typed_contract_and_post_is_not_added(self):
        response = self.client.get("/api/v1/bootstrap")
        post = self.client.post("/api/v1/bootstrap", json={})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["server"]["server_id"], str(FIXED_ID))
        self.assertEqual(response.headers["content-type"], "application/json")
        self.assertEqual(post.status_code, 405)


class MainApplicationBootstrapTests(unittest.TestCase):
    def test_main_lifespan_persists_identity_across_restarts(self):
        with tempfile.TemporaryDirectory() as temp_name:
            db_path = str(Path(temp_name) / "joeos.db")
            environment = {
                "JOEOS_DB_PATH": db_path,
                "LEMONADE_CONNECT_TIMEOUT": "0.1",
                "LEMONADE_READ_TIMEOUT": "0.2",
            }
            with patch.dict(os.environ, environment, clear=False):
                with TestClient(backend.app, base_url="http://127.0.0.1") as first_client:
                    first = first_client.get("/api/v1/bootstrap")
                with TestClient(backend.app, base_url="http://127.0.0.1") as second_client:
                    second = second_client.get("/api/v1/bootstrap")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["server"]["server_id"], second.json()["server"]["server_id"])
        self.assertEqual(first.headers["cache-control"], "no-store")


if __name__ == "__main__":
    unittest.main()
