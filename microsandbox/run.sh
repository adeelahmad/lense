#!/bin/sh
# Lens in one microsandbox microVM (docs/microsandbox.md): SurrealDB, the API, a job worker and the web app, with no
# Docker on the machine. install.sh starts it with LENS_BACKEND=microsandbox:
#
#   msb run -d --name lens ... -v ~/lens:/lens:ro --mount-named lens-data:/data python:3.12-slim-bookworm \
#     -- sh /lens/microsandbox/run.sh
#
# microsandbox runs images but can't build them, and sandboxes can't reach each other by name, so the stack the Docker
# images hold is set up here, inside one sandbox started from the stock Python image: the system packages once, then
# the backend and the web app from the code in /lens (read-only) each time it changes. Everything the stack keeps (the
# database, the archive, the built code) is in /data, the named volume that outlives the sandbox. Each process is
# restarted if it stops, as `restart: unless-stopped` does in Compose.
set -eu

CODE=/lens
DATA=/data
OPT=$DATA/.lens       # tools and builds, kept with the data so a replaced sandbox doesn't download them again
SURREAL_VERSION=v3.2.4
RCLONE_VERSION=v1.75.1
NODE_MAJOR=22

log() { printf '[lens] %s\n' "$*"; }

# The settings install.sh wrote (secrets, port, target), the same .env Compose reads
set -a
# shellcheck disable=SC1091
. "$CODE/.env"
set +a
TARGET="${LENS_TARGET:-full}"

case "$(uname -m)" in
  x86_64) arch=amd64 node_arch=x64 ;;
  aarch64 | arm64) arch=arm64 node_arch=arm64 ;;
  *) log "Unsupported CPU: $(uname -m)" && exit 1 ;;
esac

mkdir -p "$OPT/bin" "$DATA/surreal" /tmp/lens
export PATH="$OPT/bin:$OPT/node/bin:$OPT/venv/bin:$PATH"

# --- System packages: in the sandbox's own disk, installed on each start (install.sh replaces the sandbox), from
# the packages kept in the volume, so only the first start downloads them ------------------------------------------
packages="ffmpeg tesseract-ocr antiword poppler-utils ca-certificates curl xz-utils unzip"
[ "$TARGET" = full ] && packages="$packages libreoffice-writer-nogui libreoffice-calc-nogui libreoffice-impress-nogui
  chromium fonts-dejavu-core fonts-liberation2 fonts-noto-core fonts-noto-cjk"
stamp="/var/lib/lens-packages.$TARGET"
if [ ! -f "$stamp" ]; then
  log "Installing system packages ($TARGET)..."
  export DEBIAN_FRONTEND=noninteractive
  rm -f /etc/apt/apt.conf.d/docker-clean # the image's setting that deletes downloaded packages
  mkdir -p "$OPT/apt/partial"
  apt-get update -q
  # shellcheck disable=SC2086
  apt-get install -y -q --no-install-recommends -o Dir::Cache::archives="$OPT/apt" $packages
  rm -rf /var/lib/apt/lists/*
  id lens >/dev/null 2>&1 || useradd --create-home --uid 1000 lens
  touch "$stamp"
fi

# --- Tools: SurrealDB, rclone, Node, uv, pinned like the Docker images -------------------------------------------
fetch() { curl -fsSL --retry 5 --retry-delay 3 "$@"; }
if [ ! -x "$OPT/bin/surreal" ] || ! "$OPT/bin/surreal" version 2>/dev/null | grep -q "${SURREAL_VERSION#v}"; then
  log "Getting SurrealDB $SURREAL_VERSION..."
  fetch "https://download.surrealdb.com/$SURREAL_VERSION/surreal-$SURREAL_VERSION.linux-$arch.tgz" | tar -xz -C "$OPT/bin"
fi
if [ ! -x "$OPT/bin/rclone" ]; then
  log "Getting rclone $RCLONE_VERSION..."
  fetch -o /tmp/lens/rclone.zip "https://downloads.rclone.org/$RCLONE_VERSION/rclone-$RCLONE_VERSION-linux-$arch.zip"
  unzip -q -o -j /tmp/lens/rclone.zip '*/rclone' -d "$OPT/bin" && rm /tmp/lens/rclone.zip
fi
if [ ! -x "$OPT/node/bin/node" ]; then
  log "Getting Node $NODE_MAJOR..."
  base="https://nodejs.org/dist/latest-v$NODE_MAJOR.x"
  file="$(fetch "$base/SHASUMS256.txt" | awk "/linux-$node_arch.tar.xz\$/ {print \$2}")"
  rm -rf "$OPT/node" && mkdir -p "$OPT/node"
  fetch "$base/$file" | tar -xJ -C "$OPT/node" --strip-components 1
fi
if [ ! -x "$OPT/bin/uv" ]; then
  log "Getting uv..."
  python3 -m pip install -q --no-cache-dir --target /tmp/lens/uv "uv>=0.8,<0.9"
  cp /tmp/lens/uv/bin/uv "$OPT/bin/uv" && rm -rf /tmp/lens/uv
fi

# --- Build: the backend and the web app, again only when the code changed ----------------------------------------
build_id="$(cat "$CODE/.lens-build" 2>/dev/null || echo unknown)-$TARGET"
if [ "$(cat "$OPT/build-id" 2>/dev/null)" != "$build_id" ]; then
  log "Building Lens (the first build takes a few minutes)..."
  # copies, so the build writes nothing into the code on the host; the backend is installed where it runs (the
  # virtualenv points at it)
  rm -rf "$OPT/backend" "$OPT/web-src" && mkdir -p "$OPT/backend" "$OPT/web-src"
  (cd "$CODE/fastapi_backend" && tar -c --exclude .venv --exclude __pycache__ .) | tar -x -C "$OPT/backend"
  (cd "$CODE/nextjs-frontend" && tar -c --exclude node_modules --exclude .next .) | tar -x -C "$OPT/web-src"

  # shellcheck disable=SC2046
  (cd "$OPT/backend" &&
    UV_PROJECT_ENVIRONMENT="$OPT/venv" UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy \
      UV_HTTP_TIMEOUT=300 UV_HTTP_RETRIES=5 UV_CACHE_DIR="$OPT/uv-cache" \
      uv sync --frozen --no-dev --python "$(command -v python3)" \
      $(for e in ${EXTRAS:-}; do printf -- '--extra %s ' "$e"; done))

  (cd "$OPT/web-src" &&
    export COREPACK_ENABLE_DOWNLOAD_PROMPT=0 COREPACK_HOME="$OPT/corepack" &&
    corepack pnpm install --frozen-lockfile &&
    AUTH_SECRET=build-only NEXT_TELEMETRY_DISABLED=1 NEXT_OUTPUT=standalone corepack pnpm build)
  rm -rf "$OPT/web" && mkdir -p "$OPT/web/.next"
  cp -a "$OPT/web-src/.next/standalone/." "$OPT/web/"
  cp -a "$OPT/web-src/.next/static" "$OPT/web/.next/static"
  cp -a "$OPT/web-src/public" "$OPT/web-src/lens-server.js" "$OPT/web/"
  rm -rf "$OPT/web-src"
  chown -R lens:lens "$OPT/web"
  echo "$build_id" >"$OPT/build-id"
fi
# the archive is the volume itself (data_dir: /data, as in the containers); the database is a folder in it
chown lens:lens "$DATA"
chown -R lens:lens "$DATA/surreal"

# --- Run ---------------------------------------------------------------------------------------------------------
# The same settings docker-compose.prod.yml gives the containers, with every service on this sandbox's localhost
export SURREAL_URL=ws://127.0.0.1:8001 SURREAL_USER="${SURREAL_USER:-root}"
export RUN_BACKGROUND=false
export FRONTEND_URL="${FRONTEND_URL:-http://localhost:3000}"
export CORS_ORIGINS="[\"$FRONTEND_URL\"]"
export ARCHIVE_ALLOWED_HOSTS="${ARCHIVE_ALLOWED_HOSTS:-localhost,127.0.0.1}"
export LENS_TUNNEL_ORIGIN=http://127.0.0.1:3000
export LENS_TRUSTED_PROXY_HOSTS=localhost
export LENS_WEB_PROXY_HOSTS=localhost
export API_BASE_URL=http://127.0.0.1:8000
export AUTH_TRUST_HOST=true NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
export HOME=/home/lens XDG_CACHE_HOME="$DATA/.cache"
mkdir -p "$DATA/.cache" && chown lens:lens "$DATA/.cache"
# the archive in the volume, as /data in the containers; the config file the backend image ships
export ARCHIVE_CONFIG="$OPT/backend/docker/archive.yaml"

# model servers on the host machine (Ollama, LM Studio) under the name the Docker setup uses for them too
if ! grep -q host.docker.internal /etc/hosts; then
  host_ip="$(getent hosts host.microsandbox.internal | awk '{print $1; exit}')"
  [ -n "$host_ip" ] && echo "$host_ip host.docker.internal" >>/etc/hosts
fi

pids="" db=""
stop() {
  log "Stopping..."
  # the web app, the API and the worker first, then the database they write to
  # shellcheck disable=SC2086
  for pid in $pids; do [ "$pid" = "$db" ] || kill "$pid" 2>/dev/null || true; done
  # shellcheck disable=SC2086
  for pid in $pids; do [ "$pid" = "$db" ] || wait "$pid" 2>/dev/null || true; done
  kill "$db" 2>/dev/null || true
  wait
  exit 0
}
trap stop TERM INT

# keep NAME DIR COMMAND...: run it as the lens user, again 5 seconds after it stops
keep() {
  name=$1 dir=$2
  shift 2
  (
    child=
    trap 'kill $child 2>/dev/null; wait $child; exit 0' TERM
    while :; do
      (cd "$dir" && exec setpriv --reuid lens --regid lens --init-groups -- "$@") &
      child=$!
      wait $child || true
      log "$name stopped; starting it again in 5 seconds"
      sleep 5
    done
  ) &
  pids="$pids $!"
}

surreal_ready() { surreal is-ready --endpoint http://127.0.0.1:8001 >/dev/null 2>&1; }

keep surrealdb "$DATA" surreal start --log info --bind 127.0.0.1:8001 --user "$SURREAL_USER" --pass "$SURREAL_PASS" \
  "surrealkv:$DATA/surreal/lens.db"
db=$!
until surreal_ready; do sleep 1; done
keep api "$OPT/backend" fastapi run app/main.py --host 127.0.0.1 --port 8000 --proxy-headers
keep worker "$OPT/backend" lens worker
keep web "$OPT/web" env HOSTNAME=0.0.0.0 PORT=3000 node lens-server.js
log "Lens is starting on port 3000"
wait
