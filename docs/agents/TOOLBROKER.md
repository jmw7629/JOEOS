# ToolBroker

The ToolBroker is the authoritative tool catalog in the control plane
(`/api/v1/control/tools`). Agents discover only tools they are authorized to
use; model tool requests are validated against typed schemas before any
invocation; unknown tools and malformed arguments are rejected. There is no
unrestricted shell tool.

## Registered safe tools

| Tool | Category | Risk | Purpose |
| --- | --- | --- | --- |
| `joeos.system_status` | read_only | informational | Authoritative runtime/service/telemetry status |
| `joeos.list_agents` | read_only | informational | Agent profiles + configured model/provider |
| `joeos.read_memory` | retrieval | informational | Read memory records (workspace-scoped) |
| `joeos.search_knowledge` | retrieval | informational | Search knowledge index |
| `joeos.read_documentation` | read_only | informational | Read a documentation path (read-only) |

No write, build, shell, deployment, network, or privileged tools are registered.

## Guardrails

- Tool schema validation rejects undeclared parameters.
- Dangerous parameter patterns (shell tokens, secrets) are rejected.
- Agents' `allowed_tools` restrict discovery; unauthorized tools are never
  offered to a model.
- Any future privileged tool must route through ActionProposal -> Policy ->
  Approval -> Execution; the model cannot approve itself, lower risk, or bypass
  policy.
