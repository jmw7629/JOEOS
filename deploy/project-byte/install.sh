#!/usr/bin/env bash
set -euo pipefail

BRANCH="project-byte-deploy"
BASE="https://raw.githubusercontent.com/jmw7629/JOEOS/${BRANCH}/deploy/project-byte"
V4="$BASE/v4"
DEST="/home/joevps/PROJECT_BYTE"
SERVICE="/etc/systemd/system/project-byte.service"
STAMP="$(date +%Y%m%d-%H%M%S)"
EXPECTED_SERVER="86c64e3a06e5c76240753063c01b02c7aa8c83eb5b8f3bf64338a03c11d3a765"
EXPECTED_INDEX="08a324ae21ab0c19128d0dd0ce6ce9739515a03f740119ce8cd680fe5ef273c7"
EXPECTED_HOME="d47921d127fa7f43ad9cb21a948f54d890d3493d5e296ff9e23be9f7b8f84951"
EXPECTED_INSPECTOR="bffa6d45adaafade420b27bf0dcd9717fd8783c3cee1ce73c6550c8752d4cba4"

if [ "$(id -un)" != "joevps" ]; then
  echo "Run this as joevps, not root." >&2
  exit 1
fi

mkdir -p "$DEST" "$DEST/backups" "$DEST/uploads"

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
if [ -f "$DEST/home.js" ]; then cp -p "$DEST/home.js" "$DEST/backups/home-${STAMP}.js"; fi
if [ -f "$DEST/home-inspector.js" ]; then cp -p "$DEST/home-inspector.js" "$DEST/backups/home-inspector-${STAMP}.js"; fi
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
curl --fail --silent --show-error --location "$V4/home.js" -o "$TMP/home.js"
curl --fail --silent --show-error --location "$V4/home-inspector.js" -o "$TMP/home-inspector.js"
cat "$TMP"/server/*.part > "$TMP/server.py"
cat "$TMP"/index/*.part > "$TMP/index.html"

SERVER_SHA="$(sha256sum "$TMP/server.py" | awk '{print $1}')"
INDEX_SHA="$(sha256sum "$TMP/index.html" | awk '{print $1}')"
HOME_SHA="$(sha256sum "$TMP/home.js" | awk '{print $1}')"
INSPECTOR_SHA="$(sha256sum "$TMP/home-inspector.js" | awk '{print $1}')"
[ "$SERVER_SHA" = "$EXPECTED_SERVER" ] || { echo "Server checksum mismatch; refusing deployment." >&2; exit 5; }
[ "$INDEX_SHA" = "$EXPECTED_INDEX" ] || { echo "UI checksum mismatch; refusing deployment." >&2; exit 5; }
[ "$HOME_SHA" = "$EXPECTED_HOME" ] || { echo "Home module checksum mismatch; refusing deployment." >&2; exit 5; }
[ "$INSPECTOR_SHA" = "$EXPECTED_INSPECTOR" ] || { echo "Home inspector checksum mismatch; refusing deployment." >&2; exit 5; }
python3 -m py_compile "$TMP/server.py"
if command -v node >/dev/null 2>&1; then
  node - "$TMP/index.html" "$TMP/home.js" "$TMP/home-inspector.js" <<'NODE'
const fs=require('fs');
const h=fs.readFileSync(process.argv[2],'utf8');
const m=h.match(/<script>([\s\S]*)<\/script>/);
if(!m)throw new Error('inline script missing');
new Function(m[1]);
const home=fs.readFileSync(process.argv[3],'utf8');
const inspector=fs.readFileSync(process.argv[4],'utf8');
new Function(home); new Function(inspector);
for(const x of ['Portfolio','Kanban','Work next','AI','Agents','Terminal','Models','Team','Activity','Settings','Help / How-To'])if(!h.includes(x))throw new Error('missing '+x);
for(const x of ['Joe AI','Live agents','Team / org map','Current activity','My work','Ready for review','Recent memories','Portfolio pulse'])if(!home.includes(x))throw new Error('missing Home '+x);
for(const x of ['homeAgentInspector','homeAgentLens','data-inspect-run','Full terminal','Agent workspace'])if(!inspector.includes(x))throw new Error('missing inspector '+x);
NODE
fi

echo "Verified V4 source: server=$SERVER_SHA ui=$INDEX_SHA home=$HOME_SHA inspector=$INSPECTOR_SHA"

python3 - "$TMP/index.html" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1])
text=p.read_text()
markers=['<script src="/home.js"></script>','<script src="/home-inspector.js"></script>']
for marker in markers:
    if marker not in text:
        if '</body>' not in text:
            raise SystemExit('index.html body close missing')
        text=text.replace('</body>', marker+'</body>')
p.write_text(text)
PY

install -m 0644 "$TMP/server.py" "$DEST/server.py"
install -m 0644 "$TMP/index.html" "$DEST/index.html"
install -m 0644 "$TMP/home.js" "$DEST/home.js"
install -m 0644 "$TMP/home-inspector.js" "$DEST/home-inspector.js"
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
HOME_OK=0
for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do
  if HEALTH="$(curl -fsS http://127.0.0.1:8094/healthz 2>/dev/null)"; then
    if curl -fsS http://127.0.0.1:8094/ -o "$TMP/live-index.html" \
      && curl -fsS http://127.0.0.1:8094/home.js -o "$TMP/live-home.js" \
      && curl -fsS http://127.0.0.1:8094/home-inspector.js -o "$TMP/live-home-inspector.js" \
      && grep -Fq '<script src="/home.js"></script>' "$TMP/live-index.html" \
      && grep -Fq '<script src="/home-inspector.js"></script>' "$TMP/live-index.html" \
      && grep -Fq 'Joe AI' "$TMP/live-home.js" \
      && grep -Fq 'Live agents' "$TMP/live-home.js" \
      && grep -Fq 'homeAgentInspector' "$TMP/live-home-inspector.js" \
      && grep -Fq 'data-inspect-run' "$TMP/live-home-inspector.js"; then
      HOME_OK=1
      break
    fi
  fi
  sleep 1
done

if ! echo "$HEALTH" | grep -q '"version":4' || [ "$HOME_OK" -ne 1 ]; then
  echo "PROJECT_BYTE V4/Home health check failed: $HEALTH home=$HOME_OK" >&2
  echo "Rolling application code back to the previous known-good version..." >&2
  LAST_SERVER="$(ls -1t "$DEST"/backups/server-*.py 2>/dev/null | head -1 || true)"
  LAST_INDEX="$(ls -1t "$DEST"/backups/index-*.html 2>/dev/null | head -1 || true)"
  LAST_HOME="$(ls -1t "$DEST"/backups/home-*.js 2>/dev/null | head -1 || true)"
  LAST_INSPECTOR="$(ls -1t "$DEST"/backups/home-inspector-*.js 2>/dev/null | head -1 || true)"
  [ -n "$LAST_SERVER" ] && cp -p "$LAST_SERVER" "$DEST/server.py"
  [ -n "$LAST_INDEX" ] && cp -p "$LAST_INDEX" "$DEST/index.html"
  if [ -n "$LAST_HOME" ]; then cp -p "$LAST_HOME" "$DEST/home.js"; else rm -f "$DEST/home.js"; fi
  if [ -n "$LAST_INSPECTOR" ]; then cp -p "$LAST_INSPECTOR" "$DEST/home-inspector.js"; else rm -f "$DEST/home-inspector.js"; fi
  sudo systemctl restart project-byte.service
  exit 6
fi

echo "PROJECT_BYTE V4 is healthy: $HEALTH"
echo "Home command center and live agent inspector are loaded and verified."
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

refresh_bridge "$HOME/.config/joeos-opencode-bridge/stickdeath.env" "stickdeath-opencode-bridge.service"
refresh_bridge "$HOME/.config/joeos-opencode-bridge/vitros.env" "vitros-opencode-bridge.service"

if command -v tailscale >/dev/null 2>&1; then
  echo "Ensuring public HTTPS through Tailscale Funnel..."
  sudo tailscale funnel --bg --https=443 --yes 8094
  echo
  sudo tailscale funnel status || true
fi

echo
echo "PROJECT_BYTE V4 Home Command Center deployment complete."
echo "Open: https://mcso9tqzb9-1.tailb9395f.ts.net/"
