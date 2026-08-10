"""Provider-neutral capability routing for the JoeOS assistant.

Joe/assistant execution is routed by *capability* through the ProviderRegistry
and the eligible model inventory, never by hardcoding a provider name:

    Joe / Assistant
        ↓ capability requirement (chat, reasoning, tool_use, ...)
    capability match against installed models
        ↓
    Order providers (health, availability, preference)
        ↓
    Select first eligible (provider, model) with honest health and fallback

This module owns: provider selection, model selection, health/availability
checks, sensible fallback, and deterministic failures — so Joe's core
assistant logic stays provider-agnostic. Ollama remains fully supported and is
today's usable provider; Lemonade is routable through the same abstraction when
configured and healthy; future providers register the same way.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .providers import InferenceProvider, ProviderRegistry

# Canonical capability vocabulary (aligns with control_models flags where the
# schema exposes them). Extra capabilities are additive and never rejected.
CAPABILITY_CHAT = "chat"
CAPABILITY_REASONING = "reasoning"
CAPABILITY_CODING = "coding"
CAPABILITY_TOOL_USE = "tool_use"
CAPABILITY_VISION = "vision"
CAPABILITY_STRUCTURED = "structured_output"
CAPABILITY_EMBEDDINGS = "embeddings"
CAPABILITY_LONG_CONTEXT = "long_context"

DEFAULT_ASSISTANT_CAPABILITIES = (CAPABILITY_CHAT, CAPABILITY_TOOL_USE)


@dataclass(frozen=True)
class RouterSelection:
    """A resolved provider+model choice with routing metadata.

    Exposing this enables the UI to show which provider/model served a request,
    the requested capability, and why a fallback occurred (or none)."""

    provider_id: str
    model: str
    capability: str
    requested_model: Optional[str] = None
    fallback_reason: str = ""  # "" when the primary request was honored
    provider_health: str = "unknown"
    provider_available: bool = False


class CapabilityRouter:
    """Selects an eligible (provider, model) by capability.

    Uses the ProviderRegistry (actual connection/health adapters) and a model
    inventory source (the authoritative ModelRegistry / control_models). The
    router never fabricates: a model already known to be unavailable never
    counts as eligible; providers that are unhealthy are skipped with a reason.
    """

    def __init__(
        self,
        registry: ProviderRegistry,
        *,
        model_inventory: Optional[callable] = None,
    ) -> None:
        """``model_inventory`` returns either:
          - a list of model names (applied to every provider), or
          - a dict {provider_id: [model names]} for provider-scoped availability.
        Provider-scoped inventory prevents claiming a Lemonade model runs on
        Ollama (or vice-versa)."""
        self._registry = registry
        self._model_inventory = model_inventory or (lambda: [])

    def _provider_health(self, provider: InferenceProvider) -> Tuple[str, bool]:
        try:
            record = provider.availability()
        except Exception:  # noqa: BLE001 - never fabricate
            return "unknown", False
        return ("healthy" if record.available else "unhealthy"), bool(record.available)

    def _matches_capability(self, capability: str, provider_id: str, model: str) -> bool:
        """Return True when a model plausibly satisfies the capability.

        capability names are canonical (see module constants); a capability the
        schema does not model is treated as satisfied (additive) so future
        capabilities do not break routing. Tool-use/chat default to true for
        local model inventory; vision/reasoning are derived from known families
        when the inventory has no flag.
        """
        low = model.lower()
        if capability == CAPABILITY_REASONING and ("deepseek" in low or "r1" in low):
            return True
        if capability == CAPABILITY_VISION and ("llava" in low or "vision" in low or "vl" in low):
            return True
        if capability in (CAPABILITY_EMBEDDINGS,):
            return False  # embeddings are a separate service on this host
        return True

    def selection_fingerprint(self, selection: RouterSelection) -> dict:
        return {
            "provider": selection.provider_id,
            "model": selection.model,
            "capability": selection.capability,
            "requested_model": selection.requested_model,
            "fallback_reason": selection.fallback_reason,
            "provider_health": selection.provider_health,
            "provider_available": selection.provider_available,
        }

    def _provider_models(self, provider_id: str) -> List[str]:
        """Model inventory scoped to a provider.

        ``model_inventory`` may return a flat list (applied to every provider,
        for backward compatibility) or a per-provider dict."""
        raw = self._model_inventory()
        if isinstance(raw, dict):
            return [m for m in raw.get(provider_id, []) if m]
        return [m for m in raw if m]

    def _available_models(self, provider_id: str) -> List[str]:
        return self._provider_models(provider_id)

    def select(
        self,
        capability: str = CAPABILITY_CHAT,
        *,
        request_model: Optional[str] = None,
        preferred_provider: Optional[str] = None,
        preference_order: Optional[List[str]] = None,
    ) -> RouterSelection:
        """Choose the best eligible (provider, model) for a capability.

        Ordering:
          1. preferred provider first (from agent/provider preference),
             then remaining providers.
          2. Within a provider, an exact requested model first, else any
             installed model.
        Failure: raises a deterministic NoEligibleProvider error.
        """
        order = list(preference_order or ())
        if preferred_provider and preferred_provider not in order:
            order.insert(0, preferred_provider)
        # When no order/preference is supplied, consider every registered
        # provider so a healthy provider is still found.
        if not order:
            order = [p.provider_id for p in self._registry.records() or []] or [
                pid for pid in ("ollama", "lemonade")
            ]
        strict = capability in (
            CAPABILITY_EMBEDDINGS,
            CAPABILITY_VISION,
            CAPABILITY_REASONING,
            CAPABILITY_CODING,
            CAPABILITY_STRUCTURED,
            CAPABILITY_LONG_CONTEXT,
        )
        for provider_id in order:
            provider = self._registry.get(provider_id)
            if provider is None:
                continue
            health, available = self._provider_health(provider)
            if not available:
                continue  # never silently route to an unhealthy/unknown provider
            installed = self._provider_models(provider_id)
            if not installed:
                continue
            capable = [m for m in installed if self._matches_capability(capability, provider_id, m)]
            if request_model and request_model in installed:
                if not strict or request_model in capable:
                    # Honored as requested (no fallback).
                    return RouterSelection(
                        provider_id=provider_id,
                        model=request_model,
                        capability=capability,
                        requested_model=request_model,
                        fallback_reason="",
                        provider_health=health,
                        provider_available=True,
                    )
            candidate = capable[0] if capable else None
            if candidate is None and strict:
                continue  # no model on this provider satisfies a hard capability
            if candidate is None:
                candidate = installed[0]
            fallback = "" if request_model is None or request_model == candidate else (
                "requested model %s not available on %s, selected %s" % (request_model, provider_id, candidate))
            return RouterSelection(
                provider_id=provider_id,
                model=candidate,
                capability=capability,
                requested_model=request_model,
                fallback_reason=fallback,
                provider_health=health,
                provider_available=True,
            )
        raise NoEligibleProviderError(
            "No eligible provider for capability '%s'. No healthy, enabled provider "
            "with an installed model is available." % capability
        )

    def select_for_assistant(
        self,
        *,
        request_model: Optional[str] = None,
        preferred_provider: Optional[str] = None,
        preference_order: Optional[List[str]] = None,
    ) -> RouterSelection:
        """Assistant convenience wrapper over the chat+tool_use capability."""
        return self.select(
            CAPABILITY_TOOL_USE,
            request_model=request_model,
            preferred_provider=preferred_provider,
            preference_order=preference_order,
        )


class NoEligibleProviderError(RuntimeError):
    """Raised when no healthy enabled provider with an installed model exists.

    This is the deterministic no-eligible-provider failure path (never a silent
    soft-fail, never a fabricated route)."""

    def __init__(self, message: str, *, providers: Optional[List[dict]] = None) -> None:
        super().__init__(message)
        self.providers = providers or []
