#!/bin/sh
# Tests mcp.sh: the MCP runner started as the mcp user while COMPOSE_PROFILES in .env names mcp, started again when it
# stops, stopped when the profile goes, with the volume closed to it. Run it as root in the image run.sh runs in:
#
#   docker run --rm -v "$PWD:/lens:ro" python:3.12-slim-trixie sh /lens/microsandbox/test-mcp.sh
set -eu

here="$(cd "$(dirname "$0")" && pwd)"
work="$(mktemp -d)" && chmod 755 "$work"
loop=""
finish() {
  [ -z "$loop" ] || kill "$loop" 2>/dev/null || true
  wait 2>/dev/null || true
  rm -rf "$work"
}
trap finish EXIT

log() { printf '[lens] %s\n' "$*" | tee -a "$work/log"; }
fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}
ok() { printf 'ok: %s\n' "$*"; }
# wait_until COMMAND...: until it succeeds, for 20 seconds at most
wait_until() {
  i=0
  until "$@"; do
    i=$((i + 1))
    [ "$i" -lt 100 ] || return 1
    sleep 0.2
  done
}
said() { grep -c "$1" "$work/log" || true; }

# the install folder, as the sandbox sees it: .env (the profile off) and the runner's code; the volume, lens's alone
CODE="$work/lens" OPT="$work/data/.lens"
mkdir -p "$CODE/mcp-runner" "$OPT/node/bin" "$OPT/bin" "$work/data/surreal"
cp "$here/../mcp-runner/runner.py" "$CODE/mcp-runner/"
echo "COMPOSE_PROFILES=matterbridge" >"$CODE/.env"
echo secret >"$work/data/surreal/lens.db"
chmod 750 "$work/data"

# shellcheck source=SCRIPTDIR/mcp.sh
. "$here/mcp.sh"
MCP_HOME="$work/mcp-home" MCP_KEY="$work/mcp-key" MCP_RUNNER="$work/runner.py" MCP_TOOLS="$work/tools" MCP_PORT=18090
mcp_prepare || fail "mcp_prepare"
id mcp >/dev/null || fail "no mcp user"
[ "$(stat -c %U:%a "$MCP_HOME")" = mcp:700 ] || fail "the runner's home is $(stat -c %U:%a "$MCP_HOME")"

# 1. A stand-in runner: writes who it is and whether the volume is open to it, then waits
cat >"$work/fake-runner.sh" <<EOF
#!/bin/sh
if cat "$work/data/surreal/lens.db" >/dev/null 2>&1; then saw=open; else saw=closed; fi
echo "\$(id -un) \$saw \$HOME \$\$" >>"\$HOME/ran"
exec sleep 300
EOF
chmod 644 "$work/fake-runner.sh"
mcp_run() { exec setpriv --reuid mcp --regid mcp --clear-groups -- env -i HOME="$MCP_HOME" sh "$work/fake-runner.sh"; }
(
  set +e
  mcp_loop
) &
loop=$!
sleep 1
[ ! -f "$MCP_HOME/ran" ] || fail "it started with the profile off"

sed -i 's/^COMPOSE_PROFILES=.*/COMPOSE_PROFILES=matterbridge,mcp/' "$CODE/.env"
wait_until test -f "$MCP_HOME/ran" || fail "the runner didn't start once .env named mcp"
read -r user saw home pid <"$MCP_HOME/ran"
[ "$user" = mcp ] || fail "it ran as $user"
[ "$saw" = closed ] || fail "the volume is open to the mcp user"
[ "$home" = "$MCP_HOME" ] || fail "its home is $home"
ok "the runner starts as the mcp user once COMPOSE_PROFILES names mcp, with the volume closed to it"

# 2. Stopped, it's started again
kill "$pid"
ran_twice() { test "$(wc -l <"$MCP_HOME/ran")" -ge 2; }
wait_until ran_twice || fail "it wasn't started again"
[ "$(said 'starting it again')" -ge 1 ] || fail "the log doesn't say it was started again"
ok "a runner that stops is started again"

# 3. The profile taken out of .env stops it
pid="$(tail -n 1 "$MCP_HOME/ran" | cut -d' ' -f4)"
sed -i 's/^COMPOSE_PROFILES=.*/COMPOSE_PROFILES=matterbridge/' "$CODE/.env"
wait_until sh -c "! kill -0 $pid 2>/dev/null" || fail "it still runs with the profile off"
sleep 6
[ "$(wc -l <"$MCP_HOME/ran")" = 2 ] || fail "it was started again with the profile off"
ok "the profile taken out of .env stops the runner"
kill "$loop" && wait "$loop" 2>/dev/null || true
loop=""

# 4. The real runner, as mcp_run runs it: it answers on the sandbox's localhost, and its token is where Lens reads it
unset -f mcp_run
# shellcheck source=SCRIPTDIR/mcp.sh
. "$here/mcp.sh"
MCP_HOME="$work/mcp-home" MCP_KEY="$work/mcp-key" MCP_RUNNER="$work/runner.py" MCP_TOOLS="$work/tools" MCP_PORT=18090
sed -i 's/^COMPOSE_PROFILES=.*/COMPOSE_PROFILES=mcp/' "$CODE/.env"
(
  set +e
  mcp_loop
) &
loop=$!
health() {
  python3 -c 'import sys, urllib.request; sys.exit(0 if b"ok" in urllib.request.urlopen("http://127.0.0.1:18090/health", timeout=2).read() else 1)' 2>/dev/null
}
wait_until health || fail "the runner doesn't answer on 127.0.0.1:18090: $(cat "$work/log")"
wait_until test -s "$MCP_KEY/token" || fail "no token"
[ "$(stat -c %U:%a "$MCP_KEY/token")" = mcp:644 ] || fail "the token is $(stat -c %U:%a "$MCP_KEY/token")"
# and refuses anyone without the token
code="$(python3 -c 'import urllib.request, urllib.error
try: urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:18090/servers/x", data=b"{}", method="PUT"), timeout=2); print(200)
except urllib.error.HTTPError as e: print(e.code)')"
[ "$code" = 401 ] || fail "without the token it answered $code"
kill "$loop" && wait "$loop" 2>/dev/null || true
loop=""
not_health() { ! health; }
wait_until not_health || fail "the runner outlived the loop"
ok "the real runner answers on localhost with its token where Lens reads it, and stops with the loop"
