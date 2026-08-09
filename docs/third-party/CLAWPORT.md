# ClawPort Reference

ClawPort is used strictly as an **open-source functional reference** for the JoeOS Agent Command Center. JoeOS is not ClawPort; ClawPort is not the JoeOS runtime.

## Source

| Item | Value |
|---|---|
| Project | clawport-ui |
| Repository | https://github.com/JohnRiceML/clawport-ui |
| Site | https://clawport.dev |
| License | MIT (Copyright (c) 2025 John Rice) |
| Version studied | 0.8.9 |
| Reference HEAD | `40db84d69b793048a9f738db57fe6e5db9751df3` |
| Reference copy | `/home/joewillisny/joeos-agent-ui-references/clawport-ui` (read-only, outside production tree) |

## Security difference (Section W)

JoeOS does **not** inherit ClawPort's local-only trust assumptions. JoeOS keeps:

- device enrollment + authenticated application sessions
- workspace isolation
- ToolBroker / ActionProposal / Policy / Approval
- runner signatures
- server-side provider credentials
- audit
- persistent authoritative state (SQLite, not localStorage-only conversations)

## Information architecture studied

ClawPort nav (Next.js pages): Map → Kanban → Messages → Crons → Activity → Costs → Memory → Docs → Settings.

| ClawPort surface | JoeOS Command Center equivalent |
|---|---|
| Map (`/`) | Org Map (Section Z) |
| Kanban | Work board (Section AD) |
| Messages | Agent chat via universal JoeOS composer (Section AC) |
| Crons | Schedule / cron monitor (Section AF) |
| Activity | Activity console + floating live activity (Section AI/AJ) |
| Costs | Model & compute dashboard (Section AM) |
| Memory | Memory browser (Section AK) |
| Docs | JoeOS docs |
| Settings | Provider/model assignment + auto-discovery (Section AN/O/AA) |

Key components reviewed:

- `components/OrgMap.tsx` — React Flow + dagre auto-layout, teams/hierarchy layouts, node click → agent detail. JoeOS will use its own DAG/layout within the existing frontend (no new Next.js app), using authoritative delegation state.
- `components/LiveStreamWidget.tsx` — persistent live-log widget with expand/collapse/hide, SSE buffering. JoeOS mirrors the UX but surfaces human-friendly activity first (Section AJ) from JoeOS realtime/activity sources.
- `lib/agents-registry.ts` — reads agent config files for auto-discovery. JoeOS instead discovers from authoritative `AgentProfile`/`AgentVersion`/ToolBroker/`control_agents` state (Section AA); no manual duplicate config.
- `lib/cron-pipelines.ts`, `lib/cron-runs.ts` — cron + pipeline DAG modeling. JoeOS uses `AutomationDefinitions`/`AutomationRuns` and engineering campaigns as authority (Section AF/AG), never raw shell cron.
- `components/kanban/*` — board columns/cards. JoeOS Work board derives columns from real state machine (BACKLOG/READY/WORKING/VERIFYING/WAITING FOR JOE/DONE); drag cannot bypass server validation (Section AD).

## Code reuse (Section AU)

MIT code may be reused where it materially accelerates implementation, subject to:

1. license verified (MIT, see above),
2. attribution preserved,
3. concepts ported into the existing JoeOS frontend architecture,
4. no second web application created,
5. no ClawPort auth/storage assumptions adopted.

Reused code/attribution ledger (updated as implementation proceeds):

- No third-party code has been copied yet. Concept-level porting only.
- If any component/snippet is ported, the MIT copyright notice for clawport-ui (John Rice, 2025) will be recorded here and preserved in the source.

## OpenClaw / OpenCode

- OpenClaw is **not required**. If later installed and connected by the user, an optional adapter may be added. OpenClaw does not replace AgentFabric.
- OpenCode is an optional executor only; the native Builder path is OpenCode-independent.
