# Getting started with JoeOS agents

This guide is operational, not theoretical.

## Prerequisites

- JoeOS backend running from this branch (`ai-rebuild` or
  `feature/agent-live-repair` after integration).
- Ollama healthy on `127.0.0.1:11434` (loopback only).
- Identity bootstrapped (owner exists).

## Pair the browser (one time)

On the VPS, issue a pairing offer:

```
.venv/bin/python -m server.identity.cli --database data/joeos.db issue --origin https://mcso9tqzb9.tailb9395f.ts.net
```

Open the Agent Fabric console at `/os/agents`, enter the `JOEOS1|...` code, and
click **Pair browser**. Then assign the device to the owner:

```
.venv/bin/python -m server.identity.cli --database data/joeos.db authority assign \
  --device <device_id> --user <owner_user_id> --org <org_id> --workspace <ws_id> \
  --role <owner_role_id> --by <owner_user_id>
```

Back in the browser click **Check status** to establish the session.

## Talk to Joe

- Route: `/os/agents` -> Agents -> Joe -> Start run.
- Objective example: "Describe your configured role. Do not modify anything."
- Joe may answer directly or plan/delegate based on complexity.

## Start Architect

- Route: `/os/agents` -> Architect -> Start run.
- Objective example: "Identify the single most important architectural strength
  of the current JoeOS agent architecture. Do not modify anything."

## Start Builder / Researcher / Verifier / Security

Same flow: open the agent, Start run, enter a bounded objective. These agents
cannot delegate and use read-only tools only.

## View a run

`/os/agents` -> Runs tab (or Overview). Each run shows objective, status,
provider, model, and the persisted result.

## View delegation

`/os/agents` -> select Joe -> Recent runs -> the run's delegation child (via the
runs API `/api/v1/control/runs/{id}/delegations`).

## View a TaskGraph

`POST /api/v1/control/runs/{id}/tasks` to create tasks, then
`POST /api/v1/control/runs/{id}/tasks/execute`; read with
`GET /api/v1/control/runs/{id}/tasks`.

## Use the Executive Council

`POST /api/v1/control/councils` (member agents), then
`POST /api/v1/control/councils/{id}/runs`.

## How approvals work

Privileged operations go through ActionProposal -> PolicyDecision -> Approval.
Read-only low-risk tools need no approval. High/critical risks require a signed
approval challenge from an enrolled approval device (no self-approval).

## How to cancel a run

`POST /api/v1/control/runs/{id}/cancel`.

## Tell which model was used

Every run payload carries `provider_key` and `model_key` for the model actually
run. There is no fake attribution.

## Check Ollama health

`curl http://127.0.0.1:11434/api/version` (loopback), or
`ollama ps` / `ollama list` on the VPS.
