# Agent Workspace Audit

Audit of the real JoeOS agent backend so the browser OS Agents workspace is built
against true contracts, not invented ones. Verified against the `ai-rebuild` codebase
at base commit `df9898a` (P4-UI-C worktree `feature/browser-agent-workspaces`).

## Summary

The agent domain is a full production multi-agent orchestration system, not a stub:

- **REST surface**: `server/agents/router.py` (`APIRouter(prefix="/api/v1", tags=["agents"])`)
  mounted in `joeos_backend.py` at line 1323 (`app.include_router(agents_router)`).
- **70+ endpoints** covering organization, agents, missions (runs), task graphs,
  collaboration (messages, handoffs, artifacts), governance (gates, reviews,
  disagreements, consensus, debates, consultations, escalations, interventions,
  approvals), model routing, detections, performance, and org memory.
- **Typed models**: `server/agents/models.py` — every record is a `StrictAgentModel`
  with strict string enums for states.
- **Persistence**: SQLite via `AgentsStorage`, data dir = `<db parent>/agents`.
  `AgentsService` constructed in `joeos_backend.py` (line 1118) with
  `governance_blocked` wired from the security service.
- **Realtime**: NO SSE/WebSocket in the agents module. Agents are purely request/response.
  For live task-graph state the UI must poll (activity feed, health, mission envelope/graph)
  or subscribe to the conversations SSE stream.

## Security finding (important)

- **No session auth on any agent route.** `server/agents/` contains zero
  `require_application_session` / `X-JoeOS-Session` references (conversations router does use it).
  Every agent endpoint only depends on `get_agents_service(request)`.
- Mitigation today: the public funnel is gated by Caddy basic_auth, and the tailnet
  restricts direct access. But a process on localhost:8080 can read/write all agent data.
- Recommended follow-up (out of scope for this UI phase): protect `/api/v1/agents/*`
  with `require_application_session` to match the conversations router.

## Endpoint map (`/api/v1/agents/*`)

### Overview / health
| Method | Path | Returns |
|---|---|---|
| GET | `/agents/overview` | `AgentsOverview` (org + units + roles + agents + missions + health + attention) |
| GET | `/agents/health` | `OrgHealthRecord` |
| GET | `/agents/activity?limit=` | `List[dict]` activity feed |
| GET | `/agents/storage` | `dict` storage stats |
| POST | `/agents/backup` | `dict {backup_created, path}` |

### Organization
| Method | Path | Returns |
|---|---|---|
| POST | `/agents/organization` | `OrganizationRecord` (201) |
| GET | `/agents/organization` | `OrganizationRecord` |
| POST | `/agents/units` | `OrganizationalUnit` (201) |
| GET | `/agents/units` | `List[dict]` |
| POST | `/agents/roles` | `RoleDefinition` (201) |
| GET | `/agents/roles` | `List[dict]` |

### Agents
| Method | Path | Returns |
|---|---|---|
| POST | `/agents/agents` | `AgentProfile` (201) |
| GET | `/agents/agents?status=&availability=` | `List[dict]` |
| GET | `/agents/agents/{agent_id}` | `AgentProfile` |
| POST | `/agents/agents/{agent_id}/availability` | `AgentProfile` |

### Missions (runs)
| Method | Path | Returns |
|---|---|---|
| POST | `/agents/missions` | `MissionRecord` (201) |
| GET | `/agents/missions?status=&limit=` | `List[dict]` |
| GET | `/agents/missions/{mission_id}` | `MissionRecord` |
| GET | `/agents/missions/{mission_id}/envelope` | `MissionEnvelope` (mission + charter + plan + graph) |
| POST | `/agents/missions/{mission_id}/charter` | `MissionCharter` (201) |
| POST | `/agents/missions/{mission_id}/approve` | `MissionRecord` |
| POST | `/agents/missions/{mission_id}/start` | `MissionRecord` |
| POST | `/agents/missions/{mission_id}/plan` | `MissionPlan` |
| GET | `/agents/missions/{mission_id}/plan` | `MissionPlan` |
| GET | `/agents/missions/{mission_id}/graph` | `dict` (TaskGraph) |
| POST | `/agents/missions/{mission_id}/tasks` | `MissionTask` (201) |
| GET | `/agents/missions/{mission_id}/tasks` | `List[dict]` |

### Tasks
| Method | Path | Returns |
|---|---|---|
| GET | `/agents/tasks/{task_id}` | `MissionTask` |
| POST | `/agents/tasks/{task_id}/assign` | `MissionTask` (payload includes agent + explanation) |
| POST | `/agents/tasks/{task_id}/state` | `MissionTask` |

### Collaboration
| Method | Path | Returns |
|---|---|---|
| POST | `/agents/messages` | `CollaborationMessage` (201, body is full model) |
| GET | `/agents/messages?mission_id=&task_id=&limit=` | `List[dict]` |
| POST | `/agents/handoffs` | `HandoffRecord` (201, body is full model) |
| POST | `/agents/handoffs/{handoff_id}/respond` | `HandoffRecord` |
| GET | `/agents/handoffs?mission_id=&state=` | `List[dict]` |
| POST | `/agents/artifacts` | `ArtifactRecord` (201, body is full model) |
| GET | `/agents/artifacts?mission_id=&task_id=` | `List[dict]` |
| POST | `/agents/artifacts/{artifact_id}/validate` | `ArtifactRecord` |

### Governance
| Method | Path | Returns |
|---|---|---|
| POST | `/agents/gates` | `QualityGate` (201, body is full model) |
| GET | `/agents/gates?mission_id=` | `List[dict]` |
| POST | `/agents/reviews` | `ReviewRecord` (201, body is full model) |
| POST | `/agents/reviews/{review_id}/complete` | `ReviewRecord` |
| GET | `/agents/reviews?mission_id=&task_id=&status=` | `List[dict]` |
| POST | `/agents/disagreements` | `DisagreementRecord` (201, body is full model) |
| POST | `/agents/disagreements/{disagreement_id}/resolve` | `DisagreementRecord` |
| GET | `/agents/disagreements?mission_id=&state=` | `List[dict]` |
| POST | `/agents/consensus` | `ConsensusResult` (201, body is full model) |
| GET | `/agents/consensus` | `List[dict]` |
| POST | `/agents/debates` | `DebateRecord` (201, body is full model) |
| POST | `/agents/debates/{debate_id}/advance` | `DebateRecord` |
| POST | `/agents/debates/{debate_id}/conclude` | `DebateRecord` |
| POST | `/agents/consultations` | `ConsultationRecord` (201, body is full model) |
| POST | `/agents/consultations/{consultation_id}/respond` | `ConsultationRecord` |
| POST | `/agents/escalations` | `EscalationRecord` (201, body is full model) |
| POST | `/agents/escalations/{escalation_id}/resolve` | `EscalationRecord` |
| GET | `/agents/escalations?mission_id=&state=` | `List[dict]` |
| POST | `/agents/interventions` | `InterventionRecord` (201, body is full model) |
| POST | `/agents/interventions/{intervention_id}/respond` | `InterventionRecord` |
| GET | `/agents/interventions?mission_id=&state=` | `List[dict]` |
| POST | `/agents/approvals` | `ApprovalRecord` (201, body is full model) |
| POST | `/agents/approvals/{approval_id}/approve` | `ApprovalRecord` |
| POST | `/agents/approvals/{approval_id}/deny` | `ApprovalRecord` |
| GET | `/agents/approvals?mission_id=&state=` | `List[dict]` |

### Routing / detection / memory / performance
| Method | Path | Returns |
|---|---|---|
| POST | `/agents/routes` | `ModelRoute` (201) |
| GET | `/agents/routes` | `List[dict]` |
| POST | `/agents/detections/scan/{mission_id}` | `List[dict]` |
| GET | `/agents/detections?mission_id=&state=` | `List[dict]` |
| POST | `/agents/detections/{detection_id}/resolve` | `DetectionEvent` |
| GET | `/agents/performance/{agent_id}` | `dict` |
| POST | `/agents/memory-proposals` | `OrgMemoryProposal` (201, body is full model) |
| GET | `/agents/memory-proposals` | `List[dict]` |
| POST | `/agents/memory-proposals/{proposal_id}/review` | `OrgMemoryProposal` |

## State enum reference (for UI badges/colors)

- **AgentState** (lines 36-39): agent lifecycle.
- **Availability**: `available|busy|blocked|paused|offline|unavailable`.
- **MissionState** (lines 42-47): mission lifecycle.
- **Mission progress**: `not_started|planning|staffed|executing|waiting|blocked|reviewing|validating|completing|complete|incomplete|failed|cancelled`.
- **Mission health**: `healthy|attention|degraded|blocked`.
- **MissionPriority**: `urgent|high|normal|low|backlog`.
- **TaskState** (lines 54-58): task lifecycle.
- **TaskRisk**: `low|medium|high|critical`.
- **GateState**: `not_ready|ready|in_review|passed|passed_with_conditions|failed|waived|expired|blocked|cancelled`.
- **ApprovalState**: `pending|approved|denied|expired|cancelled`.
- **HandoffState**: `sent|accepted|clarification_requested|rejected|escalated|cancelled`.
- **DebateState**: `open|in_progress|synthesized|cancelled|concluded`.
- **OrgHealthState**: `healthy|attention_required|degraded|blocked|resource_constrained|partially_unavailable|unavailable|unknown`.

## Key aggregate models for the UI

- **`AgentsOverview`** (`/agents/overview`): one call gives the whole org — org record,
  units, roles, agents, missions, health, and an `attention` list. Ideal for the
  Agents Directory home.
- **`MissionEnvelope`** (`/agents/missions/{id}/envelope`): mission + charter + plan +
  graph in one response. Ideal for the Run / Task Graph workspace.
- **`TaskGraph`**: `tasks`, `dependencies`, `cycles`, `critical_path`, `parallel_groups`.
  The browser Task Graph should render `tasks` + `dependencies` (source→target with
  `relationship`), highlight `critical_path`, and use `parallel_groups` for layout.

## UI build plan (mapped to real endpoints)

1. **Directory** — GET `/agents/overview` + GET `/agents/agents`; search/filter by
   `status`/`availability`; cards show display_name, role, status, workload.
2. **Agent workspace** — GET `/agents/agents/{id}`, POST availability, GET
   `/agents/performance/{id}`; list that agent's missions from overview.
3. **Start run** — POST `/agents/missions` (MissionRecord), then POST
   `.../charter`, `.../approve`, `.../start`, `.../plan`.
4. **Task graph** — GET `/agents/missions/{id}/graph` (+ envelope); render nodes/edges,
   cycle + critical path highlight, node click → task detail (GET `/agents/tasks/{id}`),
   state transitions via POST `/agents/tasks/{id}/state`.
5. **Run workspace** — envelope + `messages?mission_id=`, `artifacts?mission_id=`,
   `handoffs`, `approvals`, `gates`, `reviews`; actions via approvals/gates endpoints.
6. **Council** — real backend support confirmed: debates (create/advance/conclude),
   consultations (request/respond), disagreements (open/resolve), consensus
   (record/list). Include the Council workspace.
7. **Realtime** — poll `/agents/health` + `/agents/activity` + mission graph on an
   interval (no SSE exists for agents).

## Files

- `server/agents/router.py` (657 lines) — all routes above.
- `server/agents/models.py` (814 lines) — all typed models + strict enums.
- `server/agents/service.py` — `AgentsService`, SQLite-backed.
- `server/agents/storage.py` — `AgentsStorage` schema.
- `server/agents/collaboration.py` — handoff/gate/collab logic.
- `server/agents/missions.py` — mission planning/execution logic.
- `joeos_backend.py` — service wiring (line 1118) + router mount (line 1323).
