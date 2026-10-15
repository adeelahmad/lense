#!/bin/sh
# Tests programs.sh: LibreOffice and Chromium installed when Lens (the lens user) asks for them, with a stand-in for
# apt that puts a program on PATH instead of installing packages. Run it as root in the image run.sh runs in:
#
#   docker run --rm -v "$PWD:/lens:ro" python:3.12-slim-trixie sh /lens/microsandbox/test-programs.sh
set -eu

here="$(cd "$(dirname "$0")" && pwd)"
work="$(mktemp -d)" && chmod 755 "$work" # the lens user reaches the folder it asks in
loop=""
finish() {
  [ -z "$loop" ] || kill "$loop" 2>/dev/null || true
  rm -rf "$work" /usr/local/bin/soffice /usr/local/bin/chromium
}
trap finish EXIT

log() { printf '[lens] %s\n' "$*" | tee -a "$work/log"; }
fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}
ok() { printf 'ok: %s\n' "$*"; }
# apt, standing in: each program's first package puts it on PATH; it fails while $work/apt-fails is there
apt_install() {
  echo "$*" >>"$work/apt"
  if [ -f "$work/apt-fails" ]; then
    echo "E: Unable to fetch some archives, maybe run apt-get update"
    return 100
  fi
  for p in "$@"; do
    case "$p" in
      libreoffice-writer-nogui) printf '#!/bin/sh\necho LibreOffice 7.4\n' >/usr/local/bin/soffice ;;
      chromium) printf '#!/bin/sh\necho Chromium 1\n' >/usr/local/bin/chromium ;;
    esac
  done
  chmod 755 /usr/local/bin/soffice /usr/local/bin/chromium 2>/dev/null || true
}
as_lens() { setpriv --reuid lens --regid lens --init-groups -- "$@"; }
# wait_for FILE: until it's there, for 10 seconds at most
wait_for() {
  i=0
  until [ -f "$1" ]; do
    i=$((i + 1))
    [ "$i" -le 50 ] || fail "no $1 after 10 seconds; the loop said: $(cat "$work/log")"
    sleep 0.2
  done
}

id lens >/dev/null 2>&1 || useradd --uid 1000 lens
OPT="$work/opt"
PROGRAMS="$OPT/programs"
mkdir -p "$OPT/apt"
# shellcheck source=SCRIPTDIR/programs.sh
. "$here/programs.sh"

# 1. An install from before first use keeps what it had: the programs whose packages are in the volume are asked for
touch "$OPT/apt/chromium_120.0-1_arm64.deb"
programs_prepare
[ "$(cat "$PROGRAMS/wanted")" = chromium ] || fail "wanted isn't just chromium: $(cat "$PROGRAMS/wanted")"
[ -f "$PROGRAMS/ask/chromium.want" ] || fail "chromium isn't asked for at the start"
[ "$(stat -c %U "$PROGRAMS/ask")" = lens ] || fail "the lens user can't ask"
if as_lens touch "$PROGRAMS/got/chromium.ok" 2>/dev/null; then fail "the lens user can answer"; fi
ok "an install from before kept Chromium, and only root answers"

# 2. The loop installs what was asked for at the start
(
  set +e
  programs_loop
) &
loop=$!
wait_for "$PROGRAMS/got/chromium.ok"
[ "$(chromium)" = "Chromium 1" ] || fail "chromium isn't installed"
grep -q "chromium is installed" "$work/log" || fail "it didn't say so"
ok "Chromium installed again at the start"

# 3. Lens asks for LibreOffice: it's installed, kept in wanted, and answered
as_lens touch "$PROGRAMS/ask/libreoffice.want"
wait_for "$PROGRAMS/got/libreoffice.ok"
[ "$(soffice)" = "LibreOffice 7.4" ] || fail "LibreOffice isn't installed"
grep -qx libreoffice "$PROGRAMS/wanted" || fail "libreoffice isn't kept in wanted"
grep -q "^libreoffice-writer-nogui libreoffice-calc-nogui libreoffice-impress-nogui fonts-" "$work/apt" ||
  fail "not LibreOffice's packages: $(tail -1 "$work/apt")"
ok "LibreOffice installed when Lens asked"

# 4. Asked again, an installed one isn't installed again
lines="$(wc -l <"$work/apt")"
as_lens touch "$PROGRAMS/ask/libreoffice.want"
sleep 3
[ ! -e "$PROGRAMS/ask/libreoffice.want" ] || fail "the second ask wasn't taken off the list"
[ "$(wc -l <"$work/apt")" = "$lines" ] || fail "it was installed again"
ok "an installed one is answered at once"

# 5. Any other name, or a link to a file, installs nothing and touches nothing
cp /etc/passwd "$work/passwd"
as_lens touch "$PROGRAMS/ask/ffmpeg.want" "$PROGRAMS/ask/-rf.want" "$PROGRAMS/ask/a b.want"
as_lens ln -s /etc/passwd "$PROGRAMS/ask/passwd.want"
sleep 3
[ -z "$(ls -A "$PROGRAMS/ask")" ] || fail "left in ask: $(ls -A "$PROGRAMS/ask")"
[ "$(ls "$PROGRAMS/got")" = "$(printf 'chromium.log\nchromium.ok\nlibreoffice.log\nlibreoffice.ok')" ] ||
  fail "got has more: $(ls "$PROGRAMS/got")"
[ "$(wc -l <"$work/apt")" = "$lines" ] || fail "something else was installed: $(tail -1 "$work/apt")"
cmp -s /etc/passwd "$work/passwd" || fail "/etc/passwd changed"
ok "other names and links are only taken off the list"

# 6. A failed install says why, and is tried again when asked again
kill "$loop" && loop=""
rm -f "$PROGRAMS/wanted" /usr/local/bin/chromium
rm "$OPT/apt/chromium_120.0-1_arm64.deb"
programs_prepare # a new start: nothing installed, nothing kept
[ ! -s "$PROGRAMS/wanted" ] || fail "wanted isn't empty: $(cat "$PROGRAMS/wanted")"
[ -z "$(ls -A "$PROGRAMS/ask")$(ls -A "$PROGRAMS/got")" ] || fail "the new start has answers from the last one"
(
  set +e
  programs_loop
) &
loop=$!
touch "$work/apt-fails"
as_lens touch "$PROGRAMS/ask/chromium.want"
wait_for "$PROGRAMS/got/chromium.failed"
grep -q "E: Unable to fetch some archives" "$PROGRAMS/got/chromium.failed" || fail "it doesn't say why"
grep -q "Installing chromium failed: E: Unable to fetch some archives" "$work/log" || fail "the log doesn't say why"
rm "$work/apt-fails"
as_lens touch "$PROGRAMS/ask/chromium.want"
wait_for "$PROGRAMS/got/chromium.ok"
[ ! -e "$PROGRAMS/got/chromium.failed" ] || fail "the failure is left after it worked"
ok "a failed install says why and works when asked again"

echo "All program tests passed"
