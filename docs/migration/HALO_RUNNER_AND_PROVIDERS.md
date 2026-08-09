# Halo Runner Enrollment (Section F) + Provider Readiness

## Runner (enrolled `2026-08-09`)

- Halo runner: `5299c2ea-b4d4-4214-9f8f-642705364201` (`halo-amd-ryzen-ai`)
- Machine fingerprint: `1fe8a0e16582a1b65246719420d251a2` (P-256 key, generated
  on Halo; private key at `/var/lib/joeos-runner/runner-key.pem` mode 0600,
  owner `joeos-runner`, never transmitted)
- Enrolled against the **authoritative VPS backend** via the two-sided
  ceremony (`server.runners.cli enroll-challenge` → `joeos_runner.cli
  enrollment-sign` → `server.runners.cli enroll`).
- Staging connectivity verified: with the Halo backend running on loopback
  (`JOEOS_CAMPAIGN_WORKER=false JOEOS_AUTONOMOUS_WORKER=false`), the runner
  connected, authenticated, and leased jobs (`POST /api/v1/runner/lease` →
  `200`), and its DB record flipped to **active / healthy**.
- Deployment: `joeos-runner.service` (systemd, hardened unit, user
  `joeos-runner`, `NoNewPrivileges`, private tmp, read-only repo).
  `backend_url` = `http://127.0.0.1:8080` (loopback-only policy; will connect
  once the Halo backend is authoritative at cutover).
- Config mirrors the VPS runner pattern (executors
  `joeos.test.deterministic,joeos.runner.diagnostics,joeos.workspace.filesystem`,
  5s heartbeat).
- Note: `joeos-runner` daemon entrypoint is `python -m joeos_runner`
  (`__main__.py`); the CLI (identity/enrollment) is `python -m joeos_runner.cli`.
  The runner checkout lives at `/opt/joeos` (owner `joeos-runner`); the Halo
  authoritative backend checkout is `/home/joewillis/JOEOS` (owner `joewillis`).

## Provider readiness

- Ollama provider: `control_providers` row is `key=ollama`, `status=active`,
  `health=healthy`, `endpoint_reference=loopback` → resolves to Halo's local
  Ollama (`http://127.0.0.1:11434`, v0.32.5) when the Halo backend is
  authoritative. No code/config change needed.
- Lemonade provider: `LocalLemonadeProvider` already wired in
  `server/ai/providers.py` (chat/stream/embed), driven by `LEMONADE_BASE_URL`
  (default `http://127.0.0.1:13305/v1`). Reachable on Halo.
- Model registry: 8 Ollama models on Halo are registered `active`; the VPS-only
  qwen2.5 models are `disabled`. `endpoint_reference` never exposes a private
  host; browsers reach models only through the backend.

## Canary results (Section AX, bounded)

| Model | Provider | Result |
|---|---|---|
| `llama3.2:3b` | Ollama | OK (1.8 s) |
| `qwen3-coder:30b-a3b-q8_0` | Ollama | OK (7.4 s) |
| `qwen3-coder-next:latest` | Ollama | OK (11 s) |
| `Qwen3-Coder-30B-A3B-Instruct-Q4_K_M` | Lemonade | **FAILED** — checkpoint dir is an empty stub (4 KB); loader queries Hugging Face → 404 `model_load_error`. Real weights not on disk (pre-existing Halo state). |
| `gpt-oss-120b-Q4_K_M` | Lemonade | **FAILED** — same stub-checkpoint cause. |

Action for Lemonade: either download real weights (`lemonade pull`) or rely on
Ollama for inference post-cutover. Ollama alone satisfies the coder workload.
**Decision (2026-08-09): rely on Ollama post-cutover; Lemonade stays gated off
until real weights are pulled on Halo.**

## Additional Section AX verification (2026-08-09)

- Context lengths (`/api/show`): kimi-k2.7-code 262144, qwen3-coder-next 262144,
  qwen3-coder:30b-a3b-q8_0 262144, qwen3.6:35b 262144, gpt-oss:120b 131072,
  llama3.3:70b 131072, llama3.2:3b 131072, deepseek-r1:14b 131072.
- Tool calling (`/api/chat` + `get_weather` tool): confirmed on llama3.2:3b,
  qwen3-coder:30b-a3b-q8_0, qwen3-coder-next:latest, llama3.3:70b; none on
  deepseek-r1:14b, gpt-oss:120b, qwen3.6:35b.
- Model registry on Halo: 8 Ollama models `active`; VPS-only qwen2.5 models
  `disabled`. Coder + reasoning + tool-calling workloads are satisfied by Ollama.

## Migration completion (2026-08-09, JOEOS-COMPLETION-LOOP-1)

- **Halo is now the authoritative JoeOS intelligence/engineering host**:
  `/home/joewillis/JOEOS` synced to `5e30607`, authoritative persistent state
  migrated (cutover swap; Halo's prior test data preserved at
  `data.pre-cutover-20260809T212626Z`).
- Halo backend running on `0.0.0.0:8080`, reachable over Tailscale Serve HTTPS
  at `https://amd-halo.tailb9395f.ts.net`.
- Halo runner `5299c2ea` reconnected to the authoritative Halo backend
  (active/healthy, heartbeat live). VPS runner `213a91d9` continues against the
  VPS rollback backend.
- Agents re-bound to Halo large models by capability (fix: `update_agent` now
  persists provider/model policy):
  joeos.joe→qwen3-coder-next:latest, architect/builder/verifier→
  qwen3-coder:30b-a3b-q8_0, researcher→qwen3.6:35b, security→llama3.3:70b.
  Engineering roles resolve `backend` → qwen3-coder:30b-a3b-q8_0 default.
- Autonomous execution validated on Halo: joeos.joe run → qwen3-coder-next →
  succeeded.
- Remaining: `/opt/joeos` runner checkout refresh requires root on Halo
  (privileged action). VPS stays intact as rollback.
