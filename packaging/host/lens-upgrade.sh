#!/bin/sh
# The upgrade agent (docs/updates.md): installs the release the web app asks for, on the machine Lens runs on.
#
# install.sh sets it up to run whenever a request lands in <install folder>/.lens-control/inbox (a systemd path unit on
# Linux, a launchd agent on a Mac). The web app has backed the database up by then. This runs install.sh again for
# the release asked for, as a person would by hand (the secrets, the database and the archive are kept), and writes
# how it went into .lens-control/upgrade-status and upgrade.log, which the web app shows.
#
# It takes a release tag (vX.Y.Z), or the branch this install already follows; nothing else, whoever wrote the
# request. Run by hand: sh packaging/host/lens-upgrade.sh <install folder>
#
# A request with action=services turns the monitoring containers on or off instead (docs/prometheus.md): it adds or
# removes `monitoring` in COMPOSE_PROFILES in .env and starts or stops Prometheus, node-exporter and Grafana, and says
# how it went in .lens-control/services-status. It comes in the same file as an upgrade, the one every agent watches.
set -eu
umask 022 # the containers read the status and the log

# Monitoring on or off (action=services): COMPOSE_PROFILES in .env, then Docker Compose starts or stops the three
services() {
  sstatus() { # state [extra key=value lines]
    {
      echo "id=$id"
      echo "monitoring=$monitoring"
      echo "state=$1"
      echo "started_at=$started"
      echo "grafana_port=$port"
      shift
      for kv in "$@"; do echo "$kv"; done
    } >"$CTL/services-status.tmp" && mv "$CTL/services-status.tmp" "$CTL/services-status"
  }
  started="$(now)"
  port="$(env_of LENS_GRAFANA_PORT | tr -cd 0-9)"
  port="${port:-3001}"
  case "$monitoring" in
    on | off) ;;
    *)
      sstatus failed "finished_at=$(now)" "code=2" "error=monitoring is on or off"
      return 0
      ;;
  esac
  if [ "$(env_of LENS_BACKEND)" = microsandbox ]; then
    sstatus failed "finished_at=$(now)" "code=2" "error=monitoring runs in Docker; this install runs in a microsandbox"
    return 0
  fi
  sstatus running
  # the other profiles stay as they are
  profiles="$(env_of COMPOSE_PROFILES | tr ',' '\n' | tr -cd 'A-Za-z0-9_\n-' | grep -v -e '^monitoring$' -e '^$' | paste -s -d, - || true)"
  [ "$monitoring" = on ] && profiles="${profiles:+$profiles,}monitoring"
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
  if [ "$monitoring" = on ]; then
    (cd "$DIR" && $compose $files --profile monitoring up -d prometheus node-exporter grafana) >"$CTL/services.log" 2>&1 </dev/null
  else
    (cd "$DIR" && $compose $files --profile monitoring rm -s -f prometheus node-exporter grafana) >"$CTL/services.log" 2>&1 </dev/null
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
action="$(val action "$REQ")"
monitoring="$(val monitoring "$REQ")"
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
  } >"$CTL/upgrade-status.tmp" && mv "$CTL/upgrade-status.tmp" "$CTL/upgrade-status"
}
started="$(now)"
: >"$CTL/upgrade.log"

branch="$(git -C "$DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
if ! printf '%s' "$ref" | grep -Eq '^v[0-9]+\.[0-9]+\.[0-9]+$' && [ "$ref" != "$branch" ]; then
  echo "Not upgrading to '$ref': only a release tag (vX.Y.Z) or this install's branch ($branch)." >>"$CTL/upgrade.log"
  status failed "finished_at=$(now)" "code=2" "error=only a release tag (vX.Y.Z) or this install's branch can be installed"
  exit 0
fi
status running

# The same settings this install was made with (install.sh keeps the port and the address itself)
set +e
(
  cd "$DIR" &&
    LENS_DIR="$DIR" LENS_REF="$ref" LENS_NO_OPEN=1 LENS_YES=1 LENS_WIZARD=0 LENS_AGENT_RUN=1 \
      LENS_REPO="$(env_of LENS_REPO)" LENS_TARGET="$(env_of LENS_TARGET)" LENS_BACKEND="$(env_of LENS_BACKEND)" \
      GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0="$DIR" \
      sh "$DIR/install.sh"
) >>"$CTL/upgrade.log" 2>&1 </dev/null
code=$?
set -e

# run as root for a Docker only root can use: what it wrote stays the install's owner's
if [ "$(id -u)" = 0 ]; then
  owner="$(stat -c %u:%g "$DIR" 2>/dev/null || stat -f %u:%g "$DIR")"
  [ "$owner" = "0:0" ] || chown -R "$owner" "$DIR" 2>/dev/null || true
fi

version="$(sed -n 's/^version = "\(.*\)"/\1/p' "$DIR/fastapi_backend/pyproject.toml" 2>/dev/null | head -1)"
if [ "$code" = 0 ]; then
  status "done" "finished_at=$(now)" "code=0" "version=$version"
else
  status failed "finished_at=$(now)" "code=$code"
fi
}

main "$@"
