#!/bin/sh
# Runs Matterbridge with the config Lens writes into the shared volume, and restarts it when the config changes or it
# stops (a WhatsApp QR code that timed out, a network that dropped it). What it prints goes to matterbridge.log there
# too, where Lens reads the WhatsApp QR code from.
set -u
DIR="${LENS_MATTERBRIDGE_DIR:-/matterbridge}"
CONF="$DIR/matterbridge.toml"
LOG="$DIR/matterbridge.log"
# Lens's server runs as another user: it writes the config here, and reads the log
chmod 1777 "$DIR"
umask 022

touch "$LOG"
tail -n 0 -F "$LOG" 2>/dev/null &  # docker logs shows it too

sum() { [ -f "$CONF" ] && cksum < "$CONF" || echo none; }
pid=""
stop() { [ -n "$pid" ] && kill "$pid" 2>/dev/null && wait "$pid" 2>/dev/null; pid=""; }
trap 'stop; exit 0' TERM INT

echo "Waiting for Lens to write $CONF (Settings → Chat rooms → Run Matterbridge here)."
running=""
while :; do
  now="$(sum)"
  if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
    wait "$pid" 2>/dev/null
    pid=""
    echo "Matterbridge stopped; starting it again." >> "$LOG"
    sleep 5
  fi
  if [ "$now" != "$running" ] && [ -n "$pid" ]; then
    echo "The config changed; restarting Matterbridge." >> "$LOG"
    stop
  fi
  if [ -z "$pid" ] && [ "$now" != none ]; then
    : > "$LOG"
    matterbridge -conf "$CONF" >> "$LOG" 2>&1 &
    pid=$!
    running="$now"
  fi
  sleep 2
done
