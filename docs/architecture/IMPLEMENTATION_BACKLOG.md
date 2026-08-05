# JoeOS Implementation Backlog

This backlog is dependency ordered. Each vertical slice must preserve current local routes, update documentation, and pass its acceptance gates before the next slice begins.

## P0 — Preserve and secure the baseline

- Record this audit and architecture.
- Rotate all restricted credentials previously exposed outside the repository.
- Ignore SQLite/WAL, logs, coverage, caches, and local model artifacts.
- Replace unreachable legacy setup code with one audited local installer while retaining recovery history.
- Preserve nested web commit `f29bfcb`; create a history-preserving monorepo migration rather than an embedded repository.
- Pin a supported Node 22.13+ toolchain for the Sites app.

Acceptance:

- No secret value in source or generated output.
- Existing 8 backend, 5 root frontend, and 2 Sites tests remain green.
- The current VPS launcher, API routes, dashboard, and Lemonade-offline failure mode still work.

## P1 — Customizable Mission Control foundation

Implementation status: **foundation delivered**. Versioned local persistence, catalog states, core Mission Control widgets, drag/touch/keyboard ordering, resizing, visibility, theme controls, command palette, and typed configuration proposals are implemented. Remaining P1 work is synchronized identity-scoped layouts, multi-breakpoint records, undo/redo, and full accessibility/browser validation.

- Add versioned widget definitions and integration manifests.
- Add persistent workspace, widget-instance, breakpoint-layout, and theme records.
- Add optimistic revisions and audit records for layout changes.
- Implement Mission Control with attention, delegable work, risk, next action, and changes-since-last-open widgets.
- Add desktop drag/reorder plus touch and keyboard move/resize controls.
- Add typography, accent, density, radius, and glass-opacity controls.
- Add widget catalog with honest `ready`, `degraded`, `permission_required`, and `integration_required` states.
- Add a typed configuration guide returning a proposal and preview; never ask for secrets in chat.

Acceptance:

- Layout survives refresh and is shared through the local server.
- Concurrent stale writes return a conflict instead of silently overwriting.
- Every layout operation has an accessible non-drag control.
- Unconnected calendar, mail, social, finance, manufacturing, and other future widgets never display fabricated data.
- Existing operational sections continue to work.

## P2 — Modular API and real-time events

Implementation status: **foundation delivered**. The dependency-free same-origin SDK, typed realtime envelopes, SQLite cursor repository, bounded WebSocket stream, origin policy, reconnect/resume logic, Mission Control integration, and polling fallback are implemented and tested. Remaining P2 work is request IDs, structured logging, trusted-host/rate-limit middleware, single-leader collection, and identity-scoped subscriptions.

- Introduce settings, repositories, services, routers, and dependency providers behind the compatibility entry point.
- Add versioned shared API/event schemas and SDK clients.
- Add request IDs, structured logs, bounded payloads, trusted-host checks, and rate limits.
- Add WebSocket subscriptions with resume cursors and polling fallback.
- Move telemetry collection under a single-leader service.

Acceptance:

- Route modules can be tested with in-memory repository fakes.
- Reconnect resumes without dropping approval/task transitions.
- Multiple server workers cannot duplicate collection.

## P3 — Identity, devices, and approvals

Implementation status: **partial security gate**. The server implements the [local-console device-enrollment protocol](../security/DEVICE_ENROLLMENT.md): one five-minute offer, two-minute challenges, distinct P-256 authentication and future-approval keys, encrypted pending pairing material, atomic idempotent activation as `active_unassigned`, append-only identity events, local listing, and revocation. Bootstrap schema version 2 advertises that bounded capability without claiming authentication or authority. The modular iOS source validates the advertisement and performs the exact reviewed ceremony with server-scoped Secure Enclave keys, Face ID enrollment confirmation, and crash-safe ThisDeviceOnly Keychain retry state. It still cannot authenticate an application session, receive a role, approve a privileged action, or execute.

Delivered in this slice:

- Local-console-only offer creation with exact running-server identity verification and no remote offer endpoint.
- Purpose-separated HMAC and ECDSA proof-of-possession ceremony with expiry, lockout, replay, and idempotency controls.
- AES-256-GCM protection for pending pairing keys using a separate owner-only or managed master key.
- Immutable paired public-key identity, `active_unassigned` activation, local revocation, and append-only audit state.
- Exact interoperable Swift client with pre-signing server/transcript verification, explicit review, purpose-separated Secure Enclave key policy, receipt validation, and idempotent completion recovery.

Still required before P3 acceptance:

- Add users, organizations, workspaces, roles, capabilities, and sessions; bind the existing enrolled-device records to authenticated principals and enforced requests.
- Complete a signed Xcode/physical-device pairing, biometric-change, restore, replacement, and revocation-freshness test matrix.
- Add passkey/OIDC sign-in and separate biometric step-up for privileged approvals in native clients.
- Add immutable action proposals, policy evaluation, expiry, digest, approval, rejection, and audit.
- Add a signed runner protocol and private-tunnel enrollment.

Acceptance:

- An unauthenticated or unauthorized client cannot read private state or mutate anything.
- Replaying or changing an approved proposal is rejected.
- Browser chat alone can never execute a privileged tool.

## P4 — Local-first data platform

- Add PostgreSQL migrations and pgvector.
- Add Redis streams/cache/locks and scheduler coordination.
- Add encrypted SQLite device cache and revisioned outbox.
- Add backups, restore tooling, retention, and key rotation.

Acceptance:

- Offline mutations synchronize deterministically after reconnect.
- Authorization is enforced before vector similarity results are returned.
- Backup and restore drills reproduce durable state.

## P5 — Agent execution plane

- Add provider interfaces for Lemonade, Ollama, Claude Code, Codex, and MCP.
- Add durable task graph, planner, delegation, cancellation, retries, progress, evidence, summaries, and per-agent memory scopes.
- Implement executive/planner first, then coding, research, email, calendar, finance, manufacturing, personal, memory, search, vision, voice, automation, security, and file specialists.

Acceptance:

- Every agent reports a terminal state and evidence.
- Tool grants are scoped per task and device.
- Failures and cancellations do not leave ambiguous running work.

## P6 — Knowledge and work surfaces

Implementation status: **project and repository intelligence foundation delivered**. The `server/intelligence/` platform provides stable repository fingerprints, incremental file inventory with classification, language/framework/package/build/test detection, symbol/reference extraction for ten languages, dependency and architecture graphs, change-impact estimation, explainable risk findings, ADR/convention ingestion, project memory, hybrid structured retrieval, context packs, and a cancellable background index with health diagnostics. See [Project and Repository Intelligence](PROJECT_INTELLIGENCE.md). Remaining P6 work is global cross-project search, semantic embeddings behind the existing retrieval envelope, documents/OCR, and the full Coding Studio surface.

- Documents, OCR, embeddings, semantic retrieval, and Q&A.
- Global search across authorized sources.
- Projects, Kanban, timeline, milestones, dependencies, risk, and roadmap.
- Notes, backlinks, wiki, journal, and media.
- Coding Studio with editor, terminal, Git, diff, previews, builds, worktrees, commits, and rollback through approvals.

## P7 — Executive connectors and automation

- Calendar, email, messages, weather, traffic, market, health, finance, manufacturing, travel, expense, and social connectors.
- Morning executive brief with provenance and freshness.
- Workflow builder with triggers, conditions, actions, schedules, retries, logs, and approvals.
- Native and web interactive notifications.

## P8 — Voice, vision, wearable, and operational hardening

- Wake word, transcription, speech, meeting capture, camera, OCR, screenshot/UI/diagram understanding.
- Even Reality G2 notification/navigation/caption/quick-action adapter.
- Load, failure, accessibility, security, privacy, disaster-recovery, upgrade, and performance testing.
- Signed releases, SBOM, vulnerability scanning, and documented incident response.

## Deferred by security gate

The following stay disabled until P3 acceptance passes: shell execution, Git mutation, downloads, deployments, payments, external emails/messages/posts, secret use, and remote device control.
