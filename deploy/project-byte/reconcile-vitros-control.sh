#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${VITROS_BRIDGE_ENV:-$HOME/.config/joeos-opencode-bridge/vitros.env}"
SERVICE_NAME="${VITROS_BRIDGE_SERVICE:-vitros-opencode-bridge.service}"
EXPECTED_REPO="${VITROS_EXPECTED_REPO-jmw7629/vitros-web-dashboard}"
SKIP_SERVICE="${VITROS_SKIP_SERVICE:-0}"
BACKUP_BASE="${VITROS_RECONCILE_BACKUP_DIR:-$HOME/.local/state/joeos-opencode-bridge/vitros-control-backups}"
STAMP="$(date +%Y%m%d-%H%M%S)"

fail() {
  echo "VITROS reconciliation refused: $*" >&2
  exit 1
}

if [ -n "${VITROS_ROOT:-}" ]; then
  ROOT="$VITROS_ROOT"
else
  [ -f "$ENV_FILE" ] || fail "bridge env file not found: $ENV_FILE"
  ROOT="$(grep '^BRIDGE_ROOT=' "$ENV_FILE" | tail -n 1 | cut -d= -f2-)"
fi

[ -n "$ROOT" ] || fail "BRIDGE_ROOT is empty"
ROOT="$(realpath "$ROOT")"
[ -d "$ROOT/.git" ] || fail "not a Git checkout: $ROOT"

branch="$(git -C "$ROOT" symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
[ "$branch" = "main" ] || fail "control checkout must be on main, found '${branch:-detached}'"

origin="$(git -C "$ROOT" remote get-url origin 2>/dev/null || true)"
[ -n "$origin" ] || fail "origin remote is missing"
if [ -n "$EXPECTED_REPO" ]; then
  normalized="$(printf '%s' "$origin" | tr '[:upper:]' '[:lower:]' | sed -E 's#^git@github.com:#https://github.com/#; s#\.git$##; s#/$##')"
  case "$normalized" in
    *"github.com/${EXPECTED_REPO,,}") ;;
    *) fail "unexpected origin '$origin'; expected $EXPECTED_REPO" ;;
  esac
fi

echo "Fetching VITROS main without modifying the working tree..."
git -C "$ROOT" fetch --prune origin main

git -C "$ROOT" merge-base --is-ancestor HEAD origin/main || \
  fail "local main is not an ancestor of origin/main; refusing non-fast-forward reconciliation"

status="$(git -C "$ROOT" status --porcelain=v1 --untracked-files=all)"
if [ -n "$status" ]; then
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    path="${line:3}"
    case "$path" in
      .gitignore|package-lock.json) ;;
      *) fail "unexpected local change '$path'; nothing was modified" ;;
    esac
  done <<< "$status"

  backup="$BACKUP_BASE/$STAMP"
  mkdir -p "$backup"
  chmod 700 "$backup"
  printf '%s\n' "$(git -C "$ROOT" rev-parse HEAD)" > "$backup/HEAD"
  printf '%s\n' "$status" > "$backup/status.txt"
  git -C "$ROOT" diff -- .gitignore package-lock.json > "$backup/local.diff"
  [ ! -f "$ROOT/.gitignore" ] || cp "$ROOT/.gitignore" "$backup/gitignore.local"
  [ ! -f "$ROOT/package-lock.json" ] || cp "$ROOT/package-lock.json" "$backup/package-lock.local.json"
  chmod -R go-rwx "$backup"
  echo "Protected local backup: $backup"

  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  git -C "$ROOT" show origin/main:.gitignore > "$tmp/gitignore.remote"
  git -C "$ROOT" show origin/main:package-lock.json > "$tmp/package-lock.remote.json"

  if git -C "$ROOT" status --porcelain=v1 -- package-lock.json | grep -q .; then
    cmp -s "$ROOT/package-lock.json" "$tmp/package-lock.remote.json" || \
      fail "local package-lock.json is not byte-identical to origin/main; backup retained at $backup"
    echo "Verified local package-lock.json already equals origin/main."
  fi

  if git -C "$ROOT" status --porcelain=v1 -- .gitignore | grep -q .; then
    python3 - "$ROOT/.gitignore" "$tmp/gitignore.remote" <<'PY'
from pathlib import Path
import sys
local = Path(sys.argv[1]).read_text().replace('\r\n','\n').splitlines()
remote = Path(sys.argv[2]).read_text().replace('\r\n','\n').splitlines()
legacy = ['node_modules','dist','.env','.env.local','.vercel','.env*']
remote_plus_broad = remote + ['.env*']
if local not in (legacy, remote, remote_plus_broad):
    raise SystemExit('local .gitignore is not one of the reviewed safe reconciliation profiles')
required = {'node_modules','dist','.env','.env.local','.env.*.local','.vercel','tmp/'}
if not required.issubset(set(remote)):
    raise SystemExit(f'origin/main .gitignore is missing required narrow entries: {sorted(required-set(remote))}')
if '.env*' in remote:
    raise SystemExit('origin/main unexpectedly contains broad .env* ignore; refusing')
print('Verified reviewed .gitignore profile; broad .env* will not be preserved.')
PY
  fi

  # Only the two reviewed files can reach this point, both are backed up and
  # independently verified. Restore them to local HEAD so ff-only can proceed.
  git -C "$ROOT" restore --source=HEAD --worktree -- .gitignore package-lock.json
  [ -z "$(git -C "$ROOT" status --porcelain=v1 --untracked-files=all)" ] || \
    fail "working tree did not become clean after reviewed-file restore"
  rm -rf "$tmp"
  trap - EXIT
else
  echo "VITROS control checkout is already clean."
fi

echo "Fast-forwarding VITROS control checkout..."
git -C "$ROOT" merge --ff-only origin/main

[ -z "$(git -C "$ROOT" status --porcelain=v1 --untracked-files=all)" ] || \
  fail "checkout is not clean after fast-forward"
[ "$(git -C "$ROOT" rev-parse HEAD)" = "$(git -C "$ROOT" rev-parse origin/main)" ] || \
  fail "HEAD does not exactly match origin/main after fast-forward"

python3 -m py_compile "$ROOT/bridge/runner.py"
python3 "$ROOT/bridge/runner.py" --self-test

if [ "$SKIP_SERVICE" != "1" ]; then
  systemctl --user restart "$SERVICE_NAME"
  systemctl --user is-active --quiet "$SERVICE_NAME" || fail "$SERVICE_NAME is not active after restart"
  echo "VITROS bridge refreshed and active: $SERVICE_NAME"
else
  echo "Service restart skipped by VITROS_SKIP_SERVICE=1."
fi

echo "VITROS_CONTROL_RECONCILE=PASS commit=$(git -C "$ROOT" rev-parse --short=12 HEAD)"
