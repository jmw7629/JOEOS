# Agent Command Center (Section BA)

The Command Center is the operational cockpit of JoeOS. It replaced the
original agent dashboard inside `agent_fabric.html` and is modeled on the
ClawPort information architecture (see `docs/third-party/CLAWPORT.md` for the
MIT-licensed reference study and reuse ledger). No ClawPort code or assets were
copied; only the surface-level information architecture was studied.

## Surfaces

The Command Center exposes nine navigation surfaces plus a floating live
activity panel:

| Surface    | Data source (authoritative)                          | Purpose                                   |
|------------|------------------------------------------------------|-------------------------------------------|
| Overview   | `/api/v1/control/overview`                            | Team state, live stats strip, ask-Joe box |
| Org Map    | `/api/v1/agents/overview` (org tree)                  | Executive hierarchy navigation            |
| Agents     | `/api/v1/agents` (detail via `/agents/{id}`)          | Agent cards, status, detail modal         |
| Work       | `/api/v1/agents/runs` + `/api/v1/agents/packages`     | Kanban of runs + packages by status       |
| Schedule   | `/api/v1/automation/overview` + `/schedules`          | Automation schedules                      |
| Pipelines  | `/api/v1/automation/runs`                             | Automation run history                    |
| Memory     | `/api/v1/memory/records`                              | Memory search + records                   |
| Activity   | `/api/v1/agents/activity` + automation activity       | Live event feed                           |
| Models     | `/api/v1/command-center/services` + providers/models  | Service health, provider/model registry   |

## Security boundaries

- Every surface reads through the same backend API as the rest of JoeOS; the
  Command Center introduces **no** new backend endpoint and no new privilege.
- Session enforcement is unchanged: unauthenticated calls return `401`, and the
  UI shows the session-expired state rather than partial data.
- The live activity panel polls the existing activity endpoints; it performs no
  write operations.
- Mobile layout (390px) keeps the same boundaries; navigation collapses to a
  bottom button bar.

## Verification

Frontend tests: `node --test tests/frontend.test.mjs` (35 pass).
Playwright DOM/console/screenshot checks: no page or console errors across all
nine views, desktop and mobile.
