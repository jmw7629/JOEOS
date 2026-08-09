# Halo AI Runtime

Live discovery **2026-08-09** via authorized runner architecture (tailnet, loopback-only where possible). This replaces any remembered model inventory.

## Ollama (Halo)

- Version: **0.32.5** (queried `100.121.165.22:11434/api/version`).
- Health: `/api/tags` 200. API reachable on the tailnet address.
- Models installed:

| Model | Family | Params | Quant | Size (approx) | Notes |
|---|---|---|---|---|---|
| `kimi-k2.7-code:cloud` | kimi-k2 | 1042B | INT4 | — | cloud-scale, not local |
| `qwen3-coder-next:latest` | qwen3next | 79.7B | Q4_K_M | ~48 GB | large coder |
| `qwen3-coder:30b-a3b-q8_0` | qwen3moe | 30.5B | Q8_0 | ~30 GB | MoE coder, q8 |
| `qwen3.6:35b` | qwen35moe | 36.0B | Q4_K_M | ~22 GB | MoE general |
| `gpt-oss:120b` | gptoss | 116.8B | MXFP4 | ~60 GB | big general |
| `llama3.3:70b` | llama | 70.6B | Q4_K_M | ~39 GB | general, ctx 131072, embed 8192 |
| `llama3.2:3b` | llama | 3.2B | Q4_K_M | ~1 GB | small fast, ctx 131072, embed 3072 |
| `deepseek-r1:14b` | qwen2 | 14.8B | Q4_K_M | ~8 GB | reasoning, MIT |

Current `ollama ps`: no models loaded (all idle).

## Lemonade (Halo)

- Version: **10.5.1** (`/usr/bin/lemonade`). Service `lemond.service` **active/running**.
- Bind: `127.0.0.1:13305` + `[::1]:13305` (loopback only — this is why the
  earlier Tailnet probe from the VPS saw the port closed; correct posture).
- API: OpenAI-compatible at `http://127.0.0.1:13305/v1/models`; also a local web UI.
- Models on disk:

| Model | Recipe | Size | ctx |
|---|---|---|---|
| `Qwen3-Coder-30B-A3B-Instruct-Q4_K_M` | llamacpp | 17.3 GB | 262144 |
| `gpt-oss-120b-Q4_K_M` | llamacpp | 58.5 GB | 131072 |

- Not yet integrated with the JoeOS ProviderRegistry (Section F/ModelCannon work).
- **Canary result (2026-08-09):** `/v1/models` advertises both models as
  `downloaded: true`, but the checkpoint dirs under
  `/var/cache/models/lemonade/` are empty stubs (4–8 KB); loading fails with
  Hugging Face 404 `model_load_error`. Ollama on Halo is fully functional
  (8 real models, live inference verified). Lemonade inference is **not**
  usable until real weights are present on disk.
- **Decision (2026-08-09):** rely on **Ollama** for inference post-cutover.
  Lemonade remains registered but is gated off until a human on Halo runs
  `lemonade pull` for real weights; Ollama alone satisfies the coder workload.

## Private access (Section E)

- Ollama currently listens on the Halo and is reachable on the tailnet address. Per JoeOS policy, browsers/clients must reach models **only** through the JoeOS backend → ProviderRegistry → model provider. Raw Ollama/Lemonade ports must not be exposed to the public internet.

## Capability matrix (verified by Section AX canaries, 2026-08-09)

| MODEL | PROVIDER | CAPABILITIES | CONTEXT | TOOL CALLING | VISION | CODING | REASONING | EMBEDDING |
|---|---|---|---|---|---|---|---|---|
| kimi-k2.7-code:cloud | Ollama | coding | 262144 | not probed (cloud) | no | strong | yes | no |
| qwen3-coder-next:latest | Ollama | coding | 262144 | **yes** | no | strong | yes | no |
| qwen3-coder:30b-a3b-q8_0 | Ollama | coding | 262144 | **yes** | no | strong | yes | no |
| qwen3.6:35b | Ollama | general | 262144 | no (probe) | no | good | yes | no |
| gpt-oss:120b | Ollama | general | 131072 | no (probe) | no | good | yes | no |
| llama3.3:70b | Ollama | general | 131072 | **yes** | no | good | yes | **8192** |
| llama3.2:3b | Ollama | small/fast | 131072 | **yes** | no | fair | no | **3072** |
| deepseek-r1:14b | Ollama | reasoning | 131072 | no (probe) | no | good | strong | no |
| Lemonade (Halo) | Lemonade | unavailable | n/a | n/a | n/a | n/a | n/a | n/a |

Section AX probes executed 2026-08-09 against Halo's local Ollama
(`100.121.165.22:11434`, v0.32.5): context lengths from `/api/show`
(`qwen3next.context_length`, `qwen3moe.context_length`, `qwen35moe.context_length`,
`gptoss.context_length`, `qwen2.context_length`, `llama.context_length`); tool-calling
from `/api/chat` with a `get_weather` function. Tool-calling confirmed on
`llama3.2:3b`, `qwen3-coder:30b-a3b-q8_0`, `qwen3-coder-next:latest`, `llama3.3:70b`;
`deepseek-r1:14b`, `gpt-oss:120b`, `qwen3.6:35b` returned no tool call (reasoning /
general assistants, not coder-tool models). No vision models on Halo; embeddings only
on llama3.3/3.2. Lemonade completion probes are not possible until real weights are
pulled (`lemonade pull`) — empty stub checkpoints remain the only blocker.
