# Name lookups in the sandbox (docs/microsandbox.md), sourced by run.sh, which defines log(). Root only: it may
# rewrite /etc/resolv.conf (in place: in a container it is a mount). test-dns.sh tests it.
#
# The sandbox asks microsandbox, which answers from this machine's resolvers. A download stops at the first name that
# doesn't resolve (uv: "dns error ... No address associated with hostname"), so run.sh waits for the names it needs
# before downloading, and retries what downloads. When this machine's resolvers don't answer those names in the
# sandbox, it asks public ones instead while it installs and builds (LENS_DNS_FALLBACK in .env, comma-separated;
# default Cloudflare's and Quad9's; empty: never), and keeps them only if this machine's still don't answer when Lens
# starts.

NAMES="deb.debian.org files.pythonhosted.org registry.npmjs.org"
DNS_FALLBACK="$(printf '%s' "${LENS_DNS_FALLBACK-1.1.1.1,9.9.9.9}" | tr ',' ' ')"
# tries 3 seconds apart: the public resolvers from the 5th, this machine's again from the 10th
DNS_TRIES=15

# NAME...: whether they all resolve, the way apt, uv and pnpm look them up; if not, which didn't and why (stderr)
resolves() {
  RES_OPTIONS="timeout:2 attempts:1" python3 -c '
import socket, sys
for name in sys.argv[1:]:
    try:
        socket.getaddrinfo(name, 443, type=socket.SOCK_STREAM)
    except OSError as e:
        sys.exit(f"{name}: {e.strerror or e}")' "$@"
}

# Wait until NAMES resolve: switch to the public resolvers if this machine's don't answer them, give up after a while
dns_ready() {
  tries=0
  # shellcheck disable=SC2086
  until why="$(resolves $NAMES 2>&1)"; do
    tries=$((tries + 1))
    if [ "$tries" = 1 ]; then log "Waiting for name lookups ($why)..."; fi
    if [ "$tries" = 5 ] && [ -n "$DNS_FALLBACK" ] && [ ! -f /etc/resolv.conf.sandbox ]; then
      cp /etc/resolv.conf /etc/resolv.conf.sandbox
      # shellcheck disable=SC2086
      printf 'nameserver %s\n' $DNS_FALLBACK >/etc/resolv.conf
      log "This machine's DNS doesn't answer in the sandbox ($why); looking names up with $DNS_FALLBACK instead"
    fi
    if [ "$tries" = 10 ] && [ -f /etc/resolv.conf.sandbox ]; then # the public ones don't answer either
      cp /etc/resolv.conf.sandbox /etc/resolv.conf && rm /etc/resolv.conf.sandbox
    fi
    if [ "$tries" -ge "$DNS_TRIES" ]; then
      log "Can't look up names in the sandbox ($why)." \
        "Check this machine's internet connection, then run the install line again."
      return 1
    fi
    sleep 3
  done
}

# COMMAND...: run it up to three times; before each retry the names are checked again, and uv downloads fewer at once
retry() {
  n=1
  until "$@"; do
    [ "$n" -lt 3 ] || return 1
    log "That failed; trying again in $((n * 15)) seconds..."
    sleep $((n * 15))
    dns_ready || return 1
    export UV_CONCURRENT_DOWNLOADS=4
    n=$((n + 1))
  done
}

# After the downloads: this machine's resolvers again if they answer now (names on its own network resolve only there)
dns_restore() {
  [ -f /etc/resolv.conf.sandbox ] || return 0
  cp /etc/resolv.conf /etc/resolv.conf.fallback
  cp /etc/resolv.conf.sandbox /etc/resolv.conf
  # shellcheck disable=SC2086
  if why="$(resolves $NAMES 2>&1)"; then
    rm -f /etc/resolv.conf.sandbox /etc/resolv.conf.fallback
    log "This machine's DNS answers in the sandbox again; using it"
  else
    cp /etc/resolv.conf.fallback /etc/resolv.conf && rm /etc/resolv.conf.fallback
    log "Looking names up with $DNS_FALLBACK while Lens runs ($why)"
  fi
}
