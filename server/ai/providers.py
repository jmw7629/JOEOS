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
        return self._providers.get("lemonade")

    def records(self) -> List[ProviderRecord]:
        return [provider.availability() for provider in self._providers.values()]
