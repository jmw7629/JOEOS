# JoeOS Completion Matrix

Mission: **JOEOS-COMPLETION-LOOP-1** — Modular Command Center + Terminal + Agent
Team Experience + Module-Scoped Joe.

Statuses: `NOT_STARTED` `READY` `ACTIVE` `VERIFYING` `DONE` `BLOCKED` `HUMAN_REQUIRED`

Updated continuously by the autonomous completion loop.

## 0. Preserve & baseline

| ID | AREA | REQUIREMENT | DEP | STATUS | TEST | COMMIT |
|----|------|-------------|-----|--------|------|--------|
| M-001 | Baseline | Working tree preserved, rollback tag present | — | DONE | `git status` clean | 4a4350d |
| M-002 | Baseline | Completion matrix created | — | DONE | exists | — |
| M-003 | Baseline | Design rollback checkpoint for current Command Center generation | M-001 | READY | — | — |

## 1. Halo migration completion

| ID | AREA | REQUIREMENT | DEP | STATUS | TEST | COMMIT |
|----|------|-------------|-----|--------|------|--------|
| M-101 | Halo | Discovery: SSH, hostname, Tailscale identity, hardware | — | DONE | SSH verified | docs |
| M-102 | Halo | Ollama + Lemonade + model inventory on Halo | — | DONE | live probes | docs |
| M-103 | Halo | Capability benchmarks (context, tool-calling) | — | DONE | AX canaries | docs |
| M-104 | Halo | VPS rollback preserved (Section I backup + rollback tag) | — | DONE | integrity check | docs |
| M-105 | Halo | Canonical source migrated to Halo checkout | — | DONE | HEAD sync 4a4350d | push |
| M-106 | Halo | Persistent state migrated to Halo (cutover swap) | M-105 | DONE | boot 200, campaign 8/8 | — |
| M-107 | Halo | Halo runner reconnected to authoritative Halo backend | M-106 | DONE | heartbeat 2s | — |
| M-108 | Halo | ProviderRegistry active on Halo (Ollama + Lemonade) | M-106 | DONE | providers query | — |
| M-109 | Halo | Agents re-bound to Halo large models by capability | M-108 | DONE | agent→model audit | — |
| M-110 | Halo | /opt/joeos runner checkout updated to new code | M-105 | ACTIVE | HEAD check | — |
| M-111 | Halo | Secure Halo browser endpoint | M-106 | DONE | TLS probe | — |
| M-112 | Halo | Engineering Director/self-build validated on Halo | M-109 | DONE | workpackage run | — |
| M-113 | Halo | VPS stays intact as rollback node | M-104 | DONE | backend up | — |

## 2. Provider-neutral Joe

| ID | AREA | REQUIREMENT | DEP | STATUS | TEST | COMMIT |
|----|------|-------------|-----|--------|------|--------|
| M-201 | AI | Assistant routes via ProviderRegistry (Ollama OR Lemonade) | M-108 | DONE (D1) | routing test | — |
| M-202 | AI | Model assignment by capability (not size) | M-201 | DONE (D1) | binding audit | — |

## 3. Modular surface primitives

| ID | AREA | REQUIREMENT | DEP | STATUS | TEST | COMMIT |
|----|------|-------------|-----|--------|------|--------|
| M-301 | UX | Reusable module surface (compact/standard/expanded/focused/inspect) | — | DONE (D2/D3) | — | — |
| M-302 | UX | Module states (loading/empty/healthy/degraded/failed/offline) | M-301 | DONE (stateNote() system-wide) | jsdom | 213c7a8 |
| M-303 | UX | Break-open module→subcomponent→detail interaction | M-301 | DONE (command center focus + terminal tabs) | — | — |
| M-304 | UX | Layout persistence (arrangement/collapse/orb) | M-301 | DONE (D2) | — | — |

## 4. Module-scoped Joe

| ID | AREA | REQUIREMENT | DEP | STATUS | TEST | COMMIT |
|----|------|-------------|-----|--------|------|--------|
| M-401 | Joe | Scoped-context contract (JoeContextScope) | — | DONE | — | — |
| M-402 | Joe | Joe on every module; clear focus indicator | M-401 | DONE (command center + agents + automation + models + terminal) | — | — |
| M-403 | Joe | Scoped actions obey ToolBroker/policy/approval | M-401 | DONE (scope never grants authority) | — | — |
| M-404 | Joe | Global floating orb preserved | M-401 | DONE | — | — |

## 5. Desktop Command Center

| ID | AREA | REQUIREMENT | DEP | STATUS | TEST | COMMIT |
|----|------|-------------|-----|--------|------|--------|
| M-501 | UX | Multi-module desktop grid (ASK JOE, AGENTS, WORK, AUTOMATIONS, …) | M-301 | DONE | — | — |
| M-502 | UX | Focus mode (double-click/expand) | M-301 | DONE | — | — |
| M-503 | UX | Inspector (right-side) on desktop | M-301 | DONE (D3) | — | — |
| M-504 | UX | Compact navigation rail | M-501 | DONE | — | — |

## 6. Agent Command Center

| ID | AREA | REQUIREMENT | DEP | STATUS | TEST | COMMIT |
|----|------|-------------|-----|--------|------|--------|
| M-601 | Agents | Agent cards (compact + expanded break-open) | M-301 | DONE (D5 tabs) | — | — |
| M-602 | Agents | Org map (interactive hierarchy) | — | DONE | smoke | a63f9f3 |
| M-603 | Agents | Team overview (working/idle/waiting/failed/needs-Joe) | M-601 | PARTIAL (command center + agent_fabric) | — | — |
| M-604 | Agents | Agent detail sections (identity/state/model/tools/memory/…) | M-601 | DONE (D5 + delegation/results) | — | — |
| M-605 | Agents | Auto-discovery from authoritative AgentFabric | — | DONE | — | — |

## 7. Work / Automation / Pipelines

| ID | AREA | REQUIREMENT | DEP | STATUS | TEST | COMMIT |
|----|------|-------------|-----|--------|------|--------|
| M-701 | Work | Work board (BACKLOG→DONE) from real state | M-301 | DONE (unified, incl. FAILED) | — | — |
| M-702 | Work | Task card break-open (objective/agent/deps/graph/tools/…) | M-701 | PARTIAL (D4 DAG) | — | — |
| M-703 | Auto | Schedule/cron monitor (failing surfacing) | M-301 | DONE (filters + failures) | — | — |
| M-704 | Auto | Pipeline/DAG visualization (TaskGraph/automation/campaign) | M-301 | DONE (automation) | — | — |
| M-705 | Auto | Schedule pipeline (trigger→result) | M-703 | DONE (lifecycle view) | test_lifecycle | b1b5edb |

## 8. Memory / Files / Activity

| ID | AREA | REQUIREMENT | DEP | STATUS | TEST | COMMIT |
|----|------|-------------|-----|--------|------|--------|
| M-801 | Memory | Memory browser (all/shared/agent/proposed/conflicts/stale) | M-301 | DONE (views + cards + detail) | — | — |
| M-802 | Memory | Memory card break-open (content/source/provenance/…) | M-801 | DONE | — | — |
| M-803 | Files | Files OS module (recent/processing/failed + detail) | M-301 | DONE (Files view + detail + scoped Joe) | test_files | c13cbc7 |
| M-804 | Activity | Unified activity console (human-first, expandable detail) | M-301 | PARTIAL (agent_fabric) | — | a63f9f3 |
| M-805 | Activity | Live activity widget (persistent, filterable) | M-804 | PARTIAL | — | a63f9f3 |

## 9. Terminal

| ID | AREA | REQUIREMENT | DEP | STATUS | TEST | COMMIT |
|----|------|-------------|-----|--------|------|--------|
| M-901 | Term | Backend PTY gateway (authenticated) + WebSocket transport | — | DONE | — | — |
| M-902 | Term | xterm.js frontend /os/terminal workspace | M-901 | DONE | — | — |
| M-903 | Term | Session controls (tabs/resize/copy/paste/clear/search) | M-902 | DONE | — | — |
| M-904 | Term | Security: no agent shell, no bypass of ToolBroker/runner | M-901 | DONE | — | — |
| M-905 | Term | Scoped Joe for terminal (bounded context) | M-401 | DONE | — | — |
| M-906 | Term | Mobile terminal UX | M-902 | DONE | — | — |

## 10. Models / Compute / Approvals / Executions / Build

| ID | AREA | REQUIREMENT | DEP | STATUS | TEST | COMMIT |
|----|------|-------------|-----|--------|------|--------|
| M-1001 | Models | Model & compute module (providers/models/loads/queue/assignments) | M-301 | DONE (provider + model cards) | — | — |
| M-1002 | Models | Model card break-open + provider card | M-1001 | DONE | — | — |
| M-1003 | Apps | Approvals attention surface | M-301 | DONE (command center + agent view) | — | — |
| M-1004 | Apps | Executions module (proposal/policy/approval/runner/artifacts) | M-301 | DONE (agent view, break-open) | — | — |
| M-1005 | Build | Build JoeOS module (campaign/roadmap/packages/checkpoints) | M-301 | DONE (/os/build + module) | — | — |

## 11. Mobile

| ID | AREA | REQUIREMENT | DEP | STATUS | TEST | COMMIT |
|----|------|-------------|-----|--------|------|--------|
| M-1101 | Mobile | Mobile home (executive summary, no tiny cards) | M-501 | DONE | — | — |
| M-1102 | Mobile | Mobile agent view + kanban segments + vertical pipelines | M-601/M-701 | DONE | — | — |
| M-1103 | Mobile | Mobile terminal | M-906 | DONE (full-screen + touch keys) | — | — |

## 12. Global UX / polish

| ID | AREA | REQUIREMENT | DEP | STATUS | TEST | COMMIT |
|----|------|-------------|-----|--------|------|--------|
| M-1201 | UX | Global search across agents/tasks/automations/memory/files/… | M-501 | DONE | — | — |
| M-1202 | UX | Command palette | M-501 | DONE (required commands added) | — | — |
| M-1203 | UX | Shortcuts (no conflicts) | M-501 | PARTIAL | — | — |
| M-1204 | UX | Visual acceptance 1440x900 / 1024x768 / 390x844 | M-501 | NOT_STARTED | — | — |
| M-1205 | UX | Accessibility pass | M-501 | NOT_STARTED | — | — |
| M-1206 | QA | Full regression (backend/runner/frontend/E2E/security) | all | NOT_STARTED | — | — |
| M-1207 | QA | Final completion audit doc | all | NOT_STARTED | — | — |
