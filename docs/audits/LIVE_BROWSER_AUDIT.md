# Live Browser Audit — JoeOS

Date: 2026-08-07.
Target: `https://mcso9tqzb9.tailb9395f.ts.net/` (Tailscale-private).
Method: headless Chromium (Playwright) driven exactly as a user would — every
nav section visited, every safe control clicked, network/console/page-error
streams captured, backend endpoints probed, and live DB state cross-checked.

## 1. Live URL status

| Check | Result |
| --- | --- |
| HTTP status (GET `/`) | **200 OK** |
| Content type | `text/html; charset=utf-8` (325,608 bytes single-file SPA) |
| Server | `uvicorn` behind Caddy (`via: 1.1 Caddy`) |
| TLS | Valid Let's Encrypt cert for `mcso9tqzb9.tailb9395f.ts.net` (issuer `YE1`, valid Jul 9 – Oct 7 2026) |
| Redirects | None (direct 200) |
| CSP | `default-src 'self'; script-src 'self' 'unsafe-inline' https://unpkg.com; connect-src 'self'; frame-ancestors 'none'` |
| HSTS | **Missing** — no `strict-transport-security` header |
| Frame policy | `X-Frame-Options: DENY` + `frame-ancestors 'none'` |
| Referrer policy | `no-referrer` |
| Permissions policy | `camera=(), geolocation=(), microphone=()` |
| Other | `X-Content-Type-Options: nosniff`, `COOP: same-origin`, `CORP: same-origin` |

The deployed application is identical (byte-for-byte) to the repository's
`frontend_dist/index.html` (SHA-256 `2635bd72…`). The serving backend runs from
`/home/joewillisny/JOEOS` (uvicorn PID 2385137, started Aug 5).

## 2. Headline finding: the deployed shell predates the modern Command Center

The **deployed browser application is the older 17-item "VPS Command Center"
shell**. It does **not** contain the newer Command Center workspace, Agents
workspace, Conversations, Memory, Files, Search, Context, Approvals, or
Executions modules described in `STATUS.md` and `AGENT_FABRIC_ACTIVATION.md`.

The newer browser frontend (with Agents / Memory / Files / Search / Context
workspaces, 445,332 bytes) exists in git but **only on unmerged feature
branches**:

- `feature/browser-memory-search-files` (HEAD `b786d1f`)
- `feature/browser-providers-integrations`

None of these branches has been merged into `ai-rebuild` (HEAD `df9898a`), and
`frontend_dist/index.html` is byte-identical to the older shell. The backend
has the full `/api/v1/*` surface those newer workspaces require (verified live:
`/api/v1/agents/*`, `/api/v1/memory/*`, `/api/v1/command-center/*` all exist and
respond), but the deployed UI never calls them.

**Consequence:** no Agent, Conversation, Memory, Files, Search, or TaskGraph
feature is reachable from the live browser today. The audit therefore audited
every surface the deployed app actually exposes and every backend endpoint it
can reach.

## 3. Deployed navigation inventory (17 sections)

All 17 are reachable via sidebar `nav-button`s and switch the same single-URL
SPA. `#` = route equivalent; navigation never changes `window.location` (state
only).

1. Mission Control
2. Executive Dashboard
3. CI/CD Pipeline
4. Infrastructure Health
5. Security & Logs
6. Plugin Manager
7. Automation
8. Communications
9. Device Manager
10. Mobile Companion
11. Security Center
12. Performance Center
13. Models & AI
14. Production & Release
15. Maintenance & Improvement
16. Settings
17. Bot Fleet

### Section classification (live evidence)

| Section | Classification | Evidence |
| --- | --- | --- |
| Mission Control | **REAL_BACKEND** (widgets composed from live metrics/runtime/security/bots) | Widget grid reads `/api/metrics`, `/api/bots`, `/api/events`, `/api/workspace`; "Executive readiness" score is a **local formula** `100 - urgent*22 - warning*9` (not a backend metric). |
| Executive Dashboard | **REAL_BACKEND** | CPU 95.7% / RAM 35.6% / disk 62% from `/api/metrics`; GPU correctly `UNMEASURED`; uptime 777,667s. |
| CI/CD Pipeline | **FIXTURE / FAKE** | Explicit header: "A functional local pipeline workspace using **representative data**." Runs (`BLD-2841`…`Maya Chen`, `Omar Reed`) are hardcoded `INITIAL_RUNS`; progress/success/failure is simulated with `Math.random()` on a 5s timer. No runs API exists. |
| Infrastructure Health | **REAL_BACKEND** | `/api/metrics` nodes + runtime; node correctly `DEGRADED` because Lemonade offline; CPU/DISK real. |
| Security & Logs | **REAL_BACKEND** | `/api/events` live event stream (40 events / 7 warnings / 0 errors); "Export evidence" uses live audit data. |
| Plugin Manager | **REAL_BACKEND** | `/api/v1/plugins`, `/api/v1/plugins/overview` — 0 installed, 0 quarantined. |
| Automation | **REAL_BACKEND** | `/api/v1/automation/overview` + `/workflows` — 1 workflow (`acme.api_comms`) enabled, health inactive. |
| Communications | **REAL_BACKEND** | `/api/v1/communications/overview` + `/notifications` — 3 unread (workflow-failed API alerts), real records. |
| Device Manager | **REAL_BACKEND** | `/api/v1/wearables/devices` — 5 paired simulator devices, all disconnected. Honest "only simulator produces devices". |
| Mobile Companion | **REAL_BACKEND** | `/api/v1/mobile/clients` + `/overview` — 1 paired client, 5 sessions, honest push=unregistered. |
| Security Center | **REAL_BACKEND** | `/api/v1/security/overview` + `/security-events`. |
| Performance Center | **REAL_BACKEND** | `/api/v1/performance/*` (overview, resources, queues, caches, models, leaks, budgets, regressions, traces, settings, benchmarks) — GPU stays `unknown`, load shedding active at 95.8% CPU. |
| Models & AI | **REAL_BACKEND** | `/api/v1/ai/overview` — Lemonade offline, no model, 0 interpretations. Honest. |
| Production & Release | **REAL_BACKEND** | `/api/v1/production/*` — v2.0.0 dirty build, 13 release gates 0 blocking, 0 backups verified. Honest `NOT_CONFIGURED` gates. |
| Maintenance & Improvement | **REAL_BACKEND** | `/api/v1/selfmaintenance/overview` — 7 health checks, 1 FAILED (verified backup missing), improvement proposal `create_backup` proposed, maintenance log real. |
| Settings | **REAL_BACKEND** | Appearance/accessibility prefs persisted via `/api/workspace` (theme, density, effects, motion, contrast, font scale). |
| Bot Fleet | **REAL_BACKEND** | `/api/bots` — 6 profiles (Claude, Codex, Event Sentry, Lemonade Copilot, +2), all `running` status but "CLI not detected". |

### Fake-data items found

| Surface | Type | Location/evidence |
| --- | --- | --- |
| CI/CD Pipeline runs & commits | **HIGH** — hardcoded demo data | `INITIAL_RUNS`, `INITIAL_COMMITS` (lines 4000-4014); 5s `Math.random()` progress simulation (lines 6722-6733); authors `Maya Chen`, `Omar Reed`, etc. No API backs it. UI admits "representative data". |
| Notification center | **MEDIUM** — hardcoded | `useState` with static `n1`/`n2` objects (lines 6325-6332); never fetched. |
| Mission "Executive readiness" score | **LOW** — derived, not fetched | Formula `100 - urgent*22 - warning*9` over live metrics. |

All other surfaces were verified to be driven by real `/api/*` state.

## 4. Command palette (Ctrl/Cmd+K)

Working. Results grouped COMMANDS / NAVIGATION / SECURITY. Tested search ("bots")
returns real palette items. Commands observed: add widget, ask JoeOS AI, customize
mission control, edit layout, focus content, open each workspace, open settings,
open security center, open keyboard shortcuts. No privileged commands exposed.
Classified: **WORKING** for navigation, **PARTIAL** (no object/agent/conversation
results because those workspaces don't exist in this shell).

## 5. Navigation & routing audit

| Behavior | Result |
| --- | --- |
| Sidebar section switching | WORKING (all 17) |
| Command palette → section | WORKING |
| Mobile drawer | WORKING (390×844; opens/closes, all 17 items) |
| Browser Back | **BROKEN** — single-URL SPA; Back leaves the app entirely (`about:blank`) |
| Browser Forward | **BROKEN** — same as Back (no history integration) |
| Deep-link `/os/bots`, `/os/memory` | **404** — backend has no `/os/*` SPA fallback in the deployed build |
| Refresh | WORKING (section restored from `localStorage` last-section) |
| Alt+1..0 section shortcuts | WORKING (source-verified + palette) |
| Ctrl+, settings / Ctrl+Shift+N notifications / Ctrl+Shift+K assistant / Ctrl+G palette | WORKING |
| Notification center | PARTIAL — panel opens, "Mark all read" works locally, but data is hardcoded |

## 6. Assistant / chat

The assistant panel (Ctrl+Shift+K) is **honest but non-functional end-to-end**:
sending the audit prompt returned "I couldn't complete that local request:
Lemonade Server is offline." The chat path does not reach AgentFabric or a model
because the configured local provider (Lemonade on the VPS loopback) is offline
and no Ollama adapter is registered. No message persisted; there are 0
conversations in the live DB.

## 7. Responsive audit

| Viewport | Horizontal overflow | Sidebar | Mobile menu | Console errors |
| --- | --- | --- | --- | --- |
| 390×844 | 0 | hidden (drawer) | yes | 0 |
| 1024×768 | 0 | visible | yes | 0 |
| 1440×900 | 0 | visible | yes | 0 |

No clipping, overflow, or tiny-target regression observed in the shell.

## 8. Accessibility spot check

- 37 buttons, **0 unnamed** (all have text or aria-label/title).
- Headings: H1 + H2 only in shell (workspace sections render content headings; the SPA uses one H1 + section titles).
- Inputs: 1 (assistant/command input) with label.
- Skip-to-content link present (`#main-content`).
- Focus trap + Escape-to-close implemented for palette/drawer/dialogs.
- Color-only states mitigated by non-color labels on status badges in the shell.

## 9. Console / network / page-error audit

Across a full 15-section crawl: **0 console errors, 0 page errors, 0 failed
requests, 0 HTTP ≥400 responses.** The deployed shell is stable and has no
JS/network breakage. (The only runtime errors observed live are the expected
"Lemonade offline" assistant failure.)

Backend endpoint status sweep (GET): all `/api/v1/*` overview endpoints used by
the shell return 200. Exceptions:

| Endpoint | Status | Cause |
| --- | --- | --- |
| `/api/v1/memory/overview` | **500** (pre-fix) | `MemoryOverview` model requires `deletion_failures`, `documents_indexed`, `active_context_count` which `MemoryService.overview()` never set. **Fixed in working tree; tests added.** |
| `/api/v1/selfmaintenance/improvements/` | 404 | GET list route does not exist (only POST `.../improvements/{id}/apply`). Referenced by the unmerged newer frontend. |
| `/api/v1/performance/actions` | 405 | POST-only by design. |
| `/api/v1/control/*`, `/api/v1/conversations`, `/api/v1/runner/*` | 401 | Session-gated (see §11). |

## 10. Realtime

The shell subscribes through the SDK (`/sdk/index.js`, `subscribeEvents`); the
stream status falls back to "polling" gracefully. `/api/events` polling works.
WebSocket/SSE resumable stream is reported healthy by `/api/v1/command-center/services`.
No disconnects observed.

## 11. Agent + conversation reachability (the central gap)

The live backend exposes a large authoritative agent surface
(`/api/v1/control/agents`, `/api/v1/agents/*`, `/api/v1/conversations`,
`/api/v1/councils`, `/api/v1/control/runners`), and the live DB contains the 8
P3G engineering role agents. **But:**

1. **No browser UI calls them.** The deployed shell's only agent surface is
   "Bot Fleet" (`/api/bots`), which shows CLI-profile placeholders, not the
   authoritative agent registry.
2. **No browser session exists.** `control/agents`, `conversations`, and
   `control/runners` require `X-Joeos-Session`, and the deployed app never
   creates one. `/api/v1/bootstrap` reports
   `application_authentication: "unavailable"` — there is no browser login/pairing
   flow in the deployed shell.
3. **Provider/Model/Tool registries are empty.** Live DB:
   `control_providers=0`, `control_models=0`, `control_tools=0`,
   `conversations=0`, `control_agent_runs=0`. Ollama has 10 models installed and
   running, but no Ollama provider adapter is registered in `server/ai/providers.py`
   (only `LocalLemonadeProvider`), so no agent can be assigned a working model.
4. The 8 engineering agents (`engineering.director`, `architect`, `builder`,
   `verification`, `applebuild`, `securityreviewer`, `release`, `watchdog`) are
   seeded and active with `provider=backend`, `model=backend` (no concrete
   binding) — they cannot run inference until a provider/model is registered.

See `AGENT_READINESS.md` for the full matrix and `LIVE_BUG_BACKLOG.md` for the
prioritized backlog.

## 12. Verdict

**CAN JOE USE AGENTS PRODUCTIVELY TODAY? NO.**

The deployed browser has no Agents/Conversations/Memory/Files UI, no browser
authentication, an empty provider/model registry, and an offline configured
model runtime. The backend agent platform exists and is healthy, but none of it
is reachable or operable from the deployed browser today.
