#!/bin/sh
# The upgrade agent (docs/updates.md): installs the release the web app asks for, on the machine Lens runs on.
#
# install.sh sets it up to run whenever a request lands in <install folder>/.lens-control/inbox (a systemd path unit on
# Linux, a launchd agent on a Mac). The web app has backed the database up by then. This runs install.sh again for
# the release asked for, as a person would by hand (the secrets, the database and the archive are kept), and writes
# how it went into .lens-control/upgrade-status and upgrade.log, which the web app shows.
#
# It takes a release tag (vX.Y.Z), or the branch this install already follows; nothing else, whoever wrote the
# request. An empty ref sets up the version running again, as Settings › HTTPS does with https=1 or 0 to open or close
# the HTTPS ports (docs/https.md), and Settings with public=1 or 0 (reachable from other machines on the network, or
# this one only) and target=full or lean (the edition). If the new version doesn't start, it goes back to the code that
# ran before and starts that (rolled-back). Run by hand: sh packaging/host/lens-upgrade.sh <install folder>
#
# A request with action=services turns one of Lens's optional containers on or off instead (profile=<name>
# turn=on|off): the MCP runner (mcp), the browser (browser, docs/assistant.md) or monitoring (docs/prometheus.md). It
# adds or removes the profile in COMPOSE_PROFILES in .env and starts or stops that profile's containers, those named in
# services_of below and nothing else, and says how it went in .lens-control/services-status. A request from before
# profiles, monitoring=on|off, is the monitoring profile. It comes in the same file as an upgrade, the one every agent
# watches.
set -eu
umask 022 # the containers read the status and the log

# The containers each optional profile starts (action=services); a profile not named here isn't turned on or off
services_of() {
  case "$1" in
    monitoring) echo "prometheus node-exporter grafana" ;;
    mcp) echo "mcp" ;;
    browser) echo "browser browser-proxy" ;;
  esac
}

# A profile on or off (action=services): COMPOSE_PROFILES in .env, then Docker Compose starts or stops its containers
services() {
  sstatus() { # state [extra key=value lines]
    {
      echo "id=$id"
      echo "profile=$profile"
      echo "turn=$turn"
      echo "state=$1"
      echo "started_at=$started"
      if [ "$profile" = monitoring ]; then echo "grafana_port=$port"; fi
      shift
      for kv in "$@"; do echo "$kv"; done
    } >"$CTL/services-status.tmp" && mv "$CTL/services-status.tmp" "$CTL/services-status"
  }
  started="$(now)"
  if [ -z "$profile" ] && [ -n "$monitoring" ]; then # a request from before profiles
    profile=monitoring
    turn="$monitoring"
  fi
  port="$(env_of LENS_GRAFANA_PORT | tr -cd 0-9)"
  port="${port:-3001}"
  names="$(services_of "$profile")"
  if [ -z "$names" ]; then
    sstatus failed "finished_at=$(now)" "code=2" "error=Lens has no optional containers called '$profile'"
    return 0
  fi
  case "$turn" in
    on | off) ;;
    *)
      sstatus failed "finished_at=$(now)" "code=2" "error=$profile is turned on or off"
      return 0
      ;;
  esac
  if [ "$(env_of LENS_BACKEND)" = microsandbox ]; then
    sstatus failed "finished_at=$(now)" "code=2" "error=$profile runs in Docker; this install runs in a microsandbox"
    return 0
  fi
  sstatus running
  # the other profiles stay as they are
  profiles="$(env_of COMPOSE_PROFILES | tr ',' '\n' | tr -cd 'A-Za-z0-9_\n-' | grep -v -e "^$profile\$" -e '^$' | paste -s -d, - || true)"
  [ "$turn" = on ] && profiles="${profiles:+$profiles,}$profile"
  if [ -f "$DIR/.env" ]; then # rewritten in place: it keeps its owner and permissions
    grep -v '^COMPOSE_PROFILES=' "$DIR/.env" >"$DIR/.env.tmp" || true
    [ -z "$profiles" ] || echo "COMPOSE_PROFILES=$profiles" >>"$DIR/.env.tmp"
    cat "$DIR/.env.tmp" >"$DIR/.env" && rm -f "$DIR/.env.tmp"
  fi
  if docker compose version >/dev/null 2>&1; then compose="docker compose"; else compose="docker-compose"; fi
  files="-f docker-compose.prod.yml"
  [ "$(env_of LENS_HTTPS)" = 1 ] && files="$files -f docker-compose.https.yml"
  set +e
  # shellcheck disable=SC2086
  if [ "$turn" = on ]; then
    (cd "$DIR" && $compose $files --profile "$profile" up -d $names) >"$CTL/services.log" 2>&1 </dev/null
  else
    (cd "$DIR" && $compose $files --profile "$profile" rm -s -f $names) >"$CTL/services.log" 2>&1 </dev/null
  fi
  code=$?
  set -e
  if [ "$code" = 0 ]; then
    sstatus "done" "finished_at=$(now)" "code=0"
  else
    sstatus failed "finished_at=$(now)" "code=$code" "error=Docker Compose stopped (exit $code); see .lens-control/services.log"
  fi
}

# All in a function: install.sh replaces this file while it runs
main() {
DIR="${1:-$(cd "$(dirname "$0")/../.." && pwd)}"
CTL="$DIR/.lens-control"
REQ="$CTL/inbox/upgrade"
[ -f "$REQ" ] || exit 0

# one at a time (a lock left by a run that was killed lapses after two hours)
find "$CTL" -maxdepth 1 -name .running -mmin +120 -exec rmdir {} \; 2>/dev/null || true
mkdir "$CTL/.running" 2>/dev/null || exit 0
trap 'rmdir "$CTL/.running" 2>/dev/null || true' EXIT

val() { sed -n "s/^$1=//p" "$2" | head -1 | tr -cd 'A-Za-z0-9._/-'; }
id="$(val id "$REQ")"
ref="$(val ref "$REQ")"
backup="$(val backup "$REQ")"
action="$(val action "$REQ")"
profile="$(val profile "$REQ")"
turn="$(val turn "$REQ")"
monitoring="$(val monitoring "$REQ")"
https="$(val https "$REQ")"
public="$(val public "$REQ")"
target="$(val target "$REQ")"
rm -f "$REQ"
now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
env_of() { [ -f "$DIR/.env" ] && sed -n "s/^$1=//p" "$DIR/.env" | head -1; }
if [ "$action" = services ]; then
  services
  exit 0
fi
status() { # state [extra key=value lines]
  {
    echo "id=$id"
    echo "ref=$ref"
    echo "state=$1"
    echo "started_at=$started"
    shift
    for kv in "$@"; do echo "$kv"; done
    if [ -n "$backup" ]; then echo "backup=$backup"; fi
  } >"$CTL/upgrade-status.tmp" && mv "$CTL/upgrade-status.tmp" "$CTL/upgrade-status"
}
started="$(now)"
: >"$CTL/upgrade.log"

# (not rev-parse --abbrev-ref: a branch named like a tag, as install.sh makes for a release, comes out heads/vX.Y.Z)
branch="$(git -C "$DIR" symbolic-ref -q HEAD 2>/dev/null | sed 's|^refs/heads/||' || true)"
if [ -z "$ref" ]; then # the version running: this install's branch, or the release tag it's on
  ref="$branch"
  [ -n "$ref" ] || ref="$(git -C "$DIR" describe --tags --exact-match 2>/dev/null || true)"
fi
case "$https" in 0 | 1 | "") ;; *) https="" ;; esac
case "$public" in 0 | 1 | "") ;; *) public="" ;; esac
case "$target" in full | lean | "") ;; *) target="" ;; esac
if ! printf '%s' "$ref" | grep -Eq '^v[0-9]+\.[0-9]+\.[0-9]+$' && [ "$ref" != "$branch" ]; then
  echo "Not upgrading to '$ref': only a release tag (vX.Y.Z) or this install's branch ($branch)." >>"$CTL/upgrade.log"
  status failed "finished_at=$(now)" "code=2" "error=only a release tag (vX.Y.Z) or this install's branch can be installed"
  exit 0
fi
status running "step=download"

# install.sh with the settings this install was made with, but for what the request changes (an empty LENS_PUBLIC
# leaves the address as .env has it), its lines in the log; the step it is on goes into the status as it goes. Its
# exit code, after.
install() { # extra environment, as NAME=value words
  rm -f "$CTL/.code"
  {
    (
      cd "$DIR" &&
        env LENS_DIR="$DIR" LENS_REF="$ref" LENS_NO_OPEN=1 LENS_YES=1 LENS_WIZARD=0 LENS_AGENT_RUN=1 \
          LENS_REPO="$(env_of LENS_REPO)" LENS_TARGET="${target:-$(env_of LENS_TARGET)}" LENS_BACKEND="$(env_of LENS_BACKEND)" \
          LENS_HTTPS="${https:-$(env_of LENS_HTTPS)}" LENS_PUBLIC="$public" \
          GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0="$DIR" "$@" \
          sh "$DIR/install.sh"
    ) </dev/null 2>&1 || echo "$?" >"$CTL/.code"
  } | while IFS= read -r line; do
    printf '%s\n' "$line" >>"$CTL/upgrade.log"
    case "$line" in
      *"Building and starting"* | *"Getting the base image"*) status "$phase" "step=build" ;;
      *"Starting Lens in a microsandbox"* | *"Waiting for the web app"*) status "$phase" "step=start" ;;
      *"Lens is running"*) status "$phase" "step=check" ;;
    esac
  done
  rc="$(cat "$CTL/.code" 2>/dev/null || echo 0)"
  rm -f "$CTL/.code"
  return "$rc"
}

# the code that runs now, to come back to if the new version doesn't start
prev="$(git -C "$DIR" rev-parse HEAD 2>/dev/null || true)"
phase=running
set +e
install
code=$?
set -e

rolled=""
if [ "$code" != 0 ] && [ -n "$prev" ] && [ "$(git -C "$DIR" rev-parse HEAD 2>/dev/null)" != "$prev" ]; then
  # The new version didn't start: the previous code comes back. When the new one had already changed the database
  # (steps the previous version doesn't know), the previous version restores the backup taken first as it starts
  # (updates.restore_on_start); otherwise the database is kept as it is, with nothing lost.
  phase=rolling-back
  status rolling-back "step=check" "code=$code"
  echo "--- The new version didn't start (exit $code): going back to the version that ran before ($prev)" >>"$CTL/upgrade.log"
  if [ -n "$backup" ]; then printf 'id=%s\nbackup=%s\n' "$id" "$backup" >"$CTL/inbox/restore"; fi
  set +e
  git -C "$DIR" -c safe.directory="$DIR" checkout -q -f -B "${branch:-lens}" "$prev" >>"$CTL/upgrade.log" 2>&1 &&
    install LENS_KEEP_CODE=1
  back=$?
  set -e
  rolled=$([ "$back" = 0 ] && echo yes || echo no)
fi

# run as root for a Docker only root can use: what it wrote stays the install's owner's
if [ "$(id -u)" = 0 ]; then
  owner="$(stat -c %u:%g "$DIR" 2>/dev/null || stat -f %u:%g "$DIR")"
  [ "$owner" = "0:0" ] || chown -R "$owner" "$DIR" 2>/dev/null || true
fi

version="$(sed -n 's/^version = "\(.*\)"/\1/p' "$DIR/fastapi_backend/pyproject.toml" 2>/dev/null | head -1)"
if [ "$code" = 0 ]; then
  status "done" "finished_at=$(now)" "code=0" "version=$version"
elif [ "$rolled" = yes ]; then
  status rolled-back "finished_at=$(now)" "code=$code" "version=$version" \
    "error=the new version didn't start, so Lens went back to the one that ran before ($version)"
elif [ "$rolled" = no ]; then
  status failed "finished_at=$(now)" "code=$code" \
    "error=the new version didn't start, and going back to the previous one failed too: run the install line on the machine"
else
  status failed "finished_at=$(now)" "code=$code"
fi
}

main "$@"
