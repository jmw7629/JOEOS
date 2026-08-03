# JoeOS Automation and Workflow Platform

Phase 11 delivers `server/automation/`: a local-first, human-governed
workflow engine that coordinates scheduled work, event-triggered work,
project workflows, agent workflows, notifications, and maintenance routines
without fabricating runs or bypassing authority.

## Principles

- **Explicit authority.** Every action runs under the workflow's approved
  definition, declared permissions, project scope, and user authority.
- **Least privilege.** A workflow receives only the permissions it declared
  and the user granted; it can never grant itself more.
- **Deterministic.** Behavior follows the versioned definition and inputs.
- **Idempotency.** Repeated triggers and retries do not duplicate side
  effects (idempotency keys + result reuse).
- **Human governance.** High-risk actions require approval bound to exact
  action arguments; agent- and AI-generated workflows remain proposals until
  validated and authorized.
- **Bounded execution.** Loops, retries, parallelism, durations, and model
  calls all have hard limits.
- **No secret leakage.** Secrets are brokered and never stored in
  definitions, runs, traces, or logs.
- **Inputs are untrusted.** Events, documents, and expressions never execute
  code and never grant authority.
- **No self-authorization.** A workflow cannot approve its own actions.

## Architecture

```text
server/automation/
├── models.py          typed contracts (definitions, runs, triggers, schedules, nodes, policies)
├── storage.py         versioned SQLite registry (automation.db)
├── workflows.py       Workflow Registry + versioning (definition hashes, superseded versions)
├── compiler.py        strict validation (no code execution) + compilation (cycles, bounded loops, reachability)
├── expressions.py     constrained expression language (no eval, no JS, no tools)
├── schedules.py       timezone-aware scheduling (IANA zones, DST, missed/overlap policies)
├── triggers.py        Trigger Registry (manual, scheduled, event, condition)
├── actions.py         Action Registry (notifications, memory, git status, delay, transform, audit, subworkflow)
├── secrets.py         Workflow Secret Broker (AES-256-GCM at rest)
├── permissions.py     WorkflowPermissionGuard (declared + granted enforcement)
├── safety.py          idempotency, concurrency, resource locks, rate limits
├── execution.py       Execution Engine (state machine, branches, loops, parallel, retries, timeouts, approvals, input, compensation, pause/resume/cancel)
├── history.py         bounded run history + traces + health
├── templates.py       safe least-privilege workflow templates
├── service.py         AutomationService facade
└── router.py          REST API under /api/v1/automation/*
```

## Execution model

A workflow is compiled into a `CompiledPlan` with a single entry node,
reachable nodes, no unbounded cycles (loops must declare `LoopConfig` with a
hard iteration and duration limit), and explicit parallel/join boundaries.
The engine executes node by node through a real state machine, recording
traces and node states. Runs are pinned to the workflow version that started
them.

Supported node types: start, end, action, condition, switch, parallel, join,
loop, delay, wait_time, wait_event, wait_approval, wait_input, transform,
notification, subworkflow, failure_handler, audit_marker.

## Scheduling

Schedules are timezone-aware (named IANA timezone; never server-local
implicitly). Daily/weekly/monthly/interval recurrences are supported with
explicit daylight-saving behavior (nonexistent times run at the next valid
time; repeated times run once). Missed-run and overlap policies (`skip`,
`run_immediately`, `catch_up_latest`, `catch_up_all`, `require_review` for
missed; `skip`, `queue`, `cancel_previous`, `parallel_bounded`,
`deduplicate` for overlap) are stored per schedule and enforced by the
central schedule service — there is no per-component timer.

## Security

- Workflows cannot access unrestricted filesystem, spawn arbitrary processes,
  or call provider APIs directly.
- Privileged actions route through the Action Registry, which checks the
  workflow's granted permission at execution time.
- Approval nodes bind approval to the workflow version, run, node, action,
  and an arguments hash; changing material content invalidates approval.
- Expressions are a constrained language: no `eval`, no JavaScript runtime
  objects, no prototype access, no filesystem/network access, and bounded
  output size.
- Secret values are AES-256-GCM encrypted at rest and delivered only through
  the broker; they are excluded from logs, traces, and history.

## Known limitations

- Subworkflow recursion depth is bounded by the node dispatch depth guard;
  true cross-workflow subworkflow invocation requires a runner and is exposed
  as an action stub.
- Webhooks, external connectors, and cloud execution are architecture only.
- There was no pre-existing Job Scheduler in the codebase; the schedule
  service here is the authoritative scheduler and does not duplicate any
  existing queue.
