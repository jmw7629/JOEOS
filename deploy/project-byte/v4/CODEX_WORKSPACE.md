# Codex inside PRFKT_PROJECT

AI_BYTE uses the official pinned Codex app-server as its agent. The existing
dashboard is the interface: owner conversations, streamed progress, automatic
specialist selection, isolated file edits and tests, Stop, review cards, patches
and pull requests all stay in the same workflow. This is an integration with
Codex's engine, not an embedded copy of the Codex desktop application.

Sessions default to `gpt-6-astra` with `ultra` effort. An owner can optionally select a per-chat model and reasoning effort from the reviewed models advertised by the connected runtime. Each accepted message persists its selection, including for specialist roles; request retries retain that original selection. They use the private
workspace's existing Codex ChatGPT sign-in, with no API-key fallback. The runtime
must advertise the selected model and effort. Unavailable connections fail
explicitly. Separate installs configure their own account and private storage.

## Execution and review

The coordinator offers explicit native dynamic tools. Codex chooses file reads,
writes, isolated commands, diffs and useful specialist assignments. Specialists
are actual child Codex sessions; up to three run in parallel with read-only file
tools. The coordinator performs edits after their reports. A run has a bounded
lifetime, command deadlines, output limits and a specialist budget. One owner
run is active at a time.

Each conversation keeps its own working files from its registered project base
commit. Raw Git objects supply the initial files; repository filters, hooks,
replacement objects, links and untracked host files are not exported. Git
metadata, OAuth, publisher credentials and other projects remain outside the
worker. The Linux broker creates a private mount/PID/network/IPC/UTS/cgroup
environment, then a fixed privilege-drop entrypoint removes capabilities,
supplementary groups and privilege escalation before executing a command.
Commands have no network. Host applications, arbitrary repositories, deployment,
desktop control and external connectors are not task capabilities in this release.

The `publish_pull_request` tool freezes the exact patch and shows it for one-time
owner approval. Approval is bound to the actual native request, run generation
and frozen review, including the fixed project identity. The trusted publisher creates a GitHub `[OC]` issue, a new
branch and a PR, without legacy bridge dispatch markers. It exposes no merge
operation. Partial or uncertain publication is recorded as unknown and is never
retried automatically. Read-only review decisions do not approve unrelated tools.

Conversations, events, permissions and artifacts live in a separate private
SQLite database with immutable owner scope. New transcripts are not copied into
legacy shared chat. Request IDs make retries idempotent. Stop cancels approvals,
interrupts native sessions and waits for active workers to drain. A run becomes
terminal only after that cleanup; reconnecting never starts a second execution.
On coordinator restart unfinished runs become interrupted and approvals expire.

## Scoped installation

The normal package includes code but does not enable a runner or configure an
account. Configure only the intended private install:

- `PROJECT_BYTE_CODEX_BIN`, `PROJECT_BYTE_CODEX_HOME`,
  `PROJECT_BYTE_CODEX_WORKSPACE`: existing verified Codex runtime, private account
  directory and empty native coordinator directory.
- `PRFKT_CODEX_STATE`: private 0700 state directory outside the public app.
- `PRFKT_CODEX_REPO`: server-selected JO EOS source checkout.
- `PRFKT_CODEX_BASE_BRANCH`: trusted publication base, initially
  `project-byte-deploy`; its tip must match the conversation's recorded base.
- `PRFKT_CODEX_SANDBOX_SOCKET`: the authenticated root broker Unix socket.
- `PRFKT_CODEX_PUBLISH=1`: enable the trusted JO EOS publisher after verifying the
  host GitHub connection. It still requires an exact review-card approval.
- `PRFKT_CODEX_PROJECTS`: optional absolute path to an owner-private JSON project
  registry. Without it the existing JO EOS behavior and storage remain intact.
  See [PROJECT_REGISTRY.md](PROJECT_REGISTRY.md) for its schema and admission rules.

Install `codex_sandbox.py` as root-owned code outside app-writable directories.
Its `--serve` argument takes a root-owned 0600 configuration containing exactly
`schema_version`, `run_root`, `uid`, `gid`, and `socket`. The socket is directly in
`/run`, 0600 and owned by the app user. Both client and broker verify peer
credentials. The broker must have permission to create namespaces and drop to
the configured user. The dashboard service keeps its existing restrictions;
never grant it a sudo rule or Docker socket. Apply memory, process, file-size and
CPU limits to the broker service, with `KillMode=control-group`.

On the current protected installation use `stage_codex_overlay.py`, which checks
the exact renamed live baseline and produces a separate code-only directory.
It inserts only the handler hook and final script tag, plus the reviewed modules
and bounded JSON-parser change. Do not deploy the feature branch's whole runtime
or HTML: that would include unrelated pending changes. Run JavaScript syntax and
browser checks before activation. Save all replaced code and service drop-ins,
activate only the app and dedicated broker, and verify the public gateway's exact
new allowlisted routes. Preserve the current public gateway sign-in, data and
settings. Do not start paused BYTE, R3, VITROS or legacy bridge units.

For an already verified native installation, `stage_codex_overlay.py
--upgrade-projects` pins the previous native module hashes and stages only the
four updated modules/assets and the new registry module. It does not rewrite
server.py, index.html, auth, data, gateway or broker code. Keep private registry
configuration and source snapshots outside the public repository.

The overlay keeps an idle app-owned SQLite connection after database startup,
before the HTTP listener binds. This preserves the WAL sidecars that the gateway
needs to read authentication from its read-only mount. The anchor holds no read
transaction; fresh gateway reads still observe account changes and revocation.
Do not replace these reads with SQLite immutable mode or grant the gateway write
access to the application database. Verify a fresh public sign-in after restarting
the app, before considering the overlay ready.

Rollback restores only this overlay's replaced code and gateway release, removes
its app service drop-in, and stops the dedicated Codex broker. Retain private
conversation storage for review; never replay an interrupted run during rollback.

## Verification

The acceptance suites cover native JSON-RPC correlation and cancellation,
callback draining, owner-only HTTP, replay/expiry/restart behavior, malicious
Git filters and links, sandbox canaries and privilege checks, immutable approval
snapshots, uncertain publication, gateway nonce enforcement, and dashboard
behavior at 320/390/768/1440 pixels. Native full-chain tests use actual Codex plus
a local scripted Responses fixture and an actual OS sandbox; the publisher is an
explicit local sink. These tests never authenticate or publish to GitHub.

VPS acceptance must additionally exercise the real root broker, native runtime,
and authenticated dashboard. A passing local fixture does not claim production
connectivity or a published PR. `PRFKT_SANDBOX_DIAGNOSTICS=1` optionally logs only
the bounded synthetic readiness probe failure; keep it off in normal operation.
