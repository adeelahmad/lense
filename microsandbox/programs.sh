# LibreOffice and Chromium in the full edition, installed the first time Lens needs one (docs/microsandbox.md),
# sourced by run.sh, which defines log(), apt_install(), wipe() and PROGRAMS. Root only. test-programs.sh tests it.
#
# Lens runs as the lens user, so it asks for one by name: it writes ask/NAME.want in $PROGRAMS
# (convert.fetch_program). programs_loop, as root, installs that name's packages (from the volume after the first
# time) and answers in got/, which only root writes: NAME.ok, or NAME.failed with why. Any other name is only taken
# off the list. What was installed once is listed in `wanted` and asked for again at each start, since each start is
# a new sandbox disk; it's installed again in the background, from the packages kept in the volume.

FONTS="fonts-dejavu-core fonts-liberation2 fonts-noto-core fonts-noto-cjk"

# program_packages NAME: its packages, or nothing for a name that isn't one of them
program_packages() {
  case "$1" in
    libreoffice) echo "libreoffice-writer-nogui libreoffice-calc-nogui libreoffice-impress-nogui $FONTS" ;;
    chromium) echo "chromium $FONTS" ;;
  esac
}

# programs_prepare: nothing is installed on this new disk yet; what was installed before is asked for again
programs_prepare() {
  wipe "$PROGRAMS/ask" "$PROGRAMS/got"
  mkdir -p "$PROGRAMS/ask" "$PROGRAMS/got"
  chown lens:lens "$PROGRAMS/ask" && chmod 700 "$PROGRAMS/ask"
  # an install from before first use: the ones the full edition installed at every start, whose packages are here
  if [ ! -f "$PROGRAMS/wanted" ]; then
    for name in libreoffice chromium; do
      first="$(program_packages "$name" | cut -d' ' -f1)"
      if ls "$OPT/apt/$first"_*.deb >/dev/null 2>&1; then echo "$name"; fi
    done >"$PROGRAMS/wanted"
  fi
  while read -r name; do
    if [ -n "$(program_packages "$name")" ]; then touch "$PROGRAMS/ask/$name.want"; fi
  done <"$PROGRAMS/wanted"
}

# programs_loop: answers what Lens asks for, until the sandbox stops
programs_loop() {
  while :; do
    for want in "$PROGRAMS"/ask/*.want; do
      [ -e "$want" ] || [ -L "$want" ] || continue
      name="$(basename "$want" .want)"
      pkgs="$(program_packages "$name")"
      if [ -n "$pkgs" ] && [ ! -f "$PROGRAMS/got/$name.ok" ]; then
        rm -f "$PROGRAMS/got/$name.failed"
        log "Installing $name..."
        # shellcheck disable=SC2086
        if apt_install $pkgs >"$PROGRAMS/got/$name.log" 2>&1; then
          grep -qx "$name" "$PROGRAMS/wanted" || echo "$name" >>"$PROGRAMS/wanted"
          touch "$PROGRAMS/got/$name.ok"
          log "$name is installed"
        else
          tail -n 3 "$PROGRAMS/got/$name.log" >"$PROGRAMS/got/$name.failed"
          log "Installing $name failed: $(tail -n 1 "$PROGRAMS/got/$name.log")"
        fi
      fi
      rm -f "$want"
    done
    sleep 2
  done
}
