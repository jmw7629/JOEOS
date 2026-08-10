# Mission Control Reference

Mission Control (`MeisnerDan/mission-control`) is used strictly as an
**open-source functional reference** for real-time agent-activity visibility.
JoeOS is not Mission Control; no AGPL code is copied. JoeOS implements the
concept with its own authoritative architecture and data.

## Source

| Item | Value |
|---|---|
| Project | mission-control |
| Repository | https://github.com/MeisnerDan/mission-control |
| License | AGPL-3.0 |
| Version studied | 0.10 |

## License posture

Mission Control is **AGPL-3.0**. Per the repo's third-party policy (same as the
MIT ClawPort precedent), JoeOS does **not** copy Mission Control code. The
feature set is ported conceptually into JoeOS's existing architecture, using
JoeOS's authoritative runtime state (AgentFabric, `control_agent_runs`,
providers, models, realtime stream). No AGPL obligations are triggered because
no AGPL source is incorporated.

## Feature concept ported (2026-08-10)

Mission Control's core value — **"see everything your agents are doing in real
time"** — is implemented in JoeOS as **Agent Mission Control**:

- Backend `GET /api/v1/control/mission`: live agent state from authoritative
  `control_agent_runs` — running runs (agent/model/provider/elapsed), recent
  completions/failures (status/objective/duration/tokens), and honest aggregate
  stats (running count, active agents, completed/failed today, tokens today).
- Frontend **Mission** view in the Agent Command Center (`agent_fabric.html`):
  live stats strip, running-agent cards with elapsed timers, and a human-first
  activity feed; refreshes via the JoeOS realtime WebSocket (agent events) with
  a 10s polling fallback.
- Auth-gated (requires an application session); only authorized agent state is
  exposed. No secrets, no credentials, no external services.

## Not adopted

- Mission Control's local JSON file store (JoeOS uses authoritative SQLite state).
- Its Claude-Code-specific execution loop (JoeOS uses its own AgentFabric /
  runner execution plane).
- Its Field Ops / external-action spend controls (JoeOS's own approval/policy/
  runner gates are the execution authority).
- Any AGPL source code or components.
