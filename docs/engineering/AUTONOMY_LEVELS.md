# Autonomy Levels

Autonomous engineering does not mean unrestricted authority. Every campaign
runs under an explicit autonomy level. Levels never grant new capabilities; they
only gate which policy constraints are already available.

| Level | Name | Permitted |
|---|---|---|
| 0 | Plan only | inspect repository, propose work, no source modification |
| 1 | Implement + verify | isolated feature branches/worktrees + tests; no automatic merge/deployment |
| 2 | Safe autonomous development (default) | implement, verify, commit, push feature branches, integrate low-risk work; production deployment gated |
| 3 | Continuous safe build | continuously select + complete low-risk roadmap items, including safe deployment when deployment policy explicitly allows |

Default: **Level 2**.

## Enforcement

- `validate_autonomy_level` rejects anything outside 0-3.
- `set_autonomy_level` requires `engineering.campaign.manage` and the campaign's
  registered autonomy policy must exist.
- The worker principal only ever holds `engineering.package.manage` — it can
  never escalate levels, resolve blockers, or approve privileged work.
- A campaign cannot raise its own level as part of ordinary implementation work.
  Policy-changing packages require explicit elevated approval.

## Gating

- Level 0 → the worker still runs but stage handlers return no source changes
  (plan-only semantics).
- Level 1 → feature branches may be created and tested; integration is blocked.
- Level 2 → ff-only integration of low-risk work + push of feature branches.
- Level 3 → safe deployments when deployment policy allows.

The autonomy policy (`joeos.engineering.ai-rebuild.v1`) binds providers, allowed
agents, protected branches, remotes, and resource limits for all levels.
