# Agent Fabric Activation Status

Date: 2026-08-08. Branch: `feature/agent-live-repair`.

## Goal

Make real Ollama-backed JoeOS agents operable from the deployed browser through
the authoritative Agent Fabric (control plane). The browser must never reach
Ollama directly; JoeOS is the single Ollama client.

## What is now real (verified)

| Layer | Status | Evidence |
| --- | --- | --- |
| Ollama runtime | READY | 0.31.2 on `127.0.0.1:11434` (loopback-only, public port closed); 10 models installed |
| Ollama provider adapter | DONE | `server/ai/providers.py` `OllamaProvider`: health, model discovery, chat, streaming, tool-call, JSON mode, timeout, keep-alive |
| ProviderRegistry | READY | Ollama registered as `local`/`private`/server-side provider; health measured on startup |
| ModelRegistry | READY | 10 installed models synced and bound; missing models disabled (never deleted) |
| Agent team | READY | Joe, Architect, Builder, Researcher, Verifier, Security — active, bound to real models |
| AgentRun execution | READY | `start_agent_run` + `execute_agent_run` run a REAL local model, persist bounded result, terminal state, provider/model attribution |
| Delegation | READY | `delegate_agent_run` creates a REAL child AgentRun (separate version/provider/model/result), depth-bounded |
| TaskGraph | READY | `create_task_graph` + `execute_task_graph`: dependency edges, ready-state, per-task child runs, failure propagation |
| Council | READY | Council members are REAL AgentRuns persisted as member runs |
| ToolBroker | READY | 5 safe read-only tools registered; agents only see authorized tools |
| Browser session | READY | Full WebCrypto device-enrollment ceremony: pair -> assign -> auth challenge -> session |
| Browser Agents UI | DONE | Agent Fabric console at `/os/agents`, `/os/providers`, `/os/models` wired to `/api/v1/control/*` |
| Canaries | PASS | Direct Architect run, Joe->Architect delegation, TaskGraph all pass with real local inference |

## Proven end-to-end paths

```
Browser (WebCrypto pair -> session)
  -> POST /api/v1/control/agents/{id}/runs   (objective)
  -> POST /api/v1/control/runs/{id}/execute  (real Ollama inference, persisted)
  -> GET  /api/v1/control/runs/{id}          (result survives reload)

Joe run -> POST /api/v1/control/runs/{id}/delegate -> Architect child run
TaskGraph -> POST /api/v1/control/runs/{id}/tasks -> execute -> per-task child runs
```

Verified in a real headless browser against a scratch backend + live Ollama:
pairing ceremony, session, Architect run (`provider=ollama`,
`model=qwen2.5-coder:1.5b`, `status=succeeded`), persisted result. The browser
made no request to port 11434.

## Resource reality (2 vCPU / 7.8 GiB)

- 14B models are OOM-killed at load; all production bindings use models that
  run here (7B family primary, 1.5B fallback).
- Model calls are sequential; `execute_agent_run`/`execute_task_graph` are
  bounded and the executor retries transient load/connection races.
- 7B model + resident browser on the same VPS is memory-tight; the backend
  canary (no browser) is stable, and the browser canary uses a small model.

## Not yet wired (deferred)

- Conversations UI bound to `/api/v1/conversations` (backend session-gated).
- The older multi-agent "org intelligence" workspace (`/api/v1/agents/*`)
  remains separate from the authoritative control plane.
- Live deployment of the new backend + console (integration into `ai-rebuild`
  pending; see deployment notes).

## P0 blockers resolved (from the live audit)

| Audit P0 | Resolution |
| --- | --- |
| No Ollama provider adapter | `OllamaProvider` in the AI provider layer, wired into the runtime and control-plane executor |
| ProviderRegistry empty | Ollama registered local/private/server-side on startup |
| ModelRegistry empty | 10 live models synced; missing models disabled, never deleted |
| Agents unbound | Joe/Architect/Builder/Researcher/Verifier/Security bound to models that run on this VPS |
| No real AgentRun execution | `execute_agent_run` runs a real local model and persists the result |
| Delegation missing | real child AgentRun per delegation, depth-bounded |
| TaskGraph partial | real executable task graph with per-task child runs |
| ToolBroker empty | 5 safe read-only tools registered |
| No browser session/auth | full WebCrypto device-enrollment ceremony -> legitimate application session |
| No browser Agents UI | Agent Fabric console at /os/agents, /os/providers, /os/models |

Deferred browser audit findings (not agent blockers) remain on the backlog:
CI/CD fabricated data, notification-center hardcoding, Back/Forward routing,
HSTS, and the older shell's other cosmetic issues.

## Deployment status

The feature branch `feature/agent-live-repair` is complete and pushed. The
canonical `ai-rebuild` checkout carries unrelated uncommitted P3G work in files
this branch also modifies (`joeos_backend.py`, `server/identity/*`). A safe
merge requires that P3G work to be committed or set aside first; the live
uvicorn on :8080 is the pre-activation backend and has not been restarted with
this code. Until then the new console runs on the scratch/dev backend; the
browser path is proven and reproducible.
