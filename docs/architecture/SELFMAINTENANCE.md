# Self-Maintenance and Continuous Improvement

The JoeOS Self-Maintenance and Continuous Improvement platform (`server/selfmaintenance/`)
runs real, honest maintenance checks over live JoeOS services, applies only
safe self-hygiene that never touches authority, detects evidence-based
improvement proposals, and lets the operator apply an approved proposal
through a real service executor.

## Principles

- **No fabrication.** A check whose source is unavailable or unmeasured reports
  `unknown` or `skipped` — never `ok`. A run only reports `completed` when every
  check passed.
- **No silent authority.** The coordinator never creates/reverts data or changes
  recovery state. Improvements are proposals; applying one requires operator
  approval and executes a bound, real service action (create backup, expire due
  memory, exit Safe/Repair Mode).
- **No duplicate persistent state.** The platform's own store
  (`selfmaintenance.db`) holds only run history, the proposal registry, and the
  maintenance log. Authoritative state stays in the Production, Memory, and
  core stores.

## Checks

Health checks read injected providers so they compose over live services in the
backend and fakes in tests:

- `database.healthy` — SELECT 1 on the main store.
- `event.store` — audit event table is readable.
- `telemetry.fresh` — newest `system_metrics` sample is current.
- `disk.space` — measured disk utilization (warning at/above pressure).
- `migration.status` — Production Migration Coordinator writability gate.
- `backup.verified` — at least one verified backup exists.
- `recovery.state` — Safe Mode / Repair Mode / crash-loop / interrupted update.

## Complete Health-Check Contract

State vocabulary: `ok`, `degraded`, `failed`, `warning`, `unknown`, `skipped`.
The worst-of outcome drives the run `completed`/`degraded`/`failed`.

## Improvement Proposals

Detected only from real observations, e.g.:

- `backup.initial` — no verified backup (apply: `create_backup`).
- `memory.expire` — due memory records (apply: `expire_memory`).
- `recovery.exit_safe_mode` / `exit_repair_mode`.
- `telemetry.first_sample`, `schema.future_detected` — informational only.

Lifecycle: `proposed` → (`approved` by operator) → `applied` | `dismissed`.
`not_actionable` proposals have no automated action and are honestly labeled.
`reconcile()` refreshes evidence while preserving any resolved/approved state.

## Wiring

The backend composes the service over `ProductionService` (backup list,
migrations writable, recovery state, executors) and `MemoryService`
(`count_due`, `expire_due`). A `_selfmaintenance_loop` runs the pass on a
schedule. The REST API (`/api/v1/selfmaintenance/*`) gates `run` and
`apply` behind governance.

See `STATUS.md` and `CHANGELOG.md` for the delivered scope.