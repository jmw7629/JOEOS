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
- Streaming (`POST /api/v1/conversations/{id}/stream`, server-sent events) is
  genuine only when the selected provider advertises streaming
  (`supports_streaming`). Otherwise the endpoint returns a single completed
  `message.delta` with honest non-streaming semantics; no partial events are
  fabricated.
- Cloud routing is blocked by local-only mode unless the provider is
  explicitly cloud-approved by the backend.
- Diagnostics never fabricate latency, tokens, queue depth, or health.

## Phase P3A application sessions and conversations

The native Swift client (`JoeOSCore`) integrates the authoritative backend
application identity and conversation contracts:

- `ApplicationSessionClient` — requests a device-key authentication challenge,
  signs it with the enrolled P-256 device-authentication key, establishes a
  short-lived revocable application session, rotates refresh credentials, and
  retrieves the authenticated principal. `KeychainSessionStore` persists the
  single-use refresh credential; the backend remains authoritative.
- `ConversationClient` — creates, reopens, lists, submits, retries, cancels,
  and streams canonical conversations with the session id presented on every
  request. Conversation history, message state, and run status are
  backend-authoritative.
- Conversation lifecycle events (`conversation.created`, `message.appended`,
  `run.started`, `run.completed`, `run.cancelled`, `run.failed`) are published
  through the existing realtime event stream with cursor-based reconnect
  (`/ws/events?after=<cursor>`); event payloads never contain message content.
- Browser access to authority-protected endpoints is gated by the same
  application-session requirement; requests without a session are rejected
  with 401 by default.
