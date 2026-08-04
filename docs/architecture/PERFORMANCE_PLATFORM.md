# Performance and Resource Governance Platform

Phase 16 delivers JoeOS's authoritative Performance Optimization and Resource
Governance layer. It is measurement-driven, integrated with the existing
telemetry and health architecture (it never creates a second telemetry
source), and honest about hardware state: **unknown stays unknown**, and
nothing is fabricated.

## Design principles

1. **Measure before optimizing.** Optimizations are only claimed when a real
   benchmark or measured sample supports them.
2. **Interactive work first.** User input, cancellation, approvals, and
   security responses outrank every background workload.
3. **Bounded everything.** Queues, caches, histories, retries, workers,
   models, agents, and workflows all carry explicit limits.
4. **Backpressure over collapse.** Slow consumers are paused or coalesced;
   producers are never allowed unbounded buildup.
5. **Cancel obsolete work.** Queued workloads are cancelled when superseded or
   when Emergency Stop fires.
6. **Hardware honesty.** GPU, VRAM, battery, and thermal values are only
   reported when actually measurable. A sanitized 0.0 is never reported as a
   healthy GPU reading (this phase fixed the telemetry source so an
   unmeasured GPU stays `None`/`unknown` instead of 0%).
7. **No hidden quality loss.** Load shedding, throttling, degradation, and
   low-resource modes are always visible with a reason.
8. **Security preserved.** Security operations receive reserved capacity;
   security-critical, approval, cancellation, audit, and final-state events
   are never dropped; security-sensitive state is never cached.

## Architecture

The platform lives in `server/performance/` and is composed into the backend
as `PerformanceService`. It consumes the existing collector's host sample and
AI runtime state (from `server/` telemetry), so there is exactly one
operational telemetry source.

```
JoeOS Performance and Resource Platform
  server/performance/
    models.py          typed models (Workload, BudgetRecord, BenchmarkRecord,
                       CacheRegistration, PressureState, ResourceSnapshot, ...)
    storage.py         versioned SQLite storage with retention
    registry.py        Performance Metrics Registry (bounded histograms)
    scheduler.py       Priority Scheduler (16 lanes, fairness, aging, deadlines,
                       cancellation, queue visibility)
    governor.py        Concurrency Governor (per-scope limits)
    resources.py       Resource Governor + memory/disk/GPU pressure + load
                       shedding order + low-power/metered modes
    admission.py       Admission Control over measured load
    backpressure.py    Bounded queues with overflow policies; preserved events
    caches.py          Cache Registry + invalidation + security guard
    ai.py              Model Resource Manager (states, preflight, idle unload)
    leaks.py           Long-session Leak Detection (trend-based)
    benchmarks.py      Benchmark Registry + runner (median/variance)
    budgets.py         Performance Budget Registry (hardware-profile scoped)
    regression.py      Regression Analyzer (variance-aware)
    traces.py          Redacted Performance Tracer
    service.py         PerformanceService facade
    router.py          /api/v1/performance/* REST API
```

## Workload classification and priority

Workloads declare a class; the scheduler derives the lane. **Callers cannot
self-declare priority or the emergency/user-input lanes** (`Workload` rejects
a caller-supplied `priority`, and `emergency`/`user_input` are not valid
classes to request). The 16 lanes follow the mandated order (emergency,
user input, cancellation, approvals, foreground, interactive model, task and
mission, user builds/tests, communication, background agent, repository
indexing, semantic indexing, synchronization, maintenance, telemetry and
cleanup, speculative preload).

The scheduler provides round-robin fairness, aging so low-priority work cannot
starve forever, deadline and cancellation support, emergency reservation for
the top lanes, and queue visibility through `snapshot()`.

## Admission control

`AdmissionController.evaluate` runs before expensive work begins and returns
`admit | queue | reject | reduce_quality` using measured state:

- critical memory/disk rejects non-user-visible work; high memory/disk queues
  background work; elevated memory pauses semantic indexing;
- metered-network mode reduces sync quality;
- a model-load preflight (`estimate_model_load`) admits/rejects/wait based on
  measured available memory with a safety margin — never a fabricated capacity.

## Concurrency governor

One authoritative governor bounds concurrency per scope (global, service,
project, model, agent, workflow, plugin, network, operation). Each scope has a
shared total active-count limit. Subsystems cannot raise their own limits
(`set_limit` without the policy `override` flag refuses increases).

## Backpressure and load shedding

Eleven bounded queues cover event bus, notification delivery, telemetry,
logging, model requests, agents, workflows, plugin events, indexing, mobile
sync, and wearable delivery. Overflow policies are `reject`, `coalesce`, or
`latest_value`. Preserved event classes (security events, approval results,
cancellation requests, required audit, final task/workflow state) are never
dropped. Load shedding follows the mandated order (speculative preload first,
semantic indexing, repository indexing, synchronization, background agents,
workflow maintenance) and never sheds security, approvals, cancellation,
foreground user work, or final-state transitions. Shed decisions and reasons
are recorded and surfaced in the Performance Center.

## Caches

Every cache is registered with purpose, bounds, TTL, privacy, and explicit
invalidation triggers. Security-sensitive purposes (approvals, session
authority, revoked permissions/sessions, secrets, trust state) are refused by
construction. Invalidation tags let permission/session/secret changes clear
matching caches immediately rather than waiting on TTL. `clear-safe-caches`
only clears caches not flagged security-sensitive.

## Models

`ModelResourceManager` tracks model inventory from the real AI runtime,
load/busy/idle/unload states, max resident models, pinned models, and
idle-unload. Footprints are runtime-reported or labeled `unmeasured`. OOM
loads mark a model `resource_blocked` (no endless retry) and require a user
override. A model is never unloaded during an active request without
cancellation coordination.

## Memory, disk, GPU, battery, thermal

Pressure states derive only from measured values. GPU/VRAM/battery/thermal are
`unknown` unless the runtime/platform actually reports them. This phase also
fixed the shared telemetry source (`_host_sample`) so an unmeasured GPU is
stored as `None` rather than the previous fabricated `0.0`, and the Command
Center UI now renders unmeasured metrics as "—" instead of a misleading 0%.

## Leak detection

`LeakDetectionService` samples bounded resource kinds (listeners,
subscriptions, watchers, sockets, timers, intervals, workers, processes,
object URLs, connections). A leak is only flagged after several samples show a
sustained growth trend; a single high sample is never flagged (false-positive
review is explicit).

## Benchmarks, budgets, regressions

- Benchmarks run real, isolated, deterministic fixtures with warmup and
  multiple iterations; results report median and variance (never the fastest
  run). Scenarios cover event throughput, queue operations, cache eviction,
  scheduler operations, and database queries.
- Budgets are versioned and scoped by hardware profile; there is no universal
  budget. Checks yield `pass | warning | fail | incomparable`.
- The Regression Analyzer compares current medians against the stored baseline
  and classifies `improved | unchanged_within_noise | warning_regression |
  budget_failure | incomparable | insufficient_samples` using a safety factor
  over measured variance. Insignificant variation is never called a
  regression.

## Traces

Spans record only safe metadata: service, operation, queue vs. execution time,
status, and cancellation. The API rejects metadata keys like
`secret`/`prompt`/`token`/`path`/`query`/`message` and non-scalar values, so
private content cannot enter traces. High-volume paths use sampling.

## Integration

- Command Center lists `performance.platform` as the 15th service.
- Bootstrap advertises `performance.overview` (route) and
  `performance.platform.read` (capability) at 128 routes / 38 capabilities.
- The realtime snapshot carries a redacted `performance` summary.
- The Mobile Companion gains a scoped `performance` provider.
- Emergency Stop cancels queued performance workloads in addition to active
  workflow runs; Lockdown blocks performance mutations (unload idle models,
  clear safe caches, pause/resume indexing, low-resource mode).
- The Performance Center (`id: performance`) in `index.html` shows real load,
  pressure, queues, caches, models, leaks, benchmarks, budgets, regressions,
  traces, and settings with actions that honor governance.

## Honest limitations

- GPU/VRAM/battery/thermal are unmeasured on this host (no Lemonade runtime
  and no battery/thermal sensor source), so they are reported as `unknown`.
- Hardware-dependent benchmarks (startup, model load, first token) require
  available hardware/runtimes and are recorded as unavailable rather than
  fabricated.
- No FPS, latency, throughput, or memory improvements are claimed without a
  real measurement.
