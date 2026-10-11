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
sandbox named `lens`, started from the stock `python:3.12-slim-bookworm` image (from Google's or Amazon's mirror of
Docker Hub, which need no sign-in; Docker Hub itself last). Inside it,
[`microsandbox/run.sh`](https://github.com/adeelahmad/lense/blob/main/microsandbox/run.sh) does what the Docker images
do:

1. Waits until the names it downloads from resolve (see [Name lookups](#name-lookups)), then installs the system
   packages, the same ones as the backend image (`LENS_TARGET=lean` leaves out LibreOffice and Chromium).
2. Fetches SurrealDB 3.2.4, rclone, Node 22 and uv.
3. Builds the backend and the web app from the code, read-only at `/lens`. It builds again only when the code
   changes.
4. Runs SurrealDB, the API, a job worker and the web app, and restarts any of them that stops.

The database, the archive, the builds and the downloaded packages are in the named volume `lens-data`, which outlives
the sandbox. A sandbox that is started again boots without running Lens, so Lens always starts in a fresh one:
`~/lens/.lens-sandbox`, which install.sh writes, replaces the sandbox. The first start downloads and builds everything,
which takes several minutes (longer on a Raspberry Pi). Later starts install the packages from the volume and reuse the
build, so they take a minute or two.

The sandbox can reach the internet, your network (mail servers, model servers) and this machine. On this machine,
Ollama or LM Studio are at `host.docker.internal`, the same address as with Docker. Only the web app's port and the
sensor ports are published, on the same addresses Docker would use (`LENS_BIND`, `LENS_SENSOR_BIND`).

On Linux with systemd, the installer adds a `lens-microsandbox` service that runs `.lens-sandbox` when the machine
starts. On a Mac, it adds a LaunchAgent that runs it at sign-in.

### Name lookups

microsandbox answers the sandbox's name lookups itself, from this machine's resolvers. When those don't answer inside
the sandbox (the first build once stopped with uv's `dns error ... No address associated with hostname`), the steps
that download would fail, so the sandbox:

- waits for `deb.debian.org`, `files.pythonhosted.org` and `registry.npmjs.org` to resolve before it downloads;
- after about 15 seconds without an answer, looks names up with public resolvers instead (Cloudflare's 1.1.1.1 and
  Quad9's 9.9.9.9), and says so in its log;
- runs each download step (the system packages, the backend's and the web app's packages) up to three times, with
  fewer downloads at once after a failure;
- when Lens starts, goes back to this machine's resolvers if they answer by then, so names on your own network
  resolve. Otherwise it keeps the public ones until the sandbox is replaced, and logs that.

If nothing answers within a minute or two, it stops with the name that didn't resolve and why. To use other public
resolvers, or none, set `LENS_DNS_FALLBACK` on the install line (`... | LENS_DNS_FALLBACK=8.8.8.8,8.8.4.4 sh`, or
`LENS_DNS_FALLBACK= sh` for none); it is kept in `.env` for later runs.

## Day to day

```bash
msb logs -f lens      # follow the logs
msb stop lens         # stop it; ~/lens/.lens-sandbox starts it again
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
