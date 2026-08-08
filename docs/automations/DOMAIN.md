# Automation domain

## AutomationDefinition

The durable intention. Fields: id, organization_id, workspace_id,
owner_principal_id, name, description, objective, agent_ref (auto/joe/architect/
builder/researcher/verifier/security/council), trigger, timezone, enabled, state
(draft/active/paused/disabled/archived), next_run_at, last_run_at,
concurrency_policy, missed_run_policy, retry_policy, notification_policy,
created_at, updated_at, revision.

Edits bump `revision`; every AutomationRun stores an immutable definition
snapshot so history identifies the exact configuration revision under which it
ran.

## AutomationRun

One occurrence. Fields: id, automation_id, occurrence_key (deterministic),
trigger_kind, scheduled_for, triggered_at, started_at, completed_at, attempt,
state (queued/running/waiting_for_approval/retry_wait/blocked/succeeded/failed/
cancelled), agent_run_id, task_graph_id, approval_id, execution_id,
result_summary, error_category, next_retry_at, worker claim/lease fields,
provider_key, model_key, definition_revision, created_at, revision.

## State machine

Definitions: draft -> active <-> paused, active -> disabled -> archived.
Runs: queued -> running -> succeeded/failed/cancelled, or -> waiting_for_approval
-> running/succeeded/failed, or -> retry_wait -> queued (bounded).

Transitions are validated server-side; never encoded only in UI labels.
