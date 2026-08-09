# Local AI Assistant

The floating Joe assistant (bottom-right FAB in the JoeOS shell) is the
canonical Joe experience: a persistent, agentic, natural-language chat window.

## Routing (as built 2026-08-09)

Browser → JoeOS backend → AgentFabric (`OllamaAgentExecutor`) + safe ToolBroker
tools → Ollama provider (VPS loopback `127.0.0.1:11434`).

- The browser never talks to Ollama or Lemonade directly (verified: no
  `127.0.0.1:11434` / `localhost:11434` references in any served HTML).
- Endpoints: `GET /api/v1/ai/chat/config`, `POST /api/v1/ai/chat/stream` (SSE).
- The assistant reuses the **same** agentic execution engine as ordinary
  AgentFabric runs (`server/agents/execution.py::OllamaAgentExecutor`), so it
  is not a competing AI architecture and its tool use is the same
  schema-validated read-only ToolBroker path.
- Safe tools offered: `joeos.system_status`, `joeos.list_agents`,
  `joeos.read_memory`, `joeos.search_knowledge`, `joeos.read_documentation`.

## Provider-neutrality debt (must be corrected during Halo integration)

The assistant currently selects its provider/model **statically** through the
`OllamaAgentExecutor` and `AIService.ollama_provider`. This is acceptable as the
temporary VPS implementation, but it MUST NOT become the permanent architecture.

The final Halo system must route:

```
Joe → ProviderRegistry / ModelRegistry → Ollama OR Lemonade
```

selected by **capability + routing policy** (model availability, tool calling,
context window, health), not by a hardcoded provider.

Required correction during Halo provider integration:

1. Replace direct `ollama_provider` / `OllamaAgentExecutor` selection in
   `server/ai/service.py::assistant_chat_stream` and
   `server/ai/service.py::assistant_config` with ProviderRegistry-backed
   resolution (provider + model chosen from `control_providers` /
   `control_models` and the AI provider registry by capability).
2. Make the agent executor a registry-resolved provider executor (a
   provider-neutral facade) so both agent runs and the assistant route through
   the same policy.
3. Keep the assistant on the safe read-only ToolBroker allowlist regardless of
   provider.
