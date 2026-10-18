# uv and uvx in the sandbox (docs/microsandbox.md), sourced by run.sh, which defines log() and fetch(). test-uv.sh tests
# it.
#
# uv comes from PyPI through the image's pip. The backend's virtualenv, which uv made without pip, is first in PATH
# once a build has made it, so its python3 can't install anything ("No module named pip"): an install made before uvx
# was needed has uv and the virtualenv but not uvx, and got stuck at "Getting uv". image_python names the image's own.

# image_python: the Python the image ships, the same with the virtualenv in PATH or not
image_python() {
  python3 -c 'import sys; print(getattr(sys, "_base_executable", "") or sys.executable)'
}

# get_uv BIN: uv and uvx into BIN, unless both are there
get_uv() {
  [ -x "$1/uv" ] && [ -x "$1/uvx" ] && return 0
  log "Getting uv..."
  py="$(image_python)"
  rm -rf /tmp/lens/uv
  "$py" -m pip install -q --no-cache-dir --disable-pip-version-check --root-user-action=ignore --target /tmp/lens/uv "uv>=0.8,<0.9" &&
    cp /tmp/lens/uv/bin/uv /tmp/lens/uv/bin/uvx "$1/" && rm -rf /tmp/lens/uv
}
