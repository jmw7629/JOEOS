# Invite-only private workspaces

This pilot hosts up to five **separate private installations** of PRFKT_PROJECT. It is an invitation service with persistent accounts, not an enterprise compliance certification or a shared copy of the owner's dashboard.

Each accepted invitation assigns one previously empty installation. A workspace has its own Unix account, filesystem root, SQLite databases, upload directory and provider credentials. The portal selects a fixed Unix socket from the authenticated account; users cannot submit a backend URL or a workspace identifier to switch tenants. No owner repository, Codex sign-in, GitHub token, recovery files or worker socket is copied or mounted.

Users can create projects through **New work**, manage Kanban tasks, connect their own AI API account in **Models**, and use saved conversations with the existing mobile/desktop chat interface. Prompts execute asynchronously against that provider and results remain available after navigation. Retries with the same request ID do not generate twice. A process restart marks unfinished requests interrupted instead of replaying them. Chat is labeled as provider chat: no execution runner, native subscription sign-in, device integration, code publication or tool approval is claimed. An external agent gateway may have its own capabilities and billing, governed by that gateway.

## Access

The host signs in to `/admin` using the separately generated admin account. **Create invitation** reserves a workspace and displays a single-use link once. The token is in the URL fragment, absent from server requests and referrers. Invitations expire in seven days. The recipient chooses a username, a password of at least 12 characters, a display name and a workspace name. Revoke an unused invitation to release its reservation. Disabling an account immediately revokes all its sessions without deleting its data. Re-enabling requires fresh sign-in.

Passwords use salted scrypt (N=32768, r=8, p=1), with bounded hashing concurrency and sign-in throttling. Persistent sessions expire after 12 hours, use a separate Secure/HttpOnly/SameSite cookie and require a session nonce for workspace API calls and administrative mutations. `/account` changes the password and revokes other sessions. The server stores invitation/session hashes and never logs credentials, request bodies or URLs. There is no email/password-reset service in this pilot; the host must handle account recovery through a reviewed administrative operation.

## Deployment

`provision.py` is an authorized root-only operation for a **new deployment**. It refuses existing service accounts and installation directories. If a partial setup fails, inspect its output; do not rerun provisioning or delete its state to force another attempt. Run it only from an inspected, checksummed source package containing the sibling `v4`, `private-install`, `public-access` and `enterprise` directories plus `install.sh`:

```sh
sudo -n python3 /path/to/project-byte/enterprise/provision.py --source /path/to/project-byte --capacity 5
```

It creates only `prfkt-portal` and `prfkt-slot01`–`05` service accounts, `/opt/prfkt-enterprise`, `/var/lib/prfkt-enterprise`, and units prefixed `prfkt-enterprise-`. The loopback portal listens on port 8100. **By default the script verifies private services without publishing. It does not touch existing services, project ownership or workers.** With `--publish`, it first verifies the private filesystem roots, actual cgroup network blocks, public provider HTTPS, blank inventories and admin sign-in, then adds only the port-10000 HTTPS route and checks that previous routes are unchanged. Any failed check prevents publication. Never reset the existing Serve/Funnel configuration.

The initial admin credential is delivered as a private file at `/home/joevps/.local/share/prfkt-enterprise-access/admin.json`; it is absent from source and deployment receipts. Keep it private and change the password through `/account`. It does not use the owner's personal dashboard PIN.

Each systemd service has a private root, read-only system libraries/code, only its own writable state, no privilege elevation, no inherited environment or devices, bounded CPU/memory/tasks and blocked private/host network addresses. Provider transport additionally validates and pins public HTTPS destination addresses, rejects private hosts and does not follow redirects. Default owner music is omitted. New workspaces contain no seeded projects, tasks, models or conversations. Storage admission limits protect each workspace's state; these are pilot application limits, not filesystem quotas. A high-assurance public multi-tenant service would additionally need filesystem quotas, abuse management, SSO/MFA, automated encrypted off-host backups, account recovery and independent penetration testing.

## Verification

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s deploy/project-byte/enterprise -p 'test_*.py' -v
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s deploy/project-byte/private-install -p 'test_*.py' -v
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s deploy/project-byte/public-access -p 'test_*.py' -v
PB_PYTHON=/path/to/python3 PB_PLAYWRIGHT_MODULE=/path/to/playwright/index.mjs PB_BROWSER_PATH=/path/to/chromium node deploy/project-byte/enterprise/browser_review.mjs
```

The two-workspace integration tests exercise real HTTP and Unix sockets with a disposable provider fixture: empty state, cross-workspace denial, project creation, own provider registration, durable history, idempotent requests, invitation reuse/races/expiry, CSRF, password changes, network-address rejection and account revocation. Browser verification exercises invitation acceptance, project creation, Models and saved chat on 390px and 1440px viewports. Fixtures never use a real subscription or perform a billable generation.

Prepared private installs keep their existing checksum manifest and offline same-identity backup/restore workflow. Back up portal state and each workspace privately; restore must preserve account-to-installation mapping. Do not restore the owner's recovery manifests into guest installations. Do not delete an assigned workspace to make room for another user.

## Repairing the initial masked-mode deployment

If the first setup reached `private-services-started-not-public` and failed its isolation check, do not rerun provisioning. The initial installer let its 0077 umask mask intended code/configuration and DNS-file permissions. `repair_permissions.py --publish` is limited to that installation's known paths and initial modes. It makes code traversable and nonsecret DNS files readable, while retaining private database/key permissions, ownership and workspace processes. It starts only the new portal and requires full verification before publication. Consult `DEPLOYMENT_STATUS.md` for the exact staged command. The verifier now reports the original failing command's stderr and exit status.
