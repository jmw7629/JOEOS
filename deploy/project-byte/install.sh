#!/usr/bin/env bash
set -euo pipefail

BRANCH="project-byte-deploy"
BASE="https://raw.githubusercontent.com/jmw7629/JOEOS/${BRANCH}/deploy/project-byte"
V4="$BASE/v4"
DEST="/home/joevps/PROJECT_BYTE"
SERVICE="/etc/systemd/system/project-byte.service"
STAMP="$(date +%Y%m%d-%H%M%S)"
EXPECTED_BACKEND="e5e84df298c20b7d076f850ce9c7c7be816669e523c7d167c44a109e105c3101"
EXPECTED_SERVER="3d7354d2f4ff29ee8573dba17bb33e78063fc6b98ba57e5a1f8d34894d2a13b8"
EXPECTED_INDEX="d1ecb8e146199d4aedf9aea65c2519230e6eec1b7719cd9c7cb6299666b6b40a"
EXPECTED_HOME="6505e61f678d89d13e441ca4537856eb29f78846eba95b1d31d09edb356455b7"
EXPECTED_INSPECTOR="bffa6d45adaafade420b27bf0dcd9717fd8783c3cee1ce73c6550c8752d4cba4"
EXPECTED_HEALTH="87487e59041263ff21985cb7371691536cc61679ed5e1f02feddf8daad79fb9c"

if [ "$(id -un)" != "joevps" ]; then
  echo "Run this as joevps, not root." >&2
  exit 1
fi

mkdir -p "$DEST" "$DEST/backups" "$DEST/uploads"

# Never leave a prior public listener exposed while application code changes.
if command -v tailscale >/dev/null 2>&1; then
  sudo tailscale funnel --https=443 off >/dev/null 2>&1 || true
fi

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
if [ -f "$DEST/backend.py" ]; then cp -p "$DEST/backend.py" "$DEST/backups/backend-${STAMP}.py"; fi
if [ -f "$DEST/index.html" ]; then cp -p "$DEST/index.html" "$DEST/backups/index-${STAMP}.html"; fi
if [ -f "$DEST/home.js" ]; then cp -p "$DEST/home.js" "$DEST/backups/home-${STAMP}.js"; fi
if [ -f "$DEST/home-inspector.js" ]; then cp -p "$DEST/home-inspector.js" "$DEST/backups/home-inspector-${STAMP}.js"; fi
if [ -f "$DEST/health-runtime.js" ]; then cp -p "$DEST/health-runtime.js" "$DEST/backups/health-runtime-${STAMP}.js"; fi
if [ -f "$DEST/admin.secret" ]; then cp -p "$DEST/admin.secret" "$DEST/backups/admin-${STAMP}.secret"; fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/backend" "$TMP/index"

for n in 01 02 03 04 05 06 07 08; do
  curl --fail --silent --show-error --location "$V4/server/$n.part" -o "$TMP/backend/$n.part"
done
for n in 01 02 03 04 05 06 07; do
  curl --fail --silent --show-error --location "$V4/index/$n.part" -o "$TMP/index/$n.part"
done
curl --fail --silent --show-error --location "$V4/runtime_server.py" -o "$TMP/server.py"
curl --fail --silent --show-error --location "$V4/home.js" -o "$TMP/home.js"
curl --fail --silent --show-error --location "$V4/home-inspector.js" -o "$TMP/home-inspector.js"
curl --fail --silent --show-error --location "$V4/health-runtime.js" -o "$TMP/health-runtime.js"
cat "$TMP"/backend/*.part > "$TMP/backend.py"
cat "$TMP"/index/*.part > "$TMP/index.html"

BACKEND_SHA="$(sha256sum "$TMP/backend.py" | awk '{print $1}')"
SERVER_SHA="$(sha256sum "$TMP/server.py" | awk '{print $1}')"
INDEX_SHA="$(sha256sum "$TMP/index.html" | awk '{print $1}')"
HOME_SHA="$(sha256sum "$TMP/home.js" | awk '{print $1}')"
INSPECTOR_SHA="$(sha256sum "$TMP/home-inspector.js" | awk '{print $1}')"
HEALTH_SHA="$(sha256sum "$TMP/health-runtime.js" | awk '{print $1}')"
[ "$BACKEND_SHA" = "$EXPECTED_BACKEND" ] || { echo "Backend checksum mismatch; refusing deployment." >&2; exit 5; }
[ "$SERVER_SHA" = "$EXPECTED_SERVER" ] || { echo "Runtime server SHA-256 mismatch; refusing deployment." >&2; exit 5; }
[ "$INDEX_SHA" = "$EXPECTED_INDEX" ] || { echo "UI checksum mismatch; refusing deployment." >&2; exit 5; }
[ "$HOME_SHA" = "$EXPECTED_HOME" ] || { echo "Home module checksum mismatch; refusing deployment." >&2; exit 5; }
[ "$INSPECTOR_SHA" = "$EXPECTED_INSPECTOR" ] || { echo "Home inspector checksum mismatch; refusing deployment." >&2; exit 5; }
[ "$HEALTH_SHA" = "$EXPECTED_HEALTH" ] || { echo "Health UI checksum mismatch; refusing deployment." >&2; exit 5; }
python3 -m py_compile "$TMP/backend.py" "$TMP/server.py"
if command -v node >/dev/null 2>&1; then
  node - "$TMP/index.html" "$TMP/home.js" "$TMP/home-inspector.js" "$TMP/health-runtime.js" <<'NODE'
const fs=require('fs');
const h=fs.readFileSync(process.argv[2],'utf8');
const m=h.match(/<script>([\s\S]*)<\/script>/);
if(!m)throw new Error('inline script missing');
new Function(m[1]);
const home=fs.readFileSync(process.argv[3],'utf8');
const inspector=fs.readFileSync(process.argv[4],'utf8');
const health=fs.readFileSync(process.argv[5],'utf8');
new Function(home); new Function(inspector); new Function(health);
for(const x of ['Portfolio','Kanban','Work next','AI','Agents','Terminal','Models','Team','Activity','Settings','Help / How-To'])if(!h.includes(x))throw new Error('missing '+x);
for(const x of ['Joe AI','Live agents','Team / org map','Current activity','My work','Ready for review','Recent memories','Portfolio pulse'])if(!home.includes(x))throw new Error('missing Home '+x);
for(const x of ['homeAgentInspector','homeAgentLens','data-inspect-run','Full terminal','Agent workspace'])if(!inspector.includes(x))throw new Error('missing inspector '+x);
for(const x of ['Verifying system health','Systems verified operational','AI CHAT TESTED OK','AI CHAT UNTESTED'])if(!health.includes(x))throw new Error('missing health behavior '+x);
NODE
fi

echo "Verified V4 source: backend=$BACKEND_SHA runtime=$SERVER_SHA ui=$INDEX_SHA home=$HOME_SHA inspector=$INSPECTOR_SHA health=$HEALTH_SHA"

python3 - "$TMP/index.html" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1])
text=p.read_text()
markers=['<script src="/home.js"></script>','<script src="/home-inspector.js"></script>','<script src="/health-runtime.js"></script>']
for marker in markers:
    if marker not in text:
        if '</body>' not in text:
            raise SystemExit('index.html body close missing')
        text=text.replace('</body>', marker+'</body>')
p.write_text(text)
PY

install -m 0644 "$TMP/backend.py" "$DEST/backend.py"
install -m 0644 "$TMP/server.py" "$DEST/server.py"
install -m 0644 "$TMP/index.html" "$DEST/index.html"
install -m 0644 "$TMP/home.js" "$DEST/home.js"
install -m 0644 "$TMP/home-inspector.js" "$DEST/home-inspector.js"
install -m 0644 "$TMP/health-runtime.js" "$DEST/health-runtime.js"
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
      && curl -fsS http://127.0.0.1:8094/health-runtime.js -o "$TMP/live-health-runtime.js" \
      && grep -Fq '<script src="/home.js"></script>' "$TMP/live-index.html" \
      && grep -Fq '<script src="/home-inspector.js"></script>' "$TMP/live-index.html" \
      && grep -Fq '<script src="/health-runtime.js"></script>' "$TMP/live-index.html" \
      && grep -Fq 'Joe AI' "$TMP/live-home.js" \
      && grep -Fq 'Live agents' "$TMP/live-home.js" \
      && grep -Fq 'homeAgentInspector' "$TMP/live-home-inspector.js" \
      && grep -Fq 'data-inspect-run' "$TMP/live-home-inspector.js" \
      && grep -Fq 'Verifying system health' "$TMP/live-health-runtime.js" \
      && grep -Fq 'AI CHAT TESTED OK' "$TMP/live-health-runtime.js"; then
      HOME_OK=1
      break
    fi
  fi
  sleep 1
done

if ! echo "$HEALTH" | grep -q '"version":4' || ! echo "$HEALTH" | grep -q '"ok":true' || ! echo "$HEALTH" | grep -q '"components":' || [ "$HOME_OK" -ne 1 ]; then
  echo "PROJECT_BYTE V4 core health check failed: $HEALTH home=$HOME_OK" >&2
  echo "Rolling application code back to the previous known-good version..." >&2
  LAST_SERVER="$(ls -1t "$DEST"/backups/server-*.py 2>/dev/null | head -1 || true)"
  LAST_BACKEND="$(ls -1t "$DEST"/backups/backend-*.py 2>/dev/null | head -1 || true)"
  LAST_INDEX="$(ls -1t "$DEST"/backups/index-*.html 2>/dev/null | head -1 || true)"
  LAST_HOME="$(ls -1t "$DEST"/backups/home-*.js 2>/dev/null | head -1 || true)"
  LAST_INSPECTOR="$(ls -1t "$DEST"/backups/home-inspector-*.js 2>/dev/null | head -1 || true)"
  LAST_HEALTH="$(ls -1t "$DEST"/backups/health-runtime-*.js 2>/dev/null | head -1 || true)"
  [ -n "$LAST_SERVER" ] && cp -p "$LAST_SERVER" "$DEST/server.py"
  if [ -n "$LAST_BACKEND" ]; then cp -p "$LAST_BACKEND" "$DEST/backend.py"; else rm -f "$DEST/backend.py"; fi
  [ -n "$LAST_INDEX" ] && cp -p "$LAST_INDEX" "$DEST/index.html"
  if [ -n "$LAST_HOME" ]; then cp -p "$LAST_HOME" "$DEST/home.js"; else rm -f "$DEST/home.js"; fi
  if [ -n "$LAST_INSPECTOR" ]; then cp -p "$LAST_INSPECTOR" "$DEST/home-inspector.js"; else rm -f "$DEST/home-inspector.js"; fi
  if [ -n "$LAST_HEALTH" ]; then cp -p "$LAST_HEALTH" "$DEST/health-runtime.js"; else rm -f "$DEST/health-runtime.js"; fi
  sudo systemctl restart project-byte.service
  exit 6
fi

echo "PROJECT_BYTE core service is healthy: $HEALTH"
echo "Home command center, agent inspector, and evidence-backed health UI are loaded."
echo "Existing tasks, projects, attachments, database, and owner key were preserved."

BRIDGE_REFRESH_FAILURES=0
refresh_bridge() {
  local env_file="$1"
  local service_name="$2"
  local root=""
  local branch=""
  local dirty=""
  local unsafe=""
  local remote_head=""
  local local_head=""
  local service_active=0

  if [ ! -f "$env_file" ]; then
    echo "Bridge refresh skipped for $service_name: configuration file is absent, so liveness cannot be verified." >&2
    return 1
  fi
  root="$(grep '^BRIDGE_ROOT=' "$env_file" 2>/dev/null | head -1 | cut -d= -f2- || true)"
  if [ -z "$root" ] || [ ! -d "$root/.git" ]; then
    echo "Bridge refresh failed for $service_name: BRIDGE_ROOT is not a Git checkout." >&2
    return 1
  fi

  branch="$(git -C "$root" symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
  if [ "$branch" != "main" ]; then
    echo "Bridge refresh skipped for $service_name: control checkout is on '$branch', not main." >&2
    return 1
  fi

  dirty="$(git -C "$root" status --porcelain 2>/dev/null || true)"
  if [ -n "$dirty" ]; then
    unsafe="$(printf '%s\n' "$dirty" | grep -Ev '^\?\? .*(__pycache__/|\.py[co]$)' || true)"
    if [ -n "$unsafe" ]; then
      echo "Bridge refresh skipped for $service_name: control checkout has real changes." >&2
      printf '%s\n' "$unsafe" | sed 's/^/  /' >&2
      return 1
    fi
  fi

  echo "Refreshing bridge checkout for $service_name..."
  if ! git -C "$root" fetch --prune origin main; then
    echo "Bridge refresh failed for $service_name: git fetch failed." >&2
    return 1
  fi
  remote_head="$(git -C "$root" rev-parse origin/main 2>/dev/null || true)"
  local_head="$(git -C "$root" rev-parse HEAD 2>/dev/null || true)"
  if [ -z "$remote_head" ] || [ -z "$local_head" ]; then
    echo "Bridge refresh failed for $service_name: could not resolve local/remote heads." >&2
    return 1
  fi

  if systemctl --user is-active --quiet "$service_name"; then
    service_active=1
  fi

  if [ "$local_head" = "$remote_head" ]; then
    if [ "$service_active" -eq 1 ]; then
      echo "Bridge already current and active: $service_name @ ${local_head:0:12}; restart not required."
      return 0
    fi
  elif [ "$service_active" -eq 1 ]; then
    echo "Bridge update is available for $service_name, but the service is active; control checkout and HEAD were left unchanged and restart deferred." >&2
    return 1
  else
    if ! git -C "$root" merge --ff-only origin/main; then
      echo "Bridge refresh skipped for $service_name: fast-forward was not safe." >&2
      return 1
    fi
    local_head="$(git -C "$root" rev-parse HEAD 2>/dev/null || true)"
    if [ "$local_head" != "$remote_head" ]; then
      echo "Bridge refresh failed for $service_name: checkout is not at origin/main after refresh." >&2
      return 1
    fi
  fi

  if ! systemctl --user restart "$service_name"; then
    echo "Bridge refresh failed for $service_name: service restart failed." >&2
    return 1
  fi
  if ! systemctl --user is-active --quiet "$service_name"; then
    echo "Bridge refresh failed for $service_name: service is not active after restart." >&2
    return 1
  fi
  echo "Bridge refreshed and active: $service_name @ ${local_head:0:12}"
}

check_bridge_service() {
  local service_name="$1"
  if ! systemctl --user is-active --quiet "$service_name"; then
    echo "Bridge liveness check failed for $service_name: service is not active; no control checkout was changed." >&2
    return 1
  fi
  echo "Bridge service active: $service_name"
}

if ! refresh_bridge "$HOME/.config/joeos-opencode-bridge/stickdeath.env" "stickdeath-opencode-bridge.service"; then BRIDGE_REFRESH_FAILURES=1; fi
if ! refresh_bridge "$HOME/.config/joeos-opencode-bridge/vitros.env" "vitros-opencode-bridge.service"; then BRIDGE_REFRESH_FAILURES=1; fi
# The independent VITROS verifier is a required production execution boundary.
# Gate Funnel on its liveness without mutating its shared VITROS control checkout.
if ! check_bridge_service "vitros-opencode-verifier.service"; then BRIDGE_REFRESH_FAILURES=1; fi

if [ "$BRIDGE_REFRESH_FAILURES" -ne 0 ]; then
  echo "One or more bridge refresh/liveness checks failed. PROJECT_BYTE remains local and reports execution health as degraded/unknown until corrected." >&2
fi

if command -v tailscale >/dev/null 2>&1; then
  if [ "${PROJECT_BYTE_ENABLE_FUNNEL:-0}" = "1" ] && [ "$BRIDGE_REFRESH_FAILURES" -eq 0 ]; then
    echo "Enabling public HTTPS through Tailscale Funnel by explicit request..."
    sudo tailscale funnel --bg --https=443 --yes 8094
    echo
    sudo tailscale funnel status || true
  elif [ "${PROJECT_BYTE_ENABLE_FUNNEL:-0}" = "1" ]; then
    echo "Public Funnel was not enabled because bridge liveness is not verified." >&2
  else
    echo "Public Tailscale Funnel remains disabled. Re-enable only after production gates are approved."
  fi
fi

echo
echo "PROJECT_BYTE V4 Home Command Center deployment complete."
echo "Local: http://127.0.0.1:8094/"
