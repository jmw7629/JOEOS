#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${VITROS_BRIDGE_ENV:-$HOME/.config/joeos-opencode-bridge/vitros.env}"
SERVICE_NAME="${VITROS_BRIDGE_SERVICE:-vitros-opencode-bridge.service}"
EXPECTED_REPO="${VITROS_EXPECTED_REPO-jmw7629/vitros-web-dashboard}"
SKIP_SERVICE="${VITROS_SKIP_SERVICE:-0}"

fail() {
  echo "VITROS reconciliation refused: $*" >&2
  exit 1
}

if [ -n "${VITROS_ROOT:-}" ]; then
  ROOT="$VITROS_ROOT"
else
  [ -f "$ENV_FILE" ] || fail "bridge env file not found"
  ROOT="$(grep '^BRIDGE_ROOT=' "$ENV_FILE" | tail -n 1 | cut -d= -f2-)"
fi

[ -n "$ROOT" ] || fail "BRIDGE_ROOT is empty"
ROOT="$(realpath "$ROOT")"
[ -d "$ROOT/.git" ] || fail "BRIDGE_ROOT is not a Git checkout"

branch="$(git -C "$ROOT" symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
[ "$branch" = "main" ] || fail "control checkout must be on main; nothing was modified"

origin="$(git -C "$ROOT" remote get-url origin 2>/dev/null || true)"
[ -n "$origin" ] || fail "origin remote is missing"
if [ -n "$EXPECTED_REPO" ]; then
  normalized="$(printf '%s' "$origin" | tr '[:upper:]' '[:lower:]' | sed -E 's#^git@github.com:#https://github.com/#; s#\.git$##; s#/$##')"
  case "$normalized" in
    *"github.com/${EXPECTED_REPO,,}") ;;
    *) fail "unexpected origin; nothing was modified" ;;
  esac
fi

# A control checkout is evidence, not scratch space. Never normalize, restore,
# stash, reset, clean, or otherwise overwrite local changes automatically.
status="$(git -C "$ROOT" status --porcelain=v1 --untracked-files=all)"
if [ -n "$status" ]; then
  count="$(printf '%s\n' "$status" | sed '/^$/d' | wc -l | tr -d ' ')"
  fail "dirty control checkout detected (${count} path(s)); no fetch, merge, restore, reset, clean, stash, or restart was performed"
fi

# Fetch updates remote metadata only after the working tree is proven clean.
echo "Fetching VITROS main without modifying the working tree..."
git -C "$ROOT" fetch --prune origin main

git -C "$ROOT" merge-base --is-ancestor HEAD origin/main || \
  fail "local main is not an ancestor of origin/main; refusing non-fast-forward reconciliation"

local_head="$(git -C "$ROOT" rev-parse HEAD)"
remote_head="$(git -C "$ROOT" rev-parse origin/main)"
service_active=0
if [ "$SKIP_SERVICE" != "1" ] && systemctl --user is-active --quiet "$SERVICE_NAME"; then
  service_active=1
fi

if [ "$local_head" != "$remote_head" ]; then
  if [ "$service_active" -eq 1 ]; then
    fail "bridge service is active while control checkout is behind origin/main; HEAD/worktree were left unchanged and restart was deferred"
  fi
  echo "Fast-forwarding clean, inactive VITROS control checkout..."
  git -C "$ROOT" merge --ff-only origin/main
  local_head="$(git -C "$ROOT" rev-parse HEAD)"
  [ "$local_head" = "$remote_head" ] || fail "HEAD does not exactly match origin/main after fast-forward"
else
  echo "VITROS control checkout already matches origin/main."
fi

[ -z "$(git -C "$ROOT" status --porcelain=v1 --untracked-files=all)" ] || \
  fail "checkout became dirty during verification; service was not restarted"

# Verify without creating __pycache__ or other files inside the control checkout.
verify_tmp="$(mktemp -d)"
trap 'rm -rf "$verify_tmp"' EXIT
PYTHONPYCACHEPREFIX="$verify_tmp/pycache" python3 -m py_compile "$ROOT/bridge/runner.py"
PYTHONDONTWRITEBYTECODE=1 python3 "$ROOT/bridge/runner.py" --self-test
rm -rf "$verify_tmp"
trap - EXIT

[ -z "$(git -C "$ROOT" status --porcelain=v1 --untracked-files=all)" ] || \
  fail "bridge verification dirtied the control checkout; service was not restarted"

if [ "$SKIP_SERVICE" = "1" ]; then
  echo "Service state/restart intentionally skipped."
elif [ "$service_active" -eq 1 ]; then
  # A current active executor is already healthy enough to preserve in place.
  echo "VITROS bridge already current and active; restart not required."
else
  systemctl --user restart "$SERVICE_NAME"
  systemctl --user is-active --quiet "$SERVICE_NAME" || fail "bridge service is not active after restart"
  echo "VITROS bridge refreshed and active."
fi

echo "VITROS_CONTROL_RECONCILE=PASS commit=$(git -C "$ROOT" rev-parse --short=12 HEAD)"
