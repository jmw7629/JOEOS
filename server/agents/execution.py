"""Real AgentFabric execution engine.

Bridges the authoritative control plane (ActionService) to the local Ollama
runtime through the AI provider registry. The executor is the ONLY place a
model call happens for agent runs; results are persisted verbatim and runs
transition through the canonical state machine. No browser ever reaches Ollama;
this process is the single Ollama client.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Dict, List, Optional

from server.ai.service import AIService


class OllamaAgentExecutor:
    """Builds bounded messages for an agent run and calls the local Ollama
    runtime through the AI provider registry."""

    def __init__(
        self,
        ai_service: AIService,
        *,
        default_model: str = "qwen2.5-coder:7b",
        max_tokens: int = 1200,
    ) -> None:
        self._ai = ai_service
        self._default_model = default_model
        self._max_tokens = max_tokens

    async def __call__(self, messages: List[Dict[str, str]], tools: List[Dict], decision: Dict) -> Dict[str, Any]:
        model = decision.get("model") or self._default_model
        last_error = None
        # Bounded retry for transient model-load/connection races on the
        # memory-constrained VPS. Never retries semantic failures.
        for attempt in range(3):
            try:
                if tools:
                    result = await self._ai.ollama_provider.infer_tool_call(
                        messages, model=model, tools=tools, max_tokens=self._max_tokens,
                    )
                else:
                    result = await self._ai.ollama_provider.infer(
                        messages, model=model, max_tokens=self._max_tokens,
                    )
                return {
                    "content": result.reply,
                    "token_usage": result.tokens_used or 0,
                    "model": result.model,
                }
            except Exception as error:  # noqa: BLE001
                import logging as _logging
                _logging.getLogger("joeos.agent-executor").warning(
                    "ollama executor attempt %d failed: %s", attempt + 1, repr(error)[:300])
                last_error = error
                text = str(error).lower()
                transient = (
                    "not reachable" in text or "connect" in text or "connection" in text
                    or "disconnected" in text or "timeout" in text
                    or "load" in text or "loading" in text
                    or "terminated" in text or "killed" in text
                    or "server error" in text
                )
                if transient and attempt < 2:
                    await asyncio.sleep(2.0 * (attempt + 1))
                    continue
                raise
        error = last_error or RuntimeError("inference failed")
        try:
            raise error
        except Exception as exc:  # noqa: BLE001
            error = exc
            # Raise a typed, normalized failure the control plane can map.
            import logging as _logging
            _logging.getLogger("joeos.agent-executor").warning(
                "ollama executor failure: %s", repr(error)[:400])
            from server.actions.service import ActionDeniedError
            text = str(error).lower()
            if "not reachable" in text or "connect" in text or "connection" in text:
                raise ActionDeniedError(503, "OLLAMA_UNAVAILABLE",
                                        "Ollama is not reachable on the VPS loopback.") from error
            if "model" in text and ("not found" in text or "not installed" in text):
                raise ActionDeniedError(404, "MODEL_NOT_FOUND",
                                        "The configured model is not installed in Ollama.") from error
            if "load" in text or "loading" in text or "terminated" in text or "killed" in text:
                raise ActionDeniedError(503, "MODEL_LOADING",
                                        "The model could not stay loaded in Ollama (resource limit).") from error
            if "timeout" in text:
                raise ActionDeniedError(504, "MODEL_TIMEOUT",
                                        "The model call timed out.") from error
            raise ActionDeniedError(500, "OLLAMA_ERROR", "Ollama inference failed.") from error
        return {
            "content": result.reply,
            "token_usage": result.tokens_used or 0,
            "model": result.model,
        }


def build_agent_executor(ai_service: AIService, *, default_model: str = "qwen2.5-coder:7b") -> OllamaAgentExecutor:
    return OllamaAgentExecutor(ai_service, default_model=default_model)
