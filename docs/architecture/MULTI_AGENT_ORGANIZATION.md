# Multi-Agent Collaboration and Organizational Intelligence

Phase 9 delivers the `server/agents/` platform: a local-first, evidence-based
multi-agent organization that coordinates missions, tasks, reviews, and
decisions without ever fabricating activity or silently running agents.

## Core principle

Configured agents are **profiles**, not running intelligence. Every progress
value, consensus, and completion is derived from stored, authoritative task
state, evidence, and validation. Hidden chain-of-thought and secrets are never
stored. Authoritative authority lives in the security and Tool Broker systems;
this platform coordinates, it never self-grants permission.

## Boundaries and reuse

This platform is one authoritative coordination layer. It does not duplicate
existing systems — it composes them through its facade:

- Agent identity and permission checks use the Agent Registry / Tool Broker
  authority (this platform records matching evidence, not authority).
- Missions and task graphs are the single source of scheduling truth; a task
  never starts before its required dependencies complete.
- Approvals are a protocol: a requester can never approve their own action
  (`self_approval_blocked` defaults to true).
- Model routing is local-first and deterministic with an explicit disclosure;
  selecting a route never spawns inference.

## Modules

| Module | Responsibility |
| --- | --- |
| `models.py` | Strict, typed contracts for the whole platform. |
| `storage.py` | Versioned SQLite (`agents.db`, 23 tables, WAL, `VACUUM INTO` backup). |
| `organization.py` | Organization, units, roles, and configured agent profiles. |
| `missions.py` | Charters, plans, tasks, dependencies, task graph (cycles, critical path, parallel groups), assignments with explanations. |
| `collaboration.py` | Messages (secret-redacted), handoffs, artifacts, reviews and gates, disagreements, consensus, debates, consultations. |
| `governance.py` | Escalations, interventions, approvals (no self-approval). |
| `budget.py` | Resource budget enforcement for missions, tasks, and agents. |
| `routing.py` | Local-first model selection with honest disclosure. |
| `detection.py` | Deadlock, loop, and stagnation detection from authoritative state. |
| `health.py` | Organizational health, performance snapshots, activity feed. |
| `memory_proposals.py` | Organizational memory proposals with explicit review. |
| `service.py` | `AgentsService` facade over the composed services. |
| `router.py` | REST API under `/api/v1/agents/*`. |

## Workflow

1. **Organize** — create the organization, units, roles, and agent profiles.
2. **Charter** — a mission gets a charter with success criteria and budget.
3. **Approve** — the charter is approved by an approver (never by itself).
4. **Plan** — tasks and dependencies form a cycle-checked graph.
5. **Staff** — tasks are assigned to agents with a machine-readable
   `AssignmentExplanation` (role/capability/model/permission match).
6. **Execute** — task state transitions are recorded; blocked state propagates
   from blocking dependencies; budgets are enforced.
7. **Validate** — artifacts and reviews feed quality gates; findings are
   preserved; dissent is never hidden.
8. **Escalate** — unresolved decisions, security risks, or budget exhaustion
   route to the user through escalations/interventions/approvals.
9. **Detect** — deadlocks, loops, and stagnation are surfaced as open events.
10. **Learn** — verified outcomes become reviewed memory proposals.

## Honesty guarantees

- No agent is described as "running" unless an explicit execution record exists.
- A gate never passes because a reviewer stayed silent; reviews inspect real
  evidence and artifacts.
- Consensus records always preserve dissent and abstentions.
- Route disclosure states whether nothing leaves the device.
- Health is computed from stored counters, never from background activity.

## Data

All records live in a single versioned SQLite database
(`data/agents/agents.db`) created during the backend lifespan. The facade
(`AgentsService`) binds every sub-service to the same connection so writes are
atomic and auditable.
