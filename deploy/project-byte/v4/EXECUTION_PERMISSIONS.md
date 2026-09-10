# Execution permissions

The default PRFKT_PROJECT Observatory inbox now connects to the native Codex task controller. Its owner-only catalog includes actual pending requests across conversations, bound to their original project/run, with a frozen-patch viewer and Approve once / Deny. Deny applies to the selected request. The authoritative controller validates the one-time token, generation, expiry, active run and project binding; the inbox creates no synthetic requests and starts no new runner. Existing isolated edits/tests retain task authorization; publication requires review. The old OpenCode bridge below remains an optional separate API integration, not the native Codex approval queue.

The inbox replaces the historical static “not connected” message. Historical traces and code-review decisions remain separate. Disconnected state, errors, expired requests and logout disable or remove decision controls; pending notes survive polling. Owner-wide request outcomes come from the existing private ledger, without exposing authority bindings.

## Optional legacy OpenCode bridge

PROJECT_BYTE can present real pending tool permissions from one explicitly registered
OpenCode **1.18.29 V1** session. This is separate from the historical Observatory
adapter and code-review decisions. No configuration means **not connected**: no
runner calls, permission records, or audit database are created.

The gateway does not launch a runner, submit prompts, change policies, resume a
paused worker, or merge/deploy code. Existing BYTE, R3, and VITROS workers are excluded.
This first integration covers one session per private installation; child sessions
are not implicitly authorized. Other private installations use their own registration,
credentials, session, and audit storage. Shared multi-tenant hosting is not implemented.

## Operator registration

Connect only a dedicated API-driven runner after reviewing the installation and
execution scope. `opencode run` has its own permission consumer and is unsuitable
for this integration. Use `opencode serve` bound to `127.0.0.1`, protected with
`OPENCODE_SERVER_PASSWORD`. Its password must be at least 24 characters. Keep the
runner port private; never expose it through Funnel, a public proxy, or the browser.
No runner registration has been enabled on the production VPS by this feature.

Run the dedicated runner in an isolated OS account/container with a clean HOME and
XDG directories, only the intended checkout mounted, and only intended credentials
and network access. A directory argument or OpenCode permission rule is **not an OS
sandbox**. Approved shell commands can act with all permissions held by the runner.
Use a default-deny policy with only the intended tools set to `ask`, disable sharing,
project/default plugins and inherited external skills/configuration. Model/provider
credentials belong only in that runner's private environment.

Create the session through the V1 `/session` API; submit work through
`/session/{sessionID}/prompt_async`. The newer `/api/session/...` V2 engine uses a
separate permission queue and is intentionally unsupported here. Do not create
standalone permission records as a substitute for a real tool request.

Read the returned session's `id`, `projectID`, `directory`, and `time.created`.
Create `execution-permissions.json` beside installed `server.py`, owned by the app
account with mode **0600**. Use the canonical absolute directory (no symlinks).
The exact schema is:

```json
{
  "schema_version": 1,
  "endpoint": "http://127.0.0.1:4096",
  "directory": "/srv/private-runner/checkout",
  "session_id": "ses_REPLACE_WITH_REAL_SESSION",
  "session_created": 1,
  "project_id": "REPLACE_WITH_RETURNED_PROJECT_ID",
  "repository": "jmw7629/JOEOS",
  "generation": "REPLACE_WITH_RANDOM_REGISTRATION_ID",
  "password_env": "PB_PERMISSION_RUNNER_PASSWORD",
  "ttl_seconds": 120
}
```

These values are illustrative, not a working registration. `session_created` must
be the actual integer timestamp returned by the runner. Generate a new random
registration ID of at least 16 characters for a new session. Set the named
`PB_PERMISSION_...` environment variable in the existing private app environment to
the runner's password. No secrets or upstream URLs are accepted from browser input.
Do not rotate registration or delete audit records to retry an uncertain decision.

The current installer packages the module but does not create registration,
credentials, runner services, or sessions. Deploying the app and connecting a real
work session are separate operator actions.

## Owner workflow and decision scope

Sign in as Owner, open Agents, and use the Execution permissions panel below the
Observatory. The panel shows real requested patterns and metadata, session/tool IDs,
a bounded review window, and the count of currently pending session requests.
Historical graph nodes remain read-only.

* **Approve once** sends `once` for that request. It does not save a reusable rule.
* **Deny session's pending tools** sends `reject`. OpenCode V1 rejects all pending
  requests in that session, including requests arriving before it processes the
  decision. It does not stop the whole agent or forbid a future newly requested tool.
* An optional note is stored in the local audit. Denial notes are also delivered as
  runner feedback; approval notes are not sent because V1 ignores them.
* Runner acceptance of the permission is not proof that the tool completed.

Requests expire 30–600 seconds after their first observation (120 by default).
Polling and restarts do not reset expiry. Expiry disables this gateway's decision;
it does not cancel the runner's waiting tool. Resolve expired work in the dedicated
runner, then issue a new tool request if needed. Adding any file or symlink named
`PERMISSIONS_STOPPED_BY_OWNER` beside `server.py` blocks gateway decisions. Removing
it is an operator action; there is no web resume control.

If the request, session, registration, version, or pending-session membership changes
between review and submission, refresh and review again. An ambiguous POST outcome
is terminal **unknown**: the gateway never retries it. Check the runner's own tool
result before proceeding. An interrupted app process also recovers a durable
`dispatching` record as unknown. Unavailability disables all decision controls while
keeping an existing uncertain-decision warning and recent local audit visible.

## Storage and boundaries

The new owner-only endpoints are `GET /api/execution-permissions` and
`POST /api/execution-permissions/decision`. Both use existing access-key auth at
Owner level. POST accepts only a server-derived request handle, review token,
`approve_once` or `deny_session`, and an optional note. It rejects cross-origin
browser requests, duplicate JSON fields, malformed bodies, oversized payloads,
arbitrary URLs and `always` approvals. No cookies or new public execution routes
are added.

A separate `.execution-permissions/` directory (0700) contains `audit.sqlite3` and a
process lock (0600). The ledger stores the exact request snapshot/fingerprint,
first-observed time, expiry, state, actor identity, note, durable dispatch intent,
and result. It never stores the runner password. Back it up with the private
installation; it may contain sensitive tool input. It is a local operational audit,
not externally tamper-proof storage. At 10,000 recorded requests, new requests fail
closed until an operator performs deliberate archival/maintenance. The existing
application database, credentials, model settings, and presentation profile are not
changed.

## Verification

Run failure-path and HTTP boundary tests:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 deploy/project-byte/v4/test_execution_permissions.py
```

The existing `test_home_browser.mjs` suite exercises owner authorization, approval
payloads, denied/expired/unknown states, logout races, metadata escaping and four
viewport widths. Its permission responses are explicitly **UI test fixtures**.

For genuine execution proof, install the platform-specific `opencode-<platform>`
package pinned to 1.18.29 into an isolated test-tool directory and run:

```sh
python3 deploy/project-byte/v4/test_execution_permissions_runner.py \
  --binary /absolute/path/to/opencode \
  --output /private/test-evidence
```

This test starts one disposable loopback runner with a clean environment and a
synthetic local model. The model emits harmless Bash marker commands. The actual
OpenCode tool must produce a genuine pending permission, remain blocked, and then
run exactly once through `Gateway.decide` or remain unexecuted after denial. Replay
must return409 at the gateway. Processes are stopped at the end. It does not use
production models, files, keys, workers, or sessions. CI runs this proof on Linux.
