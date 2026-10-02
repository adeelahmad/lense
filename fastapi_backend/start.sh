#!/bin/bash
# Development server: API with hot reload, plus the watcher that regenerates the OpenAPI schema for the frontend.
# Both stop when this script is told to (docker stop sends TERM; as a container's first process, bash would otherwise
# ignore it and be killed ten seconds later).
stop() { kill $(jobs -p) 2>/dev/null; wait; }
trap 'stop; exit 0' TERM INT

if [ -f /.dockerenv ]; then
    echo "Running in Docker"
    fastapi dev app/main.py --host 0.0.0.0 --port 8000 --reload &
    python watcher.py &
    # If either stops, so does the container, rather than looking up with no API behind it
    # (wait -n needs bash 4.3; macOS ships 3.2, so outside Docker this waits for both)
    wait -n; status=$?
    stop
    exit $status
fi

echo "Running locally with uv"
uv run fastapi dev app/main.py --host 0.0.0.0 --port 8000 --reload &
uv run python watcher.py &

wait
