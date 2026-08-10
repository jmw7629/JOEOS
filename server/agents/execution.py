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
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Sequence

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
        # qwen2.5 sometimes emits the bare tool name (read_documentation) instead
        # of the registered key (joeos.read_documentation). Resolve unambiguously
        # when exactly one registered tool matches the short name.
        if tool_key not in ("joeos.read_documentation", "joeos.system_status",
                            "joeos.list_agents", "joeos.read_memory", "joeos.search_knowledge"):
            candidates = [
                t for t in ("joeos.read_documentation", "joeos.system_status",
                            "joeos.list_agents", "joeos.read_memory", "joeos.search_knowledge")
                if t.endswith("." + tool_key)
            ]
            if len(candidates) == 1:
                tool_key = candidates[0]
        return validate_and_execute(
            tool_key, arguments, principal=self._principal or {}, service=self._control_service,
        )

    def _provider_for(self, model: str):
        """Resolve the execution provider through the AI service router.

        Returns the provider instance for the resolved provider (Ollama today,
        Lemonade or future providers when healthy and eligible). Never guesses;
        the router raises deterministically when nothing is eligible."""
        selection = self._ai._resolve_assistant(model)
        provider = self._ai.providers.get(selection.provider_id)
        if provider is None:
            raise RuntimeError("Resolved provider %s is not registered." % selection.provider_id)
        return provider

    async def _model_call(self, provider, messages, *, model, tools, use_tools):
        """Invoke the provider for one turn, using structured tool-calling when
        the provider supports it, otherwise plain text (tool calls are then
        detected from the bounded content by parse_tool_calls)."""
        if use_tools and hasattr(provider, "infer_tool_call"):
            try:
                return await provider.infer_tool_call(
                    messages, model=model, tools=tools, max_tokens=self._max_tokens,
                )
            except (AttributeError, TypeError):
                pass
        return await provider.infer(
            messages, model=model, max_tokens=self._max_tokens,
        )

    async def _complete(self, messages, tools, model) -> Dict[str, Any]:
        """One model call; if the model proposes safe tool calls, execute them
        and continue (bounded rounds), then return the final answer."""
        provider = self._provider_for(model)
        working = list(messages)
        if tools and not any(m.get("role") == "system" for m in working):
            working = [{
                "role": "system",
                "content": (
                    "You may call the provided tools. To request a tool call, "
                    "respond with ONLY a JSON object of the form "
                    '{"name": "<tool>", "arguments": {...}}. When you have a '
                    "tool result, reply in plain natural language with the "
                    "answer; do not emit JSON."
                ),
            }] + working
        token_usage = 0
        for _round in range(MAX_TOOL_ROUNDS + 1):
            result = await self._model_call(
                provider, working, model=model, tools=tools,
                use_tools=bool(tools) and _round < MAX_TOOL_ROUNDS,
            )
            token_usage += result.tokens_used or 0
            # qwen2.5 emits tool calls as structured JSON in the content even
            # when the provider does not flag finish_reason=tool_calls, so we
            # detect parseable tool-call JSON whenever tools were offered.
            calls = parse_tool_calls(result.reply) if tools else []
            if not calls:
                return {
                    "content": result.reply,
                    "token_usage": token_usage,
                    "model": result.model,
                }
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
                                     ) + "\n\nNow answer the original request in plain natural language only. Do not emit JSON or a tool call."
                                 )}]
        # Final non-tool call to produce the answer.
        result = await provider.infer(
            working, model=model, max_tokens=self._max_tokens,
        )
        token_usage += result.tokens_used or 0
        return {
            "content": result.reply,
            "token_usage": token_usage,
            "model": result.model,
        }

    async def stream_events(self, messages: List[Dict[str, str]], tools: List[Dict], decision: Dict) -> AsyncIterator[Dict]:
        """Agentic chat stream for the persistent local assistant.

        Provider-neutral: the execution provider is resolved by capability
        through the AI service router (Ollama today, Lemonade or future
        providers when healthy and eligible). Mirrors the bounded ``_complete``
        tool loop but yields machine-readable events to the browser:
        ``{"kind": "tool", ...}`` when a safe tool runs, ``{"kind": "delta", ...}``
        while the final answer streams, and ``{"kind": "done", ...}``. All tool
        execution is the same schema-validated read-only ToolBroker path;
        nothing arbitrary runs."""
        model = decision.get("model") or self._default_model
        provider = self._provider_for(model)
        working = list(messages)
        if tools and not any(m.get("role") == "system" for m in working):
            working = [{
                "role": "system",
                "content": (
                    "You are the JoeOS local assistant running entirely on the "
                    "user's own local model runtime. Be concise, clear, and "
                    "natural. You may call the provided tools. To request a "
                    "tool call, respond with ONLY a JSON object of the form "
                    '{"name": "<tool>", "arguments": {...}}. When you have a '
                    "tool result, reply in plain natural language with the "
                    "answer; do not emit JSON."
                ),
            }] + working
        token_usage = 0
        for _round in range(MAX_TOOL_ROUNDS):
            result = await self._model_call(
                provider, working, model=model, tools=tools,
                use_tools=bool(tools),
            )
            token_usage += result.tokens_used or 0
            calls = parse_tool_calls(result.reply) if tools else []
            if not calls:
                break
            tool_messages = []
            for call in calls:
                try:
                    outcome = self._run_tool(call["name"], call["arguments"])
                except Exception as error:  # noqa: BLE001
                    outcome = "tool error: %s" % str(error)[:200]
                yield {
                    "kind": "tool",
                    "name": call["name"],
                    "arguments": call["arguments"],
                    "result": outcome[:1500],
                }
                tool_messages.append({
                    "role": "tool",
                    "content": outcome[:4000],
                    "name": call["name"],
                })
            working = working + [{"role": "assistant", "content": result.reply},
                                 {"role": "user", "content": (
                                     "Tool results:\n" + "\n".join(
                                         "%s: %s" % (m["name"], m["content"]) for m in tool_messages
                                     ) + "\n\nNow answer the original request in plain natural language only. Do not emit JSON or a tool call."
                                 )}]
        # Stream the final answer token-by-token so the assistant feels natural.
        async for delta in provider.stream_infer(
            working, model=model, temperature=0.4, max_tokens=self._max_tokens,
        ):
            yield {"kind": "delta", "content": delta}
        yield {"kind": "done", "model": model, "provider": provider.provider_id, "tokens_used": token_usage}

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


def build_ollama_tool_schemas(definitions: Sequence[Dict]) -> List[Dict]:
    """Convert safe tool definitions into Ollama ``tools`` schemas."""
    schemas = []
    for definition in definitions or ():
        schema = definition.get("input_schema") or {"type": "object", "properties": {}}
        schemas.append({
            "type": "function",
            "function": {
                "name": definition.get("key", ""),
                "description": definition.get("description", ""),
                "parameters": schema,
            },
        })
    return [s for s in schemas if s["function"]["name"]]
