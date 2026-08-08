# Triggers

First-class trigger types:

- **one_time** — run once at an exact timestamp.
- **recurring** — deterministic recurring schedule (daily/weekly/monthly/interval).
- **event** — react to a durable JoeOS event class (filters are server-side).
- **condition_watch** — check a deterministic backend condition periodically
  (never a per-minute LLM call); minimum interval 300s.
- **manual** — Run Now.

No arbitrary shell-based cron jobs and no arbitrary command-as-schedule. All
schedules are structured and validated server-side.

## Event / condition filtering

Filtering is server-side, never fetched-into-the-browser-then-filtered. Bounded
filters cover workspace, agent, provider, runner, execution category, and risk.
Condition watches use deterministic backend checks (e.g. runner healthy,
provider recovered); AgentFabric is only used when semantic reasoning is
genuinely required.
