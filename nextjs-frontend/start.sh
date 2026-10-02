#!/bin/bash

pnpm run dev &

if [ -f /.dockerenv ]; then
    node watcher.js &
    # If either stops, so does the container, rather than looking up with no dev server behind it
    wait -n
    exit
fi

node watcher.js

wait
