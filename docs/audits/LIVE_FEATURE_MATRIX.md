# Live Feature Matrix — JoeOS

Date: 2026-08-07. Target: `https://mcso9tqzb9.tailb9395f.ts.net/`.

Classification legend:
- **REAL** — live backend/authoritative state behind the surface.
- **PARTIAL** — real backend but incomplete (e.g. honest-but-offline, missing data).
- **FIXTURE** — hardcoded/demo data presented as if real.
- **UI_ONLY** — no backend wiring.
- **MISSING** — not present in the deployed browser.
- **BLOCKED** — backend exists but unreachable from browser (session/registry).

## Deployed shell sections

| # | Section | Route (state) | Classification | Backend evidence |
| --- | --- | --- | --- | --- |
| 1 | Mission Control | mission | REAL (score derived) | `/api/metrics`, `/api/bots`, `/api/events`, `/api/workspace` |
| 2 | Executive Dashboard | dashboard | REAL | `/api/metrics` |
| 3 | CI/CD Pipeline | pipeline | **FIXTURE** | hardcoded `INITIAL_RUNS`/`INITIAL_COMMITS` + Math.random timer; no runs API |
| 4 | Infrastructure Health | infrastructure | REAL | `/api/metrics` (nodes) |
| 5 | Security & Logs | security | REAL | `/api/events` |
| 6 | Plugin Manager | plugins | REAL | `/api/v1/plugins`, `/api/v1/plugins/overview` |
| 7 | Automation | automation | REAL | `/api/v1/automation/overview`, `/workflows` |
| 8 | Communications | communications | REAL | `/api/v1/communications/overview`, `/notifications` |
| 9 | Device Manager | wearables | REAL | `/api/v1/wearables/devices` |
| 10 | Mobile Companion | mobile | REAL | `/api/v1/mobile/clients`, `/overview` |
| 11 | Security Center | security-center | REAL | `/api/v1/security/overview`, `/security-events` |
| 12 | Performance Center | performance | REAL | `/api/v1/performance/*` |
| 13 | Models & AI | ai | REAL | `/api/v1/ai/overview`, `/interpretations` |
| 14 | Production & Release | production | REAL | `/api/v1/production/*` |
| 15 | Maintenance & Improvement | selfmaintenance | REAL | `/api/v1/selfmaintenance/overview` |
| 16 | Settings | settings | REAL | `/api/workspace` |
| 17 | Bot Fleet | bots | REAL (placeholder profiles) | `/api/bots` |

## Audit-target modules (modern Command Center scope)

| Module | In deployed browser? | Backend API (live) | Classification |
| --- | --- | --- | --- |
| Command Center workspace | **MISSING** | `/api/v1/command-center/overview`, `/services`, `/activity` — 200, real | backend READY, frontend MISSING |
| Agents directory/workspace | **MISSING** | `/api/v1/agents/agents` (E2E Agent, offline), `/overview`, `/health` — 200 | backend PARTIAL (1 leftover test agent, no model), frontend MISSING |
| Control agents (P3G roles) | **MISSING** | `/api/v1/control/agents` — 401 (needs session) | backend READY, browser BLOCKED (no session) |
| Agent runs | **MISSING** | `/api/v1/control/agents/*/runs` — session-gated | BLOCKED |
| Conversations | **MISSING** | `/api/v1/conversations` — 401 | BLOCKED (no browser session/auth) |
| Task graphs | **MISSING** | `/api/v1/agents/missions/{id}/graph` — 200 | backend READY, frontend MISSING |
| Councils | **MISSING** | `/api/v1/councils` — 200 | backend READY, frontend MISSING |
| Memory | **MISSING** | `/api/v1/memory/records` — 200 (empty); `/memory/overview` — was 500, **fixed** | backend READY, frontend MISSING |
| Files | **MISSING** | `/api/v1/engineering/projects` — 200 (empty) | backend READY, frontend MISSING |
| Search | **MISSING** | `/api/v1/memory/search`, `/api/v1/intelligence/*` — 200 | backend READY, frontend MISSING |
| Context | **MISSING** | memory + intelligence endpoints | backend READY, frontend MISSING |
| Approvals | **MISSING** | `/api/v1/control/approvals` — session-gated | BLOCKED |
| Executions | **MISSING** | `/api/v1/control/runners/*` — session-gated | BLOCKED |
| Providers/Models screens | **MISSING** | `/api/v1/control/providers`, `/models` — session-gated; registries empty | BLOCKED |
| Integrations | **MISSING** | plugin/communications connectors | frontend MISSING |

## Platform primitives (backend, live-verified)

| Capability | Backend | Browser | Notes |
| --- | --- | --- | --- |
| Telemetry / metrics | READY | REAL | GPU honestly unmeasured |
| Audit events | READY | REAL | Security & Logs |
| Realtime SSE/WS | READY | PARTIAL | stream falls back to polling |
| Automation engine | READY | REAL | 1 workflow |
| Plugin platform | READY | REAL | 0 installed |
| Communications | READY | REAL | 3 notifications |
| Wearables | READY | REAL | simulator-only, honest |
| Mobile companion | READY | REAL | 1 paired client |
| Performance platform | READY | REAL | measured-only, honest |
| AI runtime | READY (empty) | REAL (offline) | Lemonade offline, 0 interpretations |
| Production/Release | READY | REAL | honest NOT_CONFIGURED gates |
| Self-maintenance | READY | REAL | 1 FAILED check (no backup) |
| **Agent registry (8 roles)** | READY (seeded) | **MISSING** | no UI |
| **Provider registry** | EMPTY | **MISSING** | no Ollama adapter |
| **Model registry** | EMPTY | **MISSING** | 10 Ollama models unregistered |
| **Tool broker** | EMPTY | **MISSING** | no tools |
| **Sessions/auth** | READY | **MISSING** | no browser login/pairing flow |

## Legacy / planned modules

| Module | In deployed browser? | Status |
| --- | --- | --- |
| Calendar | NOT_PRESENT | documented future |
| Email | NOT_PRESENT | connectors require approval (never claimed) |
| Weather / Markets / Manufacturing | NOT_PRESENT | explicitly "appear only after connectors are approved" |
| Social | NOT_PRESENT | — |
| Documents | NOT_PRESENT | — |
| Voice / Vision | NOT_PRESENT | camera/mic never default; no UI |
| Sites | NOT_PRESENT | — |
| Even Reality / G2 | NOT_PRESENT | — |
