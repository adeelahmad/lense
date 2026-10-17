#!/usr/bin/env bash
# Upgrade a Lens that holds data from the last release to this checkout, and check that nothing was lost
# (docs/database.md, Upgrades). The Release workflow runs it before it cuts a version (.github/workflows/upgrade.yml):
#
#   ci/upgrade/upgrade.sh [FROM]
#
# FROM (default: the newest vX.Y.Z tag) is checked out into a throwaway git worktree, and its backend starts as the
# production stack runs it: the API and a worker, on the SurrealDB server its docker-compose.prod.yml names, with an
# empty database. lens_upgrade.py seeds it through the API and keeps what the API shows. Then this checkout's backend
# starts on the same database and data folder, the API and a worker at once as after an upgrade (on this checkout's
# SurrealDB when that changed), and the check fails when:
#   - the API shows any of the seeded data differently, or the admin's password or the API key no longer signs in;
#   - any record in the database is gone;
#   - an upgrade step that was pending didn't start exactly once, failed, or ran without a backup taken first;
#   - running every step again changes any record.
# Needs git, uv, Docker, curl and python3. The logs, the seeded facts and the database before and after go to
# $LENS_UPGRADE_OUT (default ./.upgrade-check/FROM).
set -euo pipefail

ROOT=$(git rev-parse --show-toplevel)
HERE=$ROOT/ci/upgrade
FROM=${1:-$(git -C "$ROOT" tag --list 'v[0-9]*.[0-9]*.[0-9]*' --sort=-v:refname | head -n1)}
[ -n "$FROM" ] || { echo "no vX.Y.Z tag to upgrade from (fetch the tags, or name a version)" >&2; exit 2; }
git -C "$ROOT" rev-parse -q --verify "$FROM^{commit}" >/dev/null || { echo "unknown version: $FROM" >&2; exit 2; }
OUT=${LENS_UPGRADE_OUT:-$ROOT/.upgrade-check/$FROM}
WORK=$(mktemp -d "${TMPDIR:-/tmp}/lens-upgrade-XXXXXX")
OLD=$WORK/from
NAME=lens-upgrade-$$-$RANDOM # a self-hosted runner's Docker outlives the job: names of its own, removed at the end
mkdir -p "$OUT"
PIDS=()

cleanup() {
  local rc=$?
  for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null || true; done
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  docker volume rm "$NAME" >/dev/null 2>&1 || true
  git -C "$ROOT" worktree remove --force "$OLD" >/dev/null 2>&1 || true
  rm -rf "$WORK"
  [ $rc -eq 0 ] || echo "The upgrade check failed; the logs are in $OUT"
}
trap cleanup EXIT

say() { echo; echo "== $*"; }
free_port() { python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])'; }
surreal_image() { sed -n 's/^ *image: *\(surrealdb\/surrealdb:[^ ]*\).*/\1/p' "$1/docker-compose.prod.yml" | head -n1; }

surreal_start() { # IMAGE: the database server, on the volume that keeps the data between versions
  docker run -d --name "$NAME" -v "$NAME:/data" -p 127.0.0.1::8000 --user root "$1" \
    start --log info --user root --pass "$SURREAL_PASS" surrealkv:/data/lens.db >/dev/null
  local port
  port=$(docker port "$NAME" 8000/tcp | head -n1 | sed 's/.*://')
  SURREAL_HTTP=http://127.0.0.1:$port
  export SURREAL_URL=ws://127.0.0.1:$port
  for _ in $(seq 60); do curl -sf "$SURREAL_HTTP/health" >/dev/null && return 0; sleep 1; done
  docker logs "$NAME" | tail -n 30
  echo "SurrealDB ($1) didn't start" >&2
  return 1
}

sync_backend() { # TREE VENV: the backend's packages, as its image installs them
  UV_PROJECT_ENVIRONMENT=$2 uv sync --frozen --no-dev --project "$1/fastapi_backend" -q
}

lens_start() { # LABEL TREE VENV: the API and a worker at once, as the production stack starts them
  local label=$1 tree=$2 venv=$3
  (cd "$tree/fastapi_backend" && exec "$venv/bin/fastapi" run app/main.py --host 127.0.0.1 --port "$API_PORT") \
    >"$OUT/$label-api.log" 2>&1 &
  API_PID=$!
  (cd "$tree/fastapi_backend" && exec "$venv/bin/lens" worker) >"$OUT/$label-worker.log" 2>&1 &
  WORKER_PID=$!
  PIDS+=("$API_PID" "$WORKER_PID")
  for _ in $(seq 180); do
    if curl -sf -o /dev/null "http://127.0.0.1:$API_PORT/api/v1/auth/status" && grep -q 'Ctrl-C to stop' "$OUT/$label-worker.log"; then
      return 0
    fi
    if ! kill -0 "$API_PID" 2>/dev/null || ! kill -0 "$WORKER_PID" 2>/dev/null; then break; fi
    sleep 1
  done
  tail -n 40 "$OUT/$label-api.log" "$OUT/$label-worker.log"
  echo "Lens ($label) didn't start" >&2
  return 1
}

lens_stop() {
  kill -TERM "$API_PID" "$WORKER_PID" 2>/dev/null || true
  for _ in $(seq 30); do
    kill -0 "$API_PID" 2>/dev/null || kill -0 "$WORKER_PID" 2>/dev/null || return 0
    sleep 1
  done
  kill -KILL "$API_PID" "$WORKER_PID" 2>/dev/null || true
}

# the same secrets for both versions, as an install's .env keeps them
export ARCHIVE_CONFIG=$WORK/archive.yaml
export ACCESS_SECRET_KEY=upgrade-check-access-key-0123456789abcdef
export ARCHIVE_SECRET_KEY=upgrade-check-archive-key-0123456789abcdef
export SURREAL_USER=root SURREAL_PASS=upgrade-check SURREAL_NS=archive SURREAL_DB=main
export LENS_ADMIN_EMAIL=admin@upgrade.example LENS_ADMIN_PASSWORD=upgrade-check-password
export RUN_BACKGROUND=false PYTHONUNBUFFERED=1
API_PORT=$(free_port)
cat >"$ARCHIVE_CONFIG" <<EOF
data_dir: $WORK/data
server:
  allowed_hosts: [127.0.0.1, localhost]
workers:
  inline: 0 # the worker does the work, as in the production stack
components:
  auto: false # nothing to fetch: no model runs here
EOF

say "Lens $FROM, with an empty database"
git -C "$ROOT" worktree add --detach -q "$OLD" "$FROM"
SEEDER=$HERE/lens_upgrade.py
[ -f "$OLD/ci/upgrade/lens_upgrade.py" ] && SEEDER=$OLD/ci/upgrade/lens_upgrade.py # written for $FROM's API
echo "seeding with ${SEEDER#"$WORK"/}"
sync_backend "$OLD" "$WORK/venv-from"
surreal_start "$(surreal_image "$OLD")"
lens_start from "$OLD" "$WORK/venv-from"

say "Seeding it through its API"
python3 "$SEEDER" seed "http://127.0.0.1:$API_PORT" "$OUT/seeded.json"
lens_stop
python3 "$HERE/lens_upgrade.py" dump "$SURREAL_HTTP" "$OUT/before.json"

say "Upgrading to $(git -C "$ROOT" describe --tags --always HEAD)"
sync_backend "$ROOT" "$WORK/venv-to"
if [ "$(surreal_image "$ROOT")" != "$(surreal_image "$OLD")" ]; then
  echo "SurrealDB: $(surreal_image "$OLD") to $(surreal_image "$ROOT")"
  docker stop "$NAME" >/dev/null && docker rm "$NAME" >/dev/null
  surreal_start "$(surreal_image "$ROOT")"
fi
lens_start to "$ROOT" "$WORK/venv-to"
grep -h 'upgrad' "$OUT/to-api.log" "$OUT/to-worker.log" || true

# each check runs whatever the others found, so one run says everything that's wrong
FAILED=0
checked() { "$@" || FAILED=$((FAILED + 1)); }

say "The seeded data, through the upgraded API"
checked python3 "$HERE/lens_upgrade.py" check "http://127.0.0.1:$API_PORT" "$OUT/seeded.json"
lens_stop

say "The database"
python3 "$HERE/lens_upgrade.py" dump "$SURREAL_HTTP" "$OUT/after.json"
checked python3 "$HERE/lens_upgrade.py" kept "$OUT/before.json" "$OUT/after.json"
checked python3 "$HERE/lens_upgrade.py" ran-once "$OUT/before.json" "$OUT/after.json" "$WORK/data" \
  "$OUT/to-api.log" "$OUT/to-worker.log"

say "Running every upgrade step again"
rerun() ( # in the backend's environment
  cd "$ROOT/fastapi_backend" && "$WORK/venv-to/bin/python" "$HERE/lens_upgrade.py" rerun "$SURREAL_HTTP" "$OUT/before.json"
)
checked rerun

if [ "$FAILED" -gt 0 ]; then
  say "Upgrading from $FROM failed $FAILED check(s); see above"
  exit 1
fi
say "Upgraded from $FROM with nothing lost"
