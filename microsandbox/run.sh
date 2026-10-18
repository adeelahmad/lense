#!/bin/sh
# Lens in one microsandbox microVM (docs/microsandbox.md): SurrealDB, the API, a job worker and the web app, with no
# Docker on the machine. install.sh starts it with LENS_BACKEND=microsandbox:
#
#   msb run -d --name lens ... -v ~/lens:/lens:ro --mount-named lens-data:/data python:3.12-slim-trixie \
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

# Each step is logged as "[lens] ...", which install.sh shows while it waits. A step that fails says so, and stops
# what was started: install.sh stops waiting and shows why.
step=""
log() {
  step="$*"
  printf '[lens] %s\n' "$*"
}
failed() {
  code=$?
  [ "$code" = 0 ] && return
  printf '[lens] Failed (exit %s): %s\n' "$code" "${step%...}"
  # shellcheck disable=SC2086
  for pid in ${pids:-}; do kill "$pid" 2>/dev/null || true; done
}
trap failed EXIT

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

# Removing folders in the volume, which a Mac shares into the sandbox in a way one rm -rf doesn't always get through
# shellcheck source=SCRIPTDIR/wipe.sh
. "$CODE/microsandbox/wipe.sh"

# Name lookups: wait for them before downloading, with public resolvers when this machine's don't answer here
# shellcheck source=SCRIPTDIR/dns.sh
. "$CODE/microsandbox/dns.sh"
dns_ready

# --- System packages: in the sandbox's own disk, installed on each start (install.sh replaces the sandbox), from
# the packages kept in the volume, so only the first start downloads them. LibreOffice and Chromium aren't among them:
# the full edition installs each the first time Lens needs it (Programs, below) ------------------------------------
# apt_install PACKAGE...: from the packages kept in the volume, downloading only what isn't there yet
apt_install() {
  export DEBIAN_FRONTEND=noninteractive
  rm -f /etc/apt/apt.conf.d/docker-clean # the image's setting that deletes downloaded packages
  mkdir -p "$OPT/apt/partial"
  retry apt-get update -q --error-on=any &&
    retry apt-get install -y -q --no-install-recommends -o Dir::Cache::archives="$OPT/apt" "$@" &&
    { apt-get autoclean -q -o Dir::Cache::archives="$OPT/apt" || true; } && # those of an earlier Debian, or version
    rm -rf /var/lib/apt/lists/*
}
stamp=/var/lib/lens-packages
if [ ! -f "$stamp" ]; then
  log "Installing system packages..."
  apt_install ffmpeg tesseract-ocr antiword poppler-utils ca-certificates libgomp1 curl xz-utils unzip
  id lens >/dev/null 2>&1 || useradd --create-home --uid 1000 lens
  touch "$stamp"
fi

# --- Tools: SurrealDB, rclone, Node, uv, pinned like the Docker images -------------------------------------------
# Archives are downloaded whole before they're unpacked: curl can't try again once it has written to a pipe, so a
# connection cut partway would hand tar half an archive
fetch() { curl -fsSL --retry 5 --retry-delay 3 --retry-all-errors "$@"; }
if [ ! -x "$OPT/bin/surreal" ] || ! "$OPT/bin/surreal" version 2>/dev/null | grep -q "${SURREAL_VERSION#v}"; then
  log "Getting SurrealDB $SURREAL_VERSION..."
  fetch -o /tmp/lens/surreal.tgz "https://download.surrealdb.com/$SURREAL_VERSION/surreal-$SURREAL_VERSION.linux-$arch.tgz"
  tar -xzf /tmp/lens/surreal.tgz -C "$OPT/bin"
  rm /tmp/lens/surreal.tgz
fi
if [ ! -x "$OPT/bin/rclone" ]; then
  log "Getting rclone $RCLONE_VERSION..."
  fetch -o /tmp/lens/rclone.zip "https://downloads.rclone.org/$RCLONE_VERSION/rclone-$RCLONE_VERSION-linux-$arch.zip"
  unzip -q -o -j /tmp/lens/rclone.zip '*/rclone' -d "$OPT/bin" && rm /tmp/lens/rclone.zip
fi
if [ ! -x "$OPT/node/bin/node" ]; then
  log "Getting Node $NODE_MAJOR..."
  base="https://nodejs.org/dist/latest-v$NODE_MAJOR.x"
  sums="$(fetch "$base/SHASUMS256.txt")"
  file="$(printf '%s\n' "$sums" | awk "/linux-$node_arch.tar.xz\$/ {print \$2}")"
  fetch -o "/tmp/lens/$file" "$base/$file"
  (cd /tmp/lens && printf '%s\n' "$sums" | grep " $file\$" | sha256sum -c --quiet -)
  wipe "$OPT/node"
  mkdir -p "$OPT/node"
  tar -xJf "/tmp/lens/$file" -C "$OPT/node" --strip-components 1
  rm "/tmp/lens/$file"
fi
if [ ! -x "$OPT/bin/uv" ] || [ ! -x "$OPT/bin/uvx" ]; then # uvx: the MCP runner's, for servers from PyPI
  log "Getting uv..."
  python3 -m pip install -q --no-cache-dir --target /tmp/lens/uv "uv>=0.8,<0.9"
  cp /tmp/lens/uv/bin/uv /tmp/lens/uv/bin/uvx "$OPT/bin/" && rm -rf /tmp/lens/uv
fi

# --- Build: the backend and the web app, again only when the code changed (the edition doesn't change them) -------
build_id="$(cat "$CODE/.lens-build" 2>/dev/null || echo unknown)"
# the web app is built on the sandbox's own disk, where its packages come and go quickly; only what it built is kept
WEB_SRC=/var/tmp/lens/web-src
if [ "$(cat "$OPT/build-id" 2>/dev/null)" != "$build_id" ]; then
  log "Building Lens (the first build takes a few minutes)..."
  # what an earlier build left: one that stopped partway kept its copy of the web app in the volume (before it was
  # built on the sandbox's disk), and anything wipe moved aside
  wipe "$OPT/web-src" || true
  empty_trash
  # copies, so the build writes nothing into the code on the host; the backend is installed where it runs (the
  # virtualenv points at it)
  wipe "$OPT/backend" "$WEB_SRC"
  mkdir -p "$OPT/backend" "$WEB_SRC"
  (cd "$CODE/fastapi_backend" && tar -c --exclude .venv --exclude __pycache__ .) | tar -x -C "$OPT/backend"
  (cd "$CODE/nextjs-frontend" && tar -c --exclude node_modules --exclude .next .) | tar -x -C "$WEB_SRC"

  log "Installing the backend's Python packages..."
  # shellcheck disable=SC2046
  (cd "$OPT/backend" &&
    export UV_PROJECT_ENVIRONMENT="$OPT/venv" UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy \
      UV_HTTP_TIMEOUT=300 UV_HTTP_RETRIES=5 UV_CACHE_DIR="$OPT/uv-cache" &&
    retry uv sync --frozen --no-dev --python "$(command -v python3)" \
      $(for e in ${EXTRAS:-}; do printf -- '--extra %s ' "$e"; done))

  web() { (cd "$WEB_SRC" && exec env COREPACK_ENABLE_DOWNLOAD_PROMPT=0 COREPACK_HOME="$OPT/corepack" "$@"); }
  log "Installing the web app's packages..."
  retry web corepack pnpm install --frozen-lockfile
  log "Building the web app..."
  web AUTH_SECRET=build-only NEXT_TELEMETRY_DISABLED=1 NEXT_OUTPUT=standalone corepack pnpm build
  wipe "$OPT/web"
  mkdir -p "$OPT/web/.next"
  cp -a "$WEB_SRC/.next/standalone/." "$OPT/web/"
  cp -a "$WEB_SRC/.next/static" "$OPT/web/.next/static"
  cp -a "$WEB_SRC/public" "$WEB_SRC/lens-server.js" "$WEB_SRC/lens-relay.js" "$OPT/web/"
  wipe "$WEB_SRC" || true # the disk is replaced with the sandbox anyway
  chown -R lens:lens "$OPT/web"
  echo "$build_id" >"$OPT/build-id"
fi
# the archive is the volume itself (data_dir: /data, as in the containers); the database is a folder in it, and the
# volume is lens's alone: the MCP runner's user (mcp.sh) has no way in
chown lens:lens "$DATA"
chmod 750 "$DATA" || true
chown -R lens:lens "$DATA/surreal"

# public resolvers stood in for the downloads: this machine's again if they answer now, so names on its network resolve
dns_restore

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
# HTTPS (docs/https.md): install.sh published 443 and 80 to the sandbox's 8443 and 8080, where Caddy listens
[ "${LENS_HTTPS:-0}" = 1 ] && export LENS_HTTPS_LISTEN=1 LENS_HTTPS_PORT="${LENS_HTTPS_PORT:-443}"
export API_BASE_URL=http://127.0.0.1:8000
export AUTH_TRUST_HOST=true NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
export HOME=/home/lens XDG_CACHE_HOME="$DATA/.cache"
mkdir -p "$DATA/.cache" && chown lens:lens "$DATA/.cache"
# upgrading from the web app (docs/updates.md): the control folder install.sh mounts, and the install folder
[ -d /control ] && export LENS_CONTROL_DIR=/control LENS_HOST_DIR="${LENS_DIR:-}"
# the archive in the volume, as /data in the containers; the config file the backend image ships
export ARCHIVE_CONFIG="$OPT/backend/docker/archive.yaml"
# the MCP runner (mcp.sh), where Lens reaches it and reads its token while it runs
export LENS_MCP_RUNNER_URL=http://127.0.0.1:8090 LENS_MCP_TOKEN_FILE=/var/lib/lens-mcp-key/token

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
# surreal_signin: how SurrealDB answers .env's SURREAL_USER and SURREAL_PASS (200: they open it, 401: they don't)
surreal_signin() {
  python3 -c 'import json, os; print(json.dumps({"user": os.environ["SURREAL_USER"], "pass": os.environ["SURREAL_PASS"]}))' |
    curl -s -o /dev/null -w '%{http_code}' --max-time 10 -X POST -H 'Accept: application/json' --data-binary @- \
      http://127.0.0.1:8001/signin || true
}

# --- Programs: LibreOffice and Chromium, installed the first time Lens needs one (the full edition; a lean install
# never fetches them) --------------------------------------------------------------------------------------------
PROGRAMS="$OPT/programs"
# shellcheck source=SCRIPTDIR/programs.sh
. "$CODE/microsandbox/programs.sh"
if [ "$TARGET" = full ]; then
  programs_prepare
  (
    set +e # a failed install is answered, and the loop goes on
    programs_loop
  ) &
  pids="$pids $!"
  export LENS_PROGRAMS="$PROGRAMS"
fi

# --- The MCP runner: on while COMPOSE_PROFILES names mcp, as the web app's switch sets it (docs/assistant.md) ----
# shellcheck source=SCRIPTDIR/mcp.sh
. "$CODE/microsandbox/mcp.sh"
if mcp_prepare && mcp_tools; then
  (
    set +e # a runner that stops is started again, and the loop goes on
    mcp_loop
  ) &
  pids="$pids $!"
else
  log "The MCP runner can't run in this sandbox (its user or tools couldn't be set up); MCP servers that are programs won't"
fi

log "Starting the database..."
keep surrealdb "$DATA" surreal start --log info --bind 127.0.0.1:8001 --user "$SURREAL_USER" --pass "$SURREAL_PASS" \
  "surrealkv:$DATA/surreal/lens.db"
db=$!
until surreal_ready; do sleep 1; done
# The database keeps the user it was first started with, so the keys in .env must be the ones that made it. Those of
# another install (a reinstall after the install folder was deleted wrote new ones, and kept this volume) never open
# it: rather than starting the API again and again, say so and stop. install.sh then starts Lens on a volume of its own.
signin=""
for _ in 1 2 3 4 5; do
  signin="$(surreal_signin)"
  case "$signin" in 200 | 401) break ;; esac
  sleep 1
done
if [ "$signin" = 401 ]; then
  log "Another install's database: the keys in .env don't open the one in this volume"
  trap - EXIT
  # shellcheck disable=SC2086
  for pid in $pids; do kill "$pid" 2>/dev/null || true; done
  wait # the database closes cleanly
  exit 3
fi
keep api "$OPT/backend" fastapi run app/main.py --host 127.0.0.1 --port 8000 --proxy-headers
keep worker "$OPT/backend" lens worker
keep web "$OPT/web" env HOSTNAME=0.0.0.0 PORT=3000 node lens-server.js
log "Lens is starting on port 3000"
wait
