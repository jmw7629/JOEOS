"""AIService — the authoritative Local AI Runtime facade.

Composes a provider-neutral inference registry (local Lemonade only unless a
cloud provider is explicitly policy-approved), a local-first embedding service,
bounded context construction, and AI-assisted interpretation with provenance.
It reports honest availability, records real latency into the Performance
Metrics Registry, and never routes to cloud silently.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

import httpx

from .context import ContextBuilder
from .embeddings import EmbeddingService
from .interpret import InterpretationService
from .models import (
    AIOverview,
    ContextResult,
    EmbeddingResult,
    InferenceResult,
    InterpretationRecord,
    ProviderRecord,
    StreamDelta,
)
from .providers import LocalLemonadeProvider, OllamaProvider, ProviderRegistry
from .routing import CapabilityRouter, NoEligibleProviderError, RouterSelection
from .storage import AIStorage


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _prefer_ollama_model(models: List[str]) -> Optional[str]:
    """Pick the strongest installed model for general orchestration, preferring
    larger coding-capable models. Unknown inventory returns None (never guessed)."""
    if not models:
        return None
    ranked = sorted(
        models,
        key=lambda name: (
            0 if ("deepseek" in name.lower() and "r1" in name.lower()) else 1,
            -int("14b" in name),
            -int("7b" in name),
            0 if "agentic" in name.lower() or "safe" in name.lower() else 1,
        ),
    )
    return ranked[0]


def _prefer_assistant_model(models: List[str]) -> Optional[str]:
    """Pick a proven conversational model for the local assistant.

    Prefers plain qwen2.5-coder variants (verified working on the VPS Ollama)
    over reasoning/custom ``-agentic`` Modelfiles that are not guaranteed to
    load. Unknown inventory returns None (never guessed)."""
    if not models:
        return None
    for candidate in (
        "qwen2.5-coder:7b",
        "qwen2.5-coder:7b-opencode-safe",
        "qwen3-coder:30b-a3b-q8_0",
        "qwen3-coder-next:latest",
        "qwen3.6:35b",
        "llama3.3:70b",
        "qwen2.5-coder:14b",
        "qwen2.5-coder:1.5b",
        "qwen2.5-coder:1.5b-fast",
        "qwen2.5-coder:1.5b-opencode-safe",
    ):
        if candidate in models:
            return candidate
    return _prefer_ollama_model(models)


class AIService:
    def __init__(
        self,
        data_dir: str,
        *,
        http_client: httpx.AsyncClient,
        runtime_provider: Callable[[], Dict[str, Any]],
        api_base: str,
        headers: Optional[Dict[str, str]] = None,
        event_sink: Optional[Callable[[str, str, str], None]] = None,
        governance_blocked: Optional[Callable[[], tuple]] = None,
        record_metric: Optional[Callable[[str, float], None]] = None,
        version: str = "2.0.0",
        ollama_api_base: str = "http://127.0.0.1:11434",
        assistant_executor: Optional[Any] = None,
        assistant_tools: Optional[Sequence[Dict]] = None,
    ) -> None:
        from pathlib import Path as _Path

        data_path = _Path(data_dir)
        data_path.mkdir(parents=True, exist_ok=True)
        db_path = data_path / "ai.db"

        def connect() -> sqlite3.Connection:
            connection = sqlite3.connect(str(db_path), timeout=10)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 10000")
            return connection

        import urllib.parse as _up
        lemonade_host = api_base.rstrip("/")
        # The OpenAI-compatible models endpoint lives on the Lemonade host at
        # /v1/models (api_base is .../api/v1). Preserve an absolute URL because
        # the shared http client has no base_url.
        self._lemonade_models_url = lemonade_host.rsplit("/api/v1", 1)[0] + "/v1/models"

        self.storage = AIStorage(connect)
        self.local_provider = LocalLemonadeProvider(
            http_client=http_client,
            runtime_provider=runtime_provider,
            api_base=api_base,
            headers=headers,
        )
        self.ollama_provider = OllamaProvider(
            http_client=http_client,
            api_base=ollama_api_base,
        )
        self.providers = ProviderRegistry(self.local_provider)
        self.providers.register_local(self.ollama_provider)
        self.embeddings = EmbeddingService(self.providers, self.storage)
        self.context = ContextBuilder(self.storage)
        self.interpretations = InterpretationService(self.storage)
        self._event_sink = event_sink
        self._governance_blocked = governance_blocked or (lambda: (False, ""))
        self._record_metric = record_metric
        self._version = version
        self.assistant_executor = assistant_executor
        self.assistant_tools = list(assistant_tools or ())
        self._model_inventory: Dict[str, List[str]] = {}
        # Provider preference order: configured agents may express a preferred
        # provider; the router tries it first, then the rest in registration
        # order. Ollama remains the primary usable provider today; Lemonade is
        # the second routable provider (its already-downloaded models appear in
        # the inventory so they are never re-downloaded).
        def _inventory() -> Dict[str, List[str]]:
            return dict(self._model_inventory)
        self.router = CapabilityRouter(self.providers, model_inventory=_inventory)

    def set_provider_model_inventory(self, provider_id: str, models: List[str]) -> None:
        """Record the authoritative installed-model inventory for a provider."""
        if not isinstance(self._model_inventory, dict):
            self._model_inventory = {}
        self._model_inventory[provider_id] = [str(m) for m in models if m]

    def providers_records(self) -> List[ProviderRecord]:
        return self.providers.records()

    def provider_streaming_supported(self) -> bool:
        """True only when the default provider genuinely advertises streaming."""
        provider = self.providers.default()
        if provider is None:
            return False
        record = provider.availability()
        return record.available and record.supports_streaming

    async def probe_ollama(self) -> Dict[str, Any]:
        """Measure the local Ollama runtime and cache its health on the adapter.

        Never fabricates state: unmeasured stays unknown. Returns a safe,
        redacted summary for the runtime collector and the control-plane
        provider registry sync."""
        version = None
        models: List[str] = []
        try:
            version = await self.ollama_provider.version()
        except Exception:  # noqa: BLE001
            version = None
        if version is None:
            self.providers.set_ollama_health(
                available=False, reason="Ollama is not reachable on the VPS loopback.",
                model=None, version=None,
            )
            return {"available": False, "reason": "Ollama is not reachable on the VPS loopback.", "models": []}
        try:
            discovered = await self.ollama_provider.list_models()
            models = [str(m["name"]) for m in discovered if m.get("name")]
        except Exception:  # noqa: BLE001
            models = []
        preferred = _prefer_ollama_model(models)
        self.providers.set_ollama_health(
            available=True, reason="Ollama is healthy on the VPS loopback.",
            model=preferred, version=version, supports_streaming=True,
        )
        return {
            "available": True,
            "reason": "Ollama is healthy on the VPS loopback.",
            "version": version,
            "model": preferred,
            "models": models,
        }

    async def probe_lemonade(self) -> Dict[str, Any]:
        """Advertised models from the running Lemonade service (already-
        downloaded on-disk weights), without claiming they are load-ready.
        Never re-downloads or fabricates."""
        ids: List[str] = []
        reachable = False
        reason = ""
        try:
            response = await self._http.get(self._lemonade_models_url, timeout=5.0)
            response.raise_for_status()
            data = response.json()
            data_list = data.get("data", []) if isinstance(data, dict) else []
            ids = [str(m["id"]) for m in data_list if isinstance(m, dict) and m.get("id")]
            reachable = True
        except Exception as error:  # noqa: BLE001 - never fabricate
            reason = "Lemonade (loopback LLM server) unreachable: %s" % type(error).__name__
        self._lemonade_models = ids
        return {"available": reachable, "reason": reason, "models": ids}

    def overview(self) -> AIOverview:

        default = self.providers.default()
        record = default.availability() if default else ProviderRecord(
            provider_id="none", name="None", available=False, reason="No inference provider is registered."
        )
        return AIOverview(
            provider_available=record.available,
            provider_reason=record.reason,
            model=record.model,
            embedding_available=record.embedding_model is not None,
            embedding_model=record.embedding_model,
            interpretation_count=self.interpretations.count(),
            generated_at=_now_iso(),
            message="Local AI runtime state reported honestly from Lemonade; cloud routing is never silent.",
        )

    async def infer(self, messages: List[dict], *, model: str = "", temperature: float = 0.25, max_tokens: int = 1200) -> InferenceResult:
        provider = self.providers.default()
        if provider is None:
            raise RuntimeError("No inference provider is registered.")
        record = provider.availability()
        if not record.available:
            raise RuntimeError(record.reason)
        chosen = model or record.model or ""
        result = await provider.infer(messages, model=chosen, temperature=temperature, max_tokens=max_tokens)
        if self._record_metric is not None and result.latency_ms is not None:
            self._record_metric("model.first_token_ms", result.latency_ms)
        if self._event_sink is not None:
            self._event_sink("info", "ai", "Local inference completed with %s." % chosen)
        return result

    async def stream_infer(
        self,
        messages: List[dict],
        *,
        model: str = "",
        temperature: float = 0.25,
        max_tokens: int = 1200,
    ) -> AsyncIterator[StreamDelta]:
        """Yields genuine partial deltas when the provider truly streams, or a
        single completed delta when it does not. Never fabricates partials."""
        provider = self.providers.default()
        if provider is None:
            raise RuntimeError("No inference provider is registered.")
        record = provider.availability()
        if not record.available:
            raise RuntimeError(record.reason)
        chosen = model or record.model or ""
        if record.supports_streaming:
            async for delta in provider.stream_infer(
                messages, model=chosen, temperature=temperature, max_tokens=max_tokens
            ):
                yield StreamDelta(
                    content=delta,
                    provider=record.provider_id,
                    model=chosen,
                    finish_reason="streaming",
                )
            yield StreamDelta(
                content="",
                provider=record.provider_id,
                model=chosen,
                finish_reason="completed",
                done=True,
            )
            return
        # Honest non-streaming: the provider returns the whole reply at once.
        result = await provider.infer(
            messages, model=chosen, temperature=temperature, max_tokens=max_tokens
        )
        yield StreamDelta(
            content=result.reply,
            provider=record.provider_id,
            model=result.model or chosen,
            finish_reason=result.finish_reason,
            tokens_used=result.tokens_used,
            done=True,
        )

    async def assistant_config(self) -> Dict[str, Any]:
        """Safe, honest assistant configuration for the browser widget.

        The default provider and model come from the capability router over the
        ProviderRegistry and the currently known model inventory. The inventory
        is recorded from the backend probe (Ollama) and any registered
        provider (e.g. Lemonade already-downloaded models). Nothing is guessed
        when unmeasured, and no unavailable model is claimed as ready."""
        ollama_models: List[str] = []
        try:
            discovered = await self.ollama_provider.list_models()
            ollama_models = [str(m["name"]) for m in discovered if m.get("name")]
        except Exception:  # noqa: BLE001 - never fabricate
            ollama_models = []
        lemonade_ids: List[str] = []
        try:
            await self.probe_lemonade()
            lemonade_ids = list(getattr(self, "_lemonade_models", []) or [])
        except Exception:  # noqa: BLE001 - Lemonade is secondary
            lemonade_ids = []

        # Provider-scoped inventory: a Lemonade model is never claimed to run on
        # Ollama and vice-versa.
        self.set_provider_model_inventory("ollama", ollama_models)
        self.set_provider_model_inventory("lemonade", lemonade_ids)
        all_models: List[str] = []
        for bucket in (ollama_models, lemonade_ids):
            for m in bucket:
                if m not in all_models:
                    all_models.append(m)
        # Keep the sensible conversational default (prefer proven models) while
        # routing stays capability-based and provider-neutral.
        default = _prefer_assistant_model(all_models) or ""

        selection: Optional[RouterSelection] = None
        try:
            selection = self.router.select(
                "tool_use",
                request_model=default or None,
                preference_order=["ollama", "lemonade"],
            )
        except Exception:  # noqa: BLE001 - no eligible provider is honest
            selection = None

        available = selection is not None
        return {
            "provider": selection.provider_id if selection else "",
            "available": available,
            "reason": (
                "" if selection else "No healthy, enabled provider with an installed model is available."
            ),
            "model": selection.model if selection else "",
            "models": all_models,
            "streaming": self.providers.default().availability().supports_streaming if self.providers.default() else False,
            "tools": [t["function"]["name"] for t in self.assistant_tools if isinstance(t, dict)],
            "routing": self.router.selection_fingerprint(selection) if selection else None,
        }

    def lemonade_advertised_models(self) -> List[str]:
        """Models Lemonade advertises via the running service (already-downloaded
        weights). Values are cached; the caller should call ``probe_lemonade``
        first. Does not claim the models are load-ready."""
        return list(getattr(self, "_lemonade_models", None) or [])

    def _scoped_context_block(self, context: Optional[Dict]) -> Optional[str]:
        """Build the bounded JoeContextScope line for the model.

        ``context`` is an authorized object reference from the UI (module type,
        object type, object id, label) — never arbitrary DOM text. Values are
        length-bounded. Returns None when no scope is present."""
        if not isinstance(context, dict):
            return None
        module_type = str(context.get("module_type") or "")[:40]
        object_type = str(context.get("object_type") or "")[:40]
        object_id = str(context.get("object_id") or "")[:80]
        label = str(context.get("label") or "")[:60]
        if not module_type and not label:
            return None
        lines = ["JOE IS FOCUSED ON THIS MODULE:"]
        if module_type:
            lines.append("- module_type: %s" % module_type)
        if object_type:
            lines.append("- object_type: %s" % object_type)
        if object_id:
            lines.append("- object_id: %s" % object_id)
        if label:
            lines.append("- label: %s" % label)
        lines.append(
            "Interpret 'this', 'that', 'here' as the focused object. You are "
            "scoped to it, not granted extra authority; all actions still obey "
            "ToolBroker, policy, and approval."
        )
        return "\n".join(lines)[:800]

    async def assistant_chat_stream(
        self, messages: List[dict], *, model: str = "", context: Optional[Dict] = None
    ) -> AsyncIterator[Dict]:
        """Agentic, streaming local assistant chat, provider-neutral.

        The provider+model are resolved by capability through the router over
        the ProviderRegistry/ModelRegistry (Ollama today, Lemonade or future
        providers when healthy and eligible). The bounded safe-tool loop runs
        via the agent executor when wired; otherwise a plain stream is used.
        Yields event dicts: ``tool``, ``delta``, ``done``, and a leading
        ``route`` event carrying routing metadata."""
        selection = self._resolve_assistant(model)
        provider = self.providers.get(selection.provider_id)
        if provider is None:
            raise RuntimeError("Resolved provider %s is not registered." % selection.provider_id)
        chosen = selection.model
        history: List[Dict[str, str]] = []
        scope_block = self._scoped_context_block(context)
        if scope_block:
            history.append({"role": "system", "content": scope_block})
        for message in (messages or [])[-12:]:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "")
            content = str(message.get("content") or "")[:4000]
            if role in ("user", "assistant") and content.strip():
                history.append({"role": role, "content": content})
        yield {"kind": "route", **self.router.selection_fingerprint(selection)}

        if self.assistant_executor is not None:
            decision = {"model": chosen, "provider": selection.provider_id}
            async for event in self.assistant_executor.stream_events(
                history, list(self.assistant_tools), decision
            ):
                yield event
            return

        async for delta in provider.stream_infer(
            history, model=chosen, temperature=0.4, max_tokens=2000
        ):
            yield {"kind": "delta", "content": delta}
        yield {"kind": "done", "model": chosen, "provider": selection.provider_id, "tokens_used": None}

    def _resolve_assistant(self, request_model: Optional[str] = None) -> RouterSelection:
        """Resolve the assistant provider+model by capability.

        Deterministic: a no-eligible-provider result raises rather than being
        fabricated. ``request_model`` is honored when it is available on a
        healthy provider; otherwise a sensible fallback is returned *with* a
        fallback_reason (never silently)."""
        try:
            return self.router.select_for_assistant(request_model=request_model)
        except NoEligibleProviderError:
            raise RuntimeError(
                "No healthy, enabled provider with an installed model is available for the assistant."
            ) from None

    async def embed(self, texts: List[str], *, project: str = "", source_refs: Optional[List[str]] = None, privacy_class: str = "restricted") -> EmbeddingResult:
        if not self.embeddings.available():
            raise RuntimeError("No local embedding model is available; vectors are not fabricated.")
        return await self.embeddings.embed(texts, project=project, source_refs=source_refs, privacy_class=privacy_class)

    def build_context(self, sources: List[dict], *, project: str = "", token_budget: int = 0, purpose: str = "analysis") -> ContextResult:
        return self.context.build(sources, project=project, token_budget=token_budget, purpose=purpose)

    def create_interpretation(self, **kwargs) -> InterpretationRecord:
        record = self.interpretations.create(**kwargs)
        if self._event_sink is not None:
            self._event_sink("info", "ai", "AI-assisted %s interpretation recorded." % record.interpretation_type)
        return record

    def list_interpretations(self, interpretation_type: str = "", limit: int = 100) -> List[InterpretationRecord]:
        return self.interpretations.list(interpretation_type=interpretation_type, limit=limit)

    def delete_interpretation(self, interpretation_id: str) -> bool:
        blocked, reason = self._governance_blocked()
        if blocked:
            raise PermissionError("governance: %s" % reason)
        return self.interpretations.delete(interpretation_id)
