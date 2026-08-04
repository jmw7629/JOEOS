# JoeOS Changelog

All notable changes to the JoeOS Command Center are documented here, grouped
by release. The authoritative version is `JOEOS_VERSION` in `joeos_backend.py`.

## [2.0.0] — 2026-08-04

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
