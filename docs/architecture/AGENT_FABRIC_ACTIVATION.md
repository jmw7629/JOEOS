# Agent Fabric Activation (Phase P3G)

Date: 2026-08-07. Branch: `ai-rebuild`.

This document is the activation record for the durable autonomous engineering
campaign. It complements `AGENT_FABRIC_INTEGRATION_AUDIT.md` (the evidence
matrix) and describes what was built, how it was validated, and how it runs.
No second agent framework was introduced; the campaign drives the existing
authoritative stores (identity, control agents, runner executors) through a
persisted state machine.

## Deliverables

| Component | Path | Purpose |
| --- | --- | --- |
| Campaign domain | `server/engineering/campaign/` | `models.py`, `state_machine.py`, `storage.py`, `service.py`, `router.py` — durable campaign/work-package state machine, roadmap queue, heartbeats, blockers, checkpoints, attempts, restart recovery. |
| Roadmap parser | `server/engineering/campaign/roadmap.py` | Validates the `ROADMAP_SCHEMA_V1` YAML into typed `RoadmapEntry` objects. |
| Autonomy policy | `server/engineering/campaign/autonomy.py` | `joeos.engineering.ai-rebuild.v1`, deny-by-default, local providers only, 8 roles, protected branches, ff-only. |
| Role profiles | `server/engineering/campaign/roles.py` | Eight immutable agent profiles registered through `ActionService.create_agent`. |
| Multi-agent graph | `server/engineering/campaign/graph.py` | Stage → role agent mapping and per-package execution plan. |
| Integration gate | `server/engineering/campaign/gate.py` | Clean tree + on-branch + tests green + no open blockers; unmeasured is never success. |
| Campaign worker | `server/engineering/campaign/worker.py` + `CampaignService.worker_tick` | Long-lived asyncio loop advancing eligible packages one stage per tick, bounded by the campaign concurrency cap and dependencies. |
| Activation CLI | `scripts/activate_campaign.py` | Seeds roles, creates campaign, imports roadmap, materializes work packages, optional `--start`. Idempotent. |
| Canary | `scripts/canary_work_package.py` | End-to-end work package against a scratch repo with a local remote, using the real GitExecutor + a deterministic handler. |
| Runner git ops | `runner/joeos_runner/operations.py` | `worktree_add/list/remove`, `ff_integrate`, `stage_all`, `commit`, `push_branch` with branch/path validation and secret scan. |
| Apple build executor | `runner/joeos_runner/operations.py` `AppleBuildExecutor` | Allowlisted rsync + xcodebuild operations for the Mac host. |
| OpenCode adapter | `runner/joeos_runner/opencode_executor.py` | Bounded `opencode run --format json` invocation with allowlisted models and worktree root enforcement. |

## How the campaign runs

1. **Activation** (one time, idempotent): `python scripts/activate_campaign.py [--start]`.
   - Seeds the eight engineering role profiles.
   - Creates `joeos-autonomous-build` (key) if absent.
   - Imports `docs/roadmap/joeos-autonomous-build.roadmap.yaml` (6 work packages).
   - Materializes one `WorkPackageRecord` per roadmap entry with dependency edges.
   - `--start` transitions the campaign to `active`.
2. **Worker**: the backend lifespan starts `CampaignWorker` as an asyncio task
   when `JOEOS_CAMPAIGN_WORKER=true` (default). Each tick it calls
   `CampaignService.worker_tick`, which selects up to `max_parallel_packages`
   eligible packages per active campaign (dependencies satisfied, state
   executable) and advances each one stage via `advance_package`.
3. **Stage handler**: injected at construction. The production handler in
   `joeos_backend.py` dispatches executable stages to the runner executors:
   `worktree` → GitExecutor `worktree_add`, `implement` → OpenCodeCodingExecutor
   (qwen2.5-coder:7b), git stages → GitExecutor. If the handler is unavailable
   the state machine advances without executing work (safe-by-default).
4. **Persistence**: every mutation goes to the campaign store (SQLite).
   `recover_after_restart` requeues in-flight packages on backend restart.

## Validation performed

- `tests/p3g_campaign_test.py` — 104 tests: state machine, autonomy policy,
  roadmap parsing, role profiles, service transitions, packages, watchdog,
  checkpoints, attempts, gate, graph, HTTP integration, worker, security.
- `tests/p3g_activation_test.py` — 3 tests: full activation flow on a scratch
  DB (bootstrap → seed → create → import → materialize → start).
- `runner/tests/test_p3g.py` — 22 tests: worktree isolation, ff-integrate,
  stage_all, Apple build executor, OpenCode adapter.
- Canary (`scripts/canary_work_package.py`): one real work package completed
  in 9 ticks through all ten stages against a scratch repo, and the branch was
  pushed to a local remote (`pushed_to_remote: true`).
- Full suite: 792 Python tests + 57 runner tests + 27 frontend contract tests
  pass.

## Security posture (P26)

- Capability enforcement is strict: creating/starting/pausing/cancelling a
  campaign, importing roadmaps, and resolving blockers each require distinct
  capabilities. The worker principal holds only `engineering.package.manage`
  and cannot create campaigns, read campaign lists, or resolve blockers.
- The router now translates `CampaignError` (including capability denials) to
  proper HTTP status codes on every route, including `list_campaigns` (was a
  latent 500).
- No approval bypass: the worker cannot reach campaign-control or
  blocker-resolution capabilities; there is no self-approval path.
- Secret hygiene: campaign events and records never contain secret-like
  values; git operations run a secret scan before commit/integrate/push.

## Runtime controls

| Env var | Default | Effect |
| --- | --- | --- |
| `JOEOS_CAMPAIGN_WORKER` | `true` | Run the campaign worker asyncio task. |
| `JOEOS_CAMPAIGN_WORKER_INTERVAL` | `30` | Worker tick interval in seconds. |

The production campaign is registered in the live database in the `proposed`
state (not started) so the worker does not execute work until an explicit
`python scripts/activate_campaign.py --start` or `POST .../start`.

## Deliberate non-goals

- No second agent framework (per audit).
- No reference-code copying.
- No generic shell/SSH executor; Apple builds go through the typed executor only.
- No OpenCode TUI automation; only the documented noninteractive `run` interface.
