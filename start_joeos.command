#!/usr/bin/env bash
set -e

JOEOS_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$JOEOS_DIR/start_joeos.sh"
