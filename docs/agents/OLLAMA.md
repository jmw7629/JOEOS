# Ollama integration

## Runtime

- Version: 0.31.2 (systemd `ollama.service`)
- Bind: `127.0.0.1:11434` (loopback only). The public port was closed and the
  bind was changed from `0.0.0.0` to `127.0.0.1` during activation.
- JoeOS backend is the ONLY Ollama client. The browser never calls port 11434.

## Adapter

`server/ai/providers.py` -> `OllamaProvider` (an `InferenceProvider`):

- `version()` / `list_models()` / `is_loaded()` — runtime discovery.
- `infer()` — non-streaming chat with timeout + keep-alive.
- `stream_infer()` — NDJSON streaming.
- `infer_tool_call()` — structured tool schema; tool args are validated by the
  control plane before any invocation.
- `infer_json()` — native JSON mode.
- `set_health()` / `availability()` — measured (never fabricated).

Wired through `server/ai/service.py` (`AIService.probe_ollama`) into the AI
runtime and the control-plane executor (`server/agents/execution.py`).

## Agent executor

`server/agents/execution.py` `OllamaAgentExecutor`:

- Builds bounded messages from agent instructions + objective.
- Calls the bound model through the Ollama adapter.
- Maps failures to typed codes (`OLLAMA_UNAVAILABLE`, `MODEL_NOT_FOUND`,
  `MODEL_LOADING`, `MODEL_TIMEOUT`, `OLLAMA_ERROR`).
- Retries transient load/connection races (max 3 attempts, bounded backoff);
  never retries semantic failures.

## Health

`AIService.probe_ollama()` runs at backend startup and caches honest health on
the adapter. `/api/v1/ai/overview` and `/api/v1/command-center/overview` reflect
it. Unmeasured stays unknown.
