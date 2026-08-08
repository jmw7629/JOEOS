# Local models

Live inventory (`ollama list`, 2026-08-08). All are local/private and run on the
VPS loopback. Capabilities are reported from Ollama's runtime metadata; unknown
capabilities are never asserted.

| Model | Params | Quant | Disk | Ollama capabilities | Notes |
| --- | --- | --- | --- | --- | --- |
| qwen2.5-coder:7b | 7.6B | Q4_K_M | 4.7GB | completion, tools, insert | strong general/coding |
| qwen2.5-coder:7b-opencode-safe | 7.6B | Q4_K_M | 4.7GB | completion, tools, insert | coding, tool-call capable |
| qwen2.5-coder:7b-agentic | 7.6B | Q4_K_M | 4.7GB | completion, tools, insert | agentic coding |
| qwen2.5-coder:14b | 14.8B | Q4_K_M | 9.0GB | completion, tools, insert | **OOM on this VPS** |
| qwen2.5-coder:14b-agentic | 14.8B | Q4_K_M | 9.0GB | completion, tools, insert | **OOM on this VPS** |
| qwen2.5-coder:1.5b | 1.5B | Q4_K_M | 986MB | completion, tools, insert | fast fallback |
| qwen2.5-coder:1.5b-fast | 1.5B | Q4_K_M | 986MB | completion, tools, insert | fast fallback |
| qwen2.5-coder:1.5b-opencode-safe | 1.5B | Q4_K_M | 986MB | completion, tools, insert | fast fallback |
| deepseek-r1:14b | 14.8B | Q4_K_M | 9.0GB | completion, thinking | **OOM on this VPS** |
| deepseek-r1:14b-agentic | 14.8B | Q4_K_M | 9.0GB | completion, thinking | **OOM on this VPS** |

## Production bindings (7.8 GiB constraint, measured)

The 7B family stays resident only in isolation; under the combined
backend+runtime load its llama-server is evicted/crashes (OOM-thrash). The
ACTIVE bindings therefore use the 1.5B family, which runs reliably end-to-end.
The 7B family remains registered and can be bound when more RAM is available.

| Agent | Preferred | Fallback |
| --- | --- | --- |
| Joe | qwen2.5-coder:1.5b-opencode-safe | qwen2.5-coder:1.5b |
| Architect | qwen2.5-coder:1.5b | qwen2.5-coder:1.5b-fast |
| Builder | qwen2.5-coder:1.5b-opencode-safe | qwen2.5-coder:1.5b-fast |
| Researcher | qwen2.5-coder:1.5b | qwen2.5-coder:1.5b-fast |
| Verifier | qwen2.5-coder:1.5b | qwen2.5-coder:1.5b-fast |
| Security | qwen2.5-coder:1.5b | qwen2.5-coder:1.5b-fast |

The 14B and 7B models are registered but resource-constrained; the ModelRegistry
reports the model actually used per run (`model_key`), never a claim.
