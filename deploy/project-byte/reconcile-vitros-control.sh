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
    if ! python3 - "$ROOT/package-lock.json" "$tmp/package-lock.remote.json" <<'PY'
import json
from pathlib import Path
import sys

local_path, remote_path = map(Path, sys.argv[1:3])
try:
    local = json.loads(local_path.read_text())
except Exception as exc:
    print(f"local package-lock.json is not valid JSON: {type(exc).__name__}", file=sys.stderr)
    raise SystemExit(2)
try:
    remote = json.loads(remote_path.read_text())
except Exception as exc:
    print(f"origin/main package-lock.json is not valid JSON: {type(exc).__name__}", file=sys.stderr)
    raise SystemExit(2)

if local == remote:
    print("Verified local package-lock.json is semantically identical to origin/main.")
    raise SystemExit(0)

paths = []
def add(path):
    if len(paths) < 12 and path not in paths:
        paths.append(path)

def walk(a, b, path='$'):
    if len(paths) >= 12:
        return
    if type(a) is not type(b):
        add(path + ' [type]')
        return
    if isinstance(a, dict):
        ak, bk = set(a), set(b)
        for key in sorted(ak - bk):
            add(f"{path}.{key} [local-only]")
        for key in sorted(bk - ak):
            add(f"{path}.{key} [remote-only]")
        for key in sorted(ak & bk):
            walk(a[key], b[key], f"{path}.{key}")
            if len(paths) >= 12:
                return
    elif isinstance(a, list):
        if len(a) != len(b):
            add(path + ' [length]')
        for idx, (left, right) in enumerate(zip(a, b)):
            walk(left, right, f"{path}[{idx}]")
            if len(paths) >= 12:
                return
    elif a != b:
        add(path)

walk(local, remote)
print("package-lock semantic mismatch paths: " + (", ".join(paths) if paths else "<unknown>"), file=sys.stderr)
raise SystemExit(1)
PY
    then
      fail "local package-lock.json differs semantically from origin/main; backup retained at $backup"
    fi
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

# Verification must not create __pycache__ inside the bridge control checkout.
# This was a previous source of self-inflicted dirty-control failures.
verify_tmp="$(mktemp -d)"
trap 'rm -rf "$verify_tmp"' EXIT
PYTHONPYCACHEPREFIX="$verify_tmp/pycache" python3 -m py_compile "$ROOT/bridge/runner.py"
PYTHONDONTWRITEBYTECODE=1 python3 "$ROOT/bridge/runner.py" --self-test
rm -rf "$verify_tmp"
trap - EXIT

[ -z "$(git -C "$ROOT" status --porcelain=v1 --untracked-files=all)" ] || \
  fail "bridge verification dirtied the control checkout; service was not restarted"

if [ "$SKIP_SERVICE" != "1" ]; then
  systemctl --user restart "$SERVICE_NAME"
  systemctl --user is-active --quiet "$SERVICE_NAME" || fail "$SERVICE_NAME is not active after restart"
  echo "VITROS bridge refreshed and active: $SERVICE_NAME"
else
  echo "Service restart skipped by VITROS_SKIP_SERVICE=1."
fi

echo "VITROS_CONTROL_RECONCILE=PASS commit=$(git -C "$ROOT" rev-parse --short=12 HEAD)"
