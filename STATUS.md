# JoeOS Status

Generated: 2026-08-08.

## Current phase

Autonomous Operations (AUTONOMY-1) — persistent background agents, automations,
schedules, triggers, recovery, and notifications (delivered).

## Live agent + autonomy status

- Local AI agents operational end-to-end from the deployed browser (`/os/agents`):
  WebCrypto device enrollment/session, principal resolution, ProviderRegistry
  (Ollama local/private/healthy), ModelRegistry (10 models), AgentRun execution,
  real delegation, real TaskGraph, Council, ToolBroker (5 safe read-only tools).
  Ollama is loopback-only (`127.0.0.1:11434`); the browser never contacts it.
- Durable autonomous operations (`server/autonomous/`): AutomationDefinition +
  AutomationRun over the AgentFabric; one_time/recurring/event/condition_watch/
  manual triggers; DST-safe timezone-aware schedules; deterministic occurrence
  keys (no duplicates); durable lease claims + expired-lease recovery; bounded
  retries; pause/resume/archive; `/os/automations` browser app; durable
  notifications with deep links via the NotificationCenter. Background agents
  default to the stable 1.5B model family. Browser is never the scheduler.
- Live canaries passed: one-time background run (browser closed), recurring
  (multiple distinct occurrences, no duplicates), restart recovery (ran once
  after backend restart), pause prevention, durable completion notification with
  deep link to the exact AutomationRun.
## What works

- Mission Control, workspace configuration, widget catalog, telemetry, private Lemonade chat, PWA/native pairing, device enrollment.
- Engineering workspace: project registry, bounded filesystem, Git read/approval-gated mutation, secret scan, command validation, search.
- Project intelligence: identity + stable fingerprint, incremental file inventory with classification, language/framework/package/build/test detection, symbol/reference parsing for 10 languages, dependency and architecture graphs, change-impact, risk findings, ADR/convention ingestion, memory registry, hybrid retrieval, context packs, cancellable background indexing with health diagnostics.
- Memory and knowledge platform: typed records, entities and relationships, review workflow, retrieval, backup, expiration, provenance.
- Multi-agent collaboration and organizational intelligence: organization, units, roles and agents, charters, plans, task graphs with dependency enforcement, assignment with explanations, messaging, handoffs, artifacts, reviews and quality gates, disagreements and consensus, debates and consultations, escalations, interventions, approvals (no self-approval), budget governance, local-first model routing, deadlock/loop/stagnation detection, organizational health, performance telemetry, memory proposals.
- Plugin and extension platform: versioned manifests, Plugin Registry, Publisher Registry, package integrity, ECDSA P-256 signature evaluation, granular permissions and Capability Broker, Contribution Registry, isolated Extension Host with typed JSON RPC, extension storage/settings/secrets, bounded events, resource governor, health/logs/diagnostics, quarantine, Safe Mode, update/rollback, uninstall, development host, plugin SDK, CLI, templates, first-party example plugin, and a Plugin Manager section in the Command Center UI.
- Automation and Workflow Engine: versioned Workflow Registry, strict validation and compilation (no code execution, bounded loops, cycle detection), constrained expression language, timezone-aware scheduling with explicit DST/missed-run/overlap policies, Trigger Registry, Action Registry, Workflow Secret Broker, permission guard, idempotency/deduplication/concurrency/locks/rate limits, real execution state machine with branches, loops, parallel, retries, timeouts, approvals, user input, compensation, pause/resume/cancel, bounded run history and traces, health and stuck-run detection, safe templates, and an Automation section in the Command Center UI.
- Communications, Inbox, and Notification Hub: provider-neutral Provider/Account/Identity/Contact registries, Recipient Resolver with ambiguity blocking, typed Message Store and Draft Store, authoritative Outbox with idempotent bounded delivery, external-send approval bound to content/recipient/attachment hashes, content sanitization, link safety, phishing and prompt-injection signals, Notification Center with routing rules, quiet hours, DND, snooze, digests, attachment validation, and a Communications section in the Command Center UI.
- Smart Glasses and Wearable Platform: authoritative Device Registry, Device Type Registry, Capability Registry, plugin-based Adapter Registry, controlled discovery, secure pairing (single-use expiring codes), capability-scoped revocable trust, authenticated expiring sessions, Connection Manager with bounded backoff, granular device permissions (camera/microphone/location never default), Glance Card system with privacy modes, allowlisted wearable command gateway with confirmation levels, push-to-talk local-first voice with enforced recording indicators, explicit camera/vision gateways, checklists with required-step enforcement, trusted handoff, idempotent offline queue, battery/thermal resource governor, an isolated Wearable Simulator (no fabricated production hardware), and a Device Manager section in the Command Center UI.
- Mobile Companion and Secure Remote Operations Platform: authoritative Mobile Client Registry, Host Registry with explicit discovery, two-party secure pairing (short-lived single-use hashed codes, host + client confirmation), short-lived revocable sessions with rotating hashed refresh credentials, allowlisted Remote Command Gateway (prohibited operations rejected, raw AI never executed), Scoped Remote API backed by real authoritative state providers, safe-only idempotent offline action queue revalidated against base versions (conflicts preserve authoritative state), handoff coordinator, opaque single-use user-bound deep links, privacy-safe provider-neutral push contracts with an isolated test fixture (production APNs/FCM not claimed), immediate server-side revocation and honest lost-device mode, an extended native SwiftUI iOS policy module (`MobileCompanionPolicy`), and a Mobile Companion section in the Command Center UI.
- Security Platform and zero-trust hardening: deny-by-default Policy Registry and Evaluation Engine (typed structured rules, never eval), Identity Registry with no cross-type impersonation, Scope Resolver with explicit path containment (traversal/symlink/NUL rejection), exact-bound Approvals with risk-based strength levels (0-5) and separation of duties, authoritative Secret Broker (AES-256-GCM at rest, rotation, revocation, destination policy, masked secret detection), hash-chained Audit Log with tamper verification, Security Events, Incident Registry, Lockdown (reauthentication to exit), Emergency Stop (honest incomplete cancellation), Quarantine, per-target Circuit Breakers, Data Classification (model can never lower), Privacy Policy Engine (cloud blocked for restricted classes), Threat Model Registry for critical boundaries, and a Security Center section in the Command Center UI. No fabricated vulnerability counts, malware results, compliance claims, or security scores are produced.
- Performance and Resource Governance Platform: an authoritative, measurement-driven layer that reuses the existing telemetry/health architecture (never a second telemetry source). Provides a Performance Metrics Registry (bounded, source/availability-typed), typed Workload Classification (callers cannot self-declare critical), a 16-lane Priority Scheduler with fairness, aging, deadlines, cancellation, and queue visibility, Admission Control over real measured CPU/memory/disk/battery/thermal/model state (capacity is never fabricated), a per-scope Concurrency Governor (subsystems cannot raise their own limits), a Resource Governor with honest pressure states (GPU/VRAM/battery/thermal stay unknown until actually measurable), ordered visible Load Shedding that never sheds security/cancellation/approvals/foreground, bounded Backpressure queues that preserve security-critical and final-state events, a Cache Registry with explicit invalidation that refuses security-sensitive state and invalidates immediately on permission/session/secret changes, a Model Resource Manager (max resident models, idle unload, OOM marks resource_blocked with no endless retry, never unloads during an active request), long-session Leak Detection (single high samples are never flagged), a Benchmark Registry running real isolated fixtures with median/variance (never fastest-run cherry-pick), a versioned Performance Budget Registry scoped by hardware profile, a Regression Analyzer that only flags regressions beyond measured noise, redacted Performance Tracing (no secrets/prompts/paths/raw values), GPU honesty fixed at the telemetry source (unmeasured GPU is no longer reported as 0%), and a Performance Center section in the Command Center UI. No fabricated FPS, latency, throughput, memory, GPU, VRAM, battery, or thermal numbers are shown; unknown stays unknown.
- Product-experience polish pass: one semantic Design Token registry (color/typography/spacing/radius/elevation/motion/z-index/touch targets), a light theme plus high-contrast, reduced-effects, reduced-motion, and density presentations applied through `data-*` attributes on the shell, a Settings workspace (Appearance + Accessibility + About) persisted locally and integrated with the workspace theme engine, a centralized Keyboard Shortcut Registry with a reference dialog and new global shortcuts (Ctrl+, Ctrl+/ ?, Alt+1..0 navigation, Ctrl+Shift+N notifications, Ctrl+G search), a ranked and categorized Command Palette with keyboard navigation, focus trapping, risk labels, and honest availability, a focus-management system (skip-to-content link, reusable focus-trap utility, focus restoration on dialog/palette/drawer close, `tabIndex=-1` main content), a consistent StatusBadge primitive with non-color labels applied to production surfaces, an accessible system banner region for Lockdown, offline runtime, low-resource mode, and critical disk pressure, a cancellable AI assistant with truthful stop state, coarse-pointer touch-target bump, and calm consistent microcopy. No decorative mocks, dead controls, placeholder routes, or fabricated state were introduced; every new surface reflects authoritative service state.
- Production readiness, reliability, packaging, and release engineering: a single source of truth for the release version (`JOEOS_VERSION` in `joeos_backend.py`) with a version-authority module (`scripts/version.py`) that validates the web manifest matches and reports internal package versions, a release engineering tool (`scripts/release.py`) that verifies consistency, builds the frontend, and packages a self-contained versioned release bundle into a requested directory with a SHA-256 release manifest (source commit, component versions, per-file digests) — the tool never mutates the working tree and supports a dry-run into a temporary directory, a redacted `/_internal/diagnostics` endpoint (versions, service states, bounded counts, storage sizes — never secrets, prompts, source code, messages, raw logs, audit content, or paths), a startup data-directory writability probe, a graceful-shutdown event, a versioned web manifest, a `CHANGELOG.md`, a `docs/architecture/RELEASING.md` process guide, and a Production and Release platform (`server/production/`): automatic build metadata (never hard-coded), an honest supported-target matrix (linux + web supported; macOS/Windows/iOS/Android unsupported on this host), explicit release gates that never fabricate success (scans, signing, SBOM, and update distribution reported `not_configured`), a Migration Coordinator with lock + backup-before-risk + future-schema write-blocking, a Backup Coordinator producing verified snapshots of all stores (online SQLite backup API; verification before success; retention never deletes the only verified backup), a Restore Coordinator that stages, validates, checkpoints the current state, and resets stale authority (mobile sessions revoked, pending approvals invalidated), an Update Coordinator that verifies staged packages (hash + manifest + version) before activation, Safe Mode / Repair Mode / crash-loop detection, a `doctor` CLI, and a Production & Release workspace in the Command Center. No signing, notarization, package publication, container publication, push delivery, or network update distribution is implemented or claimed.
- Local AI Runtime platform (`server/ai/`): a provider-neutral inference registry over the private local Lemonade server (cloud providers require explicit policy approval and are never enabled silently), local-first semantic embeddings with content-hash deduplication, dimension validation, and honest availability (vectors are never fabricated), bounded context construction with full decision tracking (deduplication, token budget, privacy exclusion), and AI-assisted interpretation records that are always labeled AI-assisted with provenance — never presented as parsed facts — plus a Models & AI workspace in the Command Center.
- Self-Maintenance and Continuous Improvement platform (`server/selfmaintenance/`): a real health-check battery over live services (local database, event store, telemetry freshness, disk, schema migrations, verified backups, recovery flags), safe self-hygiene that never touches authority (bounded retention of its own registry), an evidence-based improvement proposal registry that never self-applies and never accepts memory or changes authority without operator approval, approval-gated improvement application through real executors bound to the Production (backup/safe-mode/repair-mode) and Memory (expiry) platforms, a periodic maintenance loop, honest `unknown`/`skipped` states whenever a signal is unmeasured, and a Maintenance & Improvement workspace (18th Command Center service). No maintenance state, improvement, or availability is ever fabricated.

## Test status

- Python: 837 passed, 61 subtests passed (includes 22 autonomous tests).
  One pre-existing time-dependent communications quiet-hours failure remains
  (unchanged by this work; it depends on the host clock/timezone).
- Runner: 57 passed (process safety + P3D daemon/executor suite + HTTP transport).
- Frontend: 35/35 passed.
- SDK: 14 passed (client SDK) + 6 passed (plugin SDK).

## Phase P3D status

Delivered:

- long-lived runner daemon (outbound private connection loop, heartbeat, reconnect with bounded exponential backoff and jitter, lease polling, execution, journal)
- strict typed runner configuration (unknown-field rejection, unsafe public HTTP rejection, key/configuration permission checks)
- runner CLI (identity-init/show, enrollment-sign, config validate/effective, self-test, executors list/inspect, journal inspect/verify, emergency local-stop)
- bounded, tamper-evident local execution journal (digest chain, retention)
- real development-command executor using authoritative command templates
- real constrained Git executor (temporary local repositories and a local bare remote; no external pushes)
- user-service executor (deterministic adapter; no sudo, no arbitrary systemctl)
- typed JoeOS deployment executor (exact immutable commit, release directory, health verification)
- health-check executor (typed local/private checks)
- runner-local secret provider (resolved only at launch, redaction, leak detection)
- artifact transfer metadata
- daemon cancellation, timeout, restart recovery
- installer dry-run and VPS handoff scripts (no auto-enrollment, no secrets)
- end-to-end backend-and-daemon integration via the runner transport
- real HTTP runner transport (private endpoint, X-Runner-Credential, protocol endpoints)
- `python -m joeos_runner` daemon entrypoint

## Phase P3E status (private runner activation on the local VPS)

Delivered (on the local VPS, `100.98.25.26` Tailscale origin; loopback transport):

- host validation and installer dry-run passed
- real P-256 runner identity generated locally (0600, never printed)
- real enrollment ceremony: backend challenge (nonce-bound, fingerprint-bound) -> runner `enrollment-sign` -> backend `enroll` completion
- runner enrolled (active) and connected to the authoritative backend over private loopback
- live heartbeat verified (5s interval; `last_seen_at` advances)
- daemon restart verified (new connection record; reconnect without re-enrollment)
- revocation verified (runner set revoked; daemon denied with `runner_not_active` and backoff-reconnects)
- re-enrollment verified (fresh challenge binds the same key/fingerprint; new runner active)
- journal integrity verified

Not yet delivered:

- systemd service start (requires interactive root; installer supports it via `runner/install/install-runner.sh`)
- read-only diagnostic job execution (dispatch is approval-gated; blocked pending P3F native approval validation; no bypass, no fixture keys)
- real GitHub credential validation and push from the VPS (approval-gated; pending P3F)
- real JoeOS deployment on the VPS (approval-gated; pending P3F)
- macOS runner, physical iPhone/Face ID/Secure Enclave signing and device drills (native iOS build/simulator/tests/archive validated remotely; see `docs/security/DEVICE_ENROLLMENT_P3F_B.md`)
- unrestricted shell, root execution, arbitrary remote control, payment, unrestricted email, physical-device execution
- enterprise external secret manager, VM-grade executor isolation, multi-runner production fleet
- PostgreSQL/pgvector/Redis migration

## Not yet built

- Phase 4 local-first data platform (PostgreSQL/pgvector, Redis, outbox).
- Phase 3 privileged runner: shell authority, Git mutation, deployment, secret retrieval, email, payment, and remote control remain gated at `approved_awaiting_executor`; the private runner plane executes only registered, approved, revalidated jobs through typed executor adapters. Identity, sessions, conversations, agents, action proposals, policy, approvals, the signed runner plane, and the production runner daemon ARE delivered.
- Index-at-rest encryption, cross-project queries.
- Plugin marketplace, public signing-key distribution, OS-level sandboxing, webhooks, and remote connectors (architecture documented; not implemented).
- Real external provider adapters (email/chat), mobile push, smart-glasses delivery, and read receipts (architecture documented; not implemented).
- Real wearable manufacturer adapters (Bluetooth/USB/gaze/gesture), OS-level device pairing, and real camera/microphone capture (architecture documented; only an isolated simulator produces devices).
- Native iOS simulator build, Xcode test bundle (73/73), device `arm64` Debug/Release builds, and archive validated remotely on a Mac over Tailscale; physical-device signing, install, biometric/backup/recovery/revocation drills, and App Store distribution remain (tracked in `docs/security/DEVICE_ENROLLMENT_P3F_B.md`). Production APNs/FCM push, background-execution guarantees, biometric security, universal links, and App Store distribution (contracts documented; not claimed as implemented).
- OS keychain/hardware-backed secret storage, immutable audit logs, and full OS-level sandboxing (documented limitations; AES-GCM vault, hash-chain tamper evidence, and subprocess isolation are the honest current guarantees).

See `docs/architecture/PRODUCTION_READINESS.md` for the production readiness
and release engineering design and honest guarantees,
`docs/architecture/RELEASING.md` for the release and versioning process,
`docs/architecture/EXPERIENCE_UX.md` for the product-experience polish
design and honest guarantees,
`docs/architecture/PERFORMANCE_PLATFORM.md` for the performance and
resource governance design and honest guarantees,
`docs/architecture/SECURITY_PLATFORM.md` for the security platform
design and honest guarantees, `docs/architecture/MOBILE_COMPANION.md` for the
mobile companion design and platform strategy,
`docs/architecture/WEARABLES_PLATFORM.md` for the wearable design,
`docs/architecture/COMMUNICATIONS_PLATFORM.md` for the communications design,
`docs/architecture/AUTOMATION_PLATFORM.md` for the automation engine,
`docs/architecture/PLUGIN_PLATFORM.md` for the plugin platform, and
`docs/architecture/IMPLEMENTATION_BACKLOG.md` for the dependency-ordered plan.

## Browser OS (P4-UI-A..E) status

The browser OS is developed in isolated Git worktrees and integrated through
commits. Current state (see `docs/browser-os/` for each phase):

- Foundation + routing (`/os/<app>` deep links, `OS_APPS` registry).
- Command center + conversations.
- Agents workspace (directory, missions, task graphs, council) — uncommitted in
  the agents worktree.
- Knowledge layer (Memory, Files, Universal Search, Context workspaces) —
  built on the authoritative memory/engineering/agents APIs in the knowledge
  worktree, tracked as the browser memory/search/files phase.
- Frontend: 35/35 static tests pass in the knowledge worktree.
