"""HTTP runner transport tests: request shapes, headers, and error handling.
No external network; responses are simulated with httpx.MockTransport."""

import json
import unittest

import httpx

from joeos_runner.transport import HTTPRunnerTransport, RunnerTransportError


class TransportTests(unittest.TestCase):
    def _transport(self, handler):
        client = httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="http://127.0.0.1:8080",
        )
        transport = HTTPRunnerTransport("http://127.0.0.1:8080")
        transport._client = client
        return transport

    def test_request_connection_posts_runner_id(self):
        seen = {}

        def handler(request):
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"challenge_id": "c1", "message": "m", "expires_at": 1})

        transport = self._transport(handler)
        result = transport.request_connection("runner-1", "k1", "pk")
        self.assertEqual(seen["url"], "http://127.0.0.1:8080/api/v1/runner/connect/challenge")
        self.assertEqual(seen["body"], {"runner_id": "runner-1"})
        self.assertEqual(result["challenge_id"], "c1")

    def test_authenticate_sends_challenge_and_versions(self):
        seen = {}

        def handler(request):
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"connection_credential": "cred-1"})

        transport = self._transport(handler)
        result = transport.authenticate({"challenge_id": "c1"}, "sig-1")
        self.assertEqual(seen["body"], {
            "challenge_id": "c1", "signature": "sig-1", "protocol_version": 1,
            "runner_version": "", "catalog_digest": "",
        })
        self.assertEqual(result["connection_credential"], "cred-1")

    def test_credential_authenticated_endpoints_send_header(self):
        seen = []

        def handler(request):
            seen.append(request.headers.get("X-Runner-Credential"))
            return httpx.Response(200, json={"ok": True})

        transport = self._transport(handler)
        transport.heartbeat("cred-1")
        transport.lease("cred-1")
        transport.progress("cred-1", {"id": "j1"}, "halfway")
        self.assertEqual(seen, ["cred-1", "cred-1", "cred-1"])

    def test_complete_sends_job_id_signature_and_result(self):
        seen = {}

        def handler(request):
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"terminal_classification": "clean"})

        transport = self._transport(handler)
        transport.complete("cred-1", {"id": "j1"}, "sig-1", {"status": "succeeded"})
        self.assertEqual(seen["body"], {
            "job_id": "j1", "signature": "sig-1", "result": {"status": "succeeded"},
        })

    def test_backend_rejection_raises_transport_error(self):
        def handler(request):
            return httpx.Response(403, json={"detail": {"code": "connection_invalid", "message": "The runner connection credential is invalid."}})

        transport = self._transport(handler)
        with self.assertRaises(RunnerTransportError) as raised:
            transport.heartbeat("bad")
        self.assertIn("connection_invalid", str(raised.exception))

    def test_network_error_raises_transport_error(self):
        def handler(request):
            raise httpx.ConnectError("connection refused")

        transport = self._transport(handler)
        with self.assertRaises(RunnerTransportError):
            transport.request_connection("r1", "k1", "pk")


if __name__ == "__main__":
    unittest.main()
