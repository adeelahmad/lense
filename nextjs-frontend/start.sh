#!/bin/bash
# Dev server plus the watcher that regenerates the API client. Both stop when this script is told to (docker stop
# sends TERM, which a container's first process ignores unless it handles it).
trap 'kill $(jobs -p) 2>/dev/null; wait; exit 0' TERM INT

pnpm run dev &

node watcher.js &

wait
