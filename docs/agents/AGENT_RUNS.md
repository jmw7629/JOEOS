# Agent runs

## Lifecycle

`start_agent_run` creates a run (`queued` -> `running`), records the immutable
agent version, provider, model, objective, timestamps, and (when delegated) the
parent run + delegation depth. `execute_agent_run` invokes the Ollama executor
(the only place a model call happens), persists the bounded result, and
transitions to `succeeded` or `failed`. `cancel_agent_run` transitions to
`cancelled`.

States: `queued`, `running`, `waiting_for_tool`, `waiting_for_approval`,
`approved_awaiting_executor`, `succeeded`, `failed`, `cancelled`, `interrupted`.

## Persisted per run

- id, conversation_id, message_id, agent_id, agent_version_id
- provider_id + model_id (and provider_key/model_key in payloads)
- status, objective, result, token_usage, failure, cancellation
- started_at, completed_at, parent_run_id, delegation_depth, requested_by, trace_id

## API

| Endpoint | Purpose |
| --- | --- |
| `POST /api/v1/control/agents/{agent_id}/runs` | Start a run (objective). |
| `POST /api/v1/control/runs/{run_id}/execute` | Execute through the model. |
| `GET /api/v1/control/runs/{run_id}` | Read run + persisted result (refresh-safe). |
| `GET /api/v1/control/agents/{agent_id}/runs` | Recent runs for an agent. |
| `GET /api/v1/control/runs/{run_id}/tasks` | Task graph for a run. |
| `POST /api/v1/control/runs/{run_id}/delegate` | Delegate to a child agent (real child run). |
| `GET /api/v1/control/runs/{run_id}/delegations` | Child runs of this run. |
| `POST /api/v1/control/runs/{run_id}/cancel` | Cancel the run. |

## Attribution

Every run payload carries `provider_key` and `model_key` for the model actually
used. If a fallback occurred, the reported model is the one that ran; no fake
metadata is produced.
