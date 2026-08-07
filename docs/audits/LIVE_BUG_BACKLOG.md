# Live Bug Backlog — JoeOS

Date: 2026-08-07. Every item is backed by live observation (browser, network, or
backend endpoint). Priorities: P0 blocks core agent use; P1 major functionality
broken; P2 incomplete but usable; P3 refinement; P4 future.

## P0 — blocks core JoeOS / agent use

### BUG-001 — Deployed browser has no Agents/Conversations/Memory UI
- Feature: Agents + Conversations + Memory + Files + Search workspaces
- Route: n/a (missing from deployed shell)
- Problem: The deployed `frontend_dist/index.html` is the old 17-item VPS shell.
  The newer frontend with these workspaces exists only in unmerged git branches
  (`feature/browser-memory-search-files`, `feature/browser-providers-integrations`,
  445KB). No agent/conversation surface is reachable in the live browser.
- Evidence: live HTML byte-identical to repo `frontend_dist/index.html`
  (sha256 `2635bd72…`); `git branch` shows the newer UI unmerged; `/os/memory`
  deep link → 404.
- Backend/frontend: **frontend** (deployment gap)
- Required fix: merge the newer browser workspaces into `ai-rebuild`, rebuild
  `frontend_dist/index.html`, redeploy; add `/os/*` SPA fallback to the backend.
- Dependency: BUG-002 (auth) and BUG-003 (provider/model) for agents to work;
  Memory/Files/Search work once UI is deployed.
- Acceptance: browser shows Agents, Memory, Files, Search; each lists real backend data.

### BUG-002 — No browser session/auth flow; session-gated APIs unreachable
- Feature: authentication / application sessions
- Route: `/api/v1/auth/*`, `/api/v1/control/*`, `/api/v1/conversations`
- Problem: `/api/v1/bootstrap` reports `application_authentication: "unavailable"`.
  The deployed shell has no login/pairing flow and never sends `X-Joeos-Session`.
  Conversations, control agents, runners, approvals, providers, models all return
  401 `session_required` from the browser.
- Evidence: curl without session → 401 on `/api/v1/conversations`, `/api/v1/control/agents`;
  `authority_application_sessions` = 0 in live DB.
- Backend/frontend: **both**
- Required fix: surface a browser pairing/session flow (challenge → solve →
  session) in the shell, or relax session requirement for browser-local use
  with an explicit owner-authenticated local mode. Do NOT weaken the signing
  ceremony.
- Dependency: BUG-001.
- Acceptance: browser can obtain a session and read agents/conversations.

### BUG-003 — Empty provider/model/tool registries; Ollama not wired
- Feature: ProviderRegistry / ModelRegistry / ToolBroker
- Route: n/a (no browser screen); backend `/api/v1/control/providers|models|tools`
- Problem: `control_providers=0`, `control_models=0`, `control_tools=0`. Ollama
  0.31.2 is running with 10 models, none registered. `server/ai/providers.py`
  ships only `LocalLemonadeProvider`; no `OllamaProvider` adapter. Agents are
  seeded with `provider=backend`/`model=backend` (unbound).
- Evidence: live DB counts; `ollama list` (10 models); `ollama ps` (service
  active); `/api/v1/ai/overview` provider_available=false.
- Backend/frontend: **backend**
- Required fix: add an `OllamaProvider` adapter to the AI provider registry;
  seed ProviderRegistry + ModelRegistry from the Ollama runtime; bind the 8
  engineering agents to a concrete local model (e.g. qwen2.5-coder:7b).
- Dependency: BUG-001/002 for browser visibility.
- Acceptance: browser Models & AI shows Ollama provider healthy and registered models;
  an agent run can select a local model.

### BUG-004 — Configured model runtime (Lemonade) offline
- Feature: local inference
- Route: Models & AI section; assistant
- Problem: The AI runtime targets Lemonade on the VPS loopback, which is not
  reachable, so chat and every AI-assisted surface fail honestly ("Lemonade
  offline"). No fallback to the running Ollama server exists.
- Evidence: `/api/v1/command-center/services` → `inference.lemonade` unavailable;
  assistant returns offline error; `ollama.service` active.
- Backend/frontend: **backend** (runtime/config)
- Required fix: either start/repair Lemonade or register an Ollama-backed
  provider so a working local model is available.
- Dependency: BUG-003.
- Acceptance: assistant answers a harmless prompt using a local model.

## P1 — major functionality broken

### BUG-005 — CI/CD Pipeline shows fabricated data
- Feature: CI/CD Pipeline
- Route: pipeline
- Problem: Runs/commits are hardcoded `INITIAL_RUNS`/`INITIAL_COMMITS` (fake
  authors, SHAs) and progress is simulated with `Math.random()` on a 5s timer.
  No runs API exists. The UI header admits "representative data", but the
  pipeline cards read as real CI.
- Evidence: `live_index.html` lines 4000-4014, 6722-6733; click "Run pipeline"
  produces no network call.
- Backend/frontend: **frontend**
- Required fix: either wire to a real runs/commits API (runner + git) or label
  the section as a demo preview with no fabricated status values.
- Acceptance: pipeline shows either real runs or an explicit non-production demo banner.

### BUG-006 — Browser Back/Forward leave the app
- Feature: navigation
- Route: all
- Problem: Single-URL SPA with no history integration; Back navigates the tab to
  `about:blank`. No route state in the URL.
- Evidence: Playwright `page.go_back()` → `about:blank`; `page.go_forward()` → app.
- Backend/frontend: **frontend**
- Required fix: hash or history-based routing with section state in the URL.
- Acceptance: Back returns to the previous section without leaving the app.

## P2 — incomplete but usable

### BUG-007 — Notification center is hardcoded
- Feature: notifications
- Route: all (bell)
- Problem: Notifications are static `useState` objects (n1/n2), never fetched;
  "Mark all read" only mutates local state.
- Evidence: `live_index.html` lines 6325-6332; no request on open.
- Backend/frontend: **frontend**
- Required fix: feed from `/api/v1/communications/notifications`.
- Acceptance: bell reflects real unread notifications.

### BUG-008 — Bot Fleet profiles are placeholders
- Feature: Bot Fleet
- Route: bots
- Problem: 6 profiles all report `status: running` with "CLI not detected";
  they describe external CLIs (Claude/Codex) rather than the authoritative
  agent registry. Start/Stop change local profile state only.
- Evidence: `/api/bots` payload; agent UI missing entirely.
- Backend/frontend: **frontend/backend**
- Required fix: after BUG-001, replace with the authoritative Agents registry.
- Acceptance: Bot Fleet (or Agents) reflects real agent state.

### BUG-009 — `/api/v1/memory/overview` returns 500
- Feature: Memory
- Route: `/api/v1/memory/overview`
- Problem: `MemoryService.overview()` never set `deletion_failures`,
  `documents_indexed`, `active_context_count`, so the pydantic model failed.
- Evidence: live curl → 500; fixed in working tree (`server/memory/service.py`);
  regression tests added (`tests/memory_test.py`).
- Backend/frontend: **backend**
- Required fix: **DONE in working tree** — populate the fields from authoritative
  state. Needs redeploy + restart to reach production.
- Acceptance: endpoint returns 200 with all typed fields.

### BUG-010 — HSTS missing
- Feature: transport security
- Route: all
- Problem: No `Strict-Transport-Security` header on the Tailscale origin
  (private Tailnet; P4 for a public origin, P2 hardening for this host).
- Evidence: curl headers.
- Backend/frontend: **backend (Caddy)** — deploy-side.
- Required fix: add HSTS via Caddy `header Strict-Transport-Security`.
- Acceptance: header present on subsequent requests.

## P3 — refinement / polish

- BUG-011: Mission "Executive readiness" score is a local formula, not a backend
  metric (fine, but label it as derived). frontend.
- BUG-012: Heading hierarchy is thin (single H1 + section titles) in the shell;
  workspace content headings vary. frontend.
- BUG-013: Mobile drawer duplicates all 17 items with a scroll; acceptable but
  could be grouped. frontend.
- BUG-014: `/api/v1/selfmaintenance/improvements/` GET list missing while the
  newer frontend expects it (add a list endpoint). backend.

## P4 — future integration

- BUG-015: Calendar/Email/Weather/Markets/Manufacturing/Social/Documents/Voice/
  Vision/Sites/Reality are documented future modules; absent by design.
- BUG-016: Postgres/pgvector/Redis migration (Phase 4).
- BUG-017: public-internet deployment would require auth + HSTS + CSRF + rate
  limits (bootstrap explicitly reports not public-ready).
