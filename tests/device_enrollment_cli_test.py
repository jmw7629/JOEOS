import email.message
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import UUID

from server.api.bootstrap.service import BootstrapService
from server.identity.cli import _detected_origin, _verify_running_backend
from server.identity.service import EnrollmentOriginError


SERVER_ID = UUID("12345678-1234-4abc-8def-1234567890ab")
ORIGIN = "http://100.98.25.26:8080"


class _ServerRepository:
    def prepare(self):
        return None

    def get_or_create_server_id(self):
        return SERVER_ID


class _EnrollmentService:
    def observed_server_id(self):
        return SERVER_ID


class _Response:
    def __init__(self, body, url, content_type="application/json"):
        self.status = 200
        self._body = body
        self._url = url
        self.headers = email.message.Message()
        self.headers["Content-Type"] = content_type

    def __enter__(self):
        return self

    def __exit__(self, *arguments):
        return False

    def geturl(self):
        return self._url

    def read(self, amount):
        return self._body[:amount]


class _Opener:
    def __init__(self, response):
        self.response = response

    def open(self, request, timeout):
        return self.response


class DeviceEnrollmentCLITests(unittest.TestCase):
    @staticmethod
    def bootstrap_body(server_id=SERVER_ID):
        service = BootstrapService(
            repository=_ServerRepository(),
            server_version="2.0.0",
            now_provider=lambda: datetime(2026, 7, 29, 18, 0, tzinfo=timezone.utc),
        )
        document = service.discover().model_dump(mode="json")
        document["server"]["server_id"] = str(server_id)
        return json.dumps(document, separators=(",", ":")).encode("utf-8")

    def test_running_backend_check_validates_the_real_strict_bootstrap_shape(self):
        url = ORIGIN + "/api/v1/bootstrap"
        response = _Response(self.bootstrap_body(), url)
        with patch(
            "server.identity.cli.build_opener",
            return_value=_Opener(response),
        ):
            _verify_running_backend(ORIGIN, _EnrollmentService())

    def test_running_backend_check_rejects_redirects_and_identity_mismatch(self):
        expected = ORIGIN + "/api/v1/bootstrap"
        cases = (
            _Response(self.bootstrap_body(), "https://different.example/api/v1/bootstrap"),
            _Response(
                self.bootstrap_body(
                    UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
                ),
                expected,
            ),
        )
        for response in cases:
            with self.subTest(url=response.geturl()), patch(
                "server.identity.cli.build_opener",
                return_value=_Opener(response),
            ), self.assertRaises(EnrollmentOriginError):
                _verify_running_backend(ORIGIN, _EnrollmentService())

    def test_default_detection_uses_the_actual_tailscale_http_listener(self):
        def output(arguments):
            return "100.98.25.26\n" if arguments == ("tailscale", "ip", "-4") else None

        with patch.dict("os.environ", {}, clear=True), patch(
            "server.identity.cli._command_output", side_effect=output
        ):
            self.assertEqual(_detected_origin(8080), ORIGIN)

    def test_default_detection_prefers_a_matching_private_serve_proxy(self):
        serve = json.dumps(
            {
                "Web": {
                    "vps.tailnet-name.ts.net:443": {
                        "Handlers": {"/": {"Proxy": "http://127.0.0.1:8080"}}
                    }
                },
                "AllowFunnel": {"vps.tailnet-name.ts.net:443": False},
            }
        )

        def output(arguments):
            if arguments == ("tailscale", "serve", "status", "--json"):
                return serve
            return "100.98.25.26\n"

        with patch.dict("os.environ", {}, clear=True), patch(
            "server.identity.cli._command_output", side_effect=output
        ):
            self.assertEqual(
                _detected_origin(8080),
                "https://vps.tailnet-name.ts.net",
            )

    def test_default_detection_ignores_wrong_port_and_funnel_serve_entries(self):
        for proxy, funnel in (
            ("http://127.0.0.1:9090", False),
            ("http://127.0.0.1:8080", True),
        ):
            serve = json.dumps(
                {
                    "Web": {
                        "vps.tailnet-name.ts.net:443": {
                            "Handlers": {"/": {"Proxy": proxy}}
                        }
                    },
                    "AllowFunnel": {"vps.tailnet-name.ts.net:443": funnel},
                }
            )

            def output(arguments):
                if arguments == ("tailscale", "serve", "status", "--json"):
                    return serve
                return "100.98.25.26\n"

            with self.subTest(proxy=proxy, funnel=funnel), patch.dict(
                "os.environ", {}, clear=True
            ), patch("server.identity.cli._command_output", side_effect=output):
                self.assertEqual(_detected_origin(8080), ORIGIN)


if __name__ == "__main__":
    unittest.main()
