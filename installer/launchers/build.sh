#!/bin/sh
# Builds the double-click launchers into dist/launchers (the Launchers workflow attaches them to each release):
#   Install-Lens-macOS.zip       Install Lens.app (unzip, then right-click > Open the first time: it isn't notarized)
#   Install-Lens-Windows.cmd     runs install.ps1
#   Install-Lens-Linux.desktop   runs install.sh (allow launching it first)
# Each opens Lens's web installer in the browser.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
out="${1:-dist/launchers}"
mkdir -p "$out"
out="$(cd "$out" && pwd)"
rm -f "$out/Install-Lens-macOS.zip"
# zip keeps the executable bit the app's script needs (-X: no extra file attributes)
(cd "$here" && zip -q -r -X "$out/Install-Lens-macOS.zip" "Install Lens.app")
cp "$here/Install Lens.cmd" "$out/Install-Lens-Windows.cmd"
cp "$here/Install Lens.desktop" "$out/Install-Lens-Linux.desktop"
chmod +x "$out/Install-Lens-Linux.desktop"
ls -l "$out"
