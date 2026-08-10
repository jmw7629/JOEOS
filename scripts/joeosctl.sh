#!/usr/bin/env bash
# joeosctl — canonical JoeOS service control.
#
# The ONE controlled mechanism for deploying/restarting the JoeOS browser
# backend on the authoritative Halo host. Agents and the Engineering Director
# must use this script instead of bare `pkill`/`nohup`, which previously
# wedged production (a broad pkill killed the supervised backend and left a
# half-dead instance that accepted TCP but never answered HTTP).
#
# Services are deliberately separate and must not be conflated:
#   1. joeos-backend  — the browser/API service (uvicorn, port 8080)
#   2. joeos-runner   — the privileged agent runner (systemd unit)
#   3. ollama         — local model runtime (systemd unit)
#   4. lemonade       — local inference server (systemd unit)
#   5. tailscale serve— the HTTPS endpoint (managed by tailscale)
#
# Usage:
#   joeosctl.sh status            — show backend + related service health
#   joeosctl.sh restart           — restart the backend via systemd (or the
#                                   fallback launch below if sudo is absent)
#   joeosctl.sh deploy            — pull origin/ai-rebuild + restart
#   joeosctl.sh health            — print /healthz and /healthz/ready
#   joeosctl.sh log [N]           — tail the backend log (N lines, default 50)
#
# Requires: joewillis user on Halo, checkout at /home/joewillis/JOEOS.

set -uo pipefail

JOEOS_DIR="${JOEOS_DIR:-/home/joewillis/JOEOS}"
PORT="${JOEOS_PORT:-8080}"
LOG="${JOEOS_LOG:-/tmp/joeos-halo-backend.log}"
UNIT="joeos-backend.service"
BIN="$JOEOS_DIR/.venv/bin/python"
MODULE="joeos_backend:app"

command -v curl >/dev/null 2>&1 || { echo "curl is required"; exit 1; }

health() {
  echo "== /healthz =="
  curl -sf --max-time 6 "http://127.0.0.1:$PORT/healthz" || echo "  UNREACHABLE"
  echo
  echo "== /healthz/ready =="
  curl -s --max-time 6 -o /dev/null -w "  status=%{http_code}\n" "http://127.0.0.1:$PORT/healthz/ready" || echo "  UNREACHABLE"
}

start_fallback() {
  # Fallback launch used only when systemd (sudo) is unavailable. Prefers an
  # existing systemd unit; otherwise starts a supervised foreground process
  # that exits on failure so a wrapper/supervisor can restart it.
  if systemctl is-active --quiet "$UNIT" 2>/dev/null; then
    echo "systemd unit $UNIT is already active; not starting a duplicate."
    return 0
  fi
  if pgrep -f "uvicorn $MODULE" >/dev/null 2>&1; then
    echo "a backend process is already running on this host; not duplicating it."
    return 0
  fi
  echo "Starting backend via fallback launch (no sudo): $BIN -m uvicorn $MODULE --host 0.0.0.0 --port $PORT"
  cd "$JOEOS_DIR" || exit 1
  nohup "$BIN" -m uvicorn "$MODULE" --host 0.0.0.0 --port "$PORT" --no-access-log \
    >>"$LOG" 2>&1 &
  echo "Launched PID $!"
}

restart() {
  if systemctl is-active --quiet "$UNIT" 2>/dev/null; then
    echo "Restarting $UNIT via systemd..."
    if sudo -n systemctl restart "$UNIT" 2>/dev/null; then
      return 0
    fi
    echo "  (no passwordless sudo; falling back to user-space restart)"
  fi
  # Controlled user-space restart: stop only THIS module, never a broad pkill.
  for pid in $(pgrep -f "uvicorn $MODULE" 2>/dev/null); do
    echo "  stopping backend pid $pid"
    kill "$pid" 2>/dev/null || true
  done
  sleep 2
  start_fallback
}

deploy() {
  echo "== git pull (ff-only) =="
  (cd "$JOEOS_DIR" && git pull --ff-only origin ai-rebuild) || { echo "pull failed"; exit 1; }
  echo "HEAD now: $(cd "$JOEOS_DIR" && git rev-parse --short HEAD)"
  restart
  echo "== waiting for readiness =="
  for i in $(seq 1 30); do
    code=$(curl -s --max-time 4 -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/healthz/ready" 2>/dev/null)
    if [ "$code" = "200" ]; then echo "  ready after ${i}s"; health; return 0; fi
    sleep 1
  done
  echo "  backend did not become ready in time"; health; return 1
}

case "${1:-status}" in
  status)
    echo "== $UNIT =="
    systemctl is-active "$UNIT" 2>/dev/null || echo "  (not visible without sudo)"
    pgrep -af "uvicorn $MODULE" | head -3 || echo "  no backend process visible"
    health
    ;;
  health) health ;;
  restart) restart ;;
  deploy) deploy ;;
  log)
    lines="${2:-50}"
    tail -n "$lines" "$LOG" 2>/dev/null || echo "no log at $LOG"
    ;;
  *)
    echo "usage: $0 {status|health|restart|deploy|log [N]}"
    exit 2
    ;;
esac
