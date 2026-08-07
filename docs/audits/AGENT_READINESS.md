# Agent Readiness — JoeOS (evidence-based)

Date: 2026-08-07. Target: live app + live backend + live DB + Ollama runtime.

## Readiness matrix

Legend: READY / PARTIAL / BLOCKED / MISSING.

| Layer | Status | Evidence (live, 2026-08-07) |
| --- | --- | --- |
| Provider layer | **BLOCKED** | `control_providers` table: 0 rows. `server/ai/providers.py` registers only `LocalLemonadeProvider`; no Ollama adapter. `/api/v1/ai/overview`: `provider_available=false` ("Lemonade Server is offline."). |
| Model layer | **BLOCKED** | `control_models`: 0 rows. Ollama 0.31.2 running with **10 models installed** (`qwen2.5-coder:7b`, `14b`, `1.5b`, `deepseek-r1:14b`, agentic/safe variants) — none registered in ModelRegistry, none visible to the browser. |
| Agent profiles | PARTIAL | 8 P3G engineering agents seeded + active in `control_agents` (director, architect, builder, verification, applebuild, securityreviewer, release, watchdog), all `provider=backend`, `model=backend` (no concrete binding). Separate `server/agents/` platform has 1 leftover "E2E Agent" (configured, offline, no model) + 1 mission ("E2E", draft). No agent can currently be assigned a working model. |
| Agent runs | **BLOCKED** | `control_agent_runs`: 0. `/api/v1/control/agents` requires `X-Joeos-Session`; no browser session exists and no UI calls it. |
| Conversations | **BLOCKED** | `conversations`: 0, `conversation_messages`: 0. `/api/v1/conversations` → 401 session_required. Browser assistant returns "Lemonade Server is offline." No message persists. |
| Task graphs | PARTIAL | `/api/v1/agents/missions/{id}/graph` exists and responds 200; only 1 draft mission exists; no browser TaskGraph UI in deployed shell. |
| Delegation | **MISSING** | No parent/child delegation path wired to the browser; no runs exist to delegate. Backend `control_agent_runs` has delegation fields but zero runs. |
| Councils | PARTIAL | `/api/v1/councils` responds 200 (0 runs). No browser Council UI in the deployed shell. |
| Tools | **BLOCKED** | `control_tools`: 0 rows. ToolBroker registry empty; Bot Fleet "profiles" are placeholders ("CLI not detected"). |
| Memory/context | PARTIAL | Memory backend READY (`/api/v1/memory/records` 200, 0 records; `/memory/overview` 500 → **fixed**). No browser Memory/Context UI in the deployed shell. |
| Actions/approvals | **BLOCKED** | `/api/v1/control/approvals` session-gated; `control_action_proposals` 0; no browser Approvals UI in the deployed shell. |
| Executions | **BLOCKED** | `/api/v1/control/runners/*` session-gated; `runner_execution_jobs` 0. Runner daemon is connected (active connection, healthy heartbeats) but no browser surface in the deployed shell. |
| Realtime | PARTIAL | Resumable stream healthy; shell falls back to polling; only `/api/events` consumed by the old shell. |
| Browser Agent UI | **MISSING** | Deployed shell has only "Bot Fleet" (profiles from `/api/bots`). No Agents/Conversations/Runs/Approvals workspaces; the newer frontend exists only in unmerged git branches. |

## Ollama ↔ registry mismatch table

| Ollama model (installed) | In ModelRegistry | In ProviderRegistry | Assignable to an agent | Used by browser chat |
| --- | --- | --- | --- | --- |
| qwen2.5-coder:1.5b | no | no | no | no |
| qwen2.5-coder:7b | no | no | no | no |
| qwen2.5-coder:14b | no | no | no | no |
| qwen2.5-coder:1.5b-fast | no | no | no | no |
| qwen2.5-coder:7b-opencode-safe | no | no | no | no |
| qwen2.5-coder:7b-agentic | no | no | no | no |
| qwen2.5-coder:1.5b-opencode-safe | no | no | no | no |
| qwen2.5-coder:14b-agentic | no | no | no | no |
| deepseek-r1:14b | no | no | no | no |
| deepseek-r1:14b-agentic | no | no | no | no |

Mismatch class: **installed in Ollama but registered nowhere**. The browser's
Models & AI screen reports "Lemonade offline / no model selected"; it never
mentions Ollama.

## Session/authentication gap

`/api/v1/bootstrap` (live):
`application_authentication: "unavailable"`, `device_enrollment: operator_pairing_v1`,
`role_based_access: "unavailable"`, `privileged_actions: "unavailable"`,
`public_internet_ready: false`.

Sessions are created only through the challenge/solve ceremony
(`POST /api/v1/auth/challenge`, `POST /api/v1/auth/session`) with device-key
pairing. The deployed browser shell implements **no login/pairing flow** and
never sends `X-Joeos-Session`. Every session-gated API (control agents,
conversations, runners, approvals, providers, models) is therefore unreachable
from the deployed browser today.

## Verdict

**CAN JOE USE AGENTS PRODUCTIVELY TODAY? NO.**

What blocks it, in order:
1. No browser Agents/Conversations UI deployed (frontend gap).
2. No browser session/auth flow (session gap).
3. Empty Provider/Model/Tool registries + no Ollama adapter (runtime gap).
4. Configured model runtime (Lemonade) offline (runtime gap).

The backend agent platform is real and healthy; it is simply not wired into the
deployed browser, not bound to a provider/model, and not reachable without a
session.
