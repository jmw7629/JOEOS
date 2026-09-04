#!/usr/bin/env bash
set -euo pipefail
BRANCH="project-byte-deploy"
BASE="https://raw.githubusercontent.com/jmw7629/JOEOS/${BRANCH}/deploy/project-byte"
DEST="/home/joevps/PROJECT_BYTE"
SERVICE="/etc/systemd/system/project-byte.service"

if [ "$(id -un)" != "joevps" ]; then
  echo "Run this as joevps (the VPS account), not root." >&2
  exit 1
fi

mkdir -p "$DEST"
curl -fsSL "$BASE/server.py" -o "$DEST/server.py"
curl -fsSL "$BASE/index.html" -o "$DEST/index.html"
python3 -m py_compile "$DEST/server.py"

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
sudo systemctl enable --now project-byte.service
sleep 1
curl -fsS http://127.0.0.1:8094/healthz >/dev/null

echo "PROJECT_BYTE backend is healthy on 127.0.0.1:8094"
echo
echo "OWNER KEY (save this; share only with trusted editors):"
cat "$DEST/admin.secret"
echo

if command -v tailscale >/dev/null 2>&1; then
  echo "Configuring public HTTPS through Tailscale Funnel..."
  if sudo tailscale funnel --bg --https=443 --yes 8094; then
    echo
    sudo tailscale funnel status || true
    echo
    echo "PROJECT_BYTE is publicly shared through the HTTPS URL shown above."
  else
    echo
    echo "The app is installed and running, but Funnel needs one-time tailnet approval."
    echo "Run: sudo tailscale funnel --bg --https=443 8094"
    exit 2
  fi
else
  echo "PROJECT_BYTE is installed and running, but Tailscale is not installed on this VPS."
  echo "Expose 127.0.0.1:8094 through your existing reverse proxy to publish it."
  exit 3
fi
