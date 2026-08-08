# Executive Council

The Executive Council is orchestration over the real council architecture
(`/api/v1/control/councils`). It is not one model pretending to be several
people.

## How a council run works

1. Create a council with member agents:
   `POST /api/v1/control/councils` with `member_agent_ids`.
2. Run it: `POST /api/v1/control/councils/{id}/runs` with an objective.
3. Each member is executed as a REAL AgentRun:
   - the member run is created and persisted (`control_council_member_runs`)
   - the member's own agent version/provider/model are used
   - the result is stored per member
4. Results are aggregated; quorum (majority/unanimous) determines completion.
5. `GET /api/v1/control/councils/runs/{run_id}` returns the run with member runs.

## Bounds

- Council members are bounded by the council definition.
- Member runs are sequential (this VPS runs model calls one at a time).
- No chain-of-thought is persisted; only structured results.

## Verified

- Unanimous council completes with a real recommendation from member runs.
- A failing member reduces quorum; unanimous councils fail when a member fails.
- Member runs are persisted and count = member count.
