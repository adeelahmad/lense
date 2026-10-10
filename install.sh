#!/bin/sh
# Lens in one line:
#
#   curl -fsSL https://raw.githubusercontent.com/adeelahmad/lense/main/install.sh | sh
#
# Gets the code (or updates it), writes the secrets once, builds and starts the stack with Docker (or in a microsandbox
# microVM, if chosen), waits until the web app answers, then opens it on the setup page with the setup code already
# filled in. Run it again to update: the secrets, the database and the archive are kept.
#
# Settings, all optional (put them before `sh`: `... | LENS_DIR=/srv/lens sh`):
#   LENS_DIR       where Lens goes (default: ~/lens)
#   LENS_REF       the branch or tag to run (default: main)
#   LENS_PORT      the web app's port (default: 3000, or the next free one)
#   LENS_PUBLIC    1: reachable from other machines on the network, 0: this machine only (default: 1 on a server
#                  without a desktop, else 0)
#   LENS_TARGET    full (reads Office files, web pages and emails too) or lean (smaller) (default: full)
#   LENS_BACKEND   docker or microsandbox (one microVM, no Docker: docs/microsandbox.md). Asked on a first install
#                  when microsandbox can run here (Linux with KVM, or a Mac with Apple silicon); default: docker
#   GITHUB_TOKEN   a GitHub token to use when downloading (not needed; avoids GitHub's rate limits)
#   LENS_NO_OPEN   1: don't open a browser
set -eu

# All in a function, so nothing runs until the whole script has arrived (curl | sh streams it).
main() {

REPO="${LENS_REPO:-adeelahmad/lense}"
DIR="${LENS_DIR:-$HOME/lens}"
REF="${LENS_REF:-main}"
TARGET="${LENS_TARGET:-full}"

say() { printf '\033[1m%s\033[0m\n' "$*"; }
fail() {
  printf '\033[31m%s\033[0m\n' "$*" >&2
  exit 1
}
have() { command -v "$1" >/dev/null 2>&1; }
secret() {
  if have openssl; then openssl rand -hex 32; else od -An -N32 -tx1 /dev/urandom | tr -d ' \n'; fi
}
code() { LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 12; }

os="$(uname -s)"

sudo_ok() { [ "$(id -u)" = 0 ] && echo "" || echo sudo; }

# --- What runs it: Docker, or a microsandbox microVM ----------------------------------------------------------------
# microsandbox needs hardware virtualization: KVM on 64-bit Linux, Apple silicon on a Mac
msb_ok() {
  case "$os/$(uname -m)" in
    Linux/x86_64 | Linux/aarch64) [ -e /dev/kvm ] ;;
    Darwin/arm64) true ;;
    *) false ;;
  esac
}
BACKEND="${LENS_BACKEND:-}"
if [ -z "$BACKEND" ] && [ -f "$DIR/.env" ]; then # an existing install keeps what it runs on (its data is there)
  BACKEND="$(grep '^LENS_BACKEND=' "$DIR/.env" | cut -d= -f2- || true)"
  [ -n "$BACKEND" ] || BACKEND=docker
fi
if [ -z "$BACKEND" ]; then
  BACKEND=docker
  # asked only when there's someone to ask: piped into sh, the answer comes from the terminal, not stdin
  if msb_ok && (: </dev/tty) 2>/dev/null; then
    printf '\n\033[1mHow should Lens run?\033[0m\n'
    echo "  1) Docker (default): a container for each part, the usual way"
    echo "  2) microsandbox: one lightweight virtual machine, no Docker needed"
    printf 'Choose [1]: '
    read -r answer </dev/tty || answer=
    case "$answer" in 2 | m*) BACKEND=microsandbox ;; esac
  fi
fi
case "$BACKEND" in
  docker) ;;
  microsandbox) msb_ok || fail "microsandbox needs KVM on 64-bit Linux or a Mac with Apple silicon. Use LENS_BACKEND=docker." ;;
  *) fail "LENS_BACKEND is docker or microsandbox, not $BACKEND." ;;
esac

if [ "$BACKEND" = microsandbox ]; then
# --- microsandbox -----------------------------------------------------------------------------------------------
export PATH="$HOME/.local/bin:$HOME/.microsandbox/bin:$PATH"
if ! have msb; then
  say "Installing microsandbox (install.microsandbox.dev)..."
  have curl || fail "curl is needed to install microsandbox."
  curl -fsSL https://install.microsandbox.dev | sh
  have msb || fail "microsandbox didn't install. See https://docs.microsandbox.dev, then run this again."
fi
if [ "$os" = Linux ] && ! { [ -r /dev/kvm ] && [ -w /dev/kvm ]; }; then
  say "Letting $(id -un) use KVM (the kvm group)..."
  $(sudo_ok) usermod -aG kvm "$(id -un)" 2>/dev/null || true
  # the group counts from the next sign-in; for this run, access for this user alone
  have setfacl && $(sudo_ok) setfacl -m "u:$(id -un):rw" /dev/kvm 2>/dev/null || true
  [ -r /dev/kvm ] && [ -w /dev/kvm ] || fail "Can't use /dev/kvm. Sign out and in again (or run: msb doctor), then run this again."
fi
else
# --- Docker -------------------------------------------------------------------------------------------------------
if ! have docker; then
  case "$os" in
    Linux)
      say "Installing Docker (get.docker.com)..."
      have curl || fail "curl is needed to install Docker."
      curl -fsSL https://get.docker.com | $(sudo_ok) sh
      $(sudo_ok) systemctl enable --now docker 2>/dev/null || true
      ;;
    Darwin)
      if have brew; then
        say "Installing OrbStack, which runs Docker on a Mac (brew)..."
        brew install --cask orbstack
        open -a OrbStack
      else
        fail "Lens runs in Docker. Install OrbStack (https://orbstack.dev) or Docker Desktop, then run this again."
      fi
      ;;
    *) fail "Lens runs in Docker. Install Docker for your system, then run this again." ;;
  esac
fi
DOCKER=docker
if ! docker info >/dev/null 2>&1; then
  if [ "$os" = Linux ] && $(sudo_ok) docker info >/dev/null 2>&1; then
    DOCKER="$(sudo_ok) docker" # not in the docker group yet: works for this run
  else
    say "Waiting for Docker to start..."
    i=0
    until docker info >/dev/null 2>&1; do
      i=$((i + 1))
      [ $i -gt 60 ] && fail "Docker isn't running. Start it, then run this again."
      sleep 2
    done
  fi
fi
if $DOCKER compose version >/dev/null 2>&1; then
  COMPOSE="$DOCKER compose"
elif have docker-compose; then
  COMPOSE="docker-compose"
else
  fail "Docker Compose is missing. Install the Compose plugin (docker-compose-plugin), then run this again."
fi
fi

# --- The code -----------------------------------------------------------------------------------------------------
auth_url="https://github.com/$REPO.git"
[ -n "${GITHUB_TOKEN:-}" ] && auth_url="https://x-access-token:$GITHUB_TOKEN@github.com/$REPO.git"
if [ -d "$DIR/.git" ]; then
  say "Updating Lens in $DIR..."
  git -C "$DIR" fetch -q --depth 1 "$auth_url" "$REF" && git -C "$DIR" checkout -q -B "$REF" FETCH_HEAD
elif have git; then
  say "Getting Lens into $DIR..."
  git clone -q --depth 1 --branch "$REF" "$auth_url" "$DIR" ||
    fail "Couldn't get the code from GitHub ($REF). Check the network and LENS_REF, and try again."
  git -C "$DIR" remote set-url origin "https://github.com/$REPO.git" # the token isn't kept on disk
else
  say "Getting Lens into $DIR..."
  mkdir -p "$DIR"
  set -- -fsSL
  [ -n "${GITHUB_TOKEN:-}" ] && set -- "$@" -H "Authorization: Bearer $GITHUB_TOKEN"
  curl "$@" "https://api.github.com/repos/$REPO/tarball/$REF" | tar -xz -C "$DIR" --strip-components 1 ||
    fail "Couldn't get the code from GitHub ($REF). Check the network and LENS_REF, and try again."
fi
cd "$DIR"

# --- The port: the one asked for, else the one this install already uses, else the first free one from 3000 -------
free() { # nothing listening: curl can't connect (exit 7)
  curl -s -o /dev/null --max-time 2 "http://127.0.0.1:$1/" 2>/dev/null
  [ $? -eq 7 ]
}
PORT="${LENS_PORT:-$( [ -f .env ] && grep '^LENS_PORT=' .env | cut -d= -f2- || true)}"
if [ -z "$PORT" ]; then
  PORT=3000
  until free "$PORT"; do
    PORT=$((PORT + 1))
    [ "$PORT" -gt 3100 ] && fail "No free port between 3000 and 3100. Set LENS_PORT to one that is."
  done
  [ "$PORT" = 3000 ] || say "Port 3000 is taken, so Lens uses $PORT."
fi

# --- Where it's reached -------------------------------------------------------------------------------------------
if [ -z "${LENS_PUBLIC:-}" ] && [ -f .env ] && grep -q '^LENS_BIND=' .env; then # as the last run left it
  LENS_PUBLIC=0
  grep -q '^LENS_BIND=0.0.0.0' .env && LENS_PUBLIC=1
fi
if [ -z "${LENS_PUBLIC:-}" ]; then
  LENS_PUBLIC=0
  if [ "$os" = Linux ] && [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then LENS_PUBLIC=1; fi # a server: used from elsewhere
fi
host=localhost
bind=127.0.0.1
if [ "$LENS_PUBLIC" = 1 ]; then
  bind=0.0.0.0
  ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  [ -z "$ip" ] && ip="$(ipconfig getifaddr en0 2>/dev/null || true)"
  [ -n "$ip" ] && host="$ip"
fi

# --- Secrets, once: changing them later would sign everyone out and make stored credentials unreadable ------------
setenv() { # key value: replace the line, or add it
  if grep -q "^$1=" .env; then
    sed "s|^$1=.*|$1=$2|" .env >.env.tmp && mv .env.tmp .env
  else
    printf '%s=%s\n' "$1" "$2" >>.env
  fi
}
if [ ! -f .env ]; then
  umask 077
  printf '# Written by install.sh. Keep it with the volumes.\n' >.env
  setenv ACCESS_SECRET_KEY "$(secret)"
  setenv ARCHIVE_SECRET_KEY "$(secret)"
  setenv AUTH_SECRET "$(secret)"
  setenv SURREAL_PASS "$(secret)"
fi
grep -q '^LENS_SETUP_CODE=' .env || setenv LENS_SETUP_CODE "$(code)" # only used until the first admin exists
setenv LENS_BACKEND "$BACKEND"
setenv LENS_TARGET "$TARGET"
setenv LENS_BIND "$bind"
setenv LENS_PORT "$PORT"
setenv FRONTEND_URL "http://$host:$PORT"
setup_code="$(grep '^LENS_SETUP_CODE=' .env | cut -d= -f2-)"

# --- Build and start ----------------------------------------------------------------------------------------------
if [ "$BACKEND" = microsandbox ]; then
  # the code it was built from: the sandbox builds again when this changes
  { git rev-parse HEAD 2>/dev/null || date +%s; } >.lens-build
  # Memory: three quarters of this machine's, 2 to 8 GB (building the web app needs about 2)
  if [ "$os" = Darwin ]; then mem=$(($(sysctl -n hw.memsize) / 1048576)); else mem=$(($(awk '/MemTotal/ {print $2}' /proc/meminfo) / 1024)); fi
  mem=$((mem * 3 / 4))
  [ "$mem" -lt 2048 ] && mem=2048
  [ "$mem" -gt 8192 ] && mem=8192
  cpus="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)"
  sensors="${LENS_SENSOR_BIND:-$bind}"
  # The internet, this network (mail servers, model servers) and this machine (Ollama, LM Studio); only the web app
  # and the sensor ports are published, as with Docker.
  set -- run -d -q --name lens --replace -c "$cpus" -m "${mem}M" --root-disk 16G --net public,private,host \
    -p "$bind:$PORT:3000" \
    -p "$sensors:${LENS_MQTT_PORT:-1883}:1883" \
    -p "$sensors:${LENS_SYSLOG_PORT:-5514}:5514/udp" -p "$sensors:${LENS_SYSLOG_PORT:-5514}:5514/tcp" \
    -v "$PWD:/lens:ro" --mount-named lens-data:/data \
    --entrypoint /bin/sh python:3.12-slim-bookworm -- /lens/microsandbox/run.sh
  say "Starting Lens in a microsandbox (the first start installs and builds it, which takes a while)..."
  # The command that starts it, kept for a restart of the machine: a sandbox started again (msb start) boots without
  # running Lens, so it is always replaced; its packages come from the cache in the volume, its build stays there.
  {
    echo "#!/bin/sh"
    echo "# Starts Lens in its microsandbox (written by install.sh)"
    printf 'exec %s' "'$(command -v msb)'"
    for a in "$@"; do printf " '%s'" "$a"; done
    echo
  } >.lens-sandbox
  chmod +x .lens-sandbox
  ./.lens-sandbox >/dev/null
  LOGS="msb logs -f lens"
  STOP="msb stop lens"
  WAIT=900 # 30 minutes: the first start installs the system packages and builds everything
  # back after a restart of the machine, as Docker's restart policy does
  if [ "$os" = Linux ] && have systemctl && [ -d /run/systemd/system ]; then
    unit=/etc/systemd/system/lens-microsandbox.service
    printf '%s\n' "[Unit]" "Description=Lens (microsandbox)" "After=network-online.target" "Wants=network-online.target" "" \
      "[Service]" "Type=oneshot" "RemainAfterExit=yes" "User=$(id -un)" "ExecStart=$PWD/.lens-sandbox" \
      "ExecStop=$(command -v msb) stop lens" "" "[Install]" "WantedBy=multi-user.target" |
      $(sudo_ok) tee "$unit" >/dev/null && $(sudo_ok) systemctl enable lens-microsandbox.service >/dev/null 2>&1 || true
  elif [ "$os" = Darwin ]; then
    plist="$HOME/Library/LaunchAgents/dev.lens.microsandbox.plist"
    mkdir -p "$(dirname "$plist")"
    cat >"$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>dev.lens.microsandbox</string>
  <key>ProgramArguments</key><array><string>$PWD/.lens-sandbox</string></array>
  <key>RunAtLoad</key><true/>
</dict></plist>
PLIST
  fi
else
  say "Building and starting Lens (the first build takes a few minutes)..."
  $COMPOSE -f docker-compose.prod.yml up -d --build
  LOGS="$COMPOSE -f docker-compose.prod.yml logs"
  STOP="$COMPOSE -f docker-compose.prod.yml down"
  WAIT=300
fi

say "Waiting for the web app..."
i=0
# the page and the API behind it: the setup page needs both, and the API starts a few seconds after the page
until curl -fsS -o /dev/null "http://127.0.0.1:$PORT/login" 2>/dev/null &&
  curl -fsS -o /dev/null "http://127.0.0.1:$PORT/api/v1/auth/status" 2>/dev/null; do
  i=$((i + 1))
  [ $i -gt $WAIT ] && fail "Lens didn't answer within $((WAIT * 2 / 60)) minutes. See what happened: cd $DIR && $LOGS"
  sleep 2
done

url="http://$host:$PORT/setup?code=$setup_code"
say "Lens is running: $url"
echo "  (once the admin account exists, the link opens the sign-in page)"
echo "  Stop: cd $DIR && $STOP    Update: run the same line again"
if [ "${LENS_NO_OPEN:-0}" != 1 ]; then
  if [ "$os" = Darwin ]; then
    open "$url" 2>/dev/null || true
  elif [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && have xdg-open; then
    xdg-open "$url" >/dev/null 2>&1 || true
  fi
fi
}

main "$@"
