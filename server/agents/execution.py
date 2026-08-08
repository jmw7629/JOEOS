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
from .tool_runner import parse_tool_calls, validate_and_execute

MAX_TOOL_ROUNDS = 2


class OllamaAgentExecutor:
    """Builds bounded messages for an agent run and calls the local Ollama
    runtime through the AI provider registry. Safe read-only tool calls
    proposed by the model are validated and executed in-process, and the
    result is returned to the model for a final answer."""

    def __init__(
        self,
        ai_service: AIService,
        *,
        default_model: str = "qwen2.5-coder:7b",
        max_tokens: int = 1200,
        principal: Optional[Dict] = None,
        control_service=None,
    ) -> None:
        self._ai = ai_service
        self._default_model = default_model
        self._max_tokens = max_tokens
        self._principal = principal
        self._control_service = control_service

    def _run_tool(self, tool_key: str, arguments: Dict) -> str:
        if self._control_service is None:
            raise RuntimeError("control service unavailable for tool execution")
        return validate_and_execute(
            tool_key, arguments, principal=self._principal or {}, service=self._control_service,
        )

    async def _complete(self, messages, tools, model) -> Dict[str, Any]:
        """One model call; if the model proposes safe tool calls, execute them
        and continue (bounded rounds), then return the final answer."""
        working = list(messages)
        token_usage = 0
        for _round in range(MAX_TOOL_ROUNDS + 1):
            if tools and _round < MAX_TOOL_ROUNDS:
                result = await self._ai.ollama_provider.infer_tool_call(
                    working, model=model, tools=tools, max_tokens=self._max_tokens,
                )
            else:
                result = await self._ai.ollama_provider.infer(
                    working, model=model, max_tokens=self._max_tokens,
                )
            token_usage += result.tokens_used or 0
            if result.finish_reason != "tool_calls":
                return {
                    "content": result.reply,
                    "token_usage": token_usage,
                    "model": result.model,
                }
            calls = parse_tool_calls(result.reply)
            if not calls:
                # The provider flagged tool_calls but returned no parseable
                # call; return the text as-is rather than fabricating.
                return {
                    "content": result.reply,
                    "token_usage": token_usage,
                    "model": result.model,
                }
            tool_messages = []
            for call in calls:
                try:
                    outcome = self._run_tool(call["name"], call["arguments"])
                except Exception as error:  # noqa: BLE001
                    outcome = "tool error: %s" % str(error)[:200]
                tool_messages.append({
                    "role": "tool",
                    "content": outcome[:4000],
                    "name": call["name"],
                })
            working = working + [{"role": "assistant", "content": result.reply},
                                 {"role": "user", "content": (
                                     "Tool results:\n" + "\n".join(
                                         "%s: %s" % (m["name"], m["content"]) for m in tool_messages
                                     )
                                 )}]
        # Final non-tool call to produce the answer.
        result = await self._ai.ollama_provider.infer(
            working, model=model, max_tokens=self._max_tokens,
        )
        token_usage += result.tokens_used or 0
        return {
            "content": result.reply,
            "token_usage": token_usage,
            "model": result.model,
        }

    async def __call__(self, messages: List[Dict[str, str]], tools: List[Dict], decision: Dict) -> Dict[str, Any]:
        model = decision.get("model") or self._default_model
        last_error = None
        # Bounded retry for transient model-load/connection races on the
        # memory-constrained VPS. Never retries semantic failures.
        for attempt in range(3):
            try:
                return await self._complete(messages, tools, model)
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
        # Unreachable in normal flow; defensive normalization.
        from server.actions.service import ActionDeniedError
        raise ActionDeniedError(500, "OLLAMA_ERROR", "Ollama inference failed.")


def _normalize_executor_error(error: Exception) -> Exception:
    from server.actions.service import ActionDeniedError
    text = str(error).lower()
    if "not reachable" in text or "connect" in text or "connection" in text:
        return ActionDeniedError(503, "OLLAMA_UNAVAILABLE",
                                 "Ollama is not reachable on the VPS loopback.")
    if "model" in text and ("not found" in text or "not installed" in text):
        return ActionDeniedError(404, "MODEL_NOT_FOUND",
                                 "The configured model is not installed in Ollama.")
    if "load" in text or "loading" in text or "terminated" in text or "killed" in text:
        return ActionDeniedError(503, "MODEL_LOADING",
                                 "The model could not stay loaded in Ollama (resource limit).")
    if "timeout" in text:
        return ActionDeniedError(504, "MODEL_TIMEOUT", "The model call timed out.")
    return ActionDeniedError(500, "OLLAMA_ERROR", "Ollama inference failed.")


def build_agent_executor(
    ai_service: AIService,
    *,
    default_model: str = "qwen2.5-coder:7b",
    principal: Optional[Dict] = None,
    control_service=None,
) -> OllamaAgentExecutor:
    return OllamaAgentExecutor(
        ai_service, default_model=default_model,
        principal=principal, control_service=control_service,
    )
