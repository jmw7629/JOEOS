# JoeOS Target Architecture

## Design principles

1. **Local execution, portable control.** Models, files, terminals, and privileged tools stay on enrolled machines. iPhone and web clients use a narrow authenticated control plane.
2. **Approval is a protocol.** A chat message never grants shell, download, Git, deployment, payment, email, or device authority.
3. **Widgets consume contracts.** A widget renders typed state and emits typed intents; it never reaches directly into a provider.
4. **Adapters isolate vendors.** Lemonade, Ollama, Claude Code, Codex, MCP, email, calendar, weather, finance, manufacturing, and social sources implement capability interfaces.
5. **Local-first is explicit.** SQLite/IndexedDB keep a device cache and outbox. PostgreSQL is authoritative synchronized state; conflicts use entity revisions, not last-write guesswork.
6. **No false telemetry.** Unconnected modules show an integration-required state. Simulations are available only in an explicitly labeled demo workspace.
7. **Every mutation is observable.** Request IDs, actor/device identity, policy decision, approval, execution, result, and rollback metadata are durable audit facts.

## Runtime topology

```text
                         Authenticated HTTPS
 iPhone / macOS / Web  <---------------------->  JoeOS Control API
      SwiftUI + PWA                                FastAPI
            |                                         |
            | local cache + outbox                    +-- Identity / RBAC
            |                                         +-- Widget/Layout service
            |                                         +-- Mission Control read model
            |                                         +-- Search / Memory / RAG
            |                                         +-- Agent Orchestrator
            |                                         +-- Automation / Approvals
            |                                         +-- WebSocket/SSE gateway
            |                                         |
            |                                   PostgreSQL + pgvector
            |                                   Redis streams/cache/locks
            |                                         |
            +---------- signed device channel --------+
                                                      |
                                           VPS Execution Plane
                                           - Lemonade / Ollama
                                           - Codex / Claude Code
                                           - MCP servers
                                           - file/git/terminal tools
                                           - OS telemetry and automation
                                           - encrypted SQLite cache/outbox
```

The first supported deployment remains single-owner and private-tailnet. The contracts include organization, workspace, actor, and device identifiers from the beginning so multi-user deployment does not require rewriting every record.

## Current device-identity boundary

The first P3 slice implements server-side device key pairing without widening the execution boundary:

```text
local operator console
  -> one five-minute secret offer
  -> exact private JoeOS origin
  -> two-minute challenge binds installation + origin + device + two P-256 keys
  -> HMAC secret proof + two purpose-separated ECDSA proofs
  -> atomic SQLite device record: active_unassigned
```

Only the local CLI can issue an offer; the API exposes challenge and completion routes but no offer-creation route. Pending pairing keys are AES-256-GCM protected with a separate master key, consumed or failed material is scrubbed, identity events are append-only, and the operator can list or revoke devices locally. The default owner-only key file reduces disclosure from theft of only the SQLite database or WAL; managed deployments can inject a secret environment value or separate key file. OS Keychain wrapping remains a future hardening step.

This is key enrollment, not application authentication or authorization. The resulting state is `active_unassigned` and grants no role, session, approval, route access, or execution authority. The modular native iOS source now performs the exact ceremony with two server-scoped Secure Enclave keys, explicit review before Face ID, and a ThisDeviceOnly signed-retry journal. It deliberately treats the receipt as last validated local state rather than live revocation evidence. Full Xcode signing and physical-device recovery/security drills remain a release gate. The full contract, threat model, recovery constraints, and protocol encoding are documented in [Device Enrollment Security Protocol](../security/DEVICE_ENROLLMENT.md).

## Monorepo boundaries

```text
joeos/
├── apps/
│   ├── desktop/       macOS SwiftUI shell and native integrations
│   ├── mobile/        iOS SwiftUI shell, approvals, biometrics, notifications
│   └── web/           Sites/Vinext React application
├── server/
│   ├── api/           transport-only routers and versioned schemas
│   ├── auth/          identity, sessions, devices, RBAC and policy
│   ├── agents/        orchestrator, planner, agents and task protocol
│   ├── memory/        working, episodic, semantic and graph memory
│   ├── rag/           ingestion, chunking, embeddings and retrieval
│   ├── websocket/     event subscriptions, resume cursors and backpressure
│   └── automation/    workflows, schedules, approvals, retries and audit
├── packages/
│   ├── ui/            widget chrome, design tokens and accessibility contracts
│   ├── shared/        versioned domain/event schemas
│   └── sdk/           authenticated API, stream, cache and outbox clients
├── services/
│   ├── ollama/        Ollama and Lemonade-compatible inference adapters
│   ├── claude-code/   approval-gated Claude Code runner adapter
│   ├── codex/         approval-gated Codex runner adapter
│   ├── embeddings/    embedding providers and batching
│   └── vector-db/     pgvector repository and index policy
├── docker/            local infrastructure and production images
├── scripts/           install, migrate, backup, doctor and release tooling
└── docs/              architecture, operations, security and user guides
```

Existing root files remain compatibility entry points while implementations move behind these boundaries. Moving the Sites repository into `apps/web` requires a separate history-preserving repository migration.

## Widget platform

### Widget definition

A versioned definition declares:

- stable `kind`, semantic version, title, icon, category, and description;
- renderer capability and minimum client version;
- allowed sizes and breakpoint defaults;
- refresh strategy (`snapshot`, `poll`, `stream`, `manual`);
- configuration JSON Schema and defaults;
- required data capabilities and integration bindings;
- required read and action permissions;
- offline behavior and staleness limits;
- empty, loading, stale, denied, disconnected, error, and ready states.

### Widget instance

An instance belongs to a workspace and stores a stable ID, definition version, title override, breakpoint placement, size, visibility, typography overrides, color tokens, data-source binding, refresh policy, configuration, revision, and timestamps.

### Layout behavior

- Desktop drag/drop and edge/handle resizing.
- Touch-safe move and size controls on iPhone.
- Keyboard move/resize alternatives and announcements.
- Undo/redo and preview before saving.
- Device-class layouts with one shared logical widget identity.
- Optimistic concurrency through a workspace revision.
- Import/export of secret-free layout manifests.

Durable layouts are server-owned. Local storage is limited to device preferences such as last section, compact density, and dismissed tips. Offline mutations go to a revisioned outbox.

## Guided configuration protocol

```text
natural-language intent
  -> planner creates typed proposal
  -> schema validation
  -> permission and integration impact
  -> visual preview/diff
  -> user approval when mutation is material
  -> idempotent apply
  -> durable audit result
  -> undo token when reversible
```

The guide can suggest modules, layouts, colors, typography, refresh rates, and connectors. It cannot collect secrets in chat. Connector credentials use an OS keychain or a dedicated server-side secret flow.

## Agent contract

Every agent uses the same lifecycle:

```text
queued -> planning -> awaiting_approval? -> running -> summarizing
       -> completed | failed | cancelled | blocked
```

Each task records objective, owner, agent, parent task, delegated children, input references, memory scope, tool grants, approval requirements, progress events, result summary, evidence, cost/model usage, and completion state. Agents communicate through durable task/event records rather than direct in-process calls.

Initial registry: planner, executive, coding, research, email, calendar, finance, manufacturing, personal assistant, memory, search, vision, voice, automation, security, and file agents.

## Approval and execution boundary

Privileged work is represented by an immutable proposal containing:

- exact action type and normalized parameters;
- target device/repository/account;
- rendered impact summary and data-egress disclosure;
- policy result and required approver roles;
- content digest, expiry, and idempotency key;
- rollback description when available.

An enrolled runner accepts only signed, unexpired, approved proposals for an allow-listed capability. Output is size-limited, redacted, streamed as events, and attached to the audit record. Downloads are quarantined and scanned before use. Tunnels authenticate both ends and do not expose Lemonade/Ollama directly.

## Persistence responsibilities

| Store | Responsibility |
|---|---|
| PostgreSQL | users, workspaces, widgets, projects, tasks, approvals, audit, automations, documents, connector metadata |
| pgvector | embedding columns and similarity indexes tied to authorized source records |
| Redis | ephemeral cache, rate limits, distributed locks, stream fan-out, scheduler coordination |
| SQLite | encrypted local cache, outbox, device state, runner queue checkpoint, local telemetry retention |
| Object storage | encrypted document/image/audio payloads and generated artifacts |
| OS Keychain/Secure Enclave | device keys, refresh credentials, local encryption key wrapping |

Sites D1 is not a substitute for the system of record. It may be used later for a bounded edge/session concern only if the ownership and synchronization rule is explicit.

## Real-time model

Domain changes append versioned events with a monotonic workspace cursor. WebSocket clients subscribe by capability and resume from the last acknowledged cursor. Slow clients receive coalesced telemetry snapshots; approvals, task transitions, and audit facts are never coalesced. Polling remains as a compatibility fallback.

## Mission Control read model

The home screen is a materialized executive read model with five first-class questions:

1. attention required now;
2. safe work AI can handle;
3. blocked, at-risk, or overdue work;
4. recommended next action;
5. changes since the actor's last acknowledged cursor.

Every card includes source, freshness, confidence, owner, why it matters, and the smallest safe action. Recommendations never claim execution before an audited result exists.

## Security gates before remote privileged actions

- Authenticated user bound to an enrolled, non-revoked device. Device key enrollment exists; user/session binding and request enforcement do not.
- Short-lived sessions, CSRF protection, allowed-host/proxy policy, and rate limits.
- RBAC plus per-capability policy checks.
- Passkey/biometric step-up for high-impact approval.
- Encrypted secret and memory storage with rotation.
- Signed runner channel and scoped device certificates.
- Complete append-only audit trail with sensitive-field redaction.
- Backup, restore, revocation, incident-response, and recovery tests.

Until these gates pass, JoeOS remains read-only for terminals, Git, downloads, deployments, payments, external messages, and device control.
