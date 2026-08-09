# HALO Migration Baseline

Captured **2026-08-09** before any Halo migration work, per the JoeOS Engineering Directive.

## Source host (VPS)

| Item | Value |
|---|---|
| Hostname | `mcso9tqzb9` |
| Public IP | `64.202.186.3` |
| Tailscale IP | `100.98.25.26` |
| Tailscale FQDN | `mcso9tqzb9.tailb9395f.ts.net` |
| OS / arch | Linux (2 vCPU), `uname` see inventory |
| Repository | `/home/joewillisny/JOEOS` |
| Branch | `ai-rebuild` |
| git HEAD | `b59aa4ceea4edbfd903b8d966c440f5a439bd306` |
| origin/ai-rebuild | `b59aa4ceea4edbfd903b8d966c440f5a439bd306` (in sync) |
| Remote | `https://github.com/jmw7629/JOEOS.git` |
| Backend proc | `uvicorn joeos_backend:app --host 127.0.0.1 --port 8080` (pid 3696390) |
| Legacy proc | `uvicorn ... --port 8123` (pid 3230680, do not use) |
| Relay proc | `relay.py` on `100.98.25.26:8080` (pid 1822980, unrelated) |
| Caddy | `:80` → `127.0.0.1:8080`; `:8091` → `127.0.0.1:8080` |
| Funnel | `https://mcso9tqzb9.tailb9395f.ts.net` |

### Git state

- Working tree clean (no uncommitted changes).
- Worktrees:
  - `/home/joewillisny/JOEOS` (ai-rebuild)
  - `/home/joewillisny/JOEOS/data/campaign-worktrees/campaign-campaign-domain` (branch `campaign-campaign-domain`)
  - `/home/joewillisny/joeos-agent-repair-worktree` (`feature/agent-live-repair`)
  - `/home/joewillisny/joeos-browser-agents-worktree` (`feature/browser-agent-workspaces`)
  - `/home/joewillisny/joeos-browser-execution-worktree` (`feature/browser-approvals-executions`)
  - `/home/joewillisny/joeos-browser-knowledge-worktree` (`feature/browser-memory-search-files`)
  - `/home/joewillisny/joeos-browser-os-worktree` (`feature/browser-os-foundation`)
  - `/home/joewillisny/joeos-browser-providers-worktree` (`feature/browser-providers-integrations`)
  - `/home/joewillisny/joeos-executive-design-worktree` (`feature/executive-joeos-design`)
- Tags: `joeos-ui-before-executive-redesign`
- Recent history: `b59aa4c fix: tolerate corrupt agent status...`, `3202a8d docs: record autonomous self-directed development status...`, `a047d28 test: cover Engineering Director...`, `3dcbcfb feat: add Build Command Center...`, `8827fa1 feat: add Engineering Director...`

### Frontend artifact hashes

| File | SHA-256 |
|---|---|
| `index.html` | `be0221f9c9ac9b20815a95da3d16630f9a2c8822cb138e40e1904db288ae11ee` |
| `build.html` | `a3184fe91edcad0765db8bd87f82b36e6e876991d0f04042ab3ad24a419bb3de` |
| `agent_fabric.html` | `acd48b2cb5b00d0852645f26185e3be24e7a404836738b254c966a9b8b2f5127` |

## Persistent data

### `data/joeos.db` (primary) — 1.4 MB + 4.0 MB WAL

Tables (authoritative runtime/control state):

- `authority_*` — application sessions, auth challenges, roles, users, workspaces, device principals, capabilities
- `control_*` — agents (incl. engineering + joeos role profiles), agent runs, providers, models, tools, policy decisions, approval requests/decisions
- `engineering_*` — campaigns, work packages, attempts, checkpoints, blockers, heartbeats, roadmap
- `runner_*` — runner definitions, keys, enrollment challenges, connections, health, executors, execution jobs, artifacts, events
- `conversation*` — conversations, messages, runs
- `device_*` — enrollment challenges/completions, identity key metadata, pairing offers
- `enrolled_*` — device keys, devices
- `secret_*` — secret references, leases
- `server_identity`, `system_metrics`, `widget_catalog`, `workspace_widgets`, `workspaces`, `projects`, `events`, `bots`, `document_state`

### Campaign state (live)

- Campaign `camp-0ab88ce76140432886b30ed1` key `joeos-autonomous-build`, **state `active`**, autonomy level **2**.
- Work packages: `campaign-domain` **blocked** (2 attempts), `role-profiles`, `autonomy-policy`, `worktree-isolation`, `apple-build-executor`, `integration-gate` (queued).
- Open blocker `blk-1a9ebad550f14a66a9a31894`: `campaign-domain` → `gate_failed` → "worktree path not empty" (the worktree `/home/joewillisny/JOEOS/data/campaign-worktrees/campaign-campaign-domain` already exists from an earlier run).

### Per-service databases

| Path | Size | Notes |
|---|---|---|
| `data/agents/agents.db` | 316 KB | org + AgentProfiles (incl. E2E Agent row, status fixed to `configured`) |
| `data/memory/memory.db` | 192 KB | structured memory records |
| `data/memory.db` | 192 KB | legacy memory store |
| `data/automation/automation.db` | 184 KB | workflow defs/runs/schedules |
| `data/security/security.db` | 164 KB | security events |
| `data/communications/communications.db` | 172 KB | notifications/interruption policy |
| `data/mobile/mobile.db` | 148 KB | mobile control |
| `data/plugins/plugins.db` | 152 KB | plugin registry |
| `data/wearables/wearables.db` | 192 KB | wearables |
| `data/intelligence/intelligence.db` | 140 KB | intelligence/telemetry |
| `data/performance/performance.db` | 92 KB | performance metrics |
| `data/selfmaintenance/selfmaintenance.db` | 88 KB | self-maintenance |
| `data/autonomous/autonomous.db` | 88 KB | autonomous subsystem |
| `data/ai/ai.db` | 48 KB | AI registry |
| `data/production/production.db` | 20 KB | production/release state |

All DBs use WAL mode (`.db-wal`/`.db-shm` present). `PRAGMA integrity_check` on `data/joeos.db` → **ok** (see migration log for per-file verification).

### Automation state

- Workflow `acme.api_comms` ("API Comms"), version `1.0.0`, **enabled**, health `inactive`.
- No schedules (`workflow_schedules` empty).
- 5 historical runs, all `succeeded`, trigger `manual` (no unattended scheduler runs).

### Runner state

- Runner `213a91d9-6add-40ea-ae55-6501fffe9490` (`runner-key-1`): **active / healthy**, last_seen `1786291366311`.
- Runner `88c544e5-ceb6-48ac-af5b-0a353252ba34`: **revoked**.
- Enrollment mechanism: `python -m server.runners.cli {enroll-challenge,runners,enroll,revoke,emergency-stop}`.

### Agent bindings (control_agents)

- Engineering role agents: `engineering.director`, `engineering.architect`, `engineering.builder`, `engineering.verification`, `engineering.applebuild`, `engineering.securityreviewer`, `engineering.release`, `engineering.watchdog` — all `active`, provider policy `backend`.
- JoeOS agents: `joeos.joe`, `joeos.architect`, `joeos.builder`, `joeos.researcher`, `joeos.verifier`, `joeos.security` — all `active`, provider policy `ollama`, model `qwen2.5-coder:*` (small 1.5B/7B due to VPS RAM).

### Provider/model registry (control_providers / control_models)

- Provider: `92866386-...` (Ollama, `qwen2.5-coder` models, context 131072, tool-calling+streaming, no vision).
- DeepSeek-r1:14b (reasoning=1) registered.

## Secrets / keys

- `data/identity-master.key` — 32 bytes, mode `600`, present (host-bound master key for identity layer).
- `.gitignore` excludes `data/`.
- SSH keys on VPS: `~/.ssh/joeos_vps2mac` (pair), `authorized_keys` contains a Termius-generated ed25519 key (device-bound).
- No known credentials checked into repo.

## Service definitions

- Caddy: `/etc/caddy/Caddyfile` (`:80`, `:8091` → 127.0.0.1:8080).
- Tailscale: funnel on `mcso9tqzb9.tailb9395f.ts.net`.
- No systemd unit for the backend (tmux session `joeos-backend`).
- `lemond.service` (Lemonade) on VPS: installed, currently **active** (started this session; no models installed → health ok, available_models []).
- Ollama on VPS: `ollama serve` running on `127.0.0.1:11434`; models include qwen2.5-coder 1.5b/7b/14b, deepseek-r1:14b.

## Not migrated (rebuildable/cache/irrelevant)

- `data/campaign-worktrees/*` (git worktrees — rebuildable from repo; the `campaign-domain` worktree will be cleaned during cutover).
- `node_modules`, Python `.venv`, `__pycache__`, compiled frontend caches.
- Model caches (Ollama/Lemonade blobs) — Halo already has its own large models.
- `*.db-wal`/`*.db-shm` transient files (checkpointed during consistent DB backup).

## Rollback

- Rollback tag created: `joeos-before-halo-migration` (see git).
- Full persistent-state backup staged at cutover time (Section I).
- VPS is preserved intact; retirement requires explicit Joe approval.
