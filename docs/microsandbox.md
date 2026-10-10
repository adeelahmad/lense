# microsandbox

Lens can run in a [microsandbox](https://github.com/superradcompany/microsandbox) microVM instead of Docker: one small
virtual machine with its own kernel, and no Docker on the machine. Docker stays the default.

**Status: built, early.** microsandbox itself is beta software.

## Install

Run the one-line install. On a first install where microsandbox can run, it asks which to use:

```
How should Lens run?
  1) Docker (default): a container for each part, the usual way
  2) microsandbox: one lightweight virtual machine, no Docker needed
```

Or choose without the question: `curl -fsSL .../install.sh | LENS_BACKEND=microsandbox sh`. The choice is kept in
`.env` (`LENS_BACKEND`), so running the line again updates the same install. An install that already runs on Docker
stays on Docker, because its database and archive are in Docker volumes.

It needs hardware virtualization:

- **Linux** on x86-64 or 64-bit ARM (a Raspberry Pi 4 or 5 on a 64-bit OS) with `/dev/kvm`. The installer adds you to
  the `kvm` group. `msb doctor` checks the machine.
- **A Mac** with Apple silicon.

## How it runs

microsandbox runs images but can't build them, and its sandboxes can't reach each other by name, so Lens runs as one
sandbox named `lens`, started from the stock `python:3.12-slim-bookworm` image. Inside it,
[`microsandbox/run.sh`](https://github.com/adeelahmad/lense/blob/main/microsandbox/run.sh) does what the Docker images
do:

1. Installs the system packages, the same ones as the backend image (`LENS_TARGET=lean` leaves out LibreOffice and
   Chromium).
2. Fetches SurrealDB 3.2.4, rclone, Node 22 and uv.
3. Builds the backend and the web app from the code, read-only at `/lens`. It builds again only when the code
   changes.
4. Runs SurrealDB, the API, a job worker and the web app, and restarts any of them that stops.

The database, the archive and the builds are in the named volume `lens-data`, which outlives the sandbox. The first
start installs and builds everything, which takes several minutes (longer on a Raspberry Pi). Later starts take
seconds.

The sandbox can reach the internet, your network (mail servers, model servers) and this machine. On this machine,
Ollama or LM Studio are at `host.docker.internal`, the same address as with Docker. Only the web app's port and the
sensor ports are published, on the same addresses Docker would use (`LENS_BIND`, `LENS_SENSOR_BIND`).

On Linux with systemd, the installer adds a `lens-microsandbox` service that starts the sandbox when the machine
starts. On a Mac, it adds a LaunchAgent that starts it at sign-in.

## Day to day

```bash
msb logs -f lens      # follow the logs
msb stop lens         # stop it; msb start lens starts it again
msb exec lens -- sh   # a shell inside it
```

To update, run the install line again.

## Not yet

- **Matterbridge, Fedora and OpenSearch.** The opt-in Compose services don't run in the sandbox yet. Use Docker for
  these.
- **Prebuilt images.** Each machine builds Lens itself, as `install.sh` does with Docker. Published images would make
  the first start quicker.
- **Moving an install.** A Docker install doesn't move into a sandbox yet: its database and archive stay in Docker's
  volumes.
