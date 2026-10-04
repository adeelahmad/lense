#!/bin/bash
# Development server: API with hot reload, plus the watcher that regenerates the OpenAPI schema for the frontend.
# Uvicorn, not `fastapi dev`: a reload waits for open connections to close, and the web app keeps its event stream
# (/api/v1/events) open, so without a graceful-shutdown timeout a reload never finishes. Only app/ is watched, not a
# local .venv or the tests.
# Both stop when this script is told to (docker stop sends TERM; as a container's first process, bash would otherwise
# ignore it and be killed ten seconds later).
stop() { kill $(jobs -p) 2>/dev/null; wait; }
trap 'stop; exit 0' TERM INT
UVICORN_ARGS="--host 0.0.0.0 --port 8000 --reload --reload-dir app --timeout-graceful-shutdown 3"

if [ -f /.dockerenv ]; then
    echo "Running in Docker"
    uvicorn app.main:app $UVICORN_ARGS &
    python watcher.py &
    # If either stops, so does the container, rather than looking up with no API behind it
    # (wait -n needs bash 4.3; macOS ships 3.2, so outside Docker this waits for both)
    wait -n; status=$?
    stop
    exit $status
fi

echo "Running locally with uv"
uv run uvicorn app.main:app $UVICORN_ARGS &
uv run python watcher.py &

wait
