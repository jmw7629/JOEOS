# JoeOS provider services

Provider adapters normalize capabilities; they do not own product policy.

| Service boundary | Responsibility |
|---|---|
| `ollama` | Lemonade/Ollama-compatible model discovery, chat, embeddings, health, and usage |
| `claude-code` | approval-gated Claude Code planning/execution adapter |
| `codex` | approval-gated Codex planning/execution adapter |
| `embeddings` | batching, model/version metadata, classification and retention |
| `vector-db` | authorized vector persistence, filtering, indexing and retrieval |

Each adapter reports capabilities and health, accepts injected transport/secrets references, and emits typed results. No adapter may bypass the approval policy or expose a raw local port to clients.
