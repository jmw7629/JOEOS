#!/bin/bash
set -Eeuo pipefail
umask 077

JOEOS_SETUP_DIR="$(cd "$(dirname "$0")" && pwd -P)"

echo "JoeOS uses the private local VPS deployment."
echo "Starting the audited local launcher; no cloud credentials are requested."
exec "$JOEOS_SETUP_DIR/start_joeos.command"
