#!/usr/bin/env bash
set -Eeuo pipefail

JOEOS_SECURE_COMMAND_DIR="$(cd "$(dirname "$0")" && pwd -P)"
exec "$JOEOS_SECURE_COMMAND_DIR/start_joeos_secure.sh"
