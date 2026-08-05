#!/usr/bin/env bash
# JoeOS runner uninstaller. Preserves runner identity and workspace data unless
# explicitly removed.
set -euo pipefail

RUNNER_USER="joeos-runner"
JOEOS_SOURCE="/opt/joeos"

log() { echo "[joeos-runner] $*"; }

log "Stopping and disabling the service (if present)."
if systemctl list-unit-files joeos-runner.service >/dev/null 2>&1; then
  systemctl disable --now joeos-runner.service 2>/dev/null || true
  rm -f /etc/systemd/system/joeos-runner.service
  systemctl daemon-reload
fi

log "Preserving runner identity and workspace data under /var/lib/joeos-runner."
log "To remove the runner identity explicitly, delete /var/lib/joeos-runner/identity."
log "Uninstall complete. Workspace data was preserved."
