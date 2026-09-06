#!/usr/bin/env bash
set -euo pipefail

BRANCH="project-byte-deploy"
BASE="https://raw.githubusercontent.com/jmw7629/JOEOS/${BRANCH}/deploy/project-byte"
DEST="/home/joevps/PROJECT_BYTE"
SERVICE="/etc/systemd/system/project-byte.service"
STAMP="$(date +%Y%m%d-%H%M%S)"

if [ "$(id -un)" != "joevps" ]; then
  echo "Run this as joevps (the VPS account), not root." >&2
  exit 1
fi

mkdir -p "$DEST" "$DEST/backups" "$DEST/uploads"

# Preserve live data and the currently working app before touching anything.
if [ -f "$DEST/kanban.db" ]; then
  cp -p "$DEST/kanban.db" "$DEST/backups/kanban-${STAMP}.db"
  echo "Database backup: $DEST/backups/kanban-${STAMP}.db"
fi
if [ -f "$DEST/server.py" ]; then cp -p "$DEST/server.py" "$DEST/backups/server-${STAMP}.py"; fi
if [ -f "$DEST/index.html" ]; then cp -p "$DEST/index.html" "$DEST/backups/index-${STAMP}.html"; fi
if [ -f "$DEST/admin.secret" ]; then cp -p "$DEST/admin.secret" "$DEST/backups/admin-${STAMP}.secret"; fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# IMPORTANT: use ordinary source files. The previous V3 .gz.b64 artifacts were malformed.
curl --fail --silent --show-error --location "$BASE/server.py" -o "$TMP/server.py"
curl --fail --silent --show-error --location "$BASE/index.html" -o "$TMP/index.html"

# Refuse suspicious/truncated downloads.
test -s "$TMP/server.py"
test -s "$TMP/index.html"
grep -q 'PROJECT_BYTE' "$TMP/server.py"
grep -q 'PROJECT_BYTE' "$TMP/index.html"
python3 -m py_compile "$TMP/server.py"

SERVER_BYTES="$(wc -c < "$TMP/server.py")"
INDEX_BYTES="$(wc -c < "$TMP/index.html")"
if [ "$SERVER_BYTES" -lt 10000 ] || [ "$INDEX_BYTES" -lt 10000 ]; then
  echo "Downloaded source is unexpectedly small; refusing deployment." >&2
  exit 5
fi

echo "Validated source: server=${SERVER_BYTES} bytes, ui=${INDEX_BYTES} bytes"

install -m 0644 "$TMP/server.py" "$DEST/server.py"
install -m 0644 "$TMP/index.html" "$DEST/index.html"

sudo tee "$SERVICE" >/dev/null <<'UNIT'
[Unit]
Description=PROJECT_BYTE portfolio command center
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
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if HEALTH="$(curl -fsS http://127.0.0.1:8094/healthz 2>/dev/null)"; then break; fi
  sleep 1
done

if ! echo "$HEALTH" | grep -q '"ok":true'; then
  echo "PROJECT_BYTE health check failed after restart: $HEALTH" >&2
  echo "Restoring previous application files..." >&2
  LAST_SERVER="$(ls -1t "$DEST"/backups/server-*.py 2>/dev/null | head -1 || true)"
  LAST_INDEX="$(ls -1t "$DEST"/backups/index-*.html 2>/dev/null | head -1 || true)"
  [ -n "$LAST_SERVER" ] && cp -p "$LAST_SERVER" "$DEST/server.py"
  [ -n "$LAST_INDEX" ] && cp -p "$LAST_INDEX" "$DEST/index.html"
  sudo systemctl restart project-byte.service
  exit 6
fi

echo "PROJECT_BYTE is healthy: $HEALTH"
echo "Existing database, attachments, and owner key were preserved."

if command -v tailscale >/dev/null 2>&1; then
  echo "Ensuring public HTTPS through Tailscale Funnel..."
  sudo tailscale funnel --bg --https=443 --yes 8094
  echo
  sudo tailscale funnel status || true
fi

echo
echo "PROJECT_BYTE deployment complete."
