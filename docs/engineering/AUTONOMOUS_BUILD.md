# Autonomous Build — JoeOS Engineering Director

JoeOS can direct its own safe engineering work. You give Joe one high-level
objective ("Continue building JoeOS."), and JoeOS inspects the repository and
roadmap, selects the next dependency-ready work package, plans it with the
Architect, implements it with the Builder, verifies it with the Verifier,
security-reviews it with Security, commits and pushes safe feature branches, and
continues to the next package — asking you only when a real human decision,
credential, device action, or privileged approval is required.

This page describes the system as it works now, how to run it, and the exact
conditions under which it stops and asks Joe.

## One-line summary

```
Joe: "Continue building JoeOS."
→ Engineering Director resumes/creates the build campaign
→ selects next dependency-ready WorkPackage from the roadmap
→ Architect plans → Builder implements (isolated worktree) → Verifier validates
→ Security reviews → commit → push feature branch → checkpoint → next package
→ stops only when it genuinely needs Joe
```

## Components

- **EngineeringDirector** (`server/engineering/campaign/director.py`) — the
  production stage handler. Compiles each WorkPackage into bounded structured
  agent instructions and dispatches stages to the authoritative engineering
  role agents through the AgentFabric (ActionService). Applies Builder output
  inside an isolated worktree through the runner's bounded filesystem/git/test
  executors. Never grants its own capabilities.
- **CampaignService** (`server/engineering/campaign/service.py`) — durable
  state machine, roadmap queue, work packages, checkpoints, blockers, attempts,
  heartbeats. Auto-promotes queued packages whose dependencies are satisfied;
  auto-completes a campaign when every package is terminal.
- **CampaignWorker** (`server/engineering/campaign/worker.py`) — long-lived
  asyncio loop that advances the campaign each tick (30s default) even with the
  browser closed.
- **Role agents** — `engineering.director`, `engineering.architect`,
  `engineering.builder`, `engineering.verification`, `engineering.applebuild`,
  `engineering.securityreviewer`, `engineering.release`, `engineering.watchdog`
  (`server/engineering/campaign/roles.py`). Immutable profiles in the
  authoritative agent registry.
- **Autonomy policy** (`server/engineering/campaign/autonomy.py`) —
  deny-by-default constraints (local providers, allowed agents, protected
  branches, ff-only integration, limits).
- **Roadmap** — `docs/roadmap/joeos-autonomous-build.roadmap.yaml` (imported
  into the campaign) and the authoritative `docs/engineering/JOEOS_ROADMAP.md`.
- **Build Command Center** — `/os/build` (served by `build.html`), mobile-usable.
- **Ask Joe** — "Continue building JoeOS" is available from the Agents home and
  via `POST /api/v1/engineering/director/continue`.

## Autonomy levels

| Level | Name | What JoeOS may do |
|---|---|---|
| 0 | Plan only | inspect + propose; no source modification |
| 1 | Implement + verify | isolated feature branches/worktrees + tests; no auto merge/deploy |
| 2 | Safe autonomous development (default) | implement, verify, commit, push feature branches, integrate low-risk work; deployment gated |
| 3 | Continuous safe build | continuously complete low-risk roadmap items incl. safe deployment when policy allows |

The default is **Level 2**. Levels never grant new authority; they only gate
constraints the policy already allows. Escalation requires an explicit operator
action and is refused by the service for invalid values. A campaign cannot raise
its own level.

## How it works (the loop)

1. **Select**: `worker_tick` promotes `queued` packages whose dependencies are
   completed to `eligible` (auto-selection), respecting `max_parallel_packages`.
2. **Plan** (Architect agent): produce a bounded technical plan.
3. **Worktree**: isolated git worktree per package (`data/campaign-worktrees/`).
4. **Implement** (Builder agent): produce bounded file changes; the Director
   validates paths (no traversal), sizes (file + total budget), and secret scan
   before applying through the runner's filesystem executor; runs a bounded test
   battery.
5. **Validate** (Verifier agent): verdict must start with `VERIFIED`; a
   rejection routes back to the Builder for a bounded repair attempt
   (`max_attempts_per_package`).
6. **Review** (Security agent): verdict must be `PASS`/`PASS_WITH_FINDINGS`;
   `BLOCK` stops that package and raises a durable blocker.
7. **Commit / Integrate / Push**: git through the runner; protected branches are
   never mutated; pushes are feature-branch-only and never force-pushed.
8. **Checkpoint**: every stage advance is persisted; the campaign completes when
   all packages are terminal.

## Stopping and asking Joe

The campaign pauses or raises a durable blocker when:

- a WorkPackage needs a **product decision** (`human_decision`)
- work needs a **credential** (`credential_required`)
- work needs a **physical-device/GUI action** (`device_action_required`)
- a **privileged approval** is required (`approval_required`)
- **Security blocks** critical work (`security_block`)
- repeated package failure exceeds the attempt budget
- the roadmap has no READY work
- you pause it, or choose "stop after current"

Independent work continues; only the dependent package waits.

## Runtime controls

- `POST /api/v1/engineering/director/continue` — resume/start + select next work
- `POST /api/v1/engineering/campaigns/{id}/pause` / `resume` / `cancel`
- `POST /api/v1/engineering/campaigns/{id}/pause-after-current`
- `POST /api/v1/engineering/campaigns/{id}/autonomy-level` `{level}`
- `GET /api/v1/engineering/campaigns`, `/blockers`, `/checkpoints`, `/packages`

## Canaries

`python scripts/canary_engineering_director.py` proves, against a throwaway
database: multi-package continuation, repair loop, campaign auto-completion,
restart recovery, human gates, secret-scan blocking, and worker
non-escalation.
