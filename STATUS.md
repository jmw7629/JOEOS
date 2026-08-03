# JoeOS Status

Generated: 2026-08-03.

## Current phase

Phase 14 — Mobile Companion and Secure Remote Operations Platform (delivered).

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

## Test status

- Python: 401 passed, 61 subtests passed.
- Frontend: 13/13 passed.
- SDK: 14 passed (client SDK) + 6 passed (plugin SDK).

## Not yet built

- Phase 5 Local AI Runtime (`server/ai/`) — semantic embeddings, provider-neutral inference, AI-assisted interpretation (remains distinct from parsed facts when added).
- Phase 4 local-first data platform (PostgreSQL/pgvector, Redis, outbox).
- Phase 3 authority: sessions, roles, approvals, execution.
- Index-at-rest encryption, cross-project queries.
- Plugin marketplace, public signing-key distribution, OS-level sandboxing, webhooks, and remote connectors (architecture documented; not implemented).
- Real external provider adapters (email/chat), mobile push, smart-glasses delivery, and read receipts (architecture documented; not implemented).
- Real wearable manufacturer adapters (Bluetooth/USB/gaze/gesture), OS-level device pairing, and real camera/microphone capture (architecture documented; only an isolated simulator produces devices).
- Native iOS build/sign/simulator (requires Xcode, not installed on this machine), production APNs/FCM push, background-execution guarantees, biometric security, universal links, and App Store distribution (contracts documented; not claimed as implemented).

See `docs/architecture/MOBILE_COMPANION.md` for the mobile companion design
and platform strategy, `docs/architecture/WEARABLES_PLATFORM.md` for the
wearable design, `docs/architecture/COMMUNICATIONS_PLATFORM.md` for the
communications design, `docs/architecture/AUTOMATION_PLATFORM.md` for the
automation engine, `docs/architecture/PLUGIN_PLATFORM.md` for the plugin
platform, and `docs/architecture/IMPLEMENTATION_BACKLOG.md` for the
dependency-ordered plan.
