# Task graphs

A TaskGraph is a durable set of tasks under a parent AgentRun, each with a title,
objective, assigned agent, and dependency edges. It is genuinely executable, not
visual metadata.

## Creation

`POST /api/v1/control/runs/{run_id}/tasks` with:
```
{ "tasks": [
    { "key": "a", "title": "Analyze", "objective": "...",
      "assigned_agent_id": "<architect>", "dependencies": "" },
    { "key": "b", "title": "Verify", "objective": "...",
      "assigned_agent_id": "<verifier>", "dependencies": "a" }
] }
```
Tasks whose dependencies exist are created `ready`; tasks with unsatisfied
dependencies are `waiting_for_dependency`.

## Execution

`POST /api/v1/control/runs/{run_id}/tasks/execute`:

- Computes ready tasks (all dependencies `succeeded`).
- For each ready task, creates a REAL child AgentRun on the assigned agent and
  executes it (`delegate_agent_run` path), persisting the task output.
- Marks the task `succeeded`/`failed`; failed dependencies block dependents.
- Terminates when all tasks are terminal (bounded; sequential model calls).

States: `pending`, `ready`, `running`, `waiting_for_dependency`,
`waiting_for_approval`, `blocked`, `succeeded`, `failed`, `cancelled`,
`interrupted`.

## Example (Architect -> Verifier -> Joe)

```
Joe (run)
  task Analyze     (Architect)  -> child AgentRun
  task Verify      (Verifier)   -> child AgentRun  (depends on Analyze)
  task Summarize   (Joe)        -> child AgentRun  (depends on Verify)
```

Verified in tests: dependency ordering is enforced, each task runs through a
real child run on its assigned agent, and failure propagates to dependents.
