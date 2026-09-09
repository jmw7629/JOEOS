# Separate private installations

This package prepares a new, empty PRFKT_PROJECT installation from the verified v4 release assets. It never invokes `install.sh`, copies the existing owner's database or credentials, changes ownership, starts/restarts a worker, configures public networking, or resumes paused projects.

Requirements: Python 3.9+ on Unix (SQLite and `fcntl`), a private existing parent directory, and the complete checked-out `deploy/project-byte` source. The prepared HTTP runtime binds only to `127.0.0.1`; use it locally or through an independently authenticated private transport. It is not a public gateway installer.

## Prepare and run

```sh
python3 deploy/project-byte/private-install/prepare.py /absolute/private/parent/workspace \
  --name 'My Workspace' --owner 'Owner name' --assistant 'AI_BYTE'
python3 /absolute/private/parent/workspace/runtime.py --port 8098
```

Preparation refuses existing targets, symlink paths, source overlap and stale release checksums. It prints the installation identity and the **path** to the owner key, never the key value. Read that private file using your own secure local file workflow, then use the existing dashboard sign-in. Brand, assistant and owner names are selected independently for each installation.

The directory contains:

- `app/`: pinned application assets and a schema-only backend. Fresh installations receive no personal task/project/model/agent seeds. The existing layout remains; owner-specific bridge diagnostics are omitted when no bridges are connected.
- `state/`: the installation's SQLite database and attachments. Empty installations stay empty across restarts.
- `private/`: a unique owner key, private HOME/XDG directories and provider credentials. Credentials use an installation-local path that survives recovery to a new directory.
- `installation.json`: identity, owner presentation and source/output hashes. A missing key or modified packaged asset fails startup rather than silently replacing identity.

All workspace data APIs and attachments require sign-in. Public endpoints are limited to the static shell, presentation-only brand profile and non-sensitive health. Cross-origin mutations are rejected. The optional native Codex runner, historical bridges and public gateway are **not configured** by this package. No launching-shell API keys, SSH agent, Codex sign-in, worker environment or system bus are inherited. Configure each installation's own supported provider/API connections in Model Hub; another person's subscription is not copied or automatically authorized.

Native execution and additional authenticated hosting need a separately reviewed integration step. The runtime does not accept inherited environment variables as an undocumented shortcut around isolation. Health reports the working core separately from unconfigured execution.

## Offline backup and recovery

Backup requires the installation to be offline. It takes the same exclusive installation lock as the runtime and refuses if the runtime is active; it does not stop it. The SQLite backup API includes committed WAL contents in one database snapshot. Attachments, settings, code, profile, identity and private credentials are captured in the same locked operation.

```sh
python3 /absolute/private/parent/workspace/recovery.py backup \
  /absolute/private/parent/workspace /absolute/private/backups/workspace.tgz
python3 /absolute/private/parent/workspace/recovery.py restore \
  /absolute/private/backups/workspace.tgz /absolute/private/parent/recovered \
  --installation-id ID_FROM_INSTALLATION_JSON
```

The archive is sensitive: it contains identity and provider credentials. Keep it private, use encrypted storage/transport, and restore only a trusted backup. Checksums detect corruption; they are not a signature from an external authority. Restore validates identity, every file hash, code hashes, database integrity and paths before returning a stopped installation. Links, special files, traversal, duplicates, existing targets and oversized archives are rejected. Files are mode0600 and directories mode0700. Recovery preserves the **same** identity; it is not a method for cloning installations for other users. Do not run original and recovered copies simultaneously. Prepare a new installation for a new owner.

## Verification and limits

Run:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s deploy/project-byte/private-install -v
```

The integration tests start disposable local HTTP processes only. They verify independent owner identities/keys, empty databases, no inherited native runner settings, settings/task/project/attachment isolation, unauthenticated and cross-install denial, cross-origin rejection, active-runtime backup refusal, same-identity recovery with actual database/attachment contents, empty-install restart, missing-key/tamper refusal, and path/link rejection. The GitHub installer verification workflow includes this test command.

Actual browser acceptance also passed at 390 and 1440 widths: custom branding, sign-in, creating a work item and truthful unconfigured execution, with no uncaught JavaScript errors. Evidence is in the takeover workspace under `evidence/private-install/`; `browser_review.mjs` is the workstation-specific reproducible harness. No owner production files or services were changed.

This is a tested private-install and recovery foundation, not an enterprise certification. Different directories/HOME values are not an OS security boundary. Use separate OS users or VMs for mutually untrusted owners; add reviewed authenticated HTTPS hosting, SSO/MFA requirements, operational backup custody, load testing and device/accessibility acceptance before an enterprise rollout. Existing owner authentication and data remain untouched.
