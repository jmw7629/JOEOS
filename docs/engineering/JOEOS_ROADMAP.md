# JOEOS Engineering Roadmap

Authoritative roadmap abstraction for the autonomous build campaign. This is
the durable engineering backlog the Engineering Director selects from. The
machine-readable form that seeds the campaign is
`docs/roadmap/joeos-autonomous-build.roadmap.yaml`; this document is the
human-readable authority and reconciliation point.

## Authority and reconciliation

- **Live runtime evidence, current tests, and current repository** override
  stale historical documents. When documentation conflicts with what is actually
  deployed, reconcile here after verification.
- Items are imported into the campaign as WorkPackages only when their
  dependencies are satisfied (`BLOCKED`/`READY`).
- Unresolved legitimate audit items from `docs/audits/` are imported here after
  verifying current state (do not re-open items later phases already fixed).

## Backlog (curated, dependency-ordered)

| ID | Title | Area | Priority | Depends on | Status |
|---|---|---|---|---|---|
| ENG-001 | Campaign orchestration domain (state machine, roadmap, packages, checkpoints, blockers) | engineering | 10 | — | DONE |
| ENG-002 | Eight engineering role profiles in the immutable agent registry | engineering | 20 | ENG-001 | DONE |
| ENG-003 | Versioned deny-by-default autonomy policy | engineering | 30 | ENG-001 | DONE |
| ENG-004 | Worktree isolation and repo tools | engineering | 40 | ENG-001 | DONE |
| ENG-005 | Mac build-host executor (rsync + xcodebuild) | engineering | 50 | ENG-001 | DONE |
| ENG-006 | Integration gate (clean tree, on-branch, tests, no blockers) | engineering | 60 | ENG-001, ENG-004 | DONE |
| ENG-007 | Engineering Director: auto-select + repair loop + human gates + notifications | engineering | 10 | ENG-001..006 | DONE (this phase) |
| ENG-008 | Build Command Center UI (/os/build) | engineering UX | 20 | ENG-007 | DONE (this phase) |
| ENG-009 | Executive design system rollout across workspaces | design | 40 | — | IN PROGRESS (design worktree) |
| ENG-010 | Universal multimodal Joe composer (attachments, voice, Deep Dive) | UX | 50 | — | DEFERRED / parallel |
| ENG-011 | Lemonade routing + provider capability routing (vision/STT) | AI | 60 | — | DEFERRED |
| ENG-012 | Native SwiftUI composer parity | native | 70 | — | DEFERRED |

## Statuses

`BLOCKED` `READY` `ACTIVE` `VERIFYING` `DONE` `FAILED` `WAITING_FOR_HUMAN`

## Adding items

Add roadmap items to the YAML file, then import into the campaign:

```bash
.venv/bin/python scripts/activate_campaign.py --start
```

New candidates discovered by audit remain proposals until approved.
