# Private project registry

`PRFKT_CODEX_PROJECTS` names a canonical absolute JSON path outside the public
app. The file must be a regular single-link file owned by the app user with no
group/other permissions. It is read once when the controller starts. Invalid
configuration fails closed; it never silently falls back to another repository.
The browser submits only a registered project key, never paths or repository URLs.

```json
{
  "schema_version": 1,
  "projects": [
    {
      "key": "joeos",
      "label": "PRFKT_PROJECT",
      "repo": "jmw7629/JOEOS",
      "source": "/home/joevps/JOEOS",
      "base_branch": "project-byte-deploy",
      "mode": "execute",
      "publication": true,
      "detail": "",
      "context": ""
    }
  ]
}
```

Every project has exactly these fields. Up to 32 entries are accepted. Keys,
source directories and repository identities must be unique. `repo`, `source`
and `base_branch` may be null for unavailable projects. Sources must be existing
canonical absolute directories outside task working storage. The `joeos` entry
is required and must preserve the original source/repository/base identity.

- `execute`: a verified source is connected to the isolated task runner. Without
  a publication connection, edits remain private and downloadable as patches.
- `observe`: another workflow owns execution. Status/history stay visible, but
  this controller cannot start a competing task or approve publication there.
- `blocked`: source or setup is missing. The dashboard displays `detail` and
  disables task submission instead of presenting a connected worker.

`context` is optional content expressed as a required string field (empty is
valid), bounded to 20 KB. It supplies historical design and acceptance notes to
the selected native session, explicitly marked as untrusted background. It does
not grant execution permission. Neither this text nor source paths enter the
browser catalog. Do not store secrets or raw session exports in the registry.

Publication requires `execute`, `publication: true`, an exact registered GitHub
owner/repository, and a fixed base branch. Both the source fetch and push origins
must match that repository. Frozen reviews and returned GitHub issue/PR URLs are
checked against the selected repository. The original JO EOS publisher ledger
stays at its previous path; other publishers have separate private directories.
No publisher merges code or retries an uncertain external operation.

An existing issue-watching worker may react to new issues even without explicit
dispatch labels. Keep such repositories observed until ownership and trigger
rules are understood. Registry membership never starts or resumes a worker.

The existing owner ledger and object URLs remain unchanged. Each conversation
is bound to its project key and a fingerprint of source path, repository and base
branch. Selecting another project cannot reuse the conversation. Changing that
identity requires a new conversation; pending approvals cannot be retargeted.
Unbound old conversations can be adopted only by the original JO EOS identity.
One owner task runs at a time, with up to three native read-only specialists.

Source preparation must preserve existing uncommitted code. Use private reviewed
snapshots when a checkout is dirty or has active ownership; never reset it just
to connect the dashboard. Keep runtime data, credentials, caches and unrelated
untracked files out of those snapshots. Store provenance and exclusions privately.
Do not claim a historical release or test passed merely because source is present.

The sandbox still has no network, host credentials or native iOS toolchain.
Source work is available where connected; dependency installation, publishing,
deployment and physical G2/iOS acceptance each need their actual supported path.
Project availability is not a claim that every app is complete or continuously
building. Existing paused work and independent coordinators keep their ownership.

For a reviewed registry update, the private `maintenance.projects` entry in the
controller state directory closes new message admission, including idempotent
replays. Catalog reads, Stop and permission denial remain available. An operator
upgrading an older controller must first reserve the native SQLite ledger with
`BEGIN IMMEDIATE`, confirm no active run, and stop the app while holding that
reservation. Keep admission closed through verification or complete rollback;
never restart an active owner task to install an update.

Local Git object inspection cannot lazily fetch missing objects or invoke a
repository-configured transport. Publication permits only its explicit local
source fetch and exact registered GitHub HTTPS push. Missing local objects fail
closed before a publication decision is offered.
