#!/usr/bin/env bash
set -euo pipefail

BRANCH="project-byte-deploy"
BASE="https://raw.githubusercontent.com/jmw7629/JOEOS/${BRANCH}/deploy/project-byte"
V4="$BASE/v4"
DEST="/home/joevps/PROJECT_BYTE"
SERVICE="/etc/systemd/system/project-byte.service"
STAMP="$(date +%Y%m%d-%H%M%S)"
EXPECTED_SERVER="132218e2cc255ce7a58ca0adc7ee91cbdefca8033ecdd22721dd4ce3efb69aa1"
EXPECTED_INDEX="09e774e9d37f0140f39095401e3d02b52f2656806c4f3141b1869fdda20f91b7"

if [ "$(id -un)" != "joevps" ]; then
  echo "Run this as joevps, not root." >&2
  exit 1
fi

mkdir -p "$DEST" "$DEST/backups" "$DEST/uploads"

# Consistent SQLite backup, including any WAL state.
if [ -f "$DEST/kanban.db" ]; then
  python3 - "$DEST/kanban.db" "$DEST/backups/kanban-${STAMP}.db" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1:3]
a = sqlite3.connect(src)
b = sqlite3.connect(dst)
with b:
    a.backup(b)
b.close(); a.close()
PY
  echo "Database backup: $DEST/backups/kanban-${STAMP}.db"
fi
if [ -f "$DEST/server.py" ]; then cp -p "$DEST/server.py" "$DEST/backups/server-${STAMP}.py"; fi
if [ -f "$DEST/index.html" ]; then cp -p "$DEST/index.html" "$DEST/backups/index-${STAMP}.html"; fi
if [ -f "$DEST/admin.secret" ]; then cp -p "$DEST/admin.secret" "$DEST/backups/admin-${STAMP}.secret"; fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/server" "$TMP/index"

for n in 01 02 03 04 05 06 07 08; do
  curl --fail --silent --show-error --location "$V4/server/$n.part" -o "$TMP/server/$n.part"
done
for n in 01 02 03 04 05 06 07; do
  curl --fail --silent --show-error --location "$V4/index/$n.part" -o "$TMP/index/$n.part"
done
cat "$TMP"/server/*.part > "$TMP/server.py"
cat "$TMP"/index/*.part > "$TMP/index.html"

SERVER_SHA="$(sha256sum "$TMP/server.py" | awk '{print $1}')"
INDEX_SHA="$(sha256sum "$TMP/index.html" | awk '{print $1}')"
[ "$SERVER_SHA" = "$EXPECTED_SERVER" ] || { echo "Server checksum mismatch; refusing deployment." >&2; exit 5; }
[ "$INDEX_SHA" = "$EXPECTED_INDEX" ] || { echo "UI checksum mismatch; refusing deployment." >&2; exit 5; }
python3 -m py_compile "$TMP/server.py"
if command -v node >/dev/null 2>&1; then
  node - "$TMP/index.html" <<'NODE'
const fs=require('fs');const h=fs.readFileSync(process.argv[2],'utf8');const m=h.match(/<script>([\s\S]*)<\/script>/);if(!m)throw new Error('inline script missing');new Function(m[1]);
for(const x of ['Portfolio','Kanban','Work next','AI','Agents','Terminal','Models','Team','Activity','Settings','Help / How-To','Notification center'])if(!h.includes(x))throw new Error('missing '+x);
NODE
fi

echo "Verified V4 source: server=$SERVER_SHA ui=$INDEX_SHA"

install -m 0644 "$TMP/server.py" "$DEST/server.py"
install -m 0644 "$TMP/index.html" "$DEST/index.html"
# Optional server-side AI secrets can be placed here later; never in browser code.
touch "$DEST/project-byte.env"
chmod 600 "$DEST/project-byte.env"

sudo tee "$SERVICE" >/dev/null <<'UNIT'
[Unit]
Description=PROJECT_BYTE executive AI operations command center
After=network.target

[Service]
Type=simple
User=joevps
WorkingDirectory=/home/joevps/PROJECT_BYTE
ExecStart=/usr/bin/python3 /home/joevps/PROJECT_BYTE/server.py
Restart=always
RestartSec=3
Environment=KANBAN_HOST=127.0.0.1
Environment=KANBAN_PORT=8094
EnvironmentFile=-/home/joevps/PROJECT_BYTE/project-byte.env
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=/home/joevps/PROJECT_BYTE

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable project-byte.service >/dev/null
sudo systemctl restart project-byte.service

HEALTH=""
for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do
  if HEALTH="$(curl -fsS http://127.0.0.1:8094/healthz 2>/dev/null)"; then break; fi
  sleep 1
done

if ! echo "$HEALTH" | grep -q '"version":4'; then
  echo "PROJECT_BYTE V4 health check failed: $HEALTH" >&2
  echo "Rolling application code back to the previous known-good version..." >&2
  LAST_SERVER="$(ls -1t "$DEST"/backups/server-*.py 2>/dev/null | head -1 || true)"
  LAST_INDEX="$(ls -1t "$DEST"/backups/index-*.html 2>/dev/null | head -1 || true)"
  [ -n "$LAST_SERVER" ] && cp -p "$LAST_SERVER" "$DEST/server.py"
  [ -n "$LAST_INDEX" ] && cp -p "$LAST_INDEX" "$DEST/index.html"
  sudo systemctl restart project-byte.service
  exit 6
fi

echo "PROJECT_BYTE V4 is healthy: $HEALTH"
echo "Existing tasks, projects, attachments, database, and owner key were preserved."

refresh_bridge() {
  local env_file="$1"
  local service_name="$2"
  local root=""
  local dirty=""
  local unsafe=""

  [ -f "$env_file" ] || return 0
  root="$(grep '^BRIDGE_ROOT=' "$env_file" 2>/dev/null | head -1 | cut -d= -f2- || true)"
  [ -n "$root" ] && [ -d "$root/.git" ] || return 0

  dirty="$(git -C "$root" status --porcelain 2>/dev/null || true)"
  if [ -n "$dirty" ]; then
    # Permit only the known stale Python-bytecode hygiene artifact. Never discard source changes.
    unsafe="$(printf '%s\n' "$dirty" | grep -Ev '^\?\? .*(__pycache__/|\.py[co]$)' || true)"
    if [ -n "$unsafe" ]; then
      echo "Bridge refresh skipped for $service_name: control checkout has real changes."
      printf '%s\n' "$unsafe" | sed 's/^/  /'
      return 0
    fi
  fi

  echo "Refreshing bridge checkout for $service_name..."
  if git -C "$root" fetch --prune origin main && git -C "$root" merge --ff-only origin/main; then
    systemctl --user restart "$service_name" || true
    echo "Bridge refreshed: $service_name"
  else
    echo "Bridge refresh skipped for $service_name: fast-forward was not safe."
  fi
}

# Unblock existing JoeVPS issue pollers without ever resetting or overwriting source changes.
refresh_bridge "$HOME/.config/joeos-opencode-bridge/stickdeath.env" "stickdeath-opencode-bridge.service"
refresh_bridge "$HOME/.config/joeos-opencode-bridge/vitros.env" "vitros-opencode-bridge.service"

if command -v tailscale >/dev/null 2>&1; then
  echo "Ensuring public HTTPS through Tailscale Funnel..."
  sudo tailscale funnel --bg --https=443 --yes 8094
  echo
  sudo tailscale funnel status || true
fi

echo
echo "PROJECT_BYTE V4 deployment complete."
echo "Open: https://mcso9tqzb9-1.tailb9395f.ts.net/"
