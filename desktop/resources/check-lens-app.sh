#!/bin/sh
# Checks lens-app.sh on Linux, without a Mac: the bundled microsandbox lands where install.microsandbox.dev puts it,
# and the app says what to do when Lens isn't there. `pnpm exec nx test desktop` runs it, and so does CI.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
app="$here/lens-app.sh"

# Puts a microsandbox bundle where install.microsandbox.dev would
s="$(mktemp -d)"
trap 'rm -rf "$s"' EXIT
mkdir -p "$s/b"
printf '#!/bin/sh\necho msb 0.0.0\n' >"$s/b/msb"
chmod +x "$s/b/msb"
echo krun >"$s/b/libkrunfw.5.dylib"
tar -czf "$s/m.tar.gz" -C "$s/b" .
HOME="$s/home" LENS_MSB_BUNDLE="$s/m.tar.gz" sh "$app" setup-msb
test "$("$s/home/.local/bin/msb")" = "msb 0.0.0"
test "$(readlink "$s/home/.microsandbox/lib/libkrunfw.dylib")" = libkrunfw.5.dylib

# Says what to do when Lens isn't there or doesn't start
mkdir -p "$s/h/lens"
printf 'LENS_PORT=3999\nLENS_BACKEND=microsandbox\n' >"$s/h/lens/.env"
test "$(HOME="$s/h" sh "$app" url)" = "http://localhost:3999/"
out="$(HOME="$s/h" sh "$app" start || true)"
echo "$out"
echo "$out" | grep -q '^@error .*\.lens-sandbox is missing'
echo "lens-app.sh checks passed"
