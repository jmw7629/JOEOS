#!/usr/bin/env bash
# Preflight host validation for the JoeOS runner (read-only, no changes).
set -euo pipefail

echo "== JoeOS runner host validation =="
echo "distribution:  $(. /etc/os-release && echo ${ID:-unknown} ${VERSION_ID:-})"
echo "python3:       $(python3 --version 2>/dev/null || echo MISSING)"
echo "systemd:       $(systemctl --version 2>/dev/null | head -1 || echo MISSING)"
echo "tailscale:     $(tailscale version 2>/dev/null | head -1 || echo NOT FOUND)"
echo "git:           $(git --version 2>/dev/null || echo MISSING)"
echo "runner user:   $(id joeos-runner 2>/dev/null || echo NOT CREATED)"
echo "backend ping:  $(tailscale status 2>/dev/null | grep -c '100\.' || echo 0) tailscale peer(s)"

if command -v tailscale >/dev/null 2>&1; then
  echo "tailscale ip:  $(tailscale ip -4 2>/dev/null | head -1 || echo unknown)"
fi

echo
echo "Validation is informational. The runner must never expose a public listener."
