#!/usr/bin/env bash
set -euo pipefail

JOEOS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$JOEOS_DIR"

if [[ -n "${JOEOS_PYTHON:-}" ]]; then
  PYTHON_CMD="$JOEOS_PYTHON"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_CMD="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_CMD="python"
else
  echo "Python 3 is required. Install it, then run this launcher again."
  exit 1
fi

VENV_DIR="${JOEOS_VENV_DIR:-$JOEOS_DIR/.venv}"
if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  echo "Creating the private JoeOS Python environment..."
  "$PYTHON_CMD" -m venv "$VENV_DIR"
fi

if ! "$VENV_DIR/bin/python" -c "import cryptography, fastapi, httpx, psutil, uvicorn" >/dev/null 2>&1; then
  echo "Installing JoeOS runtime packages (first launch only)..."
  "$VENV_DIR/bin/python" -m pip install --disable-pip-version-check -r requirements.txt
fi

JOEOS_BIND_HOST="${JOEOS_HOST:-}"
if [[ -z "$JOEOS_BIND_HOST" ]] && command -v tailscale >/dev/null 2>&1; then
  JOEOS_BIND_HOST="$(tailscale ip -4 2>/dev/null | awk 'NR == 1 { print; exit }')"
fi
JOEOS_BIND_HOST="${JOEOS_BIND_HOST:-127.0.0.1}"
JOEOS_BIND_PORT="${JOEOS_PORT:-8080}"
export LEMONADE_BASE_URL="${LEMONADE_BASE_URL:-http://127.0.0.1:13305/api/v1}"
export PYTHONUNBUFFERED=1

echo
echo "JoeOS Command Center is starting at http://$JOEOS_BIND_HOST:$JOEOS_BIND_PORT"
echo "Lemonade Server remains private at $LEMONADE_BASE_URL"
if [[ "$JOEOS_BIND_HOST" == "127.0.0.1" ]]; then
  echo "No Tailscale IPv4 address was detected, so access is limited to this computer."
else
  echo "Open the JoeOS address on your iPhone while it is connected to the same tailnet."
fi
echo "Press Ctrl+C to stop JoeOS."
echo

exec "$VENV_DIR/bin/python" -m uvicorn joeos_backend:app \
  --host "$JOEOS_BIND_HOST" \
  --port "$JOEOS_BIND_PORT" \
  --no-access-log
