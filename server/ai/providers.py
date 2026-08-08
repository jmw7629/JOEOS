"""Provider-neutral inference for the Local AI Runtime.

One Provider Registry exposes a stable interface over the local Lemonade
OpenAI-compatible server. Cloud routing is never silent: only providers that
are explicitly approved by policy (and pass the security platform's privacy
engine) may be registered; the default registry contains only the local
provider. Availability is reported honestly from runtime state.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Protocol

import httpx

from .models import InferenceResult, ProviderRecord


class InferenceProvider(Protocol):
    provider_id: str

    async def infer(self, messages: List[dict], *, model: str, temperature: float = 0.25, max_tokens: int = 1200) -> InferenceResult:
        ...

    async def stream_infer(self, messages: List[dict], *, model: str, temperature: float = 0.25, max_tokens: int = 1200) -> AsyncIterator[str]:
        """Yields genuine partial content deltas when the provider streams."""
        ...
        if False:
            yield ""

    async def embed(self, texts: List[str], *, model: str) -> List[List[float]]:
        ...

    def availability(self) -> ProviderRecord:
        ...


class LocalLemonadeProvider:
    """Provider-neutral wrapper over the private local Lemonade server."""

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        runtime_provider: Callable[[], Dict[str, Any]],
        api_base: str,
        headers: Optional[Dict[str, str]] = None,
        privacy_class: str = "restricted",
    ) -> None:
        self.provider_id = "lemonade"
        self.name = "Lemonade (local)"
        self._http = http_client
        self._runtime_provider = runtime_provider
        self._api_base = api_base.rstrip("/")
        self._headers = headers or {}
        self._privacy_class = privacy_class

    def availability(self) -> ProviderRecord:
        runtime = self._runtime_provider() or {}
        if not runtime.get("online"):
            return ProviderRecord(
                provider_id=self.provider_id,
                name=self.name,
                kind="local",
                available=False,
                reason="Lemonade Server is offline.",
                privacy_class=self._privacy_class,
                cloud_approved=False,
            )
        model = runtime.get("model")
        if not model:
            return ProviderRecord(
                provider_id=self.provider_id,
                name=self.name,
                kind="local",
                available=False,
                reason="Lemonade is online but no downloaded text model is ready.",
                privacy_class=self._privacy_class,
                cloud_approved=False,
            )
        return ProviderRecord(
            provider_id=self.provider_id,
            name=self.name,
            kind="local",
            available=True,
            model=model,
            embedding_model=self._detect_embedding_model(runtime),
            base_url="loopback",
            privacy_class=self._privacy_class,
            cloud_approved=False,
            supports_streaming=bool(runtime.get("streaming")),
        )

    async def infer(self, messages: List[dict], *, model: str, temperature: float = 0.25, max_tokens: int = 1200) -> InferenceResult:
        import time
        start = time.monotonic()
        response = await self._http.post(
            self._api_base + "/chat/completions",
            headers=self._headers,
            json={"model": model, "messages": messages, "stream": False, "temperature": temperature, "max_tokens": max_tokens},
        )
        response.raise_for_status()
        data = response.json()
        choices = data.get("choices", []) if isinstance(data, dict) else []
        reply = choices[0].get("message", {}).get("content") if choices else None
        if not isinstance(reply, str) or not reply.strip():
            raise ValueError("Provider response did not contain assistant text")
        latency_ms = (time.monotonic() - start) * 1000.0
        usage = data.get("usage") if isinstance(data, dict) else None
        return InferenceResult(
            reply=reply.strip(),
            model=model,
            provider=self.provider_id,
            runtime="local",
            finish_reason="completed",
            tokens_used=int(usage.get("total_tokens")) if isinstance(usage, dict) and usage.get("total_tokens") else None,
            latency_ms=latency_ms,
        )

    async def stream_infer(self, messages: List[dict], *, model: str, temperature: float = 0.25, max_tokens: int = 1200) -> AsyncIterator[str]:
        """Genuine OpenAI-compatible SSE streaming from the provider.

        Only invoked when the provider advertises streaming support; otherwise
        the conversation layer reports honest non-streaming single events.
        """
        async with self._http.stream(
            "POST",
            self._api_base + "/chat/completions",
            headers=self._headers,
            json={
                "model": model,
                "messages": messages,
                "stream": True,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if payload == "[DONE]":
                    return
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices") or []
                delta = (choices[0].get("delta") or {}).get("content") if choices else None
                if isinstance(delta, str) and delta:
                    yield delta

    async def embed(self, texts: List[str], *, model: str) -> List[List[float]]:
        response = await self._http.post(
            self._api_base + "/embeddings",
            headers=self._headers,
            json={"model": model, "input": list(texts)},
        )
        response.raise_for_status()
        data = response.json()
        rows = data.get("data", []) if isinstance(data, dict) else []
        ordered = sorted(rows, key=lambda row: int(row.get("index", 0)))
        vectors = [row.get("embedding") for row in ordered if isinstance(row.get("embedding"), list)]
        if len(vectors) != len(texts):
            raise ValueError("Provider returned an unexpected number of embeddings")
        return vectors

    @staticmethod
    def _detect_embedding_model(runtime: Dict[str, Any]) -> Optional[str]:
        embedding = runtime.get("embedding_model")
        if embedding:
            return str(embedding)
        for model_id in runtime.get("available_models") or []:
            label = str(model_id).lower()
            if "embed" in label or "bge" in label or "nomic" in label:
                return str(model_id)
        return None


class OllamaProvider:
    """Provider-neutral wrapper over the private local Ollama server.

    Ollama stays private to the VPS loopback. JoeOS (this process) is the only
    Ollama client; the browser never talks to port 11434. Capabilities are
    reported from the live runtime (never fabricated); models that advertise
    tool-calling support it through Ollama's structured ``tools`` field.
    """

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        api_base: str = "http://127.0.0.1:11434",
        privacy_class: str = "restricted",
        timeout_ms: int = 300_000,
        keep_alive: str = "10m",
    ) -> None:
        self.provider_id = "ollama"
        self.name = "Ollama (local)"
        self._http = http_client
        self._api_base = api_base.rstrip("/")
        self._privacy_class = privacy_class
        self._timeout_ms = timeout_ms
        self._keep_alive = keep_alive

    # -- runtime discovery -------------------------------------------------

    async def version(self) -> Optional[str]:
        try:
            response = await self._http.get(self._api_base + "/api/version", timeout=3.0)
            response.raise_for_status()
            data = response.json()
            return str(data.get("version")) if isinstance(data, dict) else None
        except Exception:  # noqa: BLE001 - health probes never fabricate
            return None

    async def list_models(self) -> List[Dict[str, Any]]:
        response = await self._http.get(self._api_base + "/api/tags", timeout=10.0)
        response.raise_for_status()
        data = response.json()
        models = data.get("models", []) if isinstance(data, dict) else []
        out = []
        for row in models:
            details = row.get("details", {}) or {}
            capabilities = row.get("capabilities", []) or []
            out.append({
                "name": row.get("name", ""),
                "size_bytes": row.get("size", 0),
                "modified_at": row.get("modified_at", ""),
                "family": details.get("family", ""),
                "families": details.get("families", []),
                "parameter_size": details.get("parameter_size", ""),
                "quantization_level": details.get("quantization_level", ""),
                "context_length": details.get("context_length", 0),
                "embedding_length": details.get("embedding_length", 0),
                "capabilities": list(capabilities),
            })
        return out

    async def is_loaded(self, model: str) -> bool:
        try:
            response = await self._http.get(self._api_base + "/api/ps", timeout=3.0)
            response.raise_for_status()
            data = response.json()
            for row in data.get("models", []) if isinstance(data, dict) else []:
                if str(row.get("name", "")) == model or str(row.get("model", "")) == model:
                    return True
        except Exception:  # noqa: BLE001
            return False
        return False

    # -- measured health ---------------------------------------------------

    def set_health(
        self,
        *,
        available: bool,
        reason: str,
        model: Optional[str] = None,
        version: Optional[str] = None,
        supports_streaming: bool = True,
    ) -> None:
        self._available = available
        self._reason = reason
        self._model = model
        self._version = version
        self._supports_streaming = supports_streaming

    def _health_available(self) -> bool:
        return getattr(self, "_available", False)

    def _health_reason(self) -> str:
        return getattr(self, "_reason", "Ollama health has not been probed yet.")

    def _health_model(self) -> Optional[str]:
        return getattr(self, "_model", None)

    def _health_version(self) -> Optional[str]:
        return getattr(self, "_version", None)

    def _health_streaming(self) -> bool:
        return getattr(self, "_supports_streaming", True)

    # -- inference ---------------------------------------------------------

    async def infer(self, messages: List[dict], *, model: str, temperature: float = 0.25, max_tokens: int = 1200) -> InferenceResult:
        import time

        start = time.monotonic()
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "keep_alive": self._keep_alive,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        response = await self._http.post(
            self._api_base + "/api/chat", json=payload,
            timeout=self._timeout_ms / 1000.0,
        )
        response.raise_for_status()
        data = response.json()
        reply = (data.get("message") or {}).get("content") if isinstance(data, dict) else None
        if not isinstance(reply, str) or not reply.strip():
            raise ValueError("Ollama response did not contain assistant text")
        latency_ms = (time.monotonic() - start) * 1000.0
        return InferenceResult(
            reply=reply.strip(),
            model=model,
            provider=self.provider_id,
            runtime="local",
            finish_reason="completed",
            tokens_used=int(data.get("eval_count") or 0) or None,
            latency_ms=latency_ms,
        )

    async def infer_tool_call(
        self,
        messages: List[dict],
        *,
        model: str,
        tools: List[Dict[str, Any]],
        temperature: float = 0.2,
        max_tokens: int = 1600,
    ) -> InferenceResult:
        """Chat with a structured tool schema. The model may either return a
        tool-call object or plain text. JoeOS validates tool args before any
        invocation; a malformed tool call is never executed."""
        import time

        start = time.monotonic()
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "stream": False,
            "keep_alive": self._keep_alive,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        response = await self._http.post(
            self._api_base + "/api/chat", json=payload,
            timeout=self._timeout_ms / 1000.0,
        )
        response.raise_for_status()
        data = response.json()
        message = data.get("message") or {}
        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            reply = "[tool call requested]"
        else:
            reply = message.get("content") or ""
        latency_ms = (time.monotonic() - start) * 1000.0
        return InferenceResult(
            reply=reply.strip(),
            model=model,
            provider=self.provider_id,
            runtime="local",
            finish_reason="tool_calls" if tool_calls else "completed",
            tokens_used=int(data.get("eval_count") or 0) or None,
            latency_ms=latency_ms,
        )

    async def infer_json(self, messages: List[dict], *, model: str, temperature: float = 0.2, max_tokens: int = 1200) -> InferenceResult:
        """Structured JSON response using Ollama's native JSON mode."""
        import time

        start = time.monotonic()
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "format": "json",
            "stream": False,
            "keep_alive": self._keep_alive,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        response = await self._http.post(
            self._api_base + "/api/chat", json=payload,
            timeout=self._timeout_ms / 1000.0,
        )
        response.raise_for_status()
        data = response.json()
        reply = (data.get("message") or {}).get("content") if isinstance(data, dict) else None
        if not isinstance(reply, str) or not reply.strip():
            raise ValueError("Ollama response did not contain assistant text")
        latency_ms = (time.monotonic() - start) * 1000.0
        return InferenceResult(
            reply=reply.strip(),
            model=model,
            provider=self.provider_id,
            runtime="local",
            finish_reason="completed",
            tokens_used=int(data.get("eval_count") or 0) or None,
            latency_ms=latency_ms,
        )

    async def stream_infer(self, messages: List[dict], *, model: str, temperature: float = 0.25, max_tokens: int = 1200) -> AsyncIterator[str]:
        """Genuine NDJSON streaming from the Ollama chat endpoint."""
        async with self._http.stream(
            "POST",
            self._api_base + "/api/chat",
            json={
                "model": model,
                "messages": messages,
                "stream": True,
                "keep_alive": self._keep_alive,
                "options": {"temperature": temperature, "num_predict": max_tokens},
            },
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if chunk.get("done"):
                    return
                delta = (chunk.get("message") or {}).get("content")
                if isinstance(delta, str) and delta:
                    yield delta

    async def embed(self, texts: List[str], *, model: str) -> List[List[float]]:
        raise ValueError(
            "No Ollama embedding model is configured; embeddings stay unknown until an embedding-capable model is installed."
        )

    def availability(self) -> ProviderRecord:
        return ProviderRecord(
            provider_id=self.provider_id,
            name=self.name,
            kind="local",
            available=self._health_available(),
            reason=self._health_reason(),
            model=self._health_model(),
            base_url="loopback",
            privacy_class=self._privacy_class,
            cloud_approved=False,
            supports_streaming=self._health_streaming(),
        )


class ProviderRegistry:
    """One authoritative registry of inference providers."""

    def __init__(self, local_provider: LocalLemonadeProvider) -> None:
        self._providers: Dict[str, InferenceProvider] = {}
        self._register(local_provider)

    def _register(self, provider: InferenceProvider) -> None:
        self._providers[provider.provider_id] = provider

    def register_local(self, provider: InferenceProvider) -> None:
        self._register(provider)

    def register_cloud(self, provider: InferenceProvider, *, approved: bool = False) -> None:
        if not approved:
            raise ValueError("Cloud providers require explicit policy approval before registration.")
        self._register(provider)

    def get(self, provider_id: str) -> Optional[InferenceProvider]:
        return self._providers.get(provider_id)

    def default(self) -> Optional[InferenceProvider]:
        # Prefer a provider whose measured availability is positive; fall back
        # to the first registered provider for honest offline reporting.
        for provider in self._providers.values():
            try:
                if provider.availability().available:
                    return provider
            except Exception:  # noqa: BLE001
                continue
        for candidate in ("lemonade", "ollama"):
            provider = self._providers.get(candidate)
            if provider is not None:
                return provider
        return None

    def set_ollama_health(
        self,
        *,
        available: bool,
        reason: str,
        model: Optional[str] = None,
        version: Optional[str] = None,
        supports_streaming: bool = True,
    ) -> None:
        provider = self._providers.get("ollama")
        if isinstance(provider, OllamaProvider):
            provider.set_health(
                available=available, reason=reason, model=model, version=version,
                supports_streaming=supports_streaming,
            )

    def records(self) -> List[ProviderRecord]:
        return [provider.availability() for provider in self._providers.values()]
