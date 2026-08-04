import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import httpx

from server.ai import (
    AIService,
    ContextBuilder,
    EmbeddingError,
    InterpretationError,
    ProviderRegistry,
)


class _FakeTransport(httpx.AsyncBaseTransport):
    def __init__(self, handler):
        self._handler = handler
        self.requests = []

    async def handle_async_request(self, request):
        self.requests.append(request)
        return self._handler(request)


def _client(handler):
    return httpx.AsyncClient(transport=_FakeTransport(handler))


def _runtime_online(model="qwen3", embedding_model=None):
    return {"online": True, "model": model, "embedding_model": embedding_model, "available_models": [model]}


class AiServiceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tempdir.cleanup()

    def _service(self, runtime, handler=None):
        http = _client(handler) if handler else _client(lambda request: _json_response({}))
        service = AIService(
            str(Path(self.tempdir.name) / "ai"),
            http_client=http,
            runtime_provider=lambda: runtime,
            api_base="http://loopback/api/v1",
        )
        return service, http

    def test_overview_offline_is_honest(self):
        service, http = self._service({"online": False, "model": None})
        view = service.overview()
        self.assertFalse(view.provider_available)
        self.assertIn("offline", view.provider_reason.lower())

    def test_overview_online_reports_model(self):
        service, http = self._service(_runtime_online())
        view = service.overview()
        self.assertTrue(view.provider_available)
        self.assertEqual(view.model, "qwen3")

    def test_embedding_availability_is_not_fabricated(self):
        service, http = self._service(_runtime_online())
        self.assertFalse(service.embeddings.available())
        with self.assertRaises(RuntimeError):
            asyncio.run(service.embed(["text"]))

    def test_inference_uses_local_provider_and_records_latency(self):
        captured = {}

        def handler(request):
            captured["payload"] = json.loads(request.content)
            return _json_response({"choices": [{"message": {"content": "Hello from the local model."}}], "usage": {"total_tokens": 12}})

        service, http = self._service(_runtime_online(), handler)
        result = asyncio.run(service.infer([{"role": "user", "content": "hi"}]))
        self.assertEqual(result.reply, "Hello from the local model.")
        self.assertEqual(result.provider, "lemonade")
        self.assertEqual(captured["payload"]["model"], "qwen3")
        self.assertGreaterEqual(result.latency_ms, 0.0)

    def test_inference_rejects_when_offline(self):
        service, http = self._service({"online": False, "model": None})
        with self.assertRaises(RuntimeError):
            asyncio.run(service.infer([{"role": "user", "content": "hi"}]))

    def test_cloud_provider_requires_approval(self):
        service, http = self._service(_runtime_online())

        class FakeCloud:
            provider_id = "cloud-x"

            async def infer(self, messages, **kwargs):
                raise NotImplementedError

            async def embed(self, texts, **kwargs):
                raise NotImplementedError

            def availability(self):
                return None

        with self.assertRaises(ValueError):
            service.providers.register_cloud(FakeCloud(), approved=False)
        service.providers.register_cloud(FakeCloud(), approved=True)
        self.assertIsNotNone(service.providers.get("cloud-x"))


class EmbeddingTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tempdir.cleanup()

    def test_embeddings_deduplicate_by_content_hash(self):
        calls = {"count": 0}

        def handler(request):
            calls["count"] += 1
            payload = json.loads(request.content)
            n = len(payload["input"])
            return _json_response({"data": [{"index": i, "embedding": [float(i), float(i) + 1]} for i in range(n)]})

        http = _client(handler)
        service = AIService(
            str(Path(self.tempdir.name) / "ai"),
            http_client=http,
            runtime_provider=lambda: _runtime_online(embedding_model="bge-m3"),
            api_base="http://loopback/api/v1",
        )
        result = asyncio.run(service.embed(["same", "same", "different"], source_refs=["a", "a", "b"]))
        self.assertEqual(result.dimension, 2)
        self.assertEqual(result.deduplicated, 1)
        self.assertEqual(len(result.vectors), 2)

    def test_incompatible_dimensions_rejected(self):
        def handler(request):
            payload = json.loads(request.content)
            rows = []
            for i in range(len(payload["input"])):
                rows.append({"index": i, "embedding": [0.0] * (1 if i == 0 else 3)})
            return _json_response({"data": rows})

        http = _client(handler)
        service = AIService(
            str(Path(self.tempdir.name) / "ai"),
            http_client=http,
            runtime_provider=lambda: _runtime_online(embedding_model="bge-m3"),
            api_base="http://loopback/api/v1",
        )
        with self.assertRaises(EmbeddingError):
            asyncio.run(service.embed(["one", "two"]))


class ContextTests(unittest.TestCase):
    def test_deduplicates_and_respects_privacy(self):
        builder = ContextBuilder()
        result = builder.build(
            [
                {"source_ref": "a", "content": "hello world", "relevance": 0.9},
                {"source_ref": "b", "content": "hello world", "relevance": 0.5},
                {"source_ref": "c", "content": "top secret", "relevance": 0.8, "privacy_class": "secret"},
            ]
        )
        self.assertEqual(result.sources_selected, ["a"])
        self.assertIn("b", result.sources_excluded)
        self.assertIn("c", result.sources_excluded)
        self.assertEqual(result.duplicate_tokens_removed, 3)
        self.assertGreaterEqual(result.candidates_considered, 3)

    def test_token_budget_bounds_selection(self):
        builder = ContextBuilder(default_budget=300)
        result = builder.build(
            [{"source_ref": "a", "content": "x" * 1000, "relevance": 0.9}, {"source_ref": "b", "content": "y" * 1000, "relevance": 0.8}]
        )
        self.assertLessEqual(result.tokens_used, 300)
        self.assertLessEqual(len(result.chunks), 1)

    def test_low_relevance_excluded(self):
        builder = ContextBuilder(relevance_threshold=0.5)
        result = builder.build([{"source_ref": "a", "content": "hello", "relevance": 0.1}])
        self.assertEqual(result.sources_selected, [])
        self.assertIn("a", result.sources_excluded)


class InterpretationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.service, self.http = self._make()

    def tearDown(self):
        self.tempdir.cleanup()

    def _make(self):
        http = _client(lambda request: _json_response({}))
        service = AIService(
            str(Path(self.tempdir.name) / "ai"),
            http_client=http,
            runtime_provider=lambda: _runtime_online(),
            api_base="http://loopback/api/v1",
        )
        return service, http

    def test_create_is_ai_assisted_with_provenance(self):
        record = self.service.create_interpretation(
            interpretation_type="insight",
            summary="Pattern observed in module interactions",
            basis=["module-a", "module-b"],
            model="qwen3",
            privacy_class="restricted",
        )
        self.assertTrue(record.is_ai_assisted)
        self.assertEqual(record.basis, ["module-a", "module-b"])
        self.assertEqual(record.model, "qwen3")
        self.assertEqual(self.service.overview().interpretation_count, 1)

    def test_unsupported_type_rejected(self):
        with self.assertRaises(InterpretationError):
            self.service.create_interpretation(interpretation_type="fact", summary="x")

    def test_delete_requires_no_governance_block(self):
        record = self.service.create_interpretation(interpretation_type="summary", summary="x")
        self.assertTrue(self.service.delete_interpretation(record.interpretation_id))
        self.assertEqual(self.service.overview().interpretation_count, 0)

    def test_delete_blocked_under_governance(self):
        def blocked():
            return (True, "lockdown active")

        http = _client(lambda request: _json_response({}))
        service = AIService(
            str(Path(self.tempdir.name) / "ai2"),
            http_client=http,
            runtime_provider=lambda: _runtime_online(),
            api_base="http://loopback/api/v1",
            governance_blocked=blocked,
        )
        record = service.create_interpretation(interpretation_type="summary", summary="x")
        with self.assertRaises(PermissionError):
            service.delete_interpretation(record.interpretation_id)

    def test_list_returns_bounded_records(self):
        for i in range(3):
            self.service.create_interpretation(interpretation_type="hypothesis", summary="h%d" % i)
        records = self.service.list_interpretations()
        self.assertEqual(len(records), 3)


def _json_response(payload):
    body = json.dumps(payload).encode("utf-8")
    return httpx.Response(200, content=body, request=httpx.Request("POST", "http://loopback"))


if __name__ == "__main__":
    unittest.main()
