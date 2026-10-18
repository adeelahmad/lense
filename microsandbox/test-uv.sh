#!/bin/sh
# Tests uv.sh: getting uv and uvx with the backend's virtualenv, which has no pip, first in PATH, as on a sandbox whose
# volume an earlier install built in. Run it in the image run.sh runs in:
#
#   docker run --rm -v "$PWD:/lens:ro" python:3.12-slim-trixie sh /lens/microsandbox/test-uv.sh
set -eu

here="$(cd "$(dirname "$0")" && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}
ok() { printf 'ok: %s\n' "$*"; }
logged=""
log() { logged="$logged$*;"; }

# shellcheck source=SCRIPTDIR/uv.sh
. "$here/uv.sh"
mkdir -p /tmp/lens "$work/bin"

# the virtualenv a build made, without pip, first in PATH as run.sh puts it
python3 -m venv --without-pip "$work/venv"
PATH="$work/bin:$work/venv/bin:$PATH"
[ "$(command -v python3)" = "$work/venv/bin/python3" ] || fail "the virtualenv's python3 isn't first in PATH"
if python3 -m pip --version >/dev/null 2>&1; then fail "the virtualenv has pip, so this tests nothing"; fi
"$(image_python)" -m pip --version >/dev/null || fail "image_python names $(image_python), which has no pip either"
ok "image_python names a Python with pip ($(image_python)) with the virtualenv first in PATH"

# an install from before uvx was needed: uv, no uvx
printf '#!/bin/sh\n' >"$work/bin/uv" && chmod +x "$work/bin/uv"
get_uv "$work/bin" || fail "get_uv failed with the virtualenv first in PATH"
"$work/bin/uv" --version >/dev/null || fail "uv doesn't run"
"$work/bin/uvx" --version >/dev/null || fail "uvx doesn't run"
case "$logged" in *"Getting uv..."*) ;; *) fail "get_uv didn't say what it was doing" ;; esac
ok "get_uv got uv and uvx where uvx was missing"

logged=""
get_uv "$work/bin" || fail "get_uv failed with both there"
[ -z "$logged" ] || fail "get_uv downloaded again with both there"
ok "get_uv keeps uv and uvx that are there"
