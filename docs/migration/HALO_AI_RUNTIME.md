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

- **Not currently reachable** on `13305` from the VPS over the tailnet (port closed at discovery time). Lemonade is present on the Halo per the directive; its exact binary/version/service/bind/models require on-Host inventory via SSH (Section D completion) — **pending Halo shell access**.

## Private access (Section E)

- Ollama currently listens on the Halo and is reachable on the tailnet address. Per JoeOS policy, browsers/clients must reach models **only** through the JoeOS backend → ProviderRegistry → model provider. Raw Ollama/Lemonade ports must not be exposed to the public internet.

## Capability matrix (to be confirmed by on-Host canaries, Section AX)

| MODEL | PROVIDER | CAPABILITIES | CONTEXT | TOOL CALLING | VISION | CODING | REASONING | EMBEDDING |
|---|---|---|---|---|---|---|---|---|
| qwen3-coder-next:latest | Ollama | coding | TBD | yes (verify) | no | strong | yes | no |
| qwen3-coder:30b-a3b-q8_0 | Ollama | coding | TBD | yes (verify) | no | strong | yes | no |
| qwen3.6:35b | Ollama | general | TBD | yes (verify) | no | good | yes | no |
| gpt-oss:120b | Ollama | general | TBD | yes (verify) | no | good | yes | no |
| llama3.3:70b | Ollama | general | 131072 | yes (verify) | no | good | yes | **8192** |
| llama3.2:3b | Ollama | small/fast | 131072 | yes (verify) | no | fair | no | **3072** |
| deepseek-r1:14b | Ollama | reasoning | TBD | yes (verify) | no | good | strong | no |
| TBD (Lemonade on Halo) | Lemonade | TBD | TBD | TBD | TBD | TBD | TBD | TBD |

Pending: on-Host `ollama show` context lengths, tool-calling probes, and Lemonade inventory once Halo shell access is available.
