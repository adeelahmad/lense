#!/bin/sh
# Tests the upgrade agent's services action (lens-upgrade.sh, action=services): each optional profile on and off, with
# a stand-in for docker that writes down what it was asked. Needs only a POSIX shell:
#
#   sh packaging/host/test-services.sh
set -eu

here="$(cd "$(dirname "$0")" && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}
ok() { printf 'ok: %s\n' "$*"; }

inst="$work/lens"
mkdir -p "$inst/.lens-control/inbox" "$work/bin"
printf '#!/bin/sh\necho "docker $*" >>"%s/calls"\n' "$work" >"$work/bin/docker"
chmod +x "$work/bin/docker"
PATH="$work/bin:$PATH"
export PATH
printf 'ACCESS_SECRET_KEY=x\nCOMPOSE_PROFILES=matterbridge\nLENS_GRAFANA_PORT=3005\n' >"$inst/.env"

# ask LINES...: the request, one key=value per argument; then the agent runs once
ask() {
  : >"$inst/.lens-control/inbox/upgrade"
  for kv; do echo "$kv" >>"$inst/.lens-control/inbox/upgrade"; done
  sh "$here/lens-upgrade.sh" "$inst"
  [ ! -e "$inst/.lens-control/inbox/upgrade" ] || fail "the request is still in the inbox"
}
said() { sed -n "s/^$1=//p" "$inst/.lens-control/services-status"; }
profiles() { sed -n 's/^COMPOSE_PROFILES=//p' "$inst/.env"; }
last() { tail -n 1 "$work/calls"; }

# 1. The browser on: its profile joins the others in .env, and only its two containers start
ask id=b1 action=services profile=browser turn=on
[ "$(said id)" = b1 ] && [ "$(said state)" = "done" ] && [ "$(said profile)" = browser ] || fail "status: $(cat "$inst/.lens-control/services-status")"
[ "$(profiles)" = matterbridge,browser ] || fail "COMPOSE_PROFILES is $(profiles)"
grep -qx 'ACCESS_SECRET_KEY=x' "$inst/.env" || fail "the rest of .env went"
case "$(last)" in *"-f docker-compose.prod.yml --profile browser up -d browser browser-proxy") ;; *) fail "ran: $(last)" ;; esac
[ -z "$(said grafana_port)" ] || fail "a browser status names Grafana's port"
ok "the browser starts its own two containers and joins COMPOSE_PROFILES"

# 2. The MCP runner on, then the browser off: each profile comes and goes on its own
ask id=m1 action=services profile=mcp turn=on
[ "$(profiles)" = matterbridge,browser,mcp ] || fail "COMPOSE_PROFILES is $(profiles)"
case "$(last)" in *"--profile mcp up -d mcp") ;; *) fail "ran: $(last)" ;; esac
ask id=b2 action=services profile=browser turn=off
[ "$(said state)" = "done" ] && [ "$(profiles)" = matterbridge,mcp ] || fail "COMPOSE_PROFILES is $(profiles)"
case "$(last)" in *"--profile browser rm -s -f browser browser-proxy") ;; *) fail "ran: $(last)" ;; esac
ok "the MCP runner and the browser are turned on and off apart"

# 3. The code sandbox's gateway on, then off: its three containers (the certificates, the client's key, the gateway)
ask id=s1 action=services profile=sandbox turn=on
[ "$(said state)" = "done" ] && [ "$(said profile)" = sandbox ] || fail "status: $(cat "$inst/.lens-control/services-status")"
[ "$(profiles)" = matterbridge,mcp,sandbox ] || fail "COMPOSE_PROFILES is $(profiles)"
case "$(last)" in *"--profile sandbox up -d openshell-init openshell-key openshell-gateway") ;; *) fail "ran: $(last)" ;; esac
ask id=s2 action=services profile=sandbox turn=off
[ "$(said state)" = "done" ] && [ "$(profiles)" = matterbridge,mcp ] || fail "COMPOSE_PROFILES is $(profiles)"
case "$(last)" in *"--profile sandbox rm -s -f openshell-init openshell-key openshell-gateway") ;; *) fail "ran: $(last)" ;; esac
ok "the code sandbox's gateway starts and stops its three containers"

# 4. Turning on what is on already leaves it in COMPOSE_PROFILES once
ask id=m2 action=services profile=mcp turn=on
[ "$(profiles)" = matterbridge,mcp ] || fail "COMPOSE_PROFILES is $(profiles)"
ok "a profile turned on twice is in COMPOSE_PROFILES once"

# 5. A request from before profiles (monitoring=on) is the monitoring profile, with Grafana's port in the status
ask id=g1 action=services monitoring=on
[ "$(said state)" = "done" ] && [ "$(said profile)" = monitoring ] && [ "$(said grafana_port)" = 3005 ] || fail "status: $(cat "$inst/.lens-control/services-status")"
[ "$(profiles)" = matterbridge,mcp,monitoring ] || fail "COMPOSE_PROFILES is $(profiles)"
case "$(last)" in *"--profile monitoring up -d prometheus node-exporter grafana") ;; *) fail "ran: $(last)" ;; esac
ok "monitoring=on still turns monitoring on"

# 6. With HTTPS on, the HTTPS compose file comes too
echo LENS_HTTPS=1 >>"$inst/.env"
ask id=m3 action=services profile=mcp turn=off
case "$(last)" in *"-f docker-compose.prod.yml -f docker-compose.https.yml --profile mcp rm -s -f mcp") ;; *) fail "ran: $(last)" ;; esac
[ "$(profiles)" = matterbridge,monitoring ] || fail "COMPOSE_PROFILES is $(profiles)"
ok "the HTTPS compose file comes along when HTTPS is on"

# 7. The mesh (Nebula on the primary, docs/mesh.md) starts its one container, and stops it
ask id=n1 action=services profile=mesh turn=on
[ "$(said state)" = "done" ] && [ "$(profiles)" = matterbridge,monitoring,mesh ] || fail "COMPOSE_PROFILES is $(profiles)"
case "$(last)" in *"--profile mesh up -d mesh") ;; *) fail "ran: $(last)" ;; esac
ask id=n2 action=services profile=mesh turn=off
[ "$(profiles)" = matterbridge,monitoring ] || fail "COMPOSE_PROFILES is $(profiles)"
case "$(last)" in *"--profile mesh rm -s -f mesh") ;; *) fail "ran: $(last)" ;; esac
ok "the mesh starts and stops its own container"

# 8. Anything else is refused and runs nothing: a profile it doesn't know, a turn that isn't on or off, a microsandbox
calls="$(wc -l <"$work/calls")"
ask id=x1 action=services profile=matterbridge turn=on
[ "$(said state)" = failed ] || fail "a profile it doesn't know: $(said state)"
case "$(said error)" in *"no optional containers called 'matterbridge'"*) ;; *) fail "it said: $(said error)" ;; esac
ask id=x2 action=services profile='../../etc' turn=on
[ "$(said state)" = failed ] || fail "a path as a profile: $(said state)"
ask id=x3 action=services profile=browser turn=sideways
case "$(said state) $(said error)" in "failed browser is turned on or off") ;; *) fail "it said: $(said error)" ;; esac
echo LENS_BACKEND=microsandbox >>"$inst/.env"
ask id=x4 action=services profile=browser turn=on
case "$(said error)" in *"runs in Docker"*) ;; *) fail "it said: $(said error)" ;; esac
[ "$(wc -l <"$work/calls")" = "$calls" ] || fail "a refused request ran docker: $(last)"
[ "$(profiles)" = matterbridge,monitoring ] || fail "a refused request changed COMPOSE_PROFILES: $(profiles)"
ok "unknown profiles, other turns and microsandbox installs are refused, with nothing run"

# 9. In a microsandbox the MCP runner is turned on and off through .env alone: the sandbox watches COMPOSE_PROFILES
# (microsandbox/mcp.sh), and Docker isn't run
ask id=m4 action=services profile=mcp turn=on
[ "$(said state)" = "done" ] && [ "$(said profile)" = mcp ] || fail "status: $(cat "$inst/.lens-control/services-status")"
[ "$(profiles)" = matterbridge,monitoring,mcp ] || fail "COMPOSE_PROFILES is $(profiles)"
ask id=m5 action=services profile=mcp turn=off
[ "$(said state)" = "done" ] && [ "$(profiles)" = matterbridge,monitoring ] || fail "COMPOSE_PROFILES is $(profiles)"
[ "$(wc -l <"$work/calls")" = "$calls" ] || fail "a microsandbox install ran docker: $(last)"
grep -qx 'LENS_BACKEND=microsandbox' "$inst/.env" || fail "the rest of .env went"
ok "in a microsandbox the MCP runner is turned on and off in .env, with nothing run"
