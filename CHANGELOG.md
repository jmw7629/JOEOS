# JoeOS Changelog

All notable changes to the JoeOS Command Center are documented here, grouped
by release. The authoritative version is `JOEOS_VERSION` in `joeos_backend.py`.

## [2.0.0] — 2026-08-07

### Phase P3C — Signed private runner execution plane

- Runner domain (`server/runners/`): durable runner definitions, runner signing
  keys, one-time enrollment challenges, authenticated connections, health
  snapshots, the executor catalog, immutable execution jobs, secret references
  and leases, and artifact metadata.
- Private runner enrollment: one-time short-lived challenge bound to the
  installation, organization, workspace, and machine fingerprint; the runner
  proves key possession by signing `JOEOS-RUNNER-ENROLLMENT-V1`. Trusted local
  CLI for enrollment challenges, runner lifecycle, and emergency stop.
- Authenticated runner connection: outbound private connection, server-issued
  `JOEOS-RUNNER-CONNECTION-V1` challenge, connection credential rotation,
  heartbeat, and immediate revocation.
- Immutable execution jobs created only from approved action proposals with
  proposal/policy/approval digest binding, idempotency keys, and a payload
  digest. No client-created raw jobs; no client-selected proposal digest,
  policy snapshot, approval records, or runner credentials.
- Durable job leasing with generations, signed acknowledgement, lease expiry,
  restart recovery, and only one authoritative terminal state.
- Safe process foundation (`runner/joeos_runner/process.py`): `shell=False`,
  allowlisted executables, typed argument vectors, minimal allowlisted
  environment, process-group termination, bounded output, and timeout.
- Executor framework and initial adapters: read-only runner diagnostics,
  bounded workspace filesystem, and a deterministic test-only executor.
- Secret-reference broker: short-lived execution-bound secret leases, no
  plaintext retrieval endpoint, output redaction, lease revocation on
  cancellation/runner revocation.
- Cancellation, timeout, and emergency stop (dispatch pause + queued-job
  cancellation), recorded in security audit history.
- Realtime execution/runner events with bounded redacted payloads.
- Native Swift source integration: `JoeOSCore.RunnerClient` typed models and
  methods plus the execution-state model.

## [2.0.0] — 2026-08-06

### Phase P3B — Authoritative agents and approval control plane

- Authoritative provider/model registry (`server/actions/`): durable versioned
  ProviderDefinition and ModelDefinition records with health states
  (unknown/checking/healthy/degraded/unavailable/incompatible/disabled/unauthorized),
  streaming/tool-calling/structured-output capabilities, privacy and allowed
  data classifications, and audit events. Availability is backend-authoritative.
- Authoritative agent profiles with immutable agent versions: editing an agent
  creates a new version; runs bind to the version present at start; delegation
  cannot increase authority; cross-workspace access is denied.
- Agent runs and task graphs with durable states, cancellation propagation, and
  restart interruption recovery.
- Authoritative tool catalog: versioned JSON input schemas, capability
  requirements, risk classification (informational…critical), side-effect
  classification (none…privileged), and `execution_availability: unavailable`
  for privileged tools.
- Structured tool requests: provider output is parsed, validated, and persisted
  as an immutable action proposal; unknown tools, malformed or undeclared
  parameters, unsafe encodings, and traversal targets are denied.
- Deterministic policy engine: capability, status, risk-tier, reversibility,
  and separation-of-duties evaluation; deny by default on unknown state;
  persisted policy decisions with reason codes.
- Human approvals: approval requests bound to the exact proposal digest, a
  one-time `JOEOS-ACTION-APPROVAL-V1` approval challenge signed with the
  enrolled P-256 approval key, replay/expiry/digest-change/cross-workspace/
  self-approval rejection, and terminal `approved_awaiting_executor`.
- Advisory Executive Council: definitions, runs, quorum rules, member-failure
  handling, and dissent preservation; council output is advisory and never
  self-approving.
- Realtime events for agents, tasks, actions, approvals, and councils with the
  typed envelope and cursor-resume mechanism.
- Native Swift source integration: `JoeOSCore.ControlClient` typed models and
  methods plus the approval flow state machine.
- Trusted local-console CLI (`python -m server.actions.cli`) for provider,
  model, and tool inspection (never prints secret values).
- No privileged action executes: approved privileged proposals stop at
  `approved_awaiting_executor`.

## [2.0.0] — 2026-08-05

### Phase P3A — Authoritative identity, application sessions, canonical conversations

- Authority domain (`server/identity/authority_*`): users, organizations,
  workspaces, roles, capabilities (with risk classification), memberships,
  principal role assignments, device principal assignments, application
  sessions, single-use refresh credentials, authentication challenges, and
  append-only authentication events.
- Local-console CLI (`python -m server.identity.cli authority …`): idempotent
  first-owner bootstrap (no password, no second owner), device assignment and
  revocation, user status changes, and session revocation.
- Device-key application authentication: the enrolled P-256 device-authentication
  key signs a canonical `JOEOS-APPLICATION-AUTH-V1` challenge to establish a
  short-lived revocable application session (`/api/v1/auth/challenge|session|refresh|logout`).
- Protected routes deny by default (`X-JoeOS-Session` header); authenticated
  principal endpoint returns user, organization, workspace, roles, and
  capabilities.
- Canonical conversations (`server/conversations/`): stable server-assigned
  conversation ids, append-only messages with idempotency keys, durable runs,
  create/list/reopen/rename/archive, submit, retry (new related run id, no user
  duplication), server-side cancellation (queued cancels without provider work;
  running moves to `cancellation_requested`), run recovery after restart
  (`interrupted`), and `GET /events` authenticated cursor-resumable SSE.
- Realtime integration: conversation lifecycle events (`conversation.created`,
  `conversation.updated`, `conversation.archived`, `message.accepted`,
  `run.queued`, `run.started`, `run.partial`, `run.completed`, `run.failed`,
  `run.cancellation_requested`, `run.cancelled`) published to the shared events
  table with a typed envelope (schema version, org/workspace/user scope,
  conversation id, run id, timestamp, trace) and no conversation content.
- Genuine streaming: provider capability negotiation (`supports_streaming`),
  real SSE partials when the selected provider streams; honest single completed
  delta otherwise. Test-only deterministic streaming/non-streaming providers.
- Native Swift source integration (`JoeOSCore`): `ApplicationSessionClient`,
  `ApplicationSessionManager` (full state machine, Keychain storage, serialized
  refresh rotation, credential clearing), `ConversationClient` (canonical
  conversations + streaming + event subscription), and
  `JoeOSIntelligence.BackendConversationPersisting`.

## [2.0.0] — 2026-08-04

### Phase 19 — Self-Maintenance and Continuous Improvement

- Self-Maintenance platform (`server/selfmaintenance/`): a real health-check
  battery over live services (database, event store, telemetry freshness, disk,
  schema migrations, verified backups, recovery flags), safe self-hygiene that
  never touches authority (bounded retention of its own registry), an
  evidence-based Continuous Improvement proposal registry that never
  self-applies, approval-gated improvement application through real executors,
  a periodic maintenance loop, honest `unknown`/`skipped` states when a signal
  is unmeasured, and a Maintenance & Improvement workspace in the Command
  Center (18th service).
- `MemoryService.count_due()` read-only expiry observation.
- Bootstrap: 3 new `selfmaintenance.*` routes (swapped 3 intelligence detail
  routes) and 3 new capabilities; routes stay at exactly 128.

### Phase 18 — Production readiness, reliability, packaging, release

- Production and Release platform (`server/production/`): automatic build
  metadata, honest supported-target matrix, explicit release gates (never
  fabricated), Migration Coordinator with backup-before-risk and
  future-schema write-blocking, verified Backup and staged Restore with
  security-state reset, staged Update verification, Safe Mode / Repair Mode /
  crash-loop recovery, and a `doctor` CLI.
- Version authority (`scripts/version.py`), release tool (`scripts/release.py`
  with SHA-256 release manifest), redacted `/_internal/diagnostics`, data-dir
  writability probe, graceful shutdown event, versioned web manifest,
  `CHANGELOG.md`, and `docs/architecture/RELEASING.md`.
- Production & Release workspace in the Command Center.

### Phase 18 earlier — Local AI Runtime

- Provider-neutral Local AI Runtime platform (`server/ai/`): inference
  registry over the private local Lemonade server (cloud never silent),
  local-first embeddings with content-hash deduplication and honest
  availability, bounded context construction, and AI-assisted interpretation
  with provenance. Models & AI workspace in the Command Center.

### Phase 17 — UI/UX polish, interaction, accessibility, responsiveness

- Semantic design token registry (color, status, spacing, radius, elevation,
  motion, z-index, touch targets).
- Light theme plus high-contrast, reduced-effects, reduced-motion, and density
  presentations via `data-*` shell attributes.
- Settings workspace (Appearance + Accessibility + About) persisted locally.
- Centralized keyboard shortcut registry with a reference dialog and new
  global shortcuts.
- Ranked, categorized, focus-trapped command palette with risk labels.
- Skip-to-content link, reusable focus-trap utility, focus restoration, and
  input focus-ring fixes.
- Consistent status-badge primitive; accessible system banner region
  (Lockdown, offline, low-resource, disk-critical).
- Cancellable AI assistant with truthful stop state; coarse-pointer touch
  targets.

### Phase 16 — Performance optimization and resource governance

- Authoritative Performance Platform (`server/performance/`): metrics
  registry, priority scheduler, admission control, concurrency governor,
  resource governor, load shedding, backpressure queues, cache registry,
  model resource manager, leak detection, benchmarks, budgets, regressions,
  and redacted tracing.
- Performance Center workspace; honest GPU telemetry (unmeasured stays
  unknown, never 0%).

### Phase 15 — Security, permissions, secrets, zero trust

- Deny-by-default policy engine, identity/scope services, exact-bound
  approvals, AES-GCM secret broker, hash-chained audit, Lockdown, Emergency
  Stop, quarantine, circuit breakers, data classification, privacy policy
  engine, threat models, and a Security Center workspace.

### Phases 9–14 — Platforms

- Multi-agent collaboration, plugin/extension platform, automation engine,
  communications hub, wearables platform, and mobile companion were delivered
  in prior phases with authoritative local-first state.

### Phases 1–8 — Foundation

- Core OS Foundation, Executive Command Center, workspace configuration,
  telemetry, private local AI runtime integration, engineering workspace,
  project/repository intelligence, and memory/knowledge platform.
