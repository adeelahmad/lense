# Removing folders in the volume, sourced by run.sh, which defines OPT. test-wipe.sh tests it.
#
# On a Mac the volume is a folder on the Mac that microsandbox shares into the sandbox (virtio-fs), and the two sides
# can briefly disagree about what a folder holds: the sandbox lists it empty while the Mac still has a file in it,
# which shows up again a moment later (containers/libkrun#541). So one rm -rf of a big tree, such as an earlier
# build's node_modules, can stop partway with "Directory not empty". wipe tries again, and moves what is still left
# out of the way, so the folder's path is free for what comes next; a later start empties what was moved.

# wipe PATH...: removes each, trying again for a few seconds, then moving what's left into $OPT/trash. Fails only when
# it can't do either.
wipe() {
  for path; do
    wipe_tries=0
    while [ -e "$path" ] || [ -L "$path" ]; do
      rm -rf "$path" 2>/dev/null && break
      wipe_tries=$((wipe_tries + 1))
      if [ "$wipe_tries" -ge "${WIPE_TRIES:-5}" ]; then
        if mkdir -p "$OPT/trash" && aside="$(mktemp -d "$OPT/trash/$(basename "$path").XXXXXX")" &&
          mv "$path" "$aside/"; then
          printf "Couldn't remove all of %s yet: moved it to %s, which a later start removes\n" "$path" "$aside"
          break
        fi
        printf "Couldn't remove %s: %s\n" "$path" "$(rm -rf "$path" 2>&1 | tail -n 1)" >&2
        return 1
      fi
      sleep $((wipe_tries * ${WIPE_PAUSE:-1}))
    done
  done
}

# empty_trash: removes what wipe moved aside, if it goes now; what doesn't is left for the next time
empty_trash() {
  [ -d "$OPT/trash" ] || return 0
  wipe_tries=0
  until rm -rf "$OPT/trash" 2>/dev/null; do
    wipe_tries=$((wipe_tries + 1))
    [ "$wipe_tries" -lt "${WIPE_TRIES:-5}" ] || return 0
    sleep $((wipe_tries * ${WIPE_PAUSE:-1}))
  done
}
