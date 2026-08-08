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

## Production bindings (7.8 GiB constraint)

| Agent | Preferred | Fallback |
| --- | --- | --- |
| Joe | qwen2.5-coder:7b-opencode-safe | qwen2.5-coder:7b |
| Architect | qwen2.5-coder:7b-agentic | qwen2.5-coder:7b |
| Builder | qwen2.5-coder:7b-opencode-safe | qwen2.5-coder:1.5b-fast |
| Researcher | qwen2.5-coder:7b | qwen2.5-coder:1.5b |
| Verifier | qwen2.5-coder:7b-agentic | qwen2.5-coder:7b |
| Security | qwen2.5-coder:7b-agentic | qwen2.5-coder:1.5b |

The 14B models are registered but disabled-by-constraint; if more RAM is added
they can be enabled and bound. The ModelRegistry reports the model actually
used per run (`model_key`), never a claim.
