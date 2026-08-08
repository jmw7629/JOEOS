# Rollback — Executive Redesign

This document is the instant-recovery path for the JoeOS UI **before** the
executive redesign began. It is written first, before any design change, so
the previous JoeOS frontend is recoverable without manual reconstruction.

## Snapshot facts

| Item | Value |
|---|---|
| Pre-redesign HEAD | `3687c3a` (commit `feat: embed user-facing Agents home in JoeOS shell`) |
| Parent / verified base | `925eec0` (`fix(identity): repair authority assign CLI flag names`) |
| Rollback tag | `joeos-ui-before-executive-redesign` |
| Preservation branch | `preserve/ui-before-executive-redesign` |
| Rollback source files | `docs/design/rollback/*.before-redesign.html` |
| Live URL | `https://mcso9tqzb9.tailb9395f.ts.net/` |

The rollback tag points at the exact commit whose working tree was being
served live when the redesign began. `git show <tag>:index.html | sha256sum`
matches the live served hash byte-for-byte (verified before any UI change).

## Deployed frontend artifacts (pre-redesign SHA256)

| Artifact | SHA256 |
|---|---|
| `index.html` (served at `/`) | `be0221f9c9ac9b20815a95da3d16630f9a2c8822cb138e40e1904db288ae11ee` |
| `agent_fabric.html` (served at `/os/agents`, `/os/providers`, `/os/models`) | `85eca59f8a1578f57a0c13e11e1c79325c926675e58776854326ba6b580f72de` |
| `automations.html` (served at `/os/automations`) | `c73233f9438cf5a91bffe035de4b9d63a03151ca1c1c72360cf5f16f04577b9d` |
| `frontend_dist/index.html` (packaging mirror) | `2d15c63651f27350491311776080c7e1f14c8b288a72b5f6f2433d0214a5d127` |

## Backend serving mechanism

- The frontend is served by the FastAPI app in `joeos_backend.py`, running in
  tmux session **`joeos-backend`** as uvicorn on **`127.0.0.1:8080`**.
- `INDEX_PATH = _package_asset("index.html")` resolves to the **repo root**
  `index.html` (root candidate wins over `web/`). Files are read at request
  time — **no restart is required to serve an updated frontend**.
- `/os/*` deep links: `/os/agents|providers|models` → `agent_fabric.html`,
  `/os/automations` → `automations.html`, everything else → `index.html`.
- Frontend build command: `node scripts/build_frontend.mjs` (copies
  `index.html` → `frontend_dist/index.html`; not required for live serving).

## Caddy / Tailscale routing

- Tailscale Funnel: `https://mcso9tqzb9.tailb9395f.ts.net` → `127.0.0.1:8091`.
- Caddy (`/usr/bin/caddy run --environ --config /etc/caddy/Caddyfile`,
  system service, PID was `1548437`):
  - `:80  { reverse_proxy 127.0.0.1:8080 }`
  - `:8091 { reverse_proxy 127.0.0.1:8080 }`

## Restore commands

### 1. Restore source state

```bash
cd /home/joewillisny/JOEOS
git checkout joeos-ui-before-executive-redesign -- index.html agent_fabric.html automations.html
# or, to fully move the working tree to the snapshot:
# git switch --detach joeos-ui-before-executive-redesign
```

### 2. Restore the previous frontend build mirror

```bash
cd /home/joewillisny/JOEOS
node scripts/build_frontend.mjs   # copies index.html -> frontend_dist/index.html
```

### 3. Verify restored artifact hashes

```bash
sha256sum index.html agent_fabric.html automations.html
# Expect the table above.
```

### 4. Restore the live frontend

The live backend reads files from the repo root at request time, so restoring
the source files above is sufficient. To be safe, confirm the running backend
is the repo checkout:

```bash
tmux list-sessions                    # expect joeos-backend
ps aux | grep "port 8080"             # uvicorn joeos_backend:app from /home/joewillisny/JOEOS
curl -s https://mcso9tqzb9.tailb9395f.ts.net/ | sha256sum
# Expect be0221f9... (index.html pre-redesign hash)
```

If the backend itself must be restarted:

```bash
tmux attach -t joeos-backend          # Ctrl-C the uvicorn, then relaunch:
# .venv/bin/python -m uvicorn joeos_backend:app --host 127.0.0.1 --port 8080 --no-access-log
```

### 5. Verify primary routes

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://mcso9tqzb9.tailb9395f.ts.net/          # 200
curl -s -o /dev/null -w "%{http_code}\n" https://mcso9tqzb9.tailb9395f.ts.net/os/agents  # 200
curl -s -o /dev/null -w "%{http_code}\n" https://mcso9tqzb9.tailb9395f.ts.net/os/automations  # 200
```

## Design branch / worktree

Design work happens on branch **`feature/executive-joeos-design`** in worktree
**`/home/joewillisny/joeos-executive-design-worktree`**, based on the verified
`ai-rebuild` state. Production (`ai-rebuild` + the rollback tag/branch) is left
intact. See `EXECUTIVE_SHELL_CHECKPOINT.md` for the mid-design checkpoint state.

## Non-negotiable constraints honored

- No reset of unrelated work, no deleted branches, no force-push, no history
  rewrite, no backend/database/agent/pairing/signing state disturbed.
