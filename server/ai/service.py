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

        Model inventory and default come from the measured Ollama runtime;
        nothing is guessed when unmeasured."""
        record = self.ollama_provider.availability()
        models: List[str] = []
        try:
            discovered = await self.ollama_provider.list_models()
            models = [str(m["name"]) for m in discovered if m.get("name")]
        except Exception:  # noqa: BLE001 - never fabricate
            models = []
        default = _prefer_assistant_model(models) or record.model or ""
        return {
            "provider": "ollama",
            "available": record.available,
            "reason": record.reason,
            "model": default or "",
            "models": models,
            "streaming": record.supports_streaming,
            "tools": [t["function"]["name"] for t in self.assistant_tools if isinstance(t, dict)],
        }

    async def assistant_chat_stream(self, messages: List[dict], *, model: str = "") -> AsyncIterator[Dict]:
        """Agentic, streaming local assistant chat over Ollama (never Lemonade).

        When the backend has wired an agent executor, the bounded safe-tool loop
        runs and the final answer streams token-by-token. Otherwise it falls
        back to plain Ollama streaming so the assistant always works. Yields
        event dicts: ``tool``, ``delta``, and ``done``."""
        record = self.ollama_provider.availability()
        if not record.available:
            raise RuntimeError(record.reason or "Ollama is not reachable on the local loopback.")
        chosen = model or record.model or ""
        if not chosen:
            raise RuntimeError("No Ollama model is available for the assistant.")

        history: List[Dict[str, str]] = []
        for message in (messages or [])[-12:]:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "")
            content = str(message.get("content") or "")[:4000]
            if role in ("user", "assistant") and content.strip():
                history.append({"role": role, "content": content})

        if self.assistant_executor is not None:
            decision = {"model": chosen}
            async for event in self.assistant_executor.stream_events(
                history, list(self.assistant_tools), decision
            ):
                yield event
            return

        async for delta in self.ollama_provider.stream_infer(
            history, model=chosen, temperature=0.4, max_tokens=2000
        ):
            yield {"kind": "delta", "content": delta}
        yield {"kind": "done", "model": chosen, "provider": "ollama", "tokens_used": None}

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
