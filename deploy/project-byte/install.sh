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
if [ -f "$DEST/kanban.db" ]; then
  cp -p "$DEST/kanban.db" "$DEST/backups/kanban-${STAMP}.db"
  echo "Database backup: $DEST/backups/kanban-${STAMP}.db"
fi
if [ -f "$DEST/server.py" ]; then cp -p "$DEST/server.py" "$DEST/backups/server-${STAMP}.py"; fi
if [ -f "$DEST/index.html" ]; then cp -p "$DEST/index.html" "$DEST/backups/index-${STAMP}.html"; fi

curl -fsSL "$BASE/server.py" -o "$DEST/server.py.new"
curl -fsSL "$BASE/index.html" -o "$DEST/index.html.new"
python3 -m py_compile "$DEST/server.py.new"
mv "$DEST/server.py.new" "$DEST/server.py"
mv "$DEST/index.html.new" "$DEST/index.html"

sudo tee "$SERVICE" >/dev/null <<'UNIT'
[Unit]
Description=PROJECT_BYTE shared Kanban
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
sleep 1
HEALTH="$(curl -fsS http://127.0.0.1:8094/healthz)"
echo "$HEALTH" | grep -q '"version":2' || { echo "PROJECT_BYTE v2 health check failed: $HEALTH" >&2; exit 4; }

echo "PROJECT_BYTE v2 backend is healthy on 127.0.0.1:8094"
echo "Existing database and owner key were preserved."

if [ -f "$DEST/admin.secret" ]; then
  echo
  echo "Owner key remains unchanged. Do not paste it into chat."
fi

if command -v tailscale >/dev/null 2>&1; then
  echo "Ensuring public HTTPS through Tailscale Funnel..."
  if sudo tailscale funnel --bg --https=443 --yes 8094; then
    echo
    sudo tailscale funnel status || true
    echo
    echo "PROJECT_BYTE v2 is public through the HTTPS URL shown above."
  else
    echo
    echo "PROJECT_BYTE v2 is running locally, but Funnel needs attention."
    echo "Run: sudo tailscale funnel --bg --https=443 8094"
    exit 2
  fi
else
  echo "PROJECT_BYTE v2 is running locally, but Tailscale is not installed on this VPS."
  exit 3
fi
