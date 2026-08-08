# Delegation

Delegation is a REAL parent/child AgentRun relationship, not a model pretending
to be another agent.

## How it works

`POST /api/v1/control/runs/{parent}/delegate` with `{ "agent_id": ..., "objective": ... }`:

1. Creates a genuine child `AgentRun` with:
   - its own immutable `agent_version_id`
   - its own provider/model (bound to the child agent)
   - `parent_run_id` = the parent run
   - `delegation_depth` = parent depth + 1
2. Executes the child through the Ollama executor (a separate model invocation).
3. Persists the child result; the parent can read `GET /runs/{parent}/delegations`.

## Bounds (loop safety)

- `max_delegation_depth` per agent; `0` means the agent cannot delegate.
  Joe=3, Architect=1, Builder/Researcher/Verifier/Security=0.
- Delegation is strictly forward (a child can never point back to an ancestor),
  so delegation cycles are structurally impossible.
- Retries are bounded in the executor; task graphs are bounded; run result size
  is bounded.

## Verified

- Parent + child runs with distinct IDs, versions, models, and results.
- Child failure does not fabricate a parent success.
- Depth limit is enforced (Builder cannot delegate).
