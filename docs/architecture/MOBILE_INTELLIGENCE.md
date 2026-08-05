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
hard-coded. It is the only retained development profile. Every request URL is
derived from the validated profile's
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
- Conversation lifecycle events (`conversation.created`, `message.accepted`,
  `run.queued`, `run.started`, `run.partial`, `run.completed`, `run.cancelled`,
  `run.failed`) are published through the existing realtime event stream with a
  typed envelope (schema version, organization/workspace/principal scope,
  conversation id, run id, timestamp, trace id) and cursor-based reconnect
  (`/api/v1/conversations/events?cursor=<n>`, authenticated by header, scoped to
  the session's workspace); event payloads never contain message content or
  credentials.
- Browser access to authority-protected endpoints is gated by the same
  application-session requirement; requests without a session are rejected
  with 401 by default. The public shell and bootstrap metadata load; private
  conversations, users, roles, capabilities, agents, and provider configuration
  require an authenticated session.

## Phase P3A session and run behavior

- `ApplicationSessionManager` (JoeOSCore) distinguishes disconnected,
  discovering, enrollmentRequired, activeUnassigned, authenticating,
  authenticated, refreshing, offlineAuthenticatedCache, sessionExpired,
  assignmentRevoked, deviceRevoked, userDisabled, organizationDisabled,
  workspaceDisabled, backendIncompatible, transportRejected, and
  authenticationFailed. Authority is unverified while offline even when cached
  identity is displayed.
- Sessions are stored only in ThisDeviceOnly Keychain; refresh rotation is
  serialized, loop-protected, and clears credentials on any authoritative
  rejection.
- Runs are durable: queued → running → (completed | failed | cancelled |
  interrupted). Cancelling a queued run never starts provider work; cancelling
  a running run moves it to `cancellation_requested`; only one terminal state
  is persisted and late provider output cannot overwrite it. After a restart,
  runs left in queued/running/cancellation_requested are interrupted and their
  pending messages cancelled without inventing an assistant response.
- Retry creates a new run related to the original (`parent_run_id`) without
  duplicating the user message.
