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
#   LENS_PUBLIC    1: reachable from other machines on the network, 0: this machine only (default: as the last run
#                  left it, else 1 on a server without a desktop, else 0). Also in Settings › Access & embedding
#   LENS_TARGET    full (reads Office files, web pages and emails too) or lean (smaller) (default: as the last run
#                  left it, else full). Also in Settings › Components
#   LENS_BACKEND   docker or microsandbox (one microVM, no Docker: docs/microsandbox.md). Asked on a first install
#                  when microsandbox can run here (Linux with KVM, or a Mac with Apple silicon); default: docker
#   LENS_DNS_FALLBACK  in a microsandbox: the public resolvers it looks names up with when this machine's don't
#                  answer there, comma-separated (default: 1.1.1.1,9.9.9.9; empty: none; docs/microsandbox.md).
#                  Kept for later runs
#   GITHUB_TOKEN   a GitHub token to use when downloading (not needed; avoids GitHub's rate limits)
#   LENS_NO_OPEN   1: don't open a browser
#   LENS_YES       1: install what Lens needs without asking (it only asks at a terminal)
#   LENS_HTTPS     1: publish the HTTPS and HTTP ports (443, 80; LENS_HTTPS_PORT / LENS_HTTP_PORT for others) so Lens
#                  can serve https:// with certificates it gets itself, set up in Settings › HTTPS (docs/https.md);
#                  0: don't (default: as the last run left it, else 0)
#   LENS_UPGRADE_AGENT  0: don't set up the agent that lets the web app install new releases (docs/updates.md)
#   LENS_WAIT      how long to wait for the web app to answer, in seconds (default: 1800 in a microsandbox, whose
#                  first start builds everything, else 600)
#   LENS_WIZARD    1: answer everything in the web installer, in the browser (the default for a first install at a
#                  terminal, where Python 3 is there); 0: here in the terminal
#   LENS_KEEP_CODE 1: build and start the code already in LENS_DIR, without getting it (the upgrade agent's rollback)
#   LENS_IMAGES    pull: download a release's prebuilt images (amd64 or arm64) instead of building them, and without a
#                  LENS_REF install the newest release; build: build them here (default: as the last run left it, else
#                  build). Docker only; Lens is built anyway for a branch or with EXTRAS (docs/deployment.md)
#   LENS_IMAGES_REGISTRY  where those images are (default: ghcr.io/<owner of LENS_REPO>), for a mirror. Kept for later
#                  runs
set -eu

# All in a function, so nothing runs until the whole script has arrived (curl | sh streams it).
main() {

REPO="${LENS_REPO:-adeelahmad/lense}"
DIR="${LENS_DIR:-$HOME/lens}"
REF="${LENS_REF:-main}"
TARGET="${LENS_TARGET:-}"

say() { printf '\033[1m%s\033[0m\n' "$*"; }
fail() {
  printf '\033[31m%s\033[0m\n' "$*" >&2
  exit 1
}
have() { command -v "$1" >/dev/null 2>&1; }
secret() {
  if have openssl; then openssl rand -hex 32; else od -An -N32 -tx1 /dev/urandom | tr -d ' \n'; fi
}
code() { LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom 2>/dev/null | head -c 12; }

os="$(uname -s)"

sudo_ok() { # the web installer hands sudo your password through SUDO_ASKPASS
  if [ "$(id -u)" = 0 ]; then echo ""; elif [ -n "${SUDO_ASKPASS:-}" ]; then echo "sudo -A"; else echo sudo; fi
}

# --- What runs it: Docker, or a microsandbox microVM ----------------------------------------------------------------
# microsandbox needs hardware virtualization: KVM on 64-bit Linux (or a CPU that has it, whose module isn't loaded
# yet), Apple silicon on a Mac
msb_ok() {
  case "$os/$(uname -m)" in
    Linux/x86_64) [ -e /dev/kvm ] || grep -Eqw 'vmx|svm' /proc/cpuinfo 2>/dev/null ;;
    Linux/aarch64) [ -e /dev/kvm ] ;;
    Darwin/arm64) true ;;
    *) false ;;
  esac
}
tty_ok() { [ "${LENS_YES:-0}" != 1 ] && (: </dev/tty) 2>/dev/null; }
# KVM for this run: this process can use /dev/kvm, or its user is in the group that owns it, which sg gives a process
# before the next sign-in does. (The access setfacl gives the user lasts only until udev resets it, which a VM starting
# or stopping does.)
in_kvm() { id -Gn ${1:+"$1"} 2>/dev/null | tr ' ' '\n' | grep -qx kvm; }
kvm_ok() {
  { [ -r /dev/kvm ] && [ -w /dev/kvm ]; } ||
    { have sg && [ "$(stat -c %G /dev/kvm 2>/dev/null)" = kvm ] && in_kvm "$(id -un)"; }
}
# Starts Lens's sandbox, with the kvm group when msb needs it (below)
run_sandbox() {
  if [ -n "$kvm_sg" ]; then sg kvm -c ./.lens-sandbox >/dev/null; else ./.lens-sandbox >/dev/null; fi
}
# A word for sh -c, in single quotes
quote() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }
# has_db VOLUME: whether a microsandbox volume may hold a Lens database. Only one it can see has none counts as without:
# empty, or with run.sh's tools (.lens) and no database (surreal/lens.db) next to them. One it can't look into (not
# where microsandbox keeps them by default) counts as with.
has_db() {
  vol="$HOME/.microsandbox/volumes/$1"
  [ -d "$vol" ] || return 0
  [ -n "$(ls -A "$vol" 2>/dev/null)" ] || return 1
  ! { [ -d "$vol/.lens" ] && [ ! -e "$vol/surreal/lens.db" ]; }
}

# --- The web installer: a first install at a terminal continues in the browser ----------------------------------------
# It asks everything there (what to install, how Lens runs, the AI model and its key, the extras), then runs this
# script again with the answers (LENS_WIZARD=0) and shows its progress. Python 3.8+ runs it; without it, the
# questions are asked here.
py_ok() {
  have python3 && python3 -c 'import sys; sys.exit(sys.version_info < (3, 8))' 2>/dev/null || return 1
  [ "$os" != Darwin ] || xcode-select -p >/dev/null 2>&1 # else python3 is a stub that pops up an install dialog
}
WIZARD="${LENS_WIZARD:-}"
if [ -z "$WIZARD" ]; then
  WIZARD=0
  [ ! -f "$DIR/.env" ] && [ -z "${LENS_BACKEND:-}" ] && tty_ok && WIZARD=1
fi
# Started from a launcher (Install Lens.app, the .desktop file) there is no terminal to fall back to: say why in a
# window instead.
gui_fail() {
  printf '%s\n' "$1" >&2
  if ! (: </dev/tty) 2>/dev/null; then
    if [ "$os" = Darwin ]; then
      osascript -e "display dialog \"$1\" buttons {\"OK\"} default button 1 with title \"Install Lens\" with icon caution" >/dev/null 2>&1
    elif have zenity; then
      zenity --error --title "Install Lens" --text "$1" 2>/dev/null
    elif have kdialog; then
      kdialog --title "Install Lens" --error "$1" 2>/dev/null
    elif have notify-send; then
      notify-send "Install Lens" "$1" 2>/dev/null
    fi
    exit 1
  fi
}
if [ "$WIZARD" = 1 ] && py_ok && have curl; then
  wiz="$(mktemp -d)"
  trap 'rm -rf "$wiz"' EXIT
  base="${LENS_INSTALLER_URL:-https://raw.githubusercontent.com/$REPO/$REF/installer}"
  if curl -fsSL "$base/lens_installer.py" -o "$wiz/lens_installer.py" && curl -fsSL "$base/page.html" -o "$wiz/page.html"; then
    say "Opening the installer in your browser..."
    LENS_INSTALLER_FETCH=1 python3 "$wiz/lens_installer.py" --dir "$DIR" --repo "$REPO" --ref "$REF"
    exit $?
  fi
  gui_fail "Couldn't download Lens's installer. Check the internet connection, and try again."
  echo "Couldn't download the web installer; asking here instead."
elif [ "$WIZARD" = 1 ]; then
  have curl || gui_fail "Lens needs curl to download itself. Install curl (on Ubuntu: sudo apt install curl), then try again."
  if [ "$os" = Darwin ] && ! xcode-select -p >/dev/null 2>&1; then
    xcode-select --install 2>/dev/null || true
    gui_fail "Lens's installer needs Apple's Command Line Tools. Install them in the window that opened, then open Install Lens again."
  fi
  gui_fail "Lens's installer needs Python 3.8 or later. Install Python 3 with your software center, then try again."
  echo "The web installer needs Python 3.8 or later; asking here instead."
fi
BACKEND="${LENS_BACKEND:-}"
if [ -z "$BACKEND" ] && [ -f "$DIR/.env" ]; then # an existing install keeps what it runs on (its data is there)
  BACKEND="$(grep '^LENS_BACKEND=' "$DIR/.env" | cut -d= -f2- || true)"
  [ -n "$BACKEND" ] || BACKEND=docker
fi
if [ -z "$BACKEND" ]; then
  BACKEND=docker
  # asked only when there's someone to ask: piped into sh, the answer comes from the terminal, not stdin
  if msb_ok && tty_ok; then
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
case "${LENS_WAIT:-0}" in *[!0-9]*) fail "LENS_WAIT is a number of seconds, not ${LENS_WAIT:-}." ;; esac

# --- Prebuilt images, or built here: the one asked for, else as the last run left it, else built --------------------
IMAGES="${LENS_IMAGES:-$( [ -f "$DIR/.env" ] && grep '^LENS_IMAGES=' "$DIR/.env" | cut -d= -f2- || true)}"
IMAGES="${IMAGES:-build}"
case "$IMAGES" in build | pull) ;; *) fail "LENS_IMAGES is pull or build, not $IMAGES." ;; esac

# --- What's missing: listed, and installed once you say yes ---------------------------------------------------------
# Asked only at a terminal; piped in by a script or CI (no terminal, or LENS_YES=1) it goes ahead, as before.
export PATH="$HOME/.local/bin:$HOME/.microsandbox/bin:$PATH"
plan=""
need() { plan="$plan
  - $*"; }
if [ "$BACKEND" = microsandbox ]; then
  have msb || need "microsandbox (from install.microsandbox.dev, into ~/.microsandbox)"
  if [ "$os" = Linux ]; then
    [ -e /dev/kvm ] || need "the KVM kernel module (modprobe, with sudo)"
    kvm_ok || need "access to KVM for $(id -un) (the kvm group, with sudo)"
  fi
else
  if ! have docker; then
    case "$os" in
      Linux) need "Docker Engine with Compose (from get.docker.com, with sudo)" ;;
      Darwin)
        have brew || need "Homebrew (from brew.sh, asks for your password)"
        need "OrbStack, which runs Docker on a Mac (with Homebrew)"
        ;;
    esac
  elif ! docker compose version >/dev/null 2>&1 &&
    ! have docker-compose && [ "$os" = Linux ]; then
    need "the Docker Compose plugin (with your package manager, with sudo)"
  fi
fi
if [ -n "$plan" ]; then
  printf '\n\033[1mLens needs these, and will install them:\033[0m%s\n' "$plan"
  if tty_ok; then
    printf 'Install them now? [Y/n] '
    read -r answer </dev/tty || answer=
    case "$answer" in
      n* | N*) fail "Nothing was installed. Install them yourself, or run this again and answer yes." ;;
    esac
  fi
fi

if [ "$BACKEND" = microsandbox ]; then
# --- microsandbox -----------------------------------------------------------------------------------------------
if ! have msb; then
  say "Installing microsandbox (install.microsandbox.dev)..."
  have curl || fail "curl is needed to install microsandbox."
  msb_installer="$(curl -fsSL https://install.microsandbox.dev)" || fail "Couldn't download the microsandbox installer."
  # Its installer asks GitHub's API for the latest release, which refuses busy shared addresses (CI, offices); the
  # release page's redirect names it without that limit, so hand the installer the version when we can find it.
  msb_repo="$(printf '%s\n' "$msb_installer" | sed -n 's/^GITHUB_REPO="\(.*\)"$/\1/p' | head -1)"
  msb_tag=""
  [ -n "$msb_repo" ] && msb_tag="$(curl -fsSLI -o /dev/null -w '%{url_effective}' "https://github.com/$msb_repo/releases/latest" 2>/dev/null | sed -n 's#.*/releases/tag/##p')"
  if [ -n "$msb_tag" ]; then
    printf '%s\n' "$msb_installer" | sed "s|^    get_latest_version\$|    VERSION=$msb_tag|" | sh
  else
    printf '%s\n' "$msb_installer" | sh
  fi
  have msb || fail "microsandbox didn't install. See https://docs.microsandbox.dev, then run this again."
fi
if [ "$os" = Linux ] && [ ! -e /dev/kvm ]; then
  say "Loading KVM..."
  $(sudo_ok) modprobe kvm_intel 2>/dev/null || $(sudo_ok) modprobe kvm_amd 2>/dev/null || $(sudo_ok) modprobe kvm 2>/dev/null || true
  [ -e /dev/kvm ] || fail "KVM didn't load. Turn on virtualization (VT-x or AMD-V) in the firmware settings, or use LENS_BACKEND=docker."
fi
if [ "$os" = Linux ] && ! kvm_ok; then
  say "Letting $(id -un) use KVM (the kvm group)..."
  $(sudo_ok) usermod -aG kvm "$(id -un)" 2>/dev/null || true
  # the group counts from the next sign-in; for this run, access for this user alone (setfacl is in the acl package,
  # which a minimal system may not have)
  if ! have setfacl; then
    if have apt-get; then $(sudo_ok) apt-get install -y -q acl >/dev/null 2>&1 || true
    elif have dnf; then $(sudo_ok) dnf install -y -q acl >/dev/null 2>&1 || true
    fi
  fi
  have setfacl && $(sudo_ok) setfacl -m "u:$(id -un):rw" /dev/kvm 2>/dev/null || true
  kvm_ok || fail "Can't use /dev/kvm. Sign out and in again (or run: msb doctor), then run this again."
fi
MSB="$(command -v msb)"
# Until the next sign-in gives this user's processes the kvm group, msb runs with it, through sg (which takes one
# command line, so each word is quoted into it): msb can't start or stop a sandbox without KVM, and the access setfacl
# gave lasts only until udev resets it, which a VM starting or stopping does.
kvm_sg=""
if [ "$os" = Linux ] && have sg && ! in_kvm && in_kvm "$(id -un)"; then
  kvm_sg=1
  msb() {
    line="exec $(quote "$MSB")"
    for a in "$@"; do line="$line $(quote "$a")"; done
    sg kvm -c "$line"
  }
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
      if ! have brew && { tty_ok || [ -n "${SUDO_ASKPASS:-}" ]; }; then
        say "Installing Homebrew (brew.sh)..."
        brew_sh="$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        if tty_ok; then
          /bin/bash -c "$brew_sh" </dev/tty
        else # from the web installer: no questions, and sudo asks the installer for the password
          NONINTERACTIVE=1 /bin/bash -c "$brew_sh" </dev/null
        fi
        for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do [ -x "$b" ] && eval "$("$b" shellenv)" && break; done
      fi
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
elif [ "$os" = Linux ] && {
  if have apt-get; then $(sudo_ok) apt-get install -y -q docker-compose-plugin
  elif have dnf; then $(sudo_ok) dnf install -y -q docker-compose-plugin
  else false; fi
} >/dev/null 2>&1 && $DOCKER compose version >/dev/null 2>&1; then
  COMPOSE="$DOCKER compose"
else
  fail "Docker Compose is missing. Install the Compose plugin (docker-compose-plugin), then run this again."
fi
fi

# --- The code -----------------------------------------------------------------------------------------------------
# Images are published for releases (docs/deployment.md#prebuilt-images): pulling them with no LENS_REF, a new install
# or one already on a release gets the newest release. One that follows a branch keeps it (a release may be older than
# what it runs), and builds.
latest_release() {
  set -- -fsSL --max-time 20
  [ -n "${GITHUB_TOKEN:-}" ] && set -- "$@" -H "Authorization: Bearer $GITHUB_TOKEN"
  curl "$@" "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null |
    sed -n 's/^ *"tag_name": *"\(v[0-9]*\.[0-9]*\.[0-9]*\)".*/\1/p' | head -n 1
}
if [ "$IMAGES" = pull ] && [ "$BACKEND" = docker ] && [ -z "${LENS_REF:-}" ] && [ "${LENS_KEEP_CODE:-0}" != 1 ]; then
  on="$(git -C "$DIR" symbolic-ref -q --short HEAD 2>/dev/null || true)"
  if [ ! -f "$DIR/.env" ] || printf '%s' "$on" | grep -Eq '^v[0-9]+\.[0-9]+\.[0-9]+$'; then
    latest="$(latest_release)"
    if [ -n "$latest" ]; then REF="$latest"; else say "Couldn't find Lens's newest release; getting $REF instead."; fi
  fi
fi
auth_url="https://github.com/$REPO.git"
[ -n "${GITHUB_TOKEN:-}" ] && auth_url="https://x-access-token:$GITHUB_TOKEN@github.com/$REPO.git"
if [ "${LENS_KEEP_CODE:-0}" = 1 ]; then
  [ -f "$DIR/install.sh" ] || fail "LENS_KEEP_CODE=1, but there is no Lens in $DIR."
elif [ -d "$DIR/.git" ]; then
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

# --- The edition: the one asked for, else the one this install already has (a lean install stays lean), else full ---
[ -n "$TARGET" ] || TARGET="$( [ -f .env ] && grep '^LENS_TARGET=' .env | cut -d= -f2- || true)"
TARGET="${TARGET:-full}"
case "$TARGET" in full | lean) ;; *) fail "LENS_TARGET is full or lean, not $TARGET." ;; esac

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

# --- HTTPS (docs/https.md): the ports Caddy answers on, run by Lens while Settings › HTTPS has it on ---------------
if [ -z "${LENS_HTTPS:-}" ] && [ -f .env ]; then # as the last run left it
  LENS_HTTPS="$(grep '^LENS_HTTPS=' .env | cut -d= -f2- || true)"
fi
[ "${LENS_HTTPS:-0}" = 1 ] || LENS_HTTPS=0
HTTPS_PORT="${LENS_HTTPS_PORT:-$( [ -f .env ] && grep '^LENS_HTTPS_PORT=' .env | cut -d= -f2- || true)}"
HTTP_PORT="${LENS_HTTP_PORT:-$( [ -f .env ] && grep '^LENS_HTTP_PORT=' .env | cut -d= -f2- || true)}"
HTTPS_PORT="${HTTPS_PORT:-443}"
HTTP_PORT="${HTTP_PORT:-80}"

# --- Secrets, once: changing them later would sign everyone out and make stored credentials unreadable ------------
setenv() { # key value: replace the line, or add it (only this user can read the copy that replaces it)
  if grep -q "^$1=" .env; then
    (umask 077 && sed "s|^$1=.*|$1=$2|" .env >.env.tmp) && mv .env.tmp .env
  else
    printf '%s=%s\n' "$1" "$2" >>.env
  fi
}
unsetenv() { # key: drop the line, if there is one
  if grep -q "^$1=" .env; then
    (umask 077 && grep -v "^$1=" .env >.env.tmp) && mv .env.tmp .env
  fi
}
fresh=0 # a first install: its keys are new
if [ ! -f .env ]; then
  fresh=1
  umask 077
  printf '# Written by install.sh. Keep it with the volumes.\n' >.env
  setenv ACCESS_SECRET_KEY "$(secret)"
  setenv ARCHIVE_SECRET_KEY "$(secret)"
  setenv AUTH_SECRET "$(secret)"
  setenv SURREAL_PASS "$(secret)"
fi
chmod 600 .env # the secrets are this user's alone, also where an earlier update left them readable to others
grep -q '^LENS_SETUP_CODE=' .env || setenv LENS_SETUP_CODE "$(code)" # only used until the first admin exists
setenv LENS_BACKEND "$BACKEND"
setenv LENS_TARGET "$TARGET"
setenv LENS_BIND "$bind"
setenv LENS_PORT "$PORT"
setenv FRONTEND_URL "http://$host:$PORT"
setenv LENS_REPO "$REPO"
setenv LENS_HTTPS "$LENS_HTTPS"
if [ "$LENS_HTTPS" = 1 ]; then
  setenv LENS_HTTPS_PORT "$HTTPS_PORT"
  setenv LENS_HTTP_PORT "$HTTP_PORT"
fi
setenv LENS_DIR "$DIR"
# kept for later runs only when asked for: an install that never chose follows the default
[ -z "${LENS_IMAGES:-}" ] || setenv LENS_IMAGES "$IMAGES"
# the SMB share's port on this machine (docs/smb.md), when the install line names one: kept for later runs
case "${LENS_SMB_PORT:-}" in '' | *[!0-9]*) ;; *) setenv LENS_SMB_PORT "$LENS_SMB_PORT" ;; esac
# the microsandbox's public resolvers (microsandbox/dns.sh), when the install line names them, empty included: kept
[ -z "${LENS_DNS_FALLBACK+set}" ] || setenv LENS_DNS_FALLBACK "$(printf '%s' "$LENS_DNS_FALLBACK" | tr -s ' ' ',')"
# from the web installer (docs/configuration.md): the first namespace, and settings Lens saves once at its first start
for k in LENS_NAMESPACE LENS_SETUP_SETTINGS; do
  eval "v=\${$k:-}"
  [ -z "$v" ] || setenv "$k" "$v"
done
setup_code="$(grep '^LENS_SETUP_CODE=' .env | cut -d= -f2-)"

# --- The control folder: the web app asks for upgrades here, and reads how they went (docs/updates.md) -----------
# The containers write as their own user (uid 1000), so the inbox is open to them; the agent only ever installs a
# release tag or this install's branch, whoever writes the request.
mkdir -p .lens-control/inbox
chmod 755 .lens-control
chmod 777 .lens-control/inbox

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
  # The stock Python image, from mirrors that need no sign-in first: Docker Hub can refuse it ("Not authorized") when
  # this machine has Docker Hub credentials that microsandbox can't use, and limits anonymous pulls.
  say "Getting the base image..."
  image=""
  for ref in mirror.gcr.io/library/python:3.12-slim-trixie public.ecr.aws/docker/library/python:3.12-slim-trixie \
    docker.io/library/python:3.12-slim-trixie; do
    if out="$(msb pull -q "$ref" 2>&1)"; then
      image="$ref"
      break
    fi
  done
  [ -n "$image" ] || fail "Couldn't download the base image (python:3.12-slim-trixie): $out"
  # The volume its data is kept in (the database, the archive, the builds). A database opens only with the keys of
  # the install that made it, which are in that install's .env. So a first install that finds the volume of an earlier
  # one whose folder is gone (deleted, or installed elsewhere) starts on a volume of its own, and leaves that one as it
  # is: its data is still there for that install's .env. A volume without a database yet is used as it is. An install
  # that already runs on such a volume moves to one of its own the same way, once run.sh finds that the keys in .env
  # don't open its database (other_volume, below).
  # own_volume [NAME]: the first of lens-data, lens-data-2, ... that holds no database (and isn't NAME)
  own_volume() {
    volumes="$(msb volume ls -q </dev/null 2>/dev/null || true)"
    VOLUME=lens-data n=1
    while [ "$VOLUME" = "${1:-}" ] || { printf '%s\n' "$volumes" | grep -qx "$VOLUME" && has_db "$VOLUME"; }; do
      n=$((n + 1))
      VOLUME="lens-data-$n"
    done
  }
  VOLUME="$(grep '^LENS_VOLUME=' .env | cut -d= -f2- || true)"
  if [ -z "$VOLUME" ]; then
    VOLUME=lens-data
    if [ "$fresh" = 1 ]; then
      own_volume
      if [ "$VOLUME" != lens-data ]; then
        say "Starting Lens on a microsandbox volume of its own, $VOLUME..."
        echo "  The volume lens-data holds an earlier install's data, which opens only with that install's .env. It's left"
        echo "  as it is: msb volume rm lens-data removes it."
      fi
    fi
    setenv LENS_VOLUME "$VOLUME"
  fi
  # The command that starts it, kept for a restart of the machine: a sandbox started again (msb start) boots without
  # running Lens, so it is always replaced; its packages come from the cache in the volume, its build stays there.
  write_sandbox() {
    # The internet, this network (mail servers, model servers) and this machine (Ollama, LM Studio); only the web app
    # the sensor ports and the file shares' ports (S3, WebDAV) are published, as with Docker.
    set -- run -d -q --name lens --replace -c "$cpus" -m "${mem}M" --root-disk 16G --net public,private,host \
      -p "$bind:$PORT:3000" \
      -p "$sensors:${LENS_MQTT_PORT:-1883}:1883" \
      -p "$sensors:${LENS_SYSLOG_PORT:-5514}:5514/udp" -p "$sensors:${LENS_SYSLOG_PORT:-5514}:5514/tcp" \
      -p "${LENS_SHARE_BIND:-$bind}:${LENS_S3_PORT:-9555}:9555" -p "${LENS_SHARE_BIND:-$bind}:${LENS_WEBDAV_PORT:-9556}:9556"
    if [ "$LENS_HTTPS" = 1 ]; then # Caddy listens on 8443 and 8080 inside (docs/https.md)
      set -- "$@" -p "${LENS_HTTPS_BIND:-0.0.0.0}:$HTTPS_PORT:8443" -p "${LENS_HTTPS_BIND:-0.0.0.0}:$HTTP_PORT:8080"
    fi
    set -- "$@" -v "$PWD:/lens:ro" -v "$PWD/.lens-control:/control" --mount-named "$VOLUME:/data" \
      --entrypoint /bin/sh "$image" -- /lens/microsandbox/run.sh
    {
      echo "#!/bin/sh"
      echo "# Starts Lens in its microsandbox (written by install.sh)"
      printf 'exec %s' "'$MSB'"
      for a in "$@"; do printf " '%s'" "$a"; done
      echo
    } >.lens-sandbox
    chmod +x .lens-sandbox
  }
  say "Starting Lens in a microsandbox (the first start installs and builds it, which takes a while)..."
  write_sandbox
  # Run again: the running sandbox stops first, so its database closes cleanly (a replace kills it after 10 seconds)
  # and its port is free for the new one
  if msb ls --running -q </dev/null 2>/dev/null | grep -qx lens; then
    say "Stopping the Lens that's running..."
    msb stop -q -t 60 lens </dev/null >/dev/null 2>&1 || msb stop -q -f lens </dev/null >/dev/null 2>&1 || true
  fi
  i=0
  until free "$PORT"; do
    i=$((i + 1))
    if [ $i -gt 15 ]; then
      who="$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | awk 'NR == 2 {print $1 " (process " $2 ")"}' || true)"
      fail "Port $PORT is in use${who:+ by $who}, so Lens can't start on it. Stop that, or run this again with LENS_PORT set to a free port."
    fi
    sleep 2
  done
  since="$(date -u +%Y-%m-%dT%H:%M:%SZ)" # what the new sandbox logs, for the progress below
  run_sandbox
  LOGS="msb logs -f lens"
  TAIL="msb logs lens --tail 40"
  STOP="msb stop lens"
  WAIT=1800 # 30 minutes: the first start installs the system packages and builds everything
  # back after a restart of the machine, as Docker's restart policy does
  if [ "$os" = Linux ] && have systemctl && [ -d /run/systemd/system ]; then
    unit=/etc/systemd/system/lens-microsandbox.service
    printf '%s\n' "[Unit]" "Description=Lens (microsandbox)" "After=network-online.target" "Wants=network-online.target" "" \
      "[Service]" "Type=oneshot" "RemainAfterExit=yes" "User=$(id -un)" "ExecStart=$PWD/.lens-sandbox" \
      "ExecStop=$MSB stop lens" "" "[Install]" "WantedBy=multi-user.target" |
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
  files="-f docker-compose.prod.yml"
  [ "$LENS_HTTPS" = 1 ] && files="$files -f docker-compose.https.yml" # the HTTPS ports (docs/https.md)
  # A release's prebuilt images (docs/deployment.md#prebuilt-images), for the release the code is: the branch install.sh
  # made for it, or the tag a first clone is on. Named in .env, so every later `docker compose` runs them too.
  pulled=0
  if [ "$IMAGES" = pull ]; then
    on="$(git symbolic-ref -q --short HEAD 2>/dev/null || git describe --tags --exact-match 2>/dev/null || echo "$REF")"
    version="$(printf '%s' "$on" | sed -n 's/^v\([0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\)$/\1/p')"
    extras="${EXTRAS:-$(grep '^EXTRAS=' .env | cut -d= -f2- || true)}"
    if [ -z "$version" ]; then
      say "Prebuilt images are published for releases, and $on isn't one, so Lens is built here."
    elif [ ! -f docker-compose.images.yml ]; then
      say "Lens $version came before prebuilt images, so it is built here."
    elif [ -n "$extras" ]; then
      say "The prebuilt images carry no extras (EXTRAS=$extras), so Lens is built here."
    else
      registry="${LENS_IMAGES_REGISTRY:-$(grep '^LENS_IMAGES_REGISTRY=' .env | cut -d= -f2- || true)}"
      registry="${registry:-ghcr.io/$(printf '%s' "${REPO%%/*}" | tr '[:upper:]' '[:lower:]')}"
      [ -z "${LENS_IMAGES_REGISTRY:-}" ] || setenv LENS_IMAGES_REGISTRY "$registry"
      setenv LENS_BACKEND_IMAGE "$registry/lens-backend:$version-$TARGET"
      setenv LENS_FRONTEND_IMAGE "$registry/lens-frontend:$version"
      say "Downloading Lens $version and starting it..."
      # shellcheck disable=SC2086
      if $COMPOSE $files -f docker-compose.images.yml pull backend frontend &&
        $COMPOSE $files -f docker-compose.images.yml up -d --build; then
        pulled=1
      else
        say "Couldn't download Lens's images, so it is built here instead."
      fi
    fi
  fi
  if [ "$pulled" = 0 ]; then
    unsetenv LENS_BACKEND_IMAGE
    unsetenv LENS_FRONTEND_IMAGE
    say "Building and starting Lens (the first build takes a few minutes)..."
    # shellcheck disable=SC2086
    $COMPOSE $files up -d --build
  fi
  LOGS="$COMPOSE $files logs"
  TAIL="$COMPOSE $files logs --tail 40"
  STOP="$COMPOSE $files down"
  WAIT=600
fi

# --- The upgrade agent: runs packaging/host/lens-upgrade.sh when the web app asks for a new release ---------------
# systemd on Linux, launchd on a Mac; not set up again by an upgrade it runs (LENS_AGENT_RUN)
if [ "${LENS_UPGRADE_AGENT:-1}" != 0 ] && [ "${LENS_AGENT_RUN:-0}" != 1 ]; then
  agent=""
  if [ "$os" = Linux ] && have systemctl && [ -d /run/systemd/system ]; then
    # the user who installed it, unless only root can use Docker here
    run_as="$(id -un)"
    case "${DOCKER:-docker}" in sudo*) run_as=root ;; esac
    unit=/etc/systemd/system/lens-upgrade
    if printf '%s\n' "[Unit]" "Description=Lens upgrade agent: installs the release the web app asks for" "" \
      "[Path]" "PathExistsGlob=$PWD/.lens-control/inbox/upgrade" "" "[Install]" "WantedBy=multi-user.target" |
      $(sudo_ok) tee "$unit.path" >/dev/null 2>&1 &&
      printf '%s\n' "[Unit]" "Description=Lens upgrade (docs/updates.md)" "After=network-online.target" "" \
        "[Service]" "Type=oneshot" "User=$run_as" "Environment=HOME=$HOME" \
        "Environment=PATH=$HOME/.local/bin:$HOME/.microsandbox/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
        "ExecStart=/bin/sh $PWD/packaging/host/lens-upgrade.sh $PWD" |
      $(sudo_ok) tee "$unit.service" >/dev/null 2>&1 &&
      $(sudo_ok) systemctl daemon-reload >/dev/null 2>&1 &&
      $(sudo_ok) systemctl enable --now lens-upgrade.path >/dev/null 2>&1; then
      agent=systemd
    fi
  elif [ "$os" = Darwin ] && have launchctl; then
    plist="$HOME/Library/LaunchAgents/dev.lens.upgrade.plist"
    mkdir -p "$(dirname "$plist")"
    cat >"$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>dev.lens.upgrade</string>
  <key>ProgramArguments</key><array><string>/bin/sh</string><string>$PWD/packaging/host/lens-upgrade.sh</string><string>$PWD</string></array>
  <key>WatchPaths</key><array><string>$PWD/.lens-control/inbox</string></array>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>$HOME/.orbstack/bin:$HOME/.microsandbox/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string></dict>
</dict></plist>
PLIST
    launchctl bootout "gui/$(id -u)" "$plist" >/dev/null 2>&1 || true
    launchctl bootstrap "gui/$(id -u)" "$plist" >/dev/null 2>&1 && agent=launchd
  fi
  if [ -n "$agent" ]; then
    echo "kind=$agent" >.lens-control/agent
    chmod 644 .lens-control/agent # the containers read it (a first install writes with umask 077, for .env)
  else
    rm -f .lens-control/agent # the web app shows the line to run instead
  fi
fi

say "Waiting for the web app..."
# the page and the API behind it: the setup page needs both, and the API starts a few seconds after the page. Each
# try gives up after a few seconds: a port that takes the connection but never answers mustn't hold this forever.
up() {
  curl -fsS -o /dev/null --max-time 5 "http://127.0.0.1:$PORT/login" 2>/dev/null &&
    curl -fsS -o /dev/null --max-time 5 "http://127.0.0.1:$PORT/api/v1/auth/status" 2>/dev/null
}
# why it stopped waiting, after the last lines Lens logged
stuck() {
  echo "The last lines Lens logged:"
  $TAIL 2>&1 </dev/null | tail -n 40 || true
  fail "$1 See everything: cd $DIR && $LOGS"
}
# The keys in .env don't open the database in the volume Lens ran on, as run.sh found: this .env was written again
# (a reinstall after the install folder was deleted made new keys) and kept the earlier install's volume. That
# database opens only with the earlier .env, so the volume is left as it is, and Lens starts again on one of its own.
moved=""
other_volume() {
  moved="$VOLUME"
  own_volume "$moved"
  setenv LENS_VOLUME "$VOLUME"
  say "Starting Lens on a microsandbox volume of its own, $VOLUME..."
  echo "  The database in $moved was made with another install's keys, not the ones in $DIR/.env, so Lens can't open"
  echo "  it. It's left as it is: put that install's .env back (with LENS_VOLUME=$moved) to use it again, or remove it"
  echo "  with: msb volume rm $moved"
  write_sandbox
  msb stop -q -t 30 lens </dev/null >/dev/null 2>&1 || true
  since="$(date -u +%Y-%m-%dT%H:%M:%SZ)" seen=0 down=0 step=""
  run_sandbox
  start=$(date +%s) # a first start on this volume: it installs and builds everything again
  shown=$start
}
# the steps the sandbox reports (run.sh's "[lens] ..." lines), each shown once; it stops waiting when one failed or
# the sandbox stopped
seen=0 down=0 step=""
sandbox_progress() {
  lines="$(msb logs lens --since "$since" --grep '\[lens\]' </dev/null 2>/dev/null | grep '^\[lens\] ' || true)"
  n="$(printf '%s\n' "$lines" | grep -c . || true)"
  if [ "$n" -gt "$seen" ]; then
    # as steps of their own (the web installer lists them)
    printf '%s\n' "$lines" | sed -n "$((seen + 1)),\$p" | sed 's/^\[lens\] //' | while IFS= read -r line; do say "  $line"; done
    seen=$n
    shown=$now
    step="$(printf '%s\n' "$lines" | tail -n 1 | sed 's/^\[lens\] //; s/[.][.][.]$//')"
  fi
  if [ -z "$moved" ] && printf '%s\n' "$lines" | grep -q "^\[lens\] Another install's database"; then
    other_volume
    return 0
  fi
  failed="$(printf '%s\n' "$lines" | sed -n 's/^\[lens\] \(Failed.*\)/\1/p' | tail -n 1)"
  [ -z "$failed" ] || stuck "Lens couldn't start in its microsandbox. $failed."
  # a part that stops again and again (run.sh starts it again each time) won't start by waiting longer
  looping="$(printf '%s\n' "$lines" | sed -n 's/^\[lens\] \([a-z]*\) stopped; starting it again.*/\1/p' | sort | uniq -c |
    awk '$1 >= 3 {print $2; exit}')"
  case "$looping" in
    '') ;;
    surrealdb) stuck "Lens's database keeps stopping in its microsandbox." ;;
    api) stuck "Lens's API keeps stopping in its microsandbox." ;;
    web) stuck "Lens's web app keeps stopping in its microsandbox." ;;
    worker) stuck "Lens's job worker keeps stopping in its microsandbox." ;;
    *) stuck "Lens's $looping keeps stopping in its microsandbox." ;;
  esac
  if running="$(msb ls --running -q </dev/null 2>/dev/null)"; then # twice in a row, as it may be booting
    if printf '%s\n' "$running" | grep -qx lens; then down=0; else down=$((down + 1)); fi
  fi
  [ $down -lt 2 ] || stuck "Lens's microsandbox stopped${step:+ at: $step}."
}
WAIT="${LENS_WAIT:-$WAIT}"
start=$(date +%s)
shown=$start checked=0
until up; do
  now=$(date +%s)
  if [ "$BACKEND" = microsandbox ] && [ $((now - checked)) -ge 10 ]; then
    checked=$now
    sandbox_progress
  fi
  if [ $((now - start)) -ge "$WAIT" ]; then
    if [ "$WAIT" -lt 120 ]; then within="$WAIT seconds"; else within="$((WAIT / 60)) minutes"; fi
    why="Lens didn't answer on port $PORT within $within${step:+ (its last step: $step)}."
    # up in the sandbox, but not on this machine's port: the port is the problem, not Lens
    if [ "$BACKEND" = microsandbox ] &&
      msb exec lens -- curl -fsS -o /dev/null --max-time 5 http://127.0.0.1:3000/login </dev/null >/dev/null 2>&1; then
      why="Lens is running in its microsandbox, but port $PORT on this machine doesn't reach it. Is another program using the port?"
    fi
    stuck "$why"
  fi
  if [ $((now - shown)) -ge 60 ]; then # never silent for long
    echo "  still starting ($(((now - start) / 60)) min so far)..."
    shown=$now
  fi
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
