#!/bin/bash
# Dev server plus the watcher that regenerates the API client. Both stop when this script is told to (docker stop
# sends TERM, which a container's first process ignores unless it handles it).
stop() { kill $(jobs -p) 2>/dev/null; wait; }
trap 'stop; exit 0' TERM INT

pnpm run dev &

node watcher.js &

if [ -f /.dockerenv ]; then
    # If either stops, so does the container, rather than looking up with no dev server behind it
    # (wait -n needs bash 4.3; macOS ships 3.2, so outside Docker this waits for both)
    wait -n; status=$?
    stop
    exit $status
fi

wait
