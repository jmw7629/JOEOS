# JoeOS Feature Graph

Legend: **done** is real and tested, **partial** is real but incomplete, **demo** is explicitly simulated, and **missing** has no implementation yet.

## Dependency graph

```text
Identity + Device Enrollment + RBAC
  -> Workspaces + Widget Registry + Themes
     -> Mission Control read model
     -> Saved layouts across iPhone/macOS/web
  -> Approval protocol
     -> Runner enrollment + signed tunnel
        -> Codex / Claude Code / file / Git / terminal actions
        -> automations and external communications

Versioned events + WebSocket resume
  -> live telemetry
  -> notifications and approvals
  -> agent progress
  -> changes-since-last-open

PostgreSQL + encrypted SQLite outbox
  -> projects, notes, tasks and widgets
  -> memory and document metadata
  -> durable agents and automations

pgvector + embeddings + access filters
  -> semantic memory
  -> document Q&A
  -> global search
  -> research and executive brief

Connector registry
  -> calendar/email/weather/messages/social
  -> finance/manufacturing/health/travel
  -> Mission Control and morning brief

Agent task protocol
  -> planner and executive agent
  -> specialist agents
  -> delegation, approvals, summaries and evidence
```

## Feature status

| Capability | Status | Dependency / next completion gate |
|---|---|---|
| Halo CPU/RAM/GPU/disk/uptime | **partial** | Add reliable temperature/model usage sources and multi-host collection |
| Local Lemonade chat | **partial** | Streaming, durable conversation, identity, memory scopes |
| Bot fleet | **partial** | Replace profiles with durable agent/task state machine |
| Local audit stream | **partial** | Typed resumable WebSocket is live; add actor/device/request IDs, append-only policy, host security collectors |
| Responsive command center | **partial** | Persistent Mission Control and accessible layout controls are live; extract widget renderers and complete accessibility QA |
| Mobile PWA | **partial** | Private HTTPS launcher and offline shell are live; bundle CDN assets and add encrypted data outbox |
| macOS/iOS SwiftUI shells | **partial** | iOS profiles, URL/WebView policy, bootstrap discovery, reviewed native pairing, server-scoped Secure Enclave keys, Face ID enrollment confirmation, and Keychain recovery are implemented; add Xcode project/device validation, authenticated sessions, live revocation, and native privileged approvals |
| CI/CD | **demo** | Approval-gated repository runner and Git provider |
| Hosted Sites dashboard | **demo** | Authenticated API/stream SDK, no iframe, no mock production claims |
| Widget catalog/layout/theme | **partial** | Versioned SQLite layout, sizing, ordering, visibility, catalog states, and full theme controls are live; add breakpoint records and undo/redo |
| Guided OS configuration | **partial** | Deterministic typed proposal/preview is live; add identity, policy impact, audited apply, and undo tokens |
| Mission Control | **partial** | Five-question live local read model is the default; add provenance/freshness contracts and approved executive connectors |
| Identity/RBAC/device enrollment | **partial** | [Local-console two-key device pairing](../security/DEVICE_ENROLLMENT.md), native Secure Enclave/Keychain custody, explicit Face ID review, revocation, encrypted pending-key storage, and audit state are implemented; add physical-device validation, authenticated sessions, request enforcement, users/workspaces, roles, and privileged approvals |
| Engineering workspace | **partial** | Project registry, root-bounded file access, git read + approval-gated stage/commit, secret scanning, command validation, and repository search are live; add terminal and rollback through approvals |
| Project and repository intelligence | **partial** | Fingerprints, incremental inventory/classification, language/framework detection, symbol/reference parsing (10 languages), dependency/architecture graphs, change-impact, risk findings, ADR/convention ingestion, memory registry, hybrid retrieval, context packs, and cancellable index with health are live; add cross-project queries, semantic embeddings, and global symbol index |
| PostgreSQL/Redis/pgvector | **missing** | Repository interfaces, migrations, local deployment |
| SQLite offline sync/outbox | **missing** | Revisioned sync protocol and encryption key management |
| WebSocket/domain event stream | **partial** | Versioned envelopes, cursor resume, bounds, heartbeats, SDK reconnect, and polling fallback are live; add identity-scoped subscriptions and priority backpressure |
| Agent orchestrator/planner | **missing** | Durable tasks, tool grants, retries, cancellation, evidence |
| Specialist agents | **missing** | Orchestrator plus connector/tool capabilities |
| Memory/RAG/knowledge graph | **missing** | Authorized sources, embeddings, provenance, retention |
| Global search | **missing** | Unified index contract and permission filtering |
| Documents/OCR | **missing** | Object storage, ingestion queue, OCR adapters, RAG |
| Projects/notes/coding studio | **missing** | Core entities plus search/events/approvals |
| Voice/vision | **missing** | Client permissions, streaming media, model adapters, privacy controls |
| Automations | **missing** | Workflow DSL, scheduler, idempotency, approval and retry engines |
| Interactive notifications | **missing** | Event stream, push service, approval actions |
| Calendar/email/weather/messages | **missing** | Connector framework and scoped OAuth/secrets |
| Finance/manufacturing/health | **missing** | Domain connectors, schemas, policy and source evidence |
| Even Reality G2 | **missing** | Notification/navigation capability adapter after core mobile protocol |
| Secure terminal/Git/downloads | **missing** | Signed runner, sandbox, approval protocol, audit, rollback |
| Self-improvement | **missing** | Architecture lints, evaluation suite, proposal-only refactoring workflow |

## Completion rule

A feature is not **done** because a card renders. It is done only when its source is real, authorization and failure states are defined, mutations are auditable and idempotent, offline/reconnect behavior is tested where applicable, accessibility is verified, documentation is current, and regression tests pass.
