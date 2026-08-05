#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

JOEOS_SECURE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JOEOS_SECURE_PORT="${JOEOS_PORT:-8080}"
JOEOS_LOCAL_URL="http://127.0.0.1:${JOEOS_SECURE_PORT}"
JOEOS_CHILD_PID=""

stop_joeos_child() {
  if [[ -n "$JOEOS_CHILD_PID" ]] && kill -0 "$JOEOS_CHILD_PID" 2>/dev/null; then
    kill "$JOEOS_CHILD_PID" 2>/dev/null || true
    wait "$JOEOS_CHILD_PID" 2>/dev/null || true
  fi
}
trap stop_joeos_child EXIT INT TERM

if ! command -v tailscale >/dev/null 2>&1; then
  echo "Tailscale is required for private HTTPS access. Install and sign in to Tailscale, then run this launcher again."
  exit 1
fi

if ! tailscale status >/dev/null 2>&1; then
  echo "Tailscale is installed but not connected. Open Tailscale, sign in, and run this launcher again."
  exit 1
fi

echo "Starting JoeOS on the VPS loopback interface..."
JOEOS_HOST=127.0.0.1 JOEOS_PORT="$JOEOS_SECURE_PORT" "$JOEOS_SECURE_DIR/start_joeos.sh" &
JOEOS_CHILD_PID=$!

JOEOS_READY=false
for _ in {1..60}; do
  if ! kill -0 "$JOEOS_CHILD_PID" 2>/dev/null; then
    echo "JoeOS stopped before its health endpoint became ready."
    wait "$JOEOS_CHILD_PID"
    exit 1
  fi
  if command -v curl >/dev/null 2>&1 && curl --fail --silent --max-time 1 "$JOEOS_LOCAL_URL/healthz" >/dev/null 2>&1; then
    JOEOS_READY=true
    break
  fi
  sleep 1
done

if [[ "$JOEOS_READY" != true ]]; then
  echo "JoeOS did not become ready at $JOEOS_LOCAL_URL within 60 seconds."
  exit 1
fi

echo "Enabling private tailnet HTTPS on port 443..."
tailscale serve --bg --https=443 "$JOEOS_LOCAL_URL"

echo
echo "JoeOS is available only inside your tailnet at the HTTPS address shown below."
echo "Open that HTTPS address on your iPhone, then use Share → Add to Home Screen."
echo "Tailscale Funnel was not enabled. Keep this window open while using JoeOS."
echo
tailscale serve status
echo
echo "Press Ctrl+C to stop the JoeOS process. The private Serve mapping remains ready for the next launch."

wait "$JOEOS_CHILD_PID"
