#!/usr/bin/env bash
# Run the pull-request CI jobs (.github/workflows/ci.yml, pre-commit.yml, pr-title.yml) on this machine, one command:
#
#   ci/local-ci.sh [REF | --pr N] [--title "feat: …"] [--jobs backend,server,frontend,pre-commit,title]
#
# REF (default HEAD) is checked out into a throwaway git worktree, so the working tree is never touched. --pr N tests
# what GitHub's pull_request event tests: pull request N's head merged into the latest origin/main.
# Needs uv, pnpm, Node 22 and Docker (for the SurrealDB server job); ffmpeg, tesseract, poppler and LibreOffice Writer
# as on the runner. Each job's log goes to $LOCAL_CI_OUT (default ./.local-ci/<sha>/); a summary table prints at the end
# and as summary.md, and the exit code is non-zero when any job failed.
set -uo pipefail

REPO=$(git rev-parse --show-toplevel)
REF=HEAD
TITLE=""
PR=""
JOBS="backend,server,frontend,pre-commit,title"
while [ $# -gt 0 ]; do
  case "$1" in
    --title) TITLE=$2; shift 2 ;;
    --jobs) JOBS=$2; shift 2 ;;
    --pr) PR=$2; shift 2 ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) REF=$1; shift ;;
  esac
done

if [ -n "$PR" ]; then
  git -C "$REPO" fetch -q origin main "+refs/pull/$PR/head:refs/local-ci/pr-$PR" || exit 2
  REF=refs/local-ci/pr-$PR
fi
SHA=$(git -C "$REPO" rev-parse "$REF^{commit}") || { echo "unknown ref: $REF" >&2; exit 2; }
OUT=${LOCAL_CI_OUT:-$REPO/.local-ci/${PR:+pr-$PR-}${SHA:0:12}}
WT=$(mktemp -d "${TMPDIR:-/tmp}/lens-ci-XXXXXX")
mkdir -p "$OUT"
if [ -n "$PR" ]; then
  git -C "$REPO" worktree add --detach -q "$WT" origin/main
  BASE=$(git -C "$WT" rev-parse HEAD)
  if ! git -C "$WT" -c user.name=local-ci -c user.email=local-ci@localhost merge -q --no-edit "$SHA" >"$OUT/merge.log" 2>&1; then
    echo "PR #$PR head ${SHA:0:12} doesn't merge cleanly into main ${BASE:0:12}"; cat "$OUT/merge.log"
    git -C "$REPO" worktree remove --force "$WT"; exit 3
  fi
  REF="PR #$PR head ${SHA:0:12} merged into main ${BASE:0:12}"
else
  git -C "$REPO" worktree add --detach -q "$WT" "$SHA"
fi
SURREAL_CONTAINER=lens-local-ci-$$
cleanup() {
  docker rm -f "$SURREAL_CONTAINER" >/dev/null 2>&1 || true
  git -C "$REPO" worktree remove --force "$WT" >/dev/null 2>&1 || rm -rf "$WT"
}
trap cleanup EXIT

# the same values the workflows set
export ACCESS_SECRET_KEY=ci-access-secret-key-0123456789abcdef
export UV_PYTHON=${UV_PYTHON:-3.12}
# containers often set NODE_ENV=production, which breaks jest ("React.act is not a function"); CI leaves it unset
unset NODE_ENV
export CI=true

declare -a RESULTS=()
step() { # step NAME CMD… : runs in a subshell, appends to the current job's log
  echo "::: $1" >>"$LOG"
  shift
  ( "$@" ) >>"$LOG" 2>&1
}
job() { # job ID NAME FN
  local id=$1 name=$2 fn=$3 start rc
  LOG=$OUT/$id.log
  : >"$LOG"
  start=$(date +%s)
  echo "▶ $name"
  "$fn"
  rc=$?
  local secs=$(($(date +%s) - start))
  local verdict=pass
  [ $rc -ne 0 ] && verdict="FAIL ($(grep '^::: ' "$LOG" | tail -n1 | cut -c5-))"
  echo "  $verdict in ${secs}s (log: $LOG)"
  RESULTS+=("| $name | $verdict | ${secs}s |")
  return 0
}

backend_sync() { cd "$WT/fastapi_backend" && uv sync --frozen -q; }

backend() {
  cd "$WT/fastapi_backend" || return 1
  step Install backend_sync || return 1
  step Lint sh -c 'uv run ruff check . && uv run ruff format --check .' || return 1
  step "Type check" uv run mypy || return 1
  step "OpenAPI schema is up to date" sh -c '
    OPENAPI_OUTPUT_FILE="$0/openapi.json" uv run python -m commands.generate_openapi_schema &&
    diff -u ../nextjs-frontend/openapi.json "$0/openapi.json"' "$OUT" || return 1
  step Tests uv run pytest -n auto -p no:cacheprovider --durations=20 --cov=app --cov-report=term || return 1
}

server() {
  cd "$WT/fastapi_backend" || return 1
  step "Start SurrealDB" sh -c '
    docker run -d --name "$0" --tmpfs /data:mode=1777 -p 127.0.0.1::8000 surrealdb/surrealdb:v3.2.4 \
      start --user root --pass root surrealkv:/data/lens.db' "$SURREAL_CONTAINER" || return 1
  local port
  port=$(docker port "$SURREAL_CONTAINER" 8000/tcp | head -n1 | sed 's/.*://')
  for _ in $(seq 1 30); do curl -sf "http://127.0.0.1:$port/health" >/dev/null && break; sleep 1; done
  step Install backend_sync || return 1
  LENS_TEST_SURREAL_URL=ws://127.0.0.1:$port step "Tests against the server" uv run pytest -n 4 --durations=20
  local rc=$?
  docker rm -f "$SURREAL_CONTAINER" >/dev/null 2>&1
  return $rc
}

frontend_install() { cd "$WT/nextjs-frontend" && pnpm install --frozen-lockfile --reporter=append-only; }

frontend() {
  cd "$WT/nextjs-frontend" || return 1
  step Install frontend_install || return 1
  step Lint pnpm lint || return 1
  step tsc pnpm tsc || return 1
  step Tests pnpm coverage --coverageReporters=text-summary || return 1
  API_BASE_URL=http://localhost:8000 AUTH_SECRET=ci-auth-secret step Build pnpm build || return 1
}

precommit() {
  cd "$WT" || return 1
  step "Install backend" backend_sync || return 1
  step "Install frontend" frontend_install || return 1
  OPENAPI_OUTPUT_FILE=../nextjs-frontend/openapi.json \
    step "Run pre-commit" fastapi_backend/.venv/bin/pre-commit run --all-files --show-diff-on-failure
}

title() {
  if [ -z "$TITLE" ]; then echo "::: no --title given, skipped" >>"$LOG"; return 0; fi
  step "Check the title" python3 "$WT/.github/scripts/release.py" check-title "$TITLE"
}

echo "Local CI for ${SHA:0:12} ($REF), logs in $OUT"
for j in ${JOBS//,/ }; do
  case "$j" in
    backend) job backend "FastAPI (embedded SurrealDB)" backend ;;
    server) job server "FastAPI (SurrealDB server)" server ;;
    frontend) job frontend "Next.js" frontend ;;
    pre-commit) job pre-commit "pre-commit" precommit ;;
    title) job title "PR title" title ;;
    *) echo "unknown job: $j" >&2 ;;
  esac
done

{
  echo "Local CI on $REF (\`${SHA:0:12}\`), $(date -u '+%Y-%m-%d %H:%M UTC')"
  echo
  echo "| Job | Result | Time |"
  echo "|---|---|---|"
  printf '%s\n' "${RESULTS[@]}"
} | tee "$OUT/summary.md"
! grep -q FAIL "$OUT/summary.md"
