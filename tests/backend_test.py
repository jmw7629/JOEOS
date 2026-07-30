import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi import HTTPException

import joeos_backend as backend


RUNTIME_ONLINE = {
    "online": True,
    "status": "ok",
    "version": "9.9.9-test",
    "model": "local-test-model",
    "loaded_models": ["local-test-model"],
    "available_models": ["local-test-model"],
    "gpu_percent": 42.0,
    "vram_gb": 32.0,
    "npu_percent": 0.0,
    "tokens_per_second": 31.5,
    "time_to_first_token": 0.08,
    "message": "Private local inference is ready.",
}


def make_request(db_path, runtime=None, http=None):
    state = SimpleNamespace(
        db_path=db_path,
        runtime=dict(runtime or RUNTIME_ONLINE),
        http=http,
        thresholds={},
    )
    return SimpleNamespace(app=SimpleNamespace(state=state))


class LocalBackendContractTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "joeos-test.db"
        self.original_psutil = backend.psutil
        backend.psutil = None  # Tests never depend on host telemetry or psutil.
        backend._prepare_database(self.db_path)
        self._insert_metric(
            "2026-07-29T12:00:00+00:00", 24.0, 38.0, 35.0, 51.0, 3600
        )
        self._insert_metric(
            "2026-07-29T12:00:05+00:00", 28.0, 40.0, 42.0, 52.0, 3605
        )

    def tearDown(self):
        backend.psutil = self.original_psutil
        self.tempdir.cleanup()

    def _insert_metric(self, recorded_at, cpu, ram, gpu, disk, uptime):
        with backend._connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO system_metrics (
                    recorded_at, cpu_percent, ram_percent, gpu_percent,
                    disk_percent, uptime_seconds, cpu_detail, ram_detail,
                    gpu_detail, disk_detail
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    recorded_at,
                    cpu,
                    ram,
                    gpu,
                    disk,
                    uptime,
                    "32 threads",
                    "51 / 128 GiB unified memory",
                    "32 GiB shared GPU memory",
                    "520 / 1000 GiB used",
                ),
            )

    def test_required_same_origin_api_routes_and_methods_exist(self):
        methods_by_path = {}
        for route in backend.app.routes:
            if hasattr(route, "methods"):
                methods_by_path.setdefault(route.path, set()).update(route.methods or [])
        expected = {
            "/healthz": "GET",
            "/api/status": "GET",
            "/api/metrics": "GET",
            "/api/bots": "GET",
            "/api/events": "GET",
            "/api/bots/{bot_id}": "PATCH",
            "/api/bots": "GET",
            "/api/chat": "POST",
            "/sdk/index.js": "GET",
        }
        for path, method in expected.items():
            self.assertIn(path, methods_by_path)
            self.assertIn(method, methods_by_path[path])
        self.assertIn("POST", methods_by_path["/api/bots"])

    def test_browser_sdk_is_served_from_the_audited_local_package(self):
        response = backend.browser_sdk()

        self.assertEqual(Path(response.path), backend.SDK_PATH)
        self.assertTrue(backend.SDK_PATH.is_file())
        self.assertEqual(response.media_type, "application/javascript")

    def test_metrics_endpoint_returns_local_halo_contract(self):
        payload = backend.metrics(make_request(self.db_path))

        self.assertEqual(payload["uptime_seconds"], 3605)
        self.assertEqual(
            [metric["id"] for metric in payload["metrics"]],
            ["cpu", "ram", "gpu", "disk"],
        )
        self.assertEqual(payload["metrics"][0]["history"], [24.0, 28.0])
        self.assertEqual(payload["metrics"][2]["value"], 42.0)
        self.assertEqual(payload["runtime"]["model"], "local-test-model")
        self.assertEqual(payload["nodes"][0]["id"], "halo-local")
        self.assertEqual(payload["nodes"][0]["status"], "healthy")

    def test_bots_are_sqlite_backed_and_status_updates_are_audited(self):
        request = make_request(self.db_path)
        initial = backend.bots(request)
        ids = {bot["id"] for bot in initial["bots"]}
        self.assertIn("lemonade-copilot", ids)
        self.assertIn("codex-local", ids)
        self.assertIn("claude-local", ids)

        updated = backend.update_bot(
            "lemonade-copilot",
            backend.BotStatusRequest(status="stopped"),
            request,
        )
        self.assertEqual(updated["bot"]["status"], "stopped")
        self.assertIn("paused", updated["bot"]["activity"].lower())

        events = backend.events(request)
        self.assertEqual(events["summary"]["active_profiles"], len(initial["bots"]) - 1)
        self.assertTrue(
            any("Lemonade Copilot profile paused" in row["message"] for row in events["logs"])
        )

    def test_missing_bot_is_404(self):
        with self.assertRaises(HTTPException) as caught:
            backend.update_bot(
                "missing-profile",
                backend.BotStatusRequest(status="running"),
                make_request(self.db_path),
            )
        self.assertEqual(caught.exception.status_code, 404)

    def test_health_reports_api_up_even_when_lemonade_is_offline(self):
        offline = dict(RUNTIME_ONLINE, online=False, model=None)
        payload = backend.healthz(make_request(self.db_path, runtime=offline))
        self.assertEqual(payload, {"status": "ok", "lemonade": "offline", "model": None})

    def test_backend_source_has_no_cloud_credentials_or_supabase_client(self):
        source = Path(backend.__file__).read_text(encoding="utf-8")
        self.assertNotIn("SUPABASE_SERVICE_ROLE_KEY", source)
        self.assertNotIn("create_client(", source)
        self.assertNotIn("supabase", source.lower())


class LemonadeProxyContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_chat_uses_loopback_openai_contract_and_returns_text(self):
        captured = {}

        async def handler(request):
            captured["url"] = str(request.url)
            captured["payload"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"role": "assistant", "content": "Halo is healthy."}}
                    ]
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with patch.dict(
                "os.environ",
                {"LEMONADE_BASE_URL": "http://127.0.0.1:13305/api/v1"},
                clear=False,
            ):
                result = await backend._chat_with_lemonade(
                    client,
                    "Summarize health",
                    {
                        "active_section": "dashboard",
                        "history": [{"role": "user", "text": "Use local data."}],
                    },
                    RUNTIME_ONLINE,
                )

        self.assertEqual(result["reply"], "Halo is healthy.")
        self.assertEqual(result["model"], "local-test-model")
        self.assertEqual(captured["url"], "http://127.0.0.1:13305/api/v1/chat/completions")
        self.assertEqual(captured["payload"]["model"], "local-test-model")
        self.assertFalse(captured["payload"]["stream"])
        self.assertEqual(captured["payload"]["messages"][-1]["content"], "Summarize health")

    async def test_offline_runtime_returns_actionable_503_without_network(self):
        async def handler(_request):
            return httpx.Response(500)

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            with self.assertRaises(HTTPException) as caught:
                await backend._chat_with_lemonade(
                    client,
                    "hello",
                    {},
                    dict(RUNTIME_ONLINE, online=False, model=None),
                )
        self.assertEqual(caught.exception.status_code, 503)
        self.assertIn("offline", caught.exception.detail.lower())


if __name__ == "__main__":
    unittest.main()
