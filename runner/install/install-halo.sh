#!/usr/bin/env bash
# JoeOS private runner installer for the Halo computer (dry-run safe).
# Never generates a private key, never embeds secrets, never starts enrollment,
# never opens a public port, and never alters firewall rules.
set -euo pipefail

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then DRY_RUN=1; fi

RUNNER_USER="joeos-runner"
JOEOS_SOURCE="/opt/joeos"
RUNNER_HOME="/var/lib/joeos-runner"
CONFIG_DIR="/etc/joeos-runner"
VENV_DIR="${JOEOS_SOURCE}/runner/.venv"

log()  { echo "[joeos-runner] $*"; }
run()  {
  if [[ "$DRY_RUN" == "1" ]]; then log "DRY-RUN: $*"; else "$@"; fi
}

confirm() {
  if [[ "$DRY_RUN" == "1" ]]; then return 0; fi
  read -r -p "Proceed? [y/N] " answer
  [[ "$answer" == "y" || "$answer" == "Y" ]]
}

log "JoeOS runner installer (dry-run=$DRY_RUN)"
[[ -f /etc/os-release ]] && . /etc/os-release
log "distribution: ${ID:-unknown}"
log "python3: $(python3 --version 2>/dev/null || echo missing)"
command -v systemctl >/dev/null || { log "FATAL: systemd is required"; exit 1; }
command -v tailscale >/dev/null || log "WARN: Tailscale not found; private connectivity must be confirmed separately"

confirm || { log "Aborted."; exit 0; }

# Dedicated unprivileged user.
if ! id "$RUNNER_USER" >/dev/null 2>&1; then
  run useradd --system --create-home --shell /usr/sbin/nologin "$RUNNER_USER"
fi

# Directories and ownership.
for dir in "$CONFIG_DIR" "$RUNNER_HOME" "$RUNNER_HOME/work"; do
  run install -d -o "$RUNNER_USER" -g "$RUNNER_USER" -m 0700 "$dir"
done

# Repository checkout (safe fetch) and virtual environment.
if [[ ! -d "$JOEOS_SOURCE/.git" ]]; then
  run git clone https://github.com/jmw7629/JOEOS.git "$JOEOS_SOURCE"
  run git -C "$JOEOS_SOURCE" checkout ai-rebuild
fi
run install -d -o "$RUNNER_USER" -g "$RUNNER_USER" -m 0755 "$JOEOS_SOURCE/runner"
run python3 -m venv "$VENV_DIR"
run "$VENV_DIR/bin/pip" install --disable-pip-version-check -r "$JOEOS_SOURCE/requirements.txt"

# Hardened systemd unit (never passes credentials on the command line).
UNIT="/etc/systemd/system/joeos-runner.service"
if [[ -f "$JOEOS_SOURCE/runner/systemd/joeos-runner.service" ]]; then
  run install -o root -g root -m 0644 \
    "$JOEOS_SOURCE/runner/systemd/joeos-runner.service" "$UNIT"
  run systemctl daemon-reload
  run systemctl enable joeos-runner
fi

# Configuration template (no secrets, no private key).
if [[ ! -f "$CONFIG_DIR/config.json" ]]; then
  run cp "$JOEOS_SOURCE/runner/install/joeos-runner.yaml.example" "$CONFIG_DIR/config.json" 2>/dev/null || true
fi

log "Installation complete (dry-run=$DRY_RUN)."
log "Next: generate the runner identity with 'joeos-runner identity-init',"
log "obtain a one-time enrollment challenge from the backend CLI, and enroll."
log "Do not start the service until enrollment is complete."
