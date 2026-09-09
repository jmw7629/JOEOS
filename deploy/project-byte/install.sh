#!/usr/bin/env bash
set -euo pipefail

BRANCH="project-byte-deploy"
BASE="https://raw.githubusercontent.com/jmw7629/JOEOS/${BRANCH}/deploy/project-byte"
V4="$BASE/v4"
DEST="/home/joevps/PROJECT_BYTE"
SERVICE="/etc/systemd/system/project-byte.service"
STAMP="$(date +%Y%m%d-%H%M%S)"
EXPECTED_PERMISSIONS="0faf72af5e015c58ba7737fe9c6aaca227529fa0835fcc55f4e7d22d84e2c149"
EXPECTED_BACKEND="7df2ed5c0e32ebca1bc2efce46ecdc3a393318e833bc4c6ee8754404fc642526"
EXPECTED_SERVER="177d74b873876c90728d71835fd30b3f4b8ecde04c8369043c849a4e6813960b"
EXPECTED_INDEX="3587563622808bc0beeec346eb7324af641e6403f3070924f2b3d4bb79194f44"
EXPECTED_HOME="0dd8572875e5d59abb93a539380a55c7f8d15ff9097b85b65fcbc46ac52649e8"
EXPECTED_INSPECTOR="1f53a44f5950a0ff250494997dab8dae6fa50a7c80fabbc43043095fbaee6f08"
EXPECTED_HEALTH="c5b7bdb56d6c51dd56475d677f7b41dca95d7dbddbf6b9d5f739cc5606aee058"
EXPECTED_AI_RUNTIME="30ed1c2088a70e4578a0e47899e28078043d1cb87ece9040366be600aa9916d8"
EXPECTED_AI_CONNECTIONS="8f448f65333a11587cbea0062a1a62c6bdc70b5af2f47cf6db169387b7a8d1dc"
EXPECTED_CODEX_CONNECTION="835877142e6c5dc80729533144df11746d326dd364abfc75fcaad04ceec1f9bb"
EXPECTED_AI_UI="bc13a5d862ab55fc397f57a15ba48676dea3c6277c76c94f1540371b31872c1e"
EXPECTED_CODEX_TASK_RPC="be991db3ceda3e9ca89dc7361aeeba0ec6ab4a3ebac79e1b452558fc21ab1881"
EXPECTED_CODEX_SANDBOX="d71d38ad1fdb7ad9cd64e480d9d1f90dd16878e21cb92d11e51f9dfa0db96efc"
EXPECTED_CODEX_TASKS="0c19d261bf2050a53040f57e0b3f9865fb6f8629aeec1251b2f260fb346a8ec5"
EXPECTED_CODEX_TASKS_RUNTIME="af38ee1e335f4245b9e8480fb5b975149b309c6816f1ae1b133d6e37cda3e0b4"
EXPECTED_CODEX_PROJECTS="f528b817ffb614532058deb00333ad33325a1734f031a6b98ad6303f601abca5"
EXPECTED_CODEX_PUBLISHER="4be7c520d111745367c8ae21b42c31ee1e08597e925fcd6da9189cdbbf7e3451"
EXPECTED_CODEX_UI="a50318d4f4afd3ffb1e354d88ac8eff0af31bcc669a5b6530e7c5f3e303ab266"

if [ "$(id -un)" != "joevps" ]; then
  echo "Run this as joevps, not root." >&2
  exit 1
fi

# Remote/non-login shells do not always inherit the joevps user service bus.
# Establish the conventional per-user bus explicitly; service-state failures
# below still fail closed rather than being interpreted as an inactive worker.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=${XDG_RUNTIME_DIR}/bus}"

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
if [ -f "$DEST/execution_permissions.py" ]; then cp -p "$DEST/execution_permissions.py" "$DEST/backups/execution-permissions-${STAMP}.py"; fi
if [ -f "$DEST/backend.py" ]; then cp -p "$DEST/backend.py" "$DEST/backups/backend-${STAMP}.py"; fi
if [ -f "$DEST/index.html" ]; then cp -p "$DEST/index.html" "$DEST/backups/index-${STAMP}.html"; fi
if [ -f "$DEST/home.js" ]; then cp -p "$DEST/home.js" "$DEST/backups/home-${STAMP}.js"; fi
if [ -f "$DEST/home-inspector.js" ]; then cp -p "$DEST/home-inspector.js" "$DEST/backups/home-inspector-${STAMP}.js"; fi
if [ -f "$DEST/health-runtime.js" ]; then cp -p "$DEST/health-runtime.js" "$DEST/backups/health-runtime-${STAMP}.js"; fi
if [ -f "$DEST/admin.secret" ]; then cp -p "$DEST/admin.secret" "$DEST/backups/admin-${STAMP}.secret"; fi

if [ -f "$DEST/ai_runtime.py" ]; then cp -p "$DEST/ai_runtime.py" "$DEST/backups/ai_runtime.py-${STAMP}"; fi

if [ -f "$DEST/ai_connections.py" ]; then cp -p "$DEST/ai_connections.py" "$DEST/backups/ai_connections.py-${STAMP}"; fi

if [ -f "$DEST/codex_connection.py" ]; then cp -p "$DEST/codex_connection.py" "$DEST/backups/codex_connection.py-${STAMP}"; fi

if [ -f "$DEST/ai-connections.js" ]; then cp -p "$DEST/ai-connections.js" "$DEST/backups/ai-connections.js-${STAMP}"; fi
if [ -f "$DEST/codex_task_rpc.py" ]; then cp -p "$DEST/codex_task_rpc.py" "$DEST/backups/codex_task_rpc.py-${STAMP}"; fi
if [ -f "$DEST/codex_sandbox.py" ]; then cp -p "$DEST/codex_sandbox.py" "$DEST/backups/codex_sandbox.py-${STAMP}"; fi
if [ -f "$DEST/codex_tasks.py" ]; then cp -p "$DEST/codex_tasks.py" "$DEST/backups/codex_tasks.py-${STAMP}"; fi
if [ -f "$DEST/codex_tasks_runtime.py" ]; then cp -p "$DEST/codex_tasks_runtime.py" "$DEST/backups/codex_tasks_runtime.py-${STAMP}"; fi
if [ -f "$DEST/codex_projects.py" ]; then cp -p "$DEST/codex_projects.py" "$DEST/backups/codex_projects.py-${STAMP}"; fi
if [ -f "$DEST/codex_publisher.py" ]; then cp -p "$DEST/codex_publisher.py" "$DEST/backups/codex_publisher.py-${STAMP}"; fi
if [ -f "$DEST/codex-workspace.js" ]; then cp -p "$DEST/codex-workspace.js" "$DEST/backups/codex-workspace.js-${STAMP}"; fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/backend" "$TMP/index"

for n in 01 02 03 04 05 06 07 08; do
  curl --fail --silent --show-error --location "$V4/server/$n.part" -o "$TMP/backend/$n.part"
done
for n in 01 02 03 04 05 06 07; do
  curl --fail --silent --show-error --location "$V4/index/$n.part" -o "$TMP/index/$n.part"
done
curl --fail --silent --show-error --location "$V4/execution_permissions.py" -o "$TMP/execution_permissions.py"
curl --fail --silent --show-error --location "$V4/runtime_server.py" -o "$TMP/server.py"
curl --fail --silent --show-error --location "$V4/home.js" -o "$TMP/home.js"
curl --fail --silent --show-error --location "$V4/home-inspector.js" -o "$TMP/home-inspector.js"
curl --fail --silent --show-error --location "$V4/health-runtime.js" -o "$TMP/health-runtime.js"
curl --fail --silent --show-error --location "$V4/ai_runtime.py" -o "$TMP/ai_runtime.py"
curl --fail --silent --show-error --location "$V4/ai_connections.py" -o "$TMP/ai_connections.py"
curl --fail --silent --show-error --location "$V4/codex_connection.py" -o "$TMP/codex_connection.py"
curl --fail --silent --show-error --location "$V4/ai-connections.js" -o "$TMP/ai-connections.js"
curl --fail --silent --show-error --location "$V4/codex_task_rpc.py" -o "$TMP/codex_task_rpc.py"
curl --fail --silent --show-error --location "$V4/codex_sandbox.py" -o "$TMP/codex_sandbox.py"
curl --fail --silent --show-error --location "$V4/codex_tasks.py" -o "$TMP/codex_tasks.py"
curl --fail --silent --show-error --location "$V4/codex_tasks_runtime.py" -o "$TMP/codex_tasks_runtime.py"
curl --fail --silent --show-error --location "$V4/codex_projects.py" -o "$TMP/codex_projects.py"
curl --fail --silent --show-error --location "$V4/codex_publisher.py" -o "$TMP/codex_publisher.py"
curl --fail --silent --show-error --location "$V4/codex-workspace.js" -o "$TMP/codex-workspace.js"
cat "$TMP"/backend/*.part > "$TMP/backend.py"
cat "$TMP"/index/*.part > "$TMP/index.html"

PERMISSIONS_SHA="$(sha256sum "$TMP/execution_permissions.py" | awk '{print $1}')"
BACKEND_SHA="$(sha256sum "$TMP/backend.py" | awk '{print $1}')"
SERVER_SHA="$(sha256sum "$TMP/server.py" | awk '{print $1}')"
INDEX_SHA="$(sha256sum "$TMP/index.html" | awk '{print $1}')"
HOME_SHA="$(sha256sum "$TMP/home.js" | awk '{print $1}')"
INSPECTOR_SHA="$(sha256sum "$TMP/home-inspector.js" | awk '{print $1}')"
HEALTH_SHA="$(sha256sum "$TMP/health-runtime.js" | awk '{print $1}')"
AI_RUNTIME_SHA="$(sha256sum "$TMP/ai_runtime.py" | awk '{print $1}')"
[ "$AI_RUNTIME_SHA" = "$EXPECTED_AI_RUNTIME" ] || { echo "ai_runtime.py checksum mismatch; refusing deployment." >&2; exit 5; }
AI_CONNECTIONS_SHA="$(sha256sum "$TMP/ai_connections.py" | awk '{print $1}')"
[ "$AI_CONNECTIONS_SHA" = "$EXPECTED_AI_CONNECTIONS" ] || { echo "ai_connections.py checksum mismatch; refusing deployment." >&2; exit 5; }
CODEX_CONNECTION_SHA="$(sha256sum "$TMP/codex_connection.py" | awk '{print $1}')"
[ "$CODEX_CONNECTION_SHA" = "$EXPECTED_CODEX_CONNECTION" ] || { echo "codex_connection.py checksum mismatch; refusing deployment." >&2; exit 5; }
AI_UI_SHA="$(sha256sum "$TMP/ai-connections.js" | awk '{print $1}')"
[ "$AI_UI_SHA" = "$EXPECTED_AI_UI" ] || { echo "ai-connections.js checksum mismatch; refusing deployment." >&2; exit 5; }
CODEX_TASK_RPC_SHA="$(sha256sum "$TMP/codex_task_rpc.py" | awk '{print $1}')"
[ "$CODEX_TASK_RPC_SHA" = "$EXPECTED_CODEX_TASK_RPC" ] || { echo "codex_task_rpc.py checksum mismatch; refusing deployment." >&2; exit 5; }
CODEX_SANDBOX_SHA="$(sha256sum "$TMP/codex_sandbox.py" | awk '{print $1}')"
[ "$CODEX_SANDBOX_SHA" = "$EXPECTED_CODEX_SANDBOX" ] || { echo "codex_sandbox.py checksum mismatch; refusing deployment." >&2; exit 5; }
CODEX_TASKS_SHA="$(sha256sum "$TMP/codex_tasks.py" | awk '{print $1}')"
[ "$CODEX_TASKS_SHA" = "$EXPECTED_CODEX_TASKS" ] || { echo "codex_tasks.py checksum mismatch; refusing deployment." >&2; exit 5; }
CODEX_TASKS_RUNTIME_SHA="$(sha256sum "$TMP/codex_tasks_runtime.py" | awk '{print $1}')"
CODEX_PROJECTS_SHA="$(sha256sum "$TMP/codex_projects.py" | awk '{print $1}')"
[ "$CODEX_TASKS_RUNTIME_SHA" = "$EXPECTED_CODEX_TASKS_RUNTIME" ] || { echo "codex_tasks_runtime.py checksum mismatch; refusing deployment." >&2; exit 5; }
[ "$CODEX_PROJECTS_SHA" = "$EXPECTED_CODEX_PROJECTS" ] || { echo "codex_projects.py checksum mismatch; refusing deployment." >&2; exit 5; }
CODEX_PUBLISHER_SHA="$(sha256sum "$TMP/codex_publisher.py" | awk '{print $1}')"
[ "$CODEX_PUBLISHER_SHA" = "$EXPECTED_CODEX_PUBLISHER" ] || { echo "codex_publisher.py checksum mismatch; refusing deployment." >&2; exit 5; }
CODEX_UI_SHA="$(sha256sum "$TMP/codex-workspace.js" | awk '{print $1}')"
[ "$CODEX_UI_SHA" = "$EXPECTED_CODEX_UI" ] || { echo "codex-workspace.js checksum mismatch; refusing deployment." >&2; exit 5; }
[ "$PERMISSIONS_SHA" = "$EXPECTED_PERMISSIONS" ] || { echo "Permission module checksum mismatch; refusing deployment." >&2; exit 5; }
[ "$BACKEND_SHA" = "$EXPECTED_BACKEND" ] || { echo "Backend checksum mismatch; refusing deployment." >&2; exit 5; }
[ "$SERVER_SHA" = "$EXPECTED_SERVER" ] || { echo "Runtime server SHA-256 mismatch; refusing deployment." >&2; exit 5; }
[ "$INDEX_SHA" = "$EXPECTED_INDEX" ] || { echo "UI checksum mismatch; refusing deployment." >&2; exit 5; }
[ "$HOME_SHA" = "$EXPECTED_HOME" ] || { echo "Home module checksum mismatch; refusing deployment." >&2; exit 5; }
[ "$INSPECTOR_SHA" = "$EXPECTED_INSPECTOR" ] || { echo "Home inspector checksum mismatch; refusing deployment." >&2; exit 5; }
[ "$HEALTH_SHA" = "$EXPECTED_HEALTH" ] || { echo "Health UI checksum mismatch; refusing deployment." >&2; exit 5; }
python3 -m py_compile "$TMP/backend.py" "$TMP/server.py" "$TMP/execution_permissions.py" "$TMP/ai_runtime.py" "$TMP/ai_connections.py" "$TMP/codex_connection.py" "$TMP/codex_task_rpc.py" "$TMP/codex_sandbox.py" "$TMP/codex_tasks.py" "$TMP/codex_tasks_runtime.py" "$TMP/codex_projects.py" "$TMP/codex_publisher.py"
if command -v node >/dev/null 2>&1; then
  node --check "$TMP/ai-connections.js"
  node --check "$TMP/codex-workspace.js"
  node - "$TMP/index.html" "$TMP/home.js" "$TMP/home-inspector.js" "$TMP/health-runtime.js" <<'NODE'
const fs=require('fs');
const h=fs.readFileSync(process.argv[2],'utf8');
const m=h.match(/<script>([\s\S]*?)<\/script>/);
if(!m)throw new Error('inline script missing');
new Function(m[1]);
const home=fs.readFileSync(process.argv[3],'utf8');
const inspector=fs.readFileSync(process.argv[4],'utf8');
const health=fs.readFileSync(process.argv[5],'utf8');
new Function(home); new Function(inspector); new Function(health);
for(const x of ['Portfolio','Kanban','Work next','AI','Agents','Terminal','Models','Team','Activity','Settings','Help / How-To'])if(!h.includes(x))throw new Error('missing '+x);
for(const x of ['AI_BYTE','Live agents','Team / org map','Current activity','My work','Ready for review','Recent memories','Portfolio pulse'])if(!home.includes(x))throw new Error('missing Home '+x);
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

install -m 0644 "$TMP/execution_permissions.py" "$DEST/execution_permissions.py"
install -m 0644 "$TMP/backend.py" "$DEST/backend.py"
install -m 0644 "$TMP/server.py" "$DEST/server.py"
install -m 0644 "$TMP/index.html" "$DEST/index.html"
install -m 0644 "$TMP/home.js" "$DEST/home.js"
install -m 0644 "$TMP/home-inspector.js" "$DEST/home-inspector.js"
install -m 0644 "$TMP/health-runtime.js" "$DEST/health-runtime.js"
install -m 0644 "$TMP/ai_runtime.py" "$DEST/ai_runtime.py"
install -m 0644 "$TMP/ai_connections.py" "$DEST/ai_connections.py"
install -m 0644 "$TMP/codex_connection.py" "$DEST/codex_connection.py"
install -m 0644 "$TMP/ai-connections.js" "$DEST/ai-connections.js"
install -m 0644 "$TMP/codex_task_rpc.py" "$DEST/codex_task_rpc.py"
install -m 0644 "$TMP/codex_sandbox.py" "$DEST/codex_sandbox.py"
install -m 0644 "$TMP/codex_tasks.py" "$DEST/codex_tasks.py"
install -m 0644 "$TMP/codex_tasks_runtime.py" "$DEST/codex_tasks_runtime.py"
install -m 0644 "$TMP/codex_projects.py" "$DEST/codex_projects.py"
install -m 0644 "$TMP/codex_publisher.py" "$DEST/codex_publisher.py"
install -m 0644 "$TMP/codex-workspace.js" "$DEST/codex-workspace.js"
# Packaging never configures PRFKT_CODEX_STATE/REPO/PUBLISH or starts a broker.
# Native tasks remain unavailable until the owner registers an isolated runtime.
mkdir -p /home/joevps/.local/state/project-byte
chmod 700 /home/joevps/.local/state/project-byte
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
ReadWritePaths=/home/joevps/PROJECT_BYTE /home/joevps/.local/state/project-byte
ReadWritePaths=-/home/joevps/.local/share/project-byte-codex/auth

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
      && curl -fsS http://127.0.0.1:8094/codex-workspace.js -o "$TMP/live-codex-workspace.js" \
      && grep -Fq '<script src="/home.js"></script>' "$TMP/live-index.html" \
      && grep -Fq '<script src="/home-inspector.js"></script>' "$TMP/live-index.html" \
      && grep -Fq '<script src="/health-runtime.js"></script>' "$TMP/live-index.html" \
      && grep -Fq '<script src="/codex-workspace.js"></script>' "$TMP/live-index.html" \
      && grep -Fq 'PRFKT_CODEX' "$TMP/live-codex-workspace.js" \
      && grep -Fq 'AI_BYTE' "$TMP/live-home.js" \
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
  if [ -f "$DEST/backups/ai_runtime.py-${STAMP}" ]; then cp -p "$DEST/backups/ai_runtime.py-${STAMP}" "$DEST/ai_runtime.py"; else rm -f "$DEST/ai_runtime.py"; fi
  if [ -f "$DEST/backups/ai_connections.py-${STAMP}" ]; then cp -p "$DEST/backups/ai_connections.py-${STAMP}" "$DEST/ai_connections.py"; else rm -f "$DEST/ai_connections.py"; fi
  if [ -f "$DEST/backups/codex_connection.py-${STAMP}" ]; then cp -p "$DEST/backups/codex_connection.py-${STAMP}" "$DEST/codex_connection.py"; else rm -f "$DEST/codex_connection.py"; fi
  if [ -f "$DEST/backups/ai-connections.js-${STAMP}" ]; then cp -p "$DEST/backups/ai-connections.js-${STAMP}" "$DEST/ai-connections.js"; else rm -f "$DEST/ai-connections.js"; fi
  if [ -f "$DEST/backups/codex_task_rpc.py-${STAMP}" ]; then cp -p "$DEST/backups/codex_task_rpc.py-${STAMP}" "$DEST/codex_task_rpc.py"; else rm -f "$DEST/codex_task_rpc.py"; fi
  if [ -f "$DEST/backups/codex_sandbox.py-${STAMP}" ]; then cp -p "$DEST/backups/codex_sandbox.py-${STAMP}" "$DEST/codex_sandbox.py"; else rm -f "$DEST/codex_sandbox.py"; fi
  if [ -f "$DEST/backups/codex_tasks.py-${STAMP}" ]; then cp -p "$DEST/backups/codex_tasks.py-${STAMP}" "$DEST/codex_tasks.py"; else rm -f "$DEST/codex_tasks.py"; fi
  if [ -f "$DEST/backups/codex_tasks_runtime.py-${STAMP}" ]; then cp -p "$DEST/backups/codex_tasks_runtime.py-${STAMP}" "$DEST/codex_tasks_runtime.py"; else rm -f "$DEST/codex_tasks_runtime.py"; fi
  if [ -f "$DEST/backups/codex_projects.py-${STAMP}" ]; then cp -p "$DEST/backups/codex_projects.py-${STAMP}" "$DEST/codex_projects.py"; else rm -f "$DEST/codex_projects.py"; fi
  if [ -f "$DEST/backups/codex_publisher.py-${STAMP}" ]; then cp -p "$DEST/backups/codex_publisher.py-${STAMP}" "$DEST/codex_publisher.py"; else rm -f "$DEST/codex_publisher.py"; fi
  if [ -f "$DEST/backups/codex-workspace.js-${STAMP}" ]; then cp -p "$DEST/backups/codex-workspace.js-${STAMP}" "$DEST/codex-workspace.js"; else rm -f "$DEST/codex-workspace.js"; fi
  sudo systemctl restart project-byte.service
  exit 6
fi

echo "PROJECT_BYTE core service is healthy: $HEALTH"
echo "Home command center, agent inspector, and evidence-backed health UI are loaded."
echo "Existing tasks, projects, attachments, database, and owner key were preserved."

BRIDGE_REFRESH_FAILURES=0
bridge_service_state() {
  local service_name="$1"
  local state=""
  local rc=0
  state="$(systemctl --user is-active "$service_name" 2>/dev/null)" || rc=$?
  if [ "$rc" -eq 0 ]; then
    case "$state" in
      active|reloading|activating|deactivating) printf '%s\n' active; return 0 ;;
    esac
  fi
  if [ "$rc" -eq 3 ]; then
    case "$state" in
      inactive|failed) printf '%s\n' inactive; return 0 ;;
      deactivating) printf '%s\n' active; return 0 ;;
    esac
  fi
  printf '%s\n' unavailable
}

byte_owner_hold_clear() {
  python3 - "$HOME/.config/joeos-opencode-bridge/STICKDEATH_BYTE_STOPPED_BY_OWNER" <<'OWNER_HOLD_PY'
from pathlib import Path
import sys
try:
    Path(sys.argv[1]).lstat()
except FileNotFoundError:
    raise SystemExit(0)
except OSError:
    print("BYTE owner-stop evidence unavailable; checkout and service left untouched.", file=sys.stderr)
    raise SystemExit(1)
print("BYTE paused by owner; checkout and service left untouched.", file=sys.stderr)
raise SystemExit(1)
OWNER_HOLD_PY
}

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
  local service_state="unavailable"

  if [ "$service_name" = "stickdeath-byte-opencode-bridge.service" ] && ! byte_owner_hold_clear; then
    return 1
  fi

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

  if [ "$service_name" = "stickdeath-byte-opencode-bridge.service" ] && ! byte_owner_hold_clear; then
    return 1
  fi

  service_state="$(bridge_service_state "$service_name")"
  if [ "$service_state" = "unavailable" ]; then
    echo "Bridge refresh skipped for $service_name: user service state is unavailable; no fetch, merge, or restart was performed." >&2
    return 1
  fi
  [ "$service_state" = "active" ] && service_active=1

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

  service_state="$(bridge_service_state "$service_name")"
  if [ "$service_state" = "unavailable" ]; then
    echo "Bridge refresh stopped for $service_name: user service state became unavailable; checkout and restart were left untouched." >&2
    return 1
  fi
  service_active=0
  [ "$service_state" = "active" ] && service_active=1

  if [ "$local_head" = "$remote_head" ]; then
    if [ "$service_active" -eq 1 ]; then
      echo "Bridge already current and active: $service_name @ ${local_head:0:12}; restart not required."
      return 0
    fi
  elif [ "$service_active" -eq 1 ]; then
    echo "Bridge update is available for $service_name, but the service is active; control checkout and HEAD were left unchanged and restart deferred." >&2
    return 1
  else
    if [ "$service_name" = "stickdeath-byte-opencode-bridge.service" ] && ! byte_owner_hold_clear; then
      return 1
    fi
    service_state="$(bridge_service_state "$service_name")"
    if [ "$service_state" != "inactive" ]; then
      if [ "$service_state" = "active" ]; then
        echo "Bridge refresh deferred for $service_name: service became active before checkout mutation; HEAD was left unchanged." >&2
      else
        echo "Bridge refresh skipped for $service_name: user service state is unavailable before checkout mutation." >&2
      fi
      return 1
    fi
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

  if [ "$service_name" = "stickdeath-byte-opencode-bridge.service" ] && ! byte_owner_hold_clear; then
    return 1
  fi

  service_state="$(bridge_service_state "$service_name")"
  if [ "$service_state" = "active" ]; then
    echo "Bridge is active at current checkout: $service_name @ ${local_head:0:12}; restart not required."
    return 0
  fi
  if [ "$service_state" = "unavailable" ]; then
    echo "Bridge refresh stopped for $service_name: user service state is unavailable before restart." >&2
    return 1
  fi
  if ! systemctl --user restart "$service_name"; then
    echo "Bridge refresh failed for $service_name: service restart failed." >&2
    return 1
  fi
  service_state="$(bridge_service_state "$service_name")"
  if [ "$service_state" != "active" ]; then
    if [ "$service_state" = "unavailable" ]; then
      echo "Bridge refresh failed for $service_name: user service state is unavailable after restart." >&2
    else
      echo "Bridge refresh failed for $service_name: service is not active after restart." >&2
    fi
    return 1
  fi
  echo "Bridge refreshed and active: $service_name @ ${local_head:0:12}"
}

check_bridge_service() {
  local service_name="$1"
  local service_state=""
  service_state="$(bridge_service_state "$service_name")"
  if [ "$service_state" = "unavailable" ]; then
    echo "Bridge liveness check failed for $service_name: user service state is unavailable; no control checkout was changed." >&2
    return 1
  fi
  if [ "$service_state" != "active" ]; then
    echo "Bridge liveness check failed for $service_name: service is not active; no control checkout was changed." >&2
    return 1
  fi
  echo "Bridge service active: $service_name"
}

if ! refresh_bridge "$HOME/.config/joeos-opencode-bridge/stickdeath-byte.env" "stickdeath-byte-opencode-bridge.service"; then BRIDGE_REFRESH_FAILURES=1; fi
if ! refresh_bridge "$HOME/.config/joeos-opencode-bridge/vitros.env" "vitros-opencode-bridge.service"; then BRIDGE_REFRESH_FAILURES=1; fi
# The independent VITROS verifier is a required production execution boundary.
# Gate Funnel on its liveness without mutating its shared VITROS control checkout.
if ! check_bridge_service "vitros-opencode-verifier.service"; then BRIDGE_REFRESH_FAILURES=1; fi

if [ "$BRIDGE_REFRESH_FAILURES" -ne 0 ]; then
  echo "One or more bridges are paused, unavailable or unverified. PROJECT_BYTE remains local; owner holds are never cleared by this installer." >&2
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
