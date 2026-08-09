# Halo Inventory (Section C)

Captured `2026-08-09` over SSH (key-based, `joewillis@100.121.165.22`).
Authoritative for the migration; supersedes earlier discovery.

## System

| Attribute | Value |
|---|---|
| Hostname | `amd-halo` |
| OS | AMD Ryzen AI Developer Platform 1 (rex), kernel 6.18.35 |
| CPU | 32 cores |
| RAM | 125 GiB (122 GiB available) |
| Disk | 1.9T NVMe, 617G used, 1.3T free |
| GPU | AMD Strix Halo (Radeon 8050S/8060S), `/dev/dri` present |
| Tailscale IP | `100.121.165.22` (online) |

## Services

| Service | Status | Port | Notes |
|---|---|---|---|
| sshd | running | 22 | now key-auth via `joeos-vps-to-mac` |
| ollama.service | active | 11434 (all interfaces) | v0.32.5 |
| lemond.service | active | 13305 (loopback) | Lemonade Server v10.5.1, web UI |
| tailscale | running | 443 | `fd7a:...:c339:a517` v6 also listening |
| gnome-remote-de | running | 3389/3390 | RDP |
| cockpit | running | 9090 (localhost) | |

## AI runtime

### Ollama models (`/api/tags`, none loaded)

| Model | Size |
|---|---|
| `kimi-k2.7-code:cloud` | 1T INT4 |
| `qwen3-coder-next:latest` | 79.7B Q4_K_M (~48GB) |
| `qwen3-coder:30b-a3b-q8_0` | 30.5B Q8_0 (~30GB) |
| `qwen3.6:35b` | 36B (~22GB) |
| `gpt-oss:120b` | 116.8B MXFP4 (~60GB) |
| `llama3.3:70b` | 70B (ctx 131072) |
| `llama3.2:3b` | ctx 131072 |
| `deepseek-r1:14b` | 14B |

### Lemonade models (`/v1/models`, on-disk)

| Model | Recipe | Size | ctx |
|---|---|---|---|
| `Qwen3-Coder-30B-A3B-Instruct-Q4_K_M` | llamacpp | 17.3GB | 262144 |
| `gpt-oss-120b-Q4_K_M` | llamacpp | 58.5GB | 131072 |

## JoeOS artifacts already on Halo

| Path | Notes |
|---|---|
| `~/joeos-backend` | old initial import, git (2 commits), no remote, no data |
| `~/joeos-web` | stale `index.html` (+ `.save`), no git |
| `~/joeos-env` | python venv |
| `~/AI/joeos` | older `jmw7629/joeos` clone (originated on Halo) |
| `~/AI/joeos-v4` | older `jmw7629/joeos-v4` clone (with `__MACOSX`) |
| `~/doota`, `~/oom`, `~/projects`, `~/zero_capital_swarm` | unrelated |

No full canonical `JOEOS` (branch `ai-rebuild`) clone with persistent `data/`
exists on Halo; **no migration of data has occurred yet.** These pre-existing
artifacts are preserved and untouched (rollback posture).

## Key changes this session
- Added `joeos-vps-to-mac` pubkey to `~joewillis/.ssh/authorized_keys`
  (fingerprint `SHA256:p3oD3mANk+…`); key auth verified working.
- Lemonade now confirmed running (was not serving on `:13305` during the VPS
  discovery pass — it listens on loopback only, which is why the Tailnet probe
  found it closed).
