# Agent Activation Execution Plan

Date: 2026-08-07. Derived from the live audit, not from old phase numbers. The
goal is the shortest dependency-ordered sequence that makes Joe's agents
genuinely usable in the browser.

## Current blockers (evidence-based)

1. Deployed browser = old 17-item VPS shell; no Agents/Conversations/Memory UI.
2. No browser session/auth flow → all session-gated agent APIs unreachable.
3. Provider/Model/Tool registries empty; no Ollama provider adapter; Lemonade offline.
4. 8 engineering agents seeded but unbound (`provider=backend`, `model=backend`).

## Sequence (dependency-ordered)

### MUST FIX NOW

1. **Register a local provider + models.** Add an `OllamaProvider` adapter to
   `server/ai/providers.py` (or bind the existing AI registry to Ollama), seed
   `control_providers` + `control_models` from `ollama list` (10 models), and
   mark health from `ollama ps`. This is the foundation — nothing can run without
   a working model.
   - Acceptance: `/api/v1/ai/overview` reports a healthy provider and models;
     `/api/v1/control/models` lists the 10 Ollama models.
   - Verifies: browser Models & AI shows real provider health.

2. **Deploy the modern browser frontend.** Merge the Agents/Memory/Files/Search
   workspaces from `feature/browser-memory-search-files` (and
   `feature/browser-providers-integrations`) into `ai-rebuild`, rebuild
   `frontend_dist/index.html`, and add the `/os/*` SPA fallback to the backend
   (`@app.get("/os/{path:path}")` → `frontend()`).
   - Acceptance: browser shows Agents, Memory, Files, Search, Context; deep
     links like `/os/memory` load.
   - Verifies: Agents page loads and lists the 8 engineering roles.

3. **Wire a browser session.** Add a pairing/session flow to the shell that
   calls `/api/v1/auth/challenge` + `/api/v1/auth/session` (owner device-key
   ceremony, local console bootstrap already establishes the owner), stores the
   session id, and sends `X-Joeos-Session` on API calls. Keep the signing
   ceremony intact; do not add an unauthenticated bypass.
   - Acceptance: browser can list conversations and control agents.

4. **Bind agents to the local model.** Update the 8 engineering agents to a
   concrete model (e.g. `qwen2.5-coder:7b`) under the Ollama provider, and
   register ToolBroker tools they are allowed to use (read-only engineering
   tools first).
   - Acceptance: Architect shows a working model + read tools.

5. **Start a real run from the browser.** Verify AgentProfile → AgentRun → model
   → result through the browser with a safe read-only objective ("Describe your
   configured role; do not modify anything").
   - Acceptance: run persists in `control_agent_runs`, browser shows real state.

### NEXT

6. **Conversations path.** Exercise browser chat against the registered local
   provider; confirm streaming, persistence, provider/model display, stop/retry.
7. **Task graphs.** Create a small mission from the browser and render
   `/agents/missions/{id}/graph`; confirm nodes/edges/state.
8. **Delegation.** Orchestrator → Architect delegation; prove parent+child runs
   persisted with a real delegation relationship.
9. **Memory/Context from the browser.** Create a memory record, see it in the
   Memory workspace, verify provenance; fix the `/memory/overview` 500 in
   production (fix is already in the working tree).
10. **Councils.** Run a harmless read-only council and render the structured result.

### LATER

11. ToolBroker write/approval path and browser Approvals UI.
12. Executions UI bound to the connected runner (approval-gated).
13. File/engineering workspace with the bounded filesystem + secret-masked preview.
14. Universal Search across memory + project files (already exists in the newer
    frontend branch).
15. CI/CD Pipeline wired to real runner git operations (replace fabricated data).
16. Notifications fed from real `/api/v1/communications/notifications`.
17. HSTS, session rotation, public-internet hardening only if the Tailnet is
    ever exposed publicly.

## Do-not-do list (this phase)

- Do not add a second agent framework.
- Do not weaken the approval/device-key signing ceremony.
- Do not expose raw shell/SSH executors to agents.
- Do not fabricate provider/model/run state.
- Do not deploy to the public internet.
