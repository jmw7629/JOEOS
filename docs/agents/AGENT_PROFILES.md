# Agent profiles

Six authoritative production agents are created in the control plane
(`/api/v1/control/agents`) by `server/agents/activation.py` (idempotent). Each
has immutable versions (`control_agent_versions`); edits create a new version
and a run always records the exact `agent_version_id` that produced it.

## Joe (`joeos.joe`)
Personal AI orchestrator: understands objectives, answers directly when simple,
plans when complex, delegates to specialists, collects results, requests
verification, and presents a final result. Never bypasses ToolBroker/policy.
- Model: qwen2.5-coder:7b-opencode-safe (fallback 7b)
- Max delegation depth: 3
- Tools: safe read tools only

## Architect (`joeos.architect`)
Software/system architecture, decomposition, design, dependency analysis,
technical plans, architecture review, risk identification.
- Model: qwen2.5-coder:7b-agentic (fallback 7b)
- Max delegation depth: 1

## Builder (`joeos.builder`)
Implementation planning and bounded coding through authorized tools. No
unrestricted shell authority; any write/build goes through ToolBroker ->
proposal -> policy -> approval.
- Model: qwen2.5-coder:7b-opencode-safe (fallback 1.5b-fast)
- Max delegation depth: 0 (cannot delegate)

## Researcher (`joeos.researcher`)
Repository/documentation research, synthesis, provenance, evidence collection.
- Model: qwen2.5-coder:7b (fallback 1.5b)
- Max delegation depth: 0

## Verifier (`joeos.verifier`)
Independent verification; does not simply agree with Builder. Returns
VERIFIED / PARTIALLY_VERIFIED / FAILED_VERIFICATION with evidence.
- Model: qwen2.5-coder:7b-agentic (fallback 7b)
- Max delegation depth: 0

## Security (`joeos.security`)
Security review, secret scanning, permissions review, unsafe-tool detection,
trust-boundary review, provider/connector privacy. May block a result.
- Model: qwen2.5-coder:7b-agentic (fallback 1.5b)
- Max delegation depth: 0

## Executive Council
An orchestration configuration over the real council architecture
(`/api/v1/control/councils`). Each member is a REAL AgentRun with its own agent
version/provider/model; the council persists member runs and aggregates their
results. It is not one model role-playing multiple people.

## Versioning
Every agent has `latest_version_id`; `GET /api/v1/control/agents/{id}/versions`
lists immutable versions with configuration digests. A run's
`agent_version_id` is traceable to the exact configuration used.
