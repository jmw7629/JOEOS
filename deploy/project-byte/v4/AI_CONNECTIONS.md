# Private AI connections

The existing Model Hub now manages owner AI connections and the owner's default
chat model. Adding a connection does not start a worker or approve execution.
Existing model records, collaborator settings, custom agents and workspace design
are preserved. New credentials and subscription connections are owner-only.

## Codex and ChatGPT

The official Codex app-server uses ChatGPT subscription sign-in. This is separate
from an OpenAI API key and API billing. An existing authorized server sign-in is
detected automatically; otherwise the panel offers the official device sign-in.
The application never parses or returns subscription tokens.

The runtime must be Codex **0.153.4**, with a dedicated private authentication
directory and an empty private workspace. Configure `PROJECT_BYTE_CODEX_BIN`,
`PROJECT_BYTE_CODEX_HOME` and `PROJECT_BYTE_CODEX_WORKSPACE` in the service
environment. Authentication storage must be writable by the service, outside its
public application directory. Custom authentication or state paths also require
matching service `ReadWritePaths` entries. Do not install or authenticate another account
implicitly. OpenAI documents a cached-auth transfer for an already authorized
headless server in its [authentication guide](https://learn.chatgpt.com/docs/auth).

Workspace chat explicitly disables execution capabilities, requires the effective
read-only sandbox and expected model/provider, rejects every server tool request,
and uses ephemeral threads. The native fixture proves the actual inference tool
inventory on the pinned binary. Only these verified models can be selected through
the subscription connection, when available to the signed-in account:

- gpt-6-astra
- gpt-5.6-sol
- gpt-5.6-terra
- gpt-5.6-luna
- gpt-5.3-codex-spark

Other Codex models are excluded until their capabilities are verified. In
particular, the native negative controls show that gpt-5.5 and gpt-5.4-mini still
advertise `apply_patch` despite general feature restrictions. Changing the runtime
version requires another native review; it fails closed in the meantime.

Selecting a missing or disabled default produces an explicit error. It never
silently switches to Ollama or paid API usage. The existing execution bridge is a
separate connection and cannot dispatch these managed chat connections.

## Providers, local models and external agents

The provider registry includes OpenAI Responses, Anthropic, Google Gemini, xAI,
DeepSeek, Mistral, OpenRouter, Groq, Together, Fireworks, Perplexity, NVIDIA,
Cohere, and custom OpenAI-compatible APIs. Credentials are entered once and stored
in private files outside the served application directory, or referenced through
restricted `PROJECT_BYTE_AI_*` server environment variables.

Choose **Ollama**, enter the address of the server or workstation, then select
**Detect available models**. The list comes from that server's `/api/tags`; model
names are not hardcoded. The workstation address must be reachable from the
PROJECT_BYTE server. Detection lists installed models without downloading or
running them. An exact model ID can also be entered for Ollama or API providers.

**Hermes** and **OpenClaw** can be added as existing authenticated agent endpoints.
Selecting their template also creates an owner-only agent profile linked to that
connection. The panel does not install, start, resume or reconfigure those agent
servers. Their own execution settings remain authoritative; only connect a
runtime the owner intends to give that capability.

Claude Code, Gemini CLI, Kimi Code and GitHub Copilot CLI are listed separately as
native subscription runtimes whose adapters are not configured. Their subscription
credentials are not accepted as generic API keys. The panel does not pretend
those subscriptions are connected. Additional compatible API providers and
unlisted model IDs can be entered manually.

## Verification and scoped activation

`test_ai_connections.py` exercises provider wire contracts, deadlines, credential
storage, manual/discovered models and settings preservation. `test_ai_runtime.py`
exercises actual HTTP owner isolation, default routing and execution boundaries.
`test_codex_connection.py` verifies RPC lifecycle, framing and fail-closed checks.
`test_codex_runtime_tools.py` uses a local unauthenticated Responses fixture and
the pinned native binary; it never uses a real account. The browser test covers
four viewport widths, setup flows and logout/account-switch races.

The general installer packages and checksums the new modules. For the existing
owner installation, use the reviewed **d95 plus AI-only overlay** staging utility
instead of running the full installer, which also manages unrelated workers.
Staging never copies databases, settings or credentials and never deploys or
restarts a service. Review its manifest and test the resulting code before
activation. Preserve the public gateway's session/Origin checks, source mapping,
owner alias and Desktop Commander route when adding the AI route allowlist.
