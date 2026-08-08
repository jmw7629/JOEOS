# Autonomy Audit — existing infrastructure

Date: 2026-08-08.

## What already existed

| System | Purpose | Reuse |
| --- | --- | --- |
| `server/automation/` (Automation & Workflow Engine) | Durable workflow graphs (nodes/edges/actions), versioned `WorkflowDefinition`, `WorkflowRun`, schedules, triggers, retries, locks, idempotency, concurrency, approval gates. | Reused its **DST-safe recurrence + schedule service** (`schedules.py`) and its durable SQLite conventions. |
| `server/engineering/campaign/` (P3G) | Durable autonomous engineering campaign: work-package state machine, roadmap queue, background `CampaignWorker` asyncio loop, checkpoints, stage handlers, worktrees, integration gate. | Reused its **background singleton worker pattern** (asyncio task inside the backend process). |
| `server/communications/` NotificationCenter | Real durable notification store (`comms_notifications`): categories, severities, read/ack/archive, quiet hours, DND, dedup. | Reused as the authoritative notification store (deep links added additively). |
| AgentFabric (control plane) | AgentProfile/Version, AgentRun, TaskGraph, delegation, Council, ToolBroker, ActionProposal, Policy, Approval. | Reused as the exact execution path; automations create real AgentRuns. |
| ProviderRegistry / ModelRegistry / OllamaProvider | Local-first model routing. | Reused unchanged; automations bind to the working 1.5B family. |

## What was missing

- A durable **agent-based** automation domain (AutomationDefinition + AutomationRun)
  distinct from the workflow-graph engine.
- A background scheduler that drives agent automations (the existing automation
  engine's `check_due_schedules` existed but had no background loop).
- Occurrence identity/lease/fencing for agent automations.
- `/os/automations` browser app.
- Notification wiring from agent automation outcomes.

## Decision

Build `server/autonomous/` as a thin durable layer over the AgentFabric:
- reuses the automation platform's schedule service (RRULE-style recurrence, DST-safe),
- reuses the campaign worker's singleton asyncio-loop pattern,
- reuses the NotificationCenter for durable notifications,
- executes through the exact AgentFabric path (no second model runtime).

This does not create a competing automation engine or a second AgentRun engine.
