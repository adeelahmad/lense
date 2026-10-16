#!/bin/sh
# Tests wipe.sh: removing folders in the volume when one rm -rf stops partway with "Directory not empty", as it can on
# a Mac (containers/libkrun#541), with a stand-in for rm that leaves a file behind. Run it in the image run.sh runs in:
#
#   docker run --rm -v "$PWD:/lens:ro" python:3.12-slim-trixie sh /lens/microsandbox/test-wipe.sh
set -eu

here="$(cd "$(dirname "$0")" && pwd)"
work="$(mktemp -d)"
trap 'command rm -rf "$work"' EXIT

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}
ok() { printf 'ok: %s\n' "$*"; }

# rm, standing in: while $stops is above 0, each rm -rf removes all but the files named held.js, as the sandbox can't
# see those yet, and stops with "Directory not empty"
stops=0
rms=0
rm() {
  rms=$((rms + 1))
  if [ "$stops" -gt 0 ] && [ "$1" = -rf ]; then
    stops=$((stops - 1))
    shift
    for p; do
      [ -d "$p" ] || continue
      find "$p" -type f ! -name held.js -delete
      echo "rm: cannot remove '$p/node_modules/.pnpm/next/dist': Directory not empty" >&2
    done
    return 1
  fi
  command rm "$@"
}
# tree DIR: an earlier build's copy of the web app, with a file the sandbox doesn't see yet
tree() {
  mkdir -p "$1/node_modules/.pnpm/next/dist" "$1/node_modules/.pnpm/date-fns/node_modules/date-fns"
  for i in 1 2 3 4 5 6 7 8; do touch "$1/node_modules/.pnpm/date-fns/node_modules/date-fns/f$i.js"; done
  touch "$1/package.json" "$1/node_modules/.pnpm/next/dist/held.js"
}

OPT="$work/opt"
WIPE_PAUSE=0
mkdir -p "$OPT"
# shellcheck source=SCRIPTDIR/wipe.sh
. "$here/wipe.sh"

# 1. A folder that one rm -rf doesn't get through is removed by trying again, with nothing moved aside
tree "$OPT/web-src"
stops=2 rms=0
wipe "$OPT/web-src" || fail "wipe failed on a folder the third rm removes"
[ ! -e "$OPT/web-src" ] || fail "the folder is still there"
[ "$rms" = 3 ] || fail "rm ran $rms times, not 3"
[ ! -e "$OPT/trash" ] || fail "something was moved aside: $(ls "$OPT/trash")"
ok "a folder rm stops partway in is removed by trying again"

# 2. One it never gets through is moved aside, so its path is free for the build, and wipe says so
tree "$OPT/web-src"
stops=99
out="$(wipe "$OPT/web-src")" || fail "wipe failed on a folder it could move aside"
[ ! -e "$OPT/web-src" ] || fail "the folder's path isn't free"
moved="$(find "$OPT/trash" -name held.js)"
[ -n "$moved" ] || fail "what was left isn't in the trash"
case "$out" in *"Couldn't remove all of $OPT/web-src yet: moved it to $OPT/trash/web-src."*) ;; *) fail "it didn't say so: $out" ;; esac
mkdir -p "$OPT/web-src" && echo '{}' >"$OPT/web-src/package.json" # as the build goes on
ok "a folder rm never gets through is moved aside, freeing its path"

# 3. Two of the same name are moved aside apart
tree "$OPT/backend"
wipe "$OPT/backend" >/dev/null || fail "wipe failed on the backend"
tree "$OPT/backend"
wipe "$OPT/backend" >/dev/null || fail "wipe failed on the backend again"
[ "$(find "$OPT/trash" -maxdepth 1 -name 'backend.*' | wc -l)" = 2 ] || fail "the two backends aren't both in the trash"
ok "folders of the same name are moved aside apart"

# 4. The trash is emptied when rm gets through; when it still doesn't, it's left for next time and nothing fails
stops=99
empty_trash || fail "empty_trash failed while rm stops partway"
[ -n "$(find "$OPT/trash" -name held.js)" ] || fail "the held files went with an rm that stops partway"
stops=0
empty_trash || fail "empty_trash failed"
[ ! -e "$OPT/trash" ] || fail "the trash is still there"
empty_trash || fail "empty_trash failed with no trash"
ok "the trash is emptied when it can be, and is left without failing when it can't"

# 5. A path that isn't there is nothing to do; a link goes, and what it points to stays
rms=0
wipe "$OPT/none" "$OPT/also-none" || fail "wipe failed on paths that aren't there"
[ "$rms" = 0 ] || fail "rm ran for paths that aren't there"
mkdir -p "$work/keep" && touch "$work/keep/file"
ln -s "$work/keep" "$OPT/link"
wipe "$OPT/link" || fail "wipe failed on a link"
[ ! -L "$OPT/link" ] && [ -f "$work/keep/file" ] || fail "the link stayed, or what it points to went"
ln -s "$work/gone" "$OPT/dangling"
wipe "$OPT/dangling" || fail "wipe failed on a link to nothing"
[ ! -L "$OPT/dangling" ] || fail "the link to nothing stayed"
ok "missing paths are skipped, and links go without what they point to"

# 6. When the folder can't be moved aside either, wipe fails and says why, so the build stops there
tree "$OPT/web"
stops=99
mv() {
  echo "mv: cannot move '$1': Permission denied" >&2
  return 1
}
if err="$(wipe "$OPT/web" 2>&1)"; then fail "wipe succeeded on a folder it could neither remove nor move"; fi
unset -f mv
case "$err" in *"Couldn't remove $OPT/web"*) ;; *) fail "it didn't say why: $err" ;; esac
# and under set -e, as in run.sh, that stops the script (a shell of its own: this one's -e is off in conditions)
went="$(OPT="$OPT" WIPE_PAUSE=0 sh -eu -c '
  . "$1"
  rm() { return 1; }
  mv() { return 1; }
  wipe "$OPT/web" 2>/dev/null
  echo went on' sh "$here/wipe.sh")" || true
[ -z "$went" ] || fail "a failed wipe didn't stop the script"
stops=0
wipe "$OPT/web" || fail "wipe failed once rm gets through"
ok "a folder it can neither remove nor move stops the build, with why"

echo "All wipe tests passed"
