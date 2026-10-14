#!/bin/sh
# Tests dns.sh against a resolver that answers every lookup with no address, the way uv's lookups failed in a
# microsandbox ("No address associated with hostname"). Run it as root in the image run.sh runs in, with the internet:
#
#   docker run --rm -v "$PWD:/lens:ro" python:3.12-slim-bookworm sh /lens/microsandbox/test-dns.sh
#   msb exec lens -- sh /lens/microsandbox/test-dns.sh     # in the sandbox itself, through microsandbox's DNS
#
# It puts /etc/resolv.conf back as it was when it ends.
set -eu

here="$(cd "$(dirname "$0")" && pwd)"
work="$(mktemp -d)"
cp /etc/resolv.conf "$work/resolv.conf"
server=""
finish() {
  [ -z "$server" ] || kill "$server" 2>/dev/null || true
  cp "$work/resolv.conf" /etc/resolv.conf
  rm -f /etc/resolv.conf.sandbox /etc/resolv.conf.fallback
  rm -rf "$work"
}
trap finish EXIT

log() { printf '[lens] %s\n' "$*" | tee -a "$work/log"; }
fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}
ok() { printf 'ok: %s\n' "$*"; }

# A resolver on this machine's loopback that knows every name and has no address for any of them
python3 -c '
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.bind(("127.0.0.1", 53))
while True:
    q, peer = s.recvfrom(4096)
    flags = (q[2] & 0x79 | 0x80).to_bytes(1, "big") + b"\x80"  # a response, recursion available, NOERROR
    s.sendto(q[:2] + flags + q[4:6] + b"\x00\x00\x00\x00\x00\x00" + q[12:], peer)  # the question, no answers
' &
server=$!
sleep 1
nodata="nameserver 127.0.0.1"

# shellcheck source=SCRIPTDIR/dns.sh
. "$here/dns.sh"

# 1. The failure, reproduced: the names don't resolve
echo "$nodata" >/etc/resolv.conf
if why="$(resolves files.pythonhosted.org 2>&1)"; then fail "the empty resolver gave an address"; fi
case "$why" in *"No address associated with hostname"*) ok "reproduced: $why" ;; *) fail "unexpected: $why" ;; esac

# 2. It waits, then looks names up with the public resolvers, and they answer
dns_ready || fail "dns_ready gave up with the public resolvers to fall back on"
grep -q '^nameserver 1.1.1.1$' /etc/resolv.conf || fail "resolv.conf doesn't name them: $(cat /etc/resolv.conf)"
[ "$(cat /etc/resolv.conf.sandbox)" = "$nodata" ] || fail "the sandbox's own resolv.conf wasn't kept"
# shellcheck disable=SC2086
resolves $NAMES || fail "the names don't resolve with the public resolvers"
grep -q "looking names up with 1.1.1.1 9.9.9.9 instead" "$work/log" || fail "it didn't say it switched"
ok "fell back to the public resolvers"

# 3. Lens starts: this machine's resolvers still have no address, so the public ones stay
dns_restore
grep -q '^nameserver 1.1.1.1$' /etc/resolv.conf || fail "the public resolvers didn't stay"
[ -f /etc/resolv.conf.sandbox ] || fail "the sandbox's own resolv.conf is gone"
ok "kept the public resolvers while this machine's don't answer"

# 4. Lens starts and this machine's resolvers answer again: they are used
cp "$work/resolv.conf" /etc/resolv.conf.sandbox
dns_restore
[ "$(cat /etc/resolv.conf)" = "$(cat "$work/resolv.conf")" ] || fail "this machine's resolv.conf wasn't put back"
[ ! -f /etc/resolv.conf.sandbox ] || fail "resolv.conf.sandbox is left behind"
ok "went back to this machine's resolvers when they answered"

# 5. With no public resolvers allowed (LENS_DNS_FALLBACK empty), it gives up and says why
echo "$nodata" >/etc/resolv.conf
# shellcheck disable=SC2034 # read by dns_ready
DNS_FALLBACK="" DNS_TRIES=2
if dns_ready; then fail "dns_ready succeeded with nothing that answers"; fi
grep -q "Can't look up names in the sandbox (deb.debian.org: No address associated with hostname)" "$work/log" ||
  fail "it didn't say why it gave up: $(tail -1 "$work/log")"
[ "$(cat /etc/resolv.conf)" = "$nodata" ] || fail "resolv.conf changed with no public resolvers allowed"
ok "gave up with the reason when nothing answers"

# 6. A step that fails once runs again
cp "$work/resolv.conf" /etc/resolv.conf
# shellcheck disable=SC2034 # read by dns_ready
DNS_TRIES=15
once() {
  [ -f "$work/ran" ] && return 0
  touch "$work/ran" && return 1
}
retry once || fail "retry didn't run the step again"
[ "${UV_CONCURRENT_DOWNLOADS:-}" = 4 ] || fail "the retry didn't make uv download fewer at once"
ok "a failed step ran again"

echo "All DNS tests passed"
