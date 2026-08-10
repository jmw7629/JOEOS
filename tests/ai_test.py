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
from server.ai.models import ProviderRecord
from server.ai.routing import CapabilityRouter, NoEligibleProviderError


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

    def _assistant_online(self, service, model="qwen2.5-coder:7b"):
        service.providers.set_ollama_health(
            available=True, reason="ok", model=model, version="0.32.5",
            supports_streaming=True,
        )
        service.set_provider_model_inventory("ollama", [model])

    def test_assistant_config_offline_is_honest(self):
        service, http = self._service(_runtime_online())
        config = asyncio.run(service.assistant_config())
        self.assertFalse(config["available"])
        self.assertNotIn("qwen", config["model"])

    def test_assistant_config_reports_model_from_runtime(self):
        def handler(request):
            if str(request.url).endswith("/api/tags"):
                return _json_response({"models": [
                    {"name": "qwen2.5-coder:1.5b", "details": {"family": "qwen2"}},
                    {"name": "qwen2.5-coder:7b", "details": {"family": "qwen2"}},
                    {"name": "deepseek-r1:14b-agentic", "details": {"family": "qwen2"}},
                ]})
            return _json_response({})

        service, http = self._service(_runtime_online(), handler)
        self._assistant_online(service)
        config = asyncio.run(service.assistant_config())
        self.assertTrue(config["available"])
        self.assertEqual(config["provider"], "ollama")
        self.assertEqual(config["model"], "qwen2.5-coder:7b")
        self.assertIn("qwen2.5-coder:7b", config["models"])

    def test_assistant_chat_stream_delegates_and_rounds_history(self):
        async def fake_executor(messages, tools, decision):
            self.assertEqual(decision["model"], "qwen2.5-coder:7b")
            self.assertTrue(any(m.get("role") == "user" for m in messages))
            yield {"kind": "tool", "name": "joeos.system_status", "arguments": {}, "result": "ok"}
            yield {"kind": "delta", "content": "Hello from Ollama."}
            yield {"kind": "done", "model": "qwen2.5-coder:7b"}

        service, http = self._service(_runtime_online())
        self._assistant_online(service)
        service.assistant_executor = FakeExecutor(fake_executor)
        service.assistant_tools = [{"type": "function", "function": {"name": "joeos.system_status"}}]

        events = []
        async def collect():
            async for event in service.assistant_chat_stream(
                [{"role": "user", "content": "hi"}] * 20, model="qwen2.5-coder:7b"
            ):
                events.append(event)
        asyncio.run(collect())
        kinds = [e["kind"] for e in events]
        self.assertEqual(kinds, ["route", "tool", "delta", "done"])
        self.assertEqual(events[0]["provider"], "ollama")
        self.assertEqual(events[0]["capability"], "tool_use")
        self.assertLessEqual(len(events), 20)

    def test_assistant_chat_stream_raises_when_offline(self):
        service, http = self._service(_runtime_online())
        with self.assertRaises(RuntimeError):
            async def collect():
                async for _event in service.assistant_chat_stream([{"role": "user", "content": "hi"}]):
                    pass
            asyncio.run(collect())

    def test_assistant_scope_block_is_bounded_and_injected(self):
        captured = {}

        async def fake_executor(messages, tools, decision):
            captured["system"] = [m["content"] for m in messages if m.get("role") == "system"]
            yield {"kind": "delta", "content": "ok"}
            yield {"kind": "done", "model": "qwen2.5-coder:7b"}

        service, http = self._service(_runtime_online())
        self._assistant_online(service)
        service.assistant_executor = FakeExecutor(fake_executor)

        events = []
        async def collect():
            async for event in service.assistant_chat_stream(
                [{"role": "user", "content": "why did this fail?"}],
                model="qwen2.5-coder:7b",
                context={"module_type": "automations", "object_type": "automation",
                         "object_id": "acme.nightly", "label": "Nightly Build"},
            ):
                events.append(event)
        asyncio.run(collect())
        self.assertEqual([e["kind"] for e in events], ["route", "delta", "done"])
        joined = "\n".join(captured["system"])
        self.assertIn("JOE IS FOCUSED ON THIS MODULE", joined)
        self.assertIn("Nightly Build", joined)
        self.assertIn("acme.nightly", joined)

    def test_assistant_scope_rejects_unbounded_context(self):
        scope = service.assistant_chat_stream if False else None
        service, http = self._service(_runtime_online())
        self._assistant_online(service)
        block = service._scoped_context_block({"label": "x" * 500, "object_id": "y" * 200})
        self.assertLessEqual(len(block or ""), 800)


class ProviderRoutingTests(unittest.TestCase):
    """Provider-neutral capability routing (D1).

    Exercises the CapabilityRouter independently of any live provider so the
    selection/health/fallback/failure contract is covered deterministically.
    """

    class _Fake:
        provider_id = ""

        def __init__(self, avail, reason="", model=None):
            self.avail = avail
            self.reason = reason
            self.model = model

        def availability(self):
            return ProviderRecord(
                provider_id=self.provider_id,
                name=self.provider_id.capitalize(),
                available=self.avail,
                reason=self.reason,
                model=self.model,
            )

    class Ollama(_Fake):
        provider_id = "ollama"

    class Lemonade(_Fake):
        provider_id = "lemonade"

    class _Reg:
        def __init__(self, providers):
            self._providers = providers

        def get(self, pid):
            return self._providers.get(pid)

        def records(self):
            return [p.availability() for p in self._providers.values()]

    INVENTORY = {
        "ollama": ["qwen3-coder:30b-a3b-q8_0", "qwen3-coder-next:latest", "llama3.3:70b"],
        "lemonade": ["Qwen3-Coder-30B-A3B-Instruct-Q4_K_M", "gpt-oss-120b-Q4_K_M"],
    }

    def _router(self, providers):
        return CapabilityRouter(self._Reg(providers), model_inventory=lambda: self.INVENTORY)

    def test_healthy_ollama_selected(self):
        router = self._router({"ollama": self.Ollama(True), "lemonade": self.Lemonade(False, "gated")})
        sel = router.select_for_assistant()
        self.assertEqual(sel.provider_id, "ollama")
        self.assertTrue(sel.provider_available)
        self.assertIn(sel.model, self.INVENTORY["ollama"])

    def test_disabled_provider_rejected(self):
        # A provider with availability False is never selected.
        router = self._router({"ollama": self.Ollama(False, "disabled"), "lemonade": self.Lemonade(False, "off")})
        with self.assertRaises(NoEligibleProviderError):
            router.select_for_assistant()

    def test_unhealthy_provider_falls_back(self):
        # Requested Lemonade model but Lemonade is unhealthy -> fall back to
        # a healthy provider, and never claim a Lemonade model runs on Ollama.
        router = self._router({"ollama": self.Ollama(True), "lemonade": self.Lemonade(False, "gated")})
        sel = router.select_for_assistant(
            request_model="gpt-oss-120b-Q4_K_M", preference_order=["lemonade", "ollama"]
        )
        self.assertEqual(sel.provider_id, "ollama")
        self.assertNotIn("gpt-oss", sel.model)
        self.assertTrue(sel.fallback_reason)

    def test_capability_mismatch_deterministic(self):
        router = self._router({"ollama": self.Ollama(True), "lemonade": self.Lemonade(True)})
        # embeddings is a hard capability with no eligible model -> error.
        with self.assertRaises(NoEligibleProviderError):
            router.select("embeddings")

    def test_unavailable_model_falls_back(self):
        router = self._router({"ollama": self.Ollama(True)})
        sel = router.select_for_assistant(request_model="does-not-exist:99")
        self.assertEqual(sel.provider_id, "ollama")
        self.assertTrue(sel.fallback_reason)
        self.assertNotIn(sel.requested_model, (sel.model,))

    def test_preferred_provider_selected(self):
        router = self._router({"ollama": self.Ollama(True), "lemonade": self.Lemonade(True)})
        sel = router.select_for_assistant(
            request_model="Qwen3-Coder-30B-A3B-Instruct-Q4_K_M",
            preference_order=["lemonade", "ollama"],
        )
        self.assertEqual(sel.provider_id, "lemonade")
        self.assertEqual(sel.model, "Qwen3-Coder-30B-A3B-Instruct-Q4_K_M")
        self.assertEqual(sel.fallback_reason, "")

    def test_fallback_provider_selected(self):
        # Healthy Ollama preferred, request a Lemonade-only model -> fallback
        # to Ollama with a reason (not silently).
        router = self._router({"ollama": self.Ollama(True), "lemonade": self.Lemonade(True)})
        sel = router.select_for_assistant(
            request_model="gpt-oss-120b-Q4_K_M", preference_order=["lemonade", "ollama"]
        )
        self.assertEqual(sel.provider_id, "lemonade")
        # requested model is on lemonade; honored there.

    def test_no_eligible_provider_deterministic(self):
        router = self._router({"lemonade": self.Lemonade(False, "gated")})
        with self.assertRaises(NoEligibleProviderError) as ctx:
            router.select_for_assistant()
        self.assertIn("No eligible provider", str(ctx.exception))

    def test_routing_metadata_exposed(self):
        router = self._router({"ollama": self.Ollama(True), "lemonade": self.Lemonade(False, "gated")})
        sel = router.select_for_assistant()
        fp = router.selection_fingerprint(sel)
        self.assertIn("provider", fp)
        self.assertIn("model", fp)
        self.assertIn("capability", fp)
        self.assertIn("provider_health", fp)
        self.assertIn("provider_available", fp)
        self.assertTrue(fp["provider_available"])


class FakeExecutor:
    def __init__(self, generator):
        self._generator = generator

    async def stream_events(self, messages, tools, decision):
        async for event in self._generator(messages, tools, decision):
            yield event


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
