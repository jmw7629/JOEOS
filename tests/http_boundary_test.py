import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import joeos_backend as backend
from server.security import HttpRequestBoundary


class HttpRequestBoundaryPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = HttpRequestBoundary()

    def decision(self, **overrides):
        values = {
            "method": "GET",
            "path": "/api/metrics",
            "host_header": "100.121.165.22:8080",
            "origin_header": None,
            "sec_fetch_site": None,
            "content_type": None,
        }
        values.update(overrides)
        return self.policy.authorize(**values)

    def test_private_tailscale_local_and_single_label_hosts_are_allowed(self):
        for host in (
            "127.0.0.1:8080",
            "10.0.0.5",
            "172.31.10.2",
            "192.168.1.5",
            "169.254.1.4",
            "100.64.0.1",
            "100.127.255.254:8080",
            "[::1]:8080",
            "[fd12:3456::1]",
            "[fe80::1]:8080",
            "localhost:8080",
            "halo.local",
            "halo.tailnet-name.ts.net",
            "joeos-halo",
        ):
            self.assertIsNone(self.decision(host_header=host), host)

    def test_public_malformed_and_out_of_range_hosts_are_rejected(self):
        for host in (
            "example.com",
            "8.8.8.8",
            "100.128.0.1",
            "0.0.0.0",
            "224.0.0.1",
            "user@example.com",
            "example.com/path",
            "example.com\r\nX-Test: injected",
            "",
        ):
            rejection = self.decision(host_header=host)
            self.assertIsNotNone(rejection, host)
            self.assertEqual(rejection.code, "invalid_host")

    def test_explicit_hosts_are_exact_and_wildcards_fail_closed(self):
        policy = HttpRequestBoundary(["Command.Example.com."])
        allowed = policy.authorize(
            method="GET",
            path="/api/metrics",
            host_header="command.example.com:443",
            origin_header=None,
            sec_fetch_site=None,
            content_type=None,
        )

        self.assertIsNone(allowed)
        with self.assertRaises(ValueError):
            HttpRequestBoundary(["*"])

    def test_api_mutations_require_json_and_same_origin_browser_context(self):
        accepted = self.decision(
            method="POST",
            path="/api/chat",
            origin_header="http://100.121.165.22:8080",
            sec_fetch_site="same-origin",
            content_type="application/json; charset=utf-8",
        )
        native = self.decision(
            method="PATCH",
            path="/api/bots/agent",
            content_type="application/json",
        )
        wrong_media = self.decision(
            method="POST",
            path="/api/chat",
            content_type="text/plain",
        )
        cross_origin = self.decision(
            method="PUT",
            path="/api/workspace",
            origin_header="https://attacker.example",
            content_type="application/json",
        )
        cross_port = self.decision(
            method="POST",
            path="/api/chat",
            origin_header="http://100.121.165.22:9999",
            content_type="application/json",
        )
        cross_site = self.decision(
            method="POST",
            path="/api/bots",
            origin_header="http://100.121.165.22:8080",
            sec_fetch_site="cross-site",
            content_type="application/json",
        )

        self.assertIsNone(accepted)
        self.assertIsNone(native)
        self.assertEqual(wrong_media.status_code, 415)
        self.assertEqual(cross_origin.code, "origin_mismatch")
        self.assertEqual(cross_port.code, "origin_mismatch")
        self.assertEqual(cross_site.code, "cross_site_mutation_blocked")

    def test_request_ids_are_bounded_or_replaced_without_reflection(self):
        supplied = "mobile.request-1234"
        generated = self.policy.request_id("bad id\r\nsecret")

        self.assertEqual(self.policy.request_id(supplied), supplied)
        self.assertRegex(generated, re.compile(r"^[0-9a-f]{32}$"))
        self.assertNotEqual(generated, "bad id\r\nsecret")
        self.assertNotIn("\r", generated)
        self.assertNotIn("\n", generated)


class MainHttpBoundaryIntegrationTests(unittest.TestCase):
    def test_main_app_rejects_untrusted_hosts_and_cross_site_mutations(self):
        with tempfile.TemporaryDirectory() as temp_name:
            environment = {
                "JOEOS_DB_PATH": str(Path(temp_name) / "joeos.db"),
                "LEMONADE_CONNECT_TIMEOUT": "0.1",
                "LEMONADE_READ_TIMEOUT": "0.2",
            }
            with patch.dict(os.environ, environment, clear=False):
                with TestClient(backend.app, base_url="http://127.0.0.1") as client:
                    healthy = client.get("/healthz", headers={"X-Request-ID": "boundary-test-123"})
                    bad_host = client.get("/api/metrics", headers={"Host": "attacker.example"})
                    wrong_media = client.post("/api/chat", content="hello")
                    cross_site = client.post(
                        "/api/chat",
                        json={"message": "hello"},
                        headers={"Origin": "https://attacker.example", "Sec-Fetch-Site": "cross-site"},
                    )

        self.assertEqual(healthy.status_code, 200)
        self.assertEqual(healthy.headers["x-request-id"], "boundary-test-123")
        self.assertEqual(healthy.headers["x-frame-options"], "DENY")
        self.assertEqual(bad_host.status_code, 400)
        self.assertEqual(bad_host.json()["error"]["code"], "invalid_host")
        self.assertEqual(bad_host.headers["cache-control"], "no-store")
        self.assertEqual(wrong_media.status_code, 415)
        self.assertEqual(cross_site.status_code, 403)


if __name__ == "__main__":
    unittest.main()
