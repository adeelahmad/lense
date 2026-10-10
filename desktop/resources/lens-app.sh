#!/bin/sh
# What Lens.app runs (desktop/README.md): installs Lens on a first launch, starts it, stops it, updates it.
#
#   lens-app.sh launch    install Lens if it isn't (in the web installer, shown in the app's window), else start it
#   lens-app.sh start     start Lens
#   lens-app.sh stop      stop Lens
#   lens-app.sh update    install the latest Lens over this one (as running the install line again)
#   lens-app.sh setup-msb put the microsandbox the app carries in ~/.microsandbox, if there's none
#
# It tells the app what to do on its output, one line each:
#   @status <text>   show this on the app's page
#   @show <url>      show this page in the app's window (the web installer)
#   @open <url>      Lens is running here: open it in the browser (passkeys need a browser, not an app's web view)
#   @error <text>    it stopped; show why
# Everything else goes to the log: ~/Library/Logs/Lens/app.log on a Mac.
#
# LENS_DIR (default ~/lens) and LENS_REPO (default adeelahmad/lense) as for install.sh. LENS_MSB_BUNDLE: the
# microsandbox release the app carries (microsandbox-darwin-aarch64.tar.gz), put in ~/.microsandbox when it isn't there.
set -u

DIR="${LENS_DIR:-$HOME/lens}"
REPO="${LENS_REPO:-adeelahmad/lense}"
os="$(uname -s)"
export PATH="$HOME/.local/bin:$HOME/.microsandbox/bin:$HOME/.orbstack/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
if [ "$os" = Darwin ]; then LOGS="$HOME/Library/Logs/Lens"; else LOGS="${XDG_STATE_HOME:-$HOME/.local/state}/lens"; fi
mkdir -p "$LOGS"
LOG="$LOGS/app.log"

say() { printf '%s\n' "$*"; }
log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >>"$LOG"; }
status() { say "@status $*"; log "$*"; }
fail() {
  say "@error $*"
  log "error: $*"
  exit 1
}
have() { command -v "$1" >/dev/null 2>&1; }
env_of() { [ -f "$DIR/.env" ] && sed -n "s/^$1=//p" "$DIR/.env" | head -1; }

installed() { [ -f "$DIR/.env" ]; }
port() { p="$(env_of LENS_PORT)"; echo "${p:-3000}"; }
backend() { b="$(env_of LENS_BACKEND)"; echo "${b:-docker}"; }
up() { # the page and the API behind it, as install.sh waits for them
  curl -fsS -o /dev/null --max-time 3 "http://127.0.0.1:$(port)/login" 2>/dev/null &&
    curl -fsS -o /dev/null --max-time 3 "http://127.0.0.1:$(port)/api/v1/auth/status" 2>/dev/null
}
lens_url() { # where it's reached (FRONTEND_URL), on this machine
  echo "http://localhost:$(port)/"
}

compose() { # as install.sh runs it
  if docker compose version >/dev/null 2>&1; then
    (cd "$DIR" && docker compose -f docker-compose.prod.yml "$@")
  else
    (cd "$DIR" && docker-compose -f docker-compose.prod.yml "$@")
  fi
}

msb_ok() { [ "$os/$(uname -m)" = Darwin/arm64 ]; }

# The microsandbox the app carries, put where install.microsandbox.dev puts it (~/.microsandbox/bin and lib), so
# install.sh finds it and downloads nothing
install_msb() {
  [ -x "$HOME/.microsandbox/bin/msb" ] && return 0
  have msb && return 0
  bundle="${LENS_MSB_BUNDLE:-}"
  if [ -z "$bundle" ] || [ ! -f "$bundle" ]; then return 0; fi
  status "Setting up microsandbox..."
  tmp="$(mktemp -d)"
  if ! tar -xzf "$bundle" -C "$tmp"; then
    rm -rf "$tmp"
    log "couldn't unpack $bundle; install.sh downloads microsandbox instead"
    return 0
  fi
  msb="$(find "$tmp" -type f -name msb | head -1)"
  krun="$(find "$tmp" -type f -name 'libkrunfw.*.dylib' | head -1)"
  if [ -z "$msb" ] || [ -z "$krun" ]; then
    rm -rf "$tmp"
    log "the microsandbox bundle has no msb or libkrunfw; install.sh downloads microsandbox instead"
    return 0
  fi
  home="$HOME/.microsandbox"
  mkdir -p "$home/bin" "$home/lib" "$HOME/.local/bin"
  install -m 755 "$msb" "$home/bin/msb"
  ln -sf msb "$home/bin/microsandbox"
  name="$(basename "$krun")"
  cp "$krun" "$home/lib/$name"
  ln -sf "$name" "$home/lib/libkrunfw.dylib"
  for c in msb microsandbox; do
    if [ ! -e "$HOME/.local/bin/$c" ] || [ -L "$HOME/.local/bin/$c" ]; then # leave a file someone put there
      ln -sf "$home/bin/$c" "$HOME/.local/bin/$c"
    fi
  done
  if [ "$os" = Darwin ]; then
    xattr -dr com.apple.quarantine "$home" 2>/dev/null || true
  fi
  rm -rf "$tmp"
  log "installed microsandbox from the app into $home"
}

get_install_sh() { # into $1
  curl -fsSL "https://raw.githubusercontent.com/$REPO/main/install.sh" -o "$1"
}

free_port() { # the first port from $1 nothing listens on
  p="$1"
  while [ "$p" -lt $(($1 + 100)) ]; do
    curl -s -o /dev/null --max-time 2 "http://127.0.0.1:$p/" 2>/dev/null
    [ $? -eq 7 ] && echo "$p" && return 0
    p=$((p + 1))
  done
  return 1
}

token() { LC_ALL=C tr -dc 'A-Za-z0-9_-' </dev/urandom | head -c 24; }

# A first launch: the web installer, in the app's window. When it's done, its Open Lens button goes to Lens, which
# the app opens in the browser.
first_install() {
  msb_ok && install_msb
  status "Getting Lens's installer..."
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  get_install_sh "$tmp/install.sh" || fail "Couldn't download Lens. Check the internet connection, and open Lens again."
  p="$(free_port 3990)" || fail "No free port between 3990 and 4090 for the installer."
  t="$(token)"
  backend_default=docker
  msb_ok && backend_default=microsandbox # the app carries microsandbox: no Docker needed
  log "running the web installer on port $p"
  LENS_WIZARD=1 LENS_NO_OPEN=1 LENS_INSTALLER_PORT="$p" LENS_INSTALLER_TOKEN="$t" \
    LENS_BACKEND_DEFAULT="$backend_default" LENS_REPO="$REPO" LENS_DIR="$DIR" \
    sh "$tmp/install.sh" </dev/null >>"$LOG" 2>&1 &
  pid=$!
  i=0
  until curl -s -o /dev/null --max-time 2 "http://127.0.0.1:$p/" 2>/dev/null; do
    kill -0 "$pid" 2>/dev/null || fail "Lens's installer stopped before it opened. See $LOG"
    i=$((i + 1))
    [ $i -gt 150 ] && fail "Lens's installer didn't open. See $LOG"
    sleep 1
  done
  say "@show http://localhost:$p/?t=$t"
  wait "$pid"
  code=$?
  if installed && up; then
    say "@open $(lens_url)"
  elif [ "$code" -ne 0 ]; then
    fail "The install stopped. See $LOG"
  fi
}

start() {
  installed || fail "Lens isn't installed yet. Open Lens to install it."
  if up; then
    log "already running"
    return 0
  fi
  status "Starting Lens..."
  case "$(backend)" in
    microsandbox)
      [ -x "$DIR/.lens-sandbox" ] || fail "$DIR/.lens-sandbox is missing. Update Lens from the menu to write it again."
      # replaces the sandbox: one started again (msb start) boots without running Lens
      "$DIR/.lens-sandbox" >>"$LOG" 2>&1 </dev/null || fail "microsandbox couldn't start Lens. See $LOG"
      ;;
    *)
      have docker || fail "Docker isn't running. Start OrbStack or Docker Desktop, then start Lens again."
      compose up -d >>"$LOG" 2>&1 || fail "Docker couldn't start Lens. See $LOG"
      ;;
  esac
  i=0
  until up; do
    i=$((i + 1))
    [ $i -gt 450 ] && fail "Lens didn't answer within 15 minutes. See $LOG"
    [ $((i % 15)) -eq 0 ] && status "Starting Lens (the first start builds it, which takes a while)..."
    sleep 2
  done
}

stop() {
  installed || return 0
  status "Stopping Lens..."
  case "$(backend)" in
    microsandbox) msb stop lens >>"$LOG" 2>&1 || true ;;
    *) compose stop >>"$LOG" 2>&1 || true ;;
  esac
  status "Lens is stopped."
}

update() {
  installed || fail "Lens isn't installed yet. Open Lens to install it."
  status "Updating Lens..."
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  get_install_sh "$tmp/install.sh" || fail "Couldn't download Lens. Check the internet connection, and try again."
  # the same as running the install line again: the secrets, the database and the archive are kept
  (cd "$DIR" && LENS_DIR="$DIR" LENS_REPO="$(env_of LENS_REPO)" LENS_TARGET="$(env_of LENS_TARGET)" \
    LENS_BACKEND="$(env_of LENS_BACKEND)" LENS_YES=1 LENS_WIZARD=0 LENS_NO_OPEN=1 sh "$tmp/install.sh" \
    </dev/null >>"$LOG" 2>&1) || fail "The update stopped. See $LOG"
  status "Lens is up to date."
}

case "${1:-launch}" in
  launch)
    log "launch ($DIR)"
    if installed; then
      start
      say "@open $(lens_url)"
    else
      first_install
    fi
    ;;
  start)
    start
    say "@open $(lens_url)"
    ;;
  stop) stop ;;
  update)
    update
    say "@open $(lens_url)"
    ;;
  url) lens_url ;;
  setup-msb) install_msb ;;
  *)
    echo "usage: lens-app.sh launch|start|stop|update|url|setup-msb" >&2
    exit 2
    ;;
esac
