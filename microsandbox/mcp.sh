# The MCP runner in a microsandbox (docs/assistant.md "The MCP runner", docs/microsandbox.md), sourced by run.sh,
# which defines log(), CODE and OPT. Root only. test-mcp.sh tests it.
#
# With Docker the runner is a container of its own (mcp-runner/, the `mcp` Compose profile). Here it's a process in
# the sandbox as a user of its own, mcp, with the volume closed to it (/data is lens's alone, mode 750), so the servers
# it runs (the npx and uvx packages an admin named) see none of Lens's data, keys or database. Lens's Node and uv are
# given to it through read-only bind mounts, so it needn't see the volume they're kept in. It listens on the sandbox's
# localhost only, and writes its token where Lens reads it.
#
# It runs while COMPOSE_PROFILES in .env names mcp, the same setting as with Docker, which the machine's upgrade agent
# turns from the web app (packaging/host/lens-upgrade.sh: in a microsandbox it changes .env and nothing else).
# mcp_loop reads .env every few seconds (the install folder is mounted read-only, so the change shows at once),
# starts the runner, starts it again if it stopped, and stops it when the profile goes.

MCP_HOME=/var/lib/lens-mcp       # the servers' homes and caches: the sandbox's own disk, like the web app's build
MCP_KEY=/var/lib/lens-mcp-key    # the token the runner writes and Lens reads
MCP_RUNNER=/var/lib/lens-mcp-runner.py
MCP_TOOLS=/opt/lens-mcp          # Node and uv, mounted from the volume
MCP_PORT=8090

# mcp_wanted: whether .env's COMPOSE_PROFILES names mcp, read each time (the agent rewrites .env while Lens runs)
mcp_wanted() {
  sed -n 's/^COMPOSE_PROFILES=//p' "$CODE/.env" 2>/dev/null | head -1 | tr ',' '\n' | grep -qx mcp
}

# mcp_prepare: the user, its folders and a copy of the runner (/lens may be mounted with the host's permissions)
mcp_prepare() {
  id mcp >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin mcp
  mkdir -p "$MCP_HOME" "$MCP_KEY" "$MCP_TOOLS/node" "$MCP_TOOLS/bin"
  chown mcp:mcp "$MCP_HOME" "$MCP_KEY"
  chmod 700 "$MCP_HOME" && chmod 755 "$MCP_KEY"
  cp "$CODE/mcp-runner/runner.py" "$MCP_RUNNER" && chmod 644 "$MCP_RUNNER"
}

# mcp_tools: Node and uv for the runner, as read-only bind mounts (a way to them without a way into the volume)
mcp_tools() {
  for t in node bin; do
    mountpoint -q "$MCP_TOOLS/$t" || mount --bind -o ro "$OPT/$t" "$MCP_TOOLS/$t" || return 1
  done
}

# mcp_run: the runner, as the mcp user with only the environment named here (the test stands in for it)
mcp_run() {
  exec setpriv --reuid mcp --regid mcp --clear-groups -- env -i \
    PATH="$MCP_TOOLS/node/bin:$MCP_TOOLS/bin:/usr/local/bin:/usr/bin:/bin" HOME="$MCP_HOME" LANG=C.UTF-8 \
    LENS_MCP_HOME="$MCP_HOME" LENS_MCP_TOKEN_FILE="$MCP_KEY/token" LENS_MCP_BIND=127.0.0.1 LENS_MCP_PORT="$MCP_PORT" \
    python3 "$MCP_RUNNER"
}

# mcp_loop: the runner on while COMPOSE_PROFILES names mcp, off when it doesn't, until the sandbox stops
mcp_loop() {
  child=""
  trap 'if [ -n "$child" ]; then kill "$child" 2>/dev/null; wait "$child" 2>/dev/null; fi; exit 0' TERM
  while :; do
    if [ -n "$child" ] && ! kill -0 "$child" 2>/dev/null; then
      wait "$child" 2>/dev/null || true
      child=""
      if mcp_wanted; then log "The MCP runner stopped; starting it again in 5 seconds"; fi
    fi
    if mcp_wanted; then
      if [ -z "$child" ]; then
        log "Starting the MCP runner (COMPOSE_PROFILES names mcp)..."
        (cd "$MCP_HOME" && mcp_run) &
        child=$!
      fi
    elif [ -n "$child" ]; then
      log "Stopping the MCP runner (COMPOSE_PROFILES no longer names mcp)"
      kill "$child" 2>/dev/null || true
      wait "$child" 2>/dev/null || true
      child=""
    fi
    sleep 5
  done
}
