# Mobile Intelligence Architecture

The native SwiftUI iOS client is the production JoeOS client. It talks **only**
to the selected JoeOS backend. It never contacts a model provider directly;
provider execution is always server-side.

```
SwiftUI (Command Center, Connections, Conversations, Agents, Approvals,
         Diagnostics, Settings)
        │
        ▼
Conversation Engine ──► MemoryStore (layered memory)
        │
        ▼
Executive Intelligence Layer
   ├─ ProviderRegistry (from backend /api/v1/ai/providers)
   ├─ ModelRegistry    (capability metadata, honest availability)
   ├─ ExecutionRouter  (chooses provider + model; local-only enforcement)
   ├─ Tool Broker      (allowlist/deny; AgentFabric.requestAction)
   └─ ApprovalGate     (approvals bound to exact action content)
        │
        ▼
Agent Fabric ──► Executive Council (supervisory roles) ──► TaskGraph
        │
        ▼
JoeOSBackendClient (JoeOSCore) — same-origin, profile-derived URLs
        │
        ▼
JoeOS Backend ──► Local models │ cloud models │ enterprise models (server-side)
```

## Connection architecture

`ConnectionManager` (JoeOSCore) owns the saved profiles and the selection. The
default development profile is `JoeOS VPS` at `100.98.25.26` (Tailscale), HTTP
during development, with the port discovered from the backend rather than
hard-coded. The old Halo address is retained only as an explicit, editable
profile. Every request URL is derived from the validated profile's
`(transport, host, port)`, so an HTTPS migration is a profile edit with zero
source changes.

Profiles are non-secret preference data (UUID, display name, protocol, host,
port, HTTPS-required, environment, notes, API version, last-connected and
last-successful timestamps). They never contain API keys, passwords, session
tokens, or private keys.

## Honest availability

The client reports `unavailable` whenever the backend does not provide a
capability:

- Provider/Model registries are populated only from authoritative backend
  state (`/api/v1/ai/overview`, `/api/v1/ai/providers`).
- The backend inference endpoint is currently non-streaming; the client
  delivers the full response and does not simulate streaming.
- Cloud routing is blocked by local-only mode unless the provider is
  explicitly cloud-approved by the backend.
- Diagnostics never fabricate latency, tokens, queue depth, or health.
