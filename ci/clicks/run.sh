#!/bin/sh
# Runs the clicks survey against a fresh Lens: a new database and data folder, the stand-in model, the web installer.
# From the repository root, after `make env`, `uv sync` in fastapi_backend and `pnpm build` in nextjs-frontend
# (make clicks does all three):
#   ci/clicks/run.sh [--compare ci/clicks/baseline.json] [--only id,id]
# Ports 3000 (web app), 8000 (API), 3990 (installer) and 11434 (stand-in model) must be free.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
tmp=$(mktemp -d)
pids=""
stop() {
  for p in $pids; do kill "$p" 2>/dev/null || true; done
  # wait for them to exit: a server still writing its data folder makes rm fail with "Directory not empty"
  for p in $pids; do wait "$p" 2>/dev/null || true; done
  rm -rf "$tmp" 2>/dev/null || { sleep 2; rm -rf "$tmp" || true; }
}
trap stop EXIT INT TERM

# The containers' configuration, pointed at the temporary folder, with the jobs run by the API itself
mkdir -p "$tmp/audio/podcasts" "$tmp/audio/interviews"
sed -e "s#/data#$tmp/data#; s#/audio#$tmp/audio#g; s#inline: 0#inline: 1#" \
  "$repo/fastapi_backend/docker/archive.yaml" >"$tmp/archive.yaml"

(cd "$repo/fastapi_backend" && ARCHIVE_CONFIG="$tmp/archive.yaml" LENS_SETUP_CODE=survey-code RUN_BACKGROUND=true \
  exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 >"$tmp/backend.log" 2>&1) &
pids="$pids $!"
(cd "$repo/nextjs-frontend" && exec node_modules/.bin/next start -p 3000 >"$tmp/frontend.log" 2>&1) &
pids="$pids $!"

for url in http://127.0.0.1:8000/api/v1/auth/status http://127.0.0.1:3000/login; do
  i=0
  until curl -fsS -o /dev/null "$url"; do
    i=$((i + 1))
    if [ $i -gt 120 ]; then
      echo "$url didn't answer; the logs:" >&2
      tail -50 "$tmp/backend.log" "$tmp/frontend.log" >&2
      exit 1
    fi
    sleep 1
  done
done

status=0
LENS_SURVEY_DIR="$tmp/install" node "$here/survey.mjs" "$@" || status=$?
if [ $status -ne 0 ]; then tail -40 "$tmp/backend.log" >&2; fi
exit $status
