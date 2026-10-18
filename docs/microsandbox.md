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

- **Linux** on x86-64 or 64-bit ARM (a Raspberry Pi 4 or 5 on a 64-bit OS) with `/dev/kvm`. The installer loads KVM
  if it isn't and adds you to the `kvm` group, so there's nothing to do first. Until your next sign-in makes the group
  count, it runs microsandbox with the group (`sg kvm`) and lets you use `/dev/kvm` itself (with `setfacl`, installing
  the `acl` package if it's missing). CI installs on a runner whose user can't use KVM yet, and runs the line twice.
  `msb doctor` checks the machine.
- **A Mac** with Apple silicon.

## How it runs

microsandbox runs images but can't build them, and its sandboxes can't reach each other by name, so Lens runs as one
sandbox named `lens`, started from the stock `python:3.12-slim-trixie` image (from Google's or Amazon's mirror of
Docker Hub, which need no sign-in; Docker Hub itself last). Inside it,
[`microsandbox/run.sh`](https://github.com/adeelahmad/lense/blob/main/microsandbox/run.sh) does what the Docker images
do:

1. Waits until the names it downloads from resolve (see [Name lookups](#name-lookups)), then installs the system
   packages, the same ones as the backend image except LibreOffice and Chromium, which the full edition installs the
   first time Lens needs one (see [LibreOffice and Chromium](#libreoffice-and-chromium)).
2. Fetches SurrealDB 3.2.4, rclone, Node 22 and uv.
3. Builds the backend and the web app from the code, read-only at `/lens`. It builds again only when the code
   changes. The web app is built on the sandbox's own disk, and only what it builds is kept in the volume.
4. Runs SurrealDB, the API, a job worker and the web app, and restarts any of them that stops.
5. Runs the [MCP runner](assistant.md#the-mcp-runner) while `COMPOSE_PROFILES` in `.env` names `mcp`, as the switch
   in the web app sets it: a process as its own user, `mcp`, with the volume closed to it, on the sandbox's localhost.

The database, the archive, the builds and the downloaded packages are in the named volume `lens-data`, which outlives
the sandbox. A sandbox that is started again boots without running Lens, so Lens always starts in a fresh one:
`~/lens/.lens-sandbox`, which install.sh writes, replaces the sandbox. The first start downloads and builds everything,
which takes several minutes (longer on a Raspberry Pi). Later starts install the packages from the volume and reuse the
build, so they take a minute or two.

The database opens only with the keys in the `.env` of the install that made it, and the volume outlives the install
folder. So a first install (one with no `.env` yet) that finds `lens-data` holding an earlier install's database, say
after `~/lens` was deleted, leaves it as it is and starts on a volume of its own: `lens-data-2`, or the next free name.
`.env` keeps that name (`LENS_VOLUME`), so running the line again uses the same one. Put the earlier `.env` back in the
folder to use the earlier data again, or remove it with `msb volume rm lens-data`.

An install made before that check (0.8.0 or earlier) may already run on such a volume: its API stops at every start
with `SURREAL_USER/SURREAL_PASS don't match the user this database was created with`. Each start, the sandbox checks
that the keys in `.env` open the database. When they don't, it stops and says `Another install's database`, and the
install line moves Lens to a volume of its own the same way, keeping `.env`'s keys. So to recover, run the install line
again. The earlier volume is left as it is, for the `.env` that made it.

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

### LibreOffice and Chromium

LibreOffice (Word, PowerPoint and spreadsheet files) and Chromium (web pages, and text, Markdown and emails printed to
PDF) are the heavy part of the full edition, so a sandbox starts without them. The first document or web page that
needs one waits while the sandbox installs it (a few minutes the first time; its job's log says
`installing LibreOffice, the first time it's needed`), then Lens reads it as usual. From then on each start installs it
again in the background, from the packages kept in `lens-data`. Uploads and imports are accepted before it's there,
and Settings → Components says it's installed the first time it's needed. PDFs, images and links to PDFs need neither.

Lens runs as an ordinary user in the sandbox, so it asks for a program by name and the start script, which runs as
root, installs it: only these two, each a fixed list of Debian packages
([`microsandbox/programs.sh`](https://github.com/adeelahmad/lense/blob/main/microsandbox/programs.sh)). An install
from before keeps the ones it already had. A lean install (`LENS_TARGET=lean`) never installs them.

## Day to day

```bash
msb logs -f lens      # follow the logs
msb stop lens         # stop it; ~/lens/.lens-sandbox starts it again
msb exec lens -- sh   # a shell inside it
```

To update, run the install line again. It stops the running sandbox first (giving the database up to a minute to
close cleanly) and waits for its port to be free, then starts Lens in a new one. While it waits for the web app, it
shows the steps the sandbox logs (installing packages, building, starting the database). If the sandbox stops, a step
fails, a part of Lens keeps stopping (run.sh starts it again three times), or the web app doesn't answer within 30
minutes (`LENS_WAIT`, in seconds), it stops waiting and shows the last lines the sandbox logged and why, for example a
port that another program holds. The web installer shows those lines under the reason.

An update after one that stopped partway, say while it was building, builds again. On a Mac the volume is a folder on
the Mac that the sandbox reads through virtio-fs, which can briefly list a folder as empty while the Mac still has a
file in it, so one `rm -rf` of an earlier build doesn't always get through (`Directory not empty`). The sandbox tries
again for a few seconds, then moves what is left to `/data/.lens/trash` and goes on; the next build removes it
([`microsandbox/wipe.sh`](https://github.com/adeelahmad/lense/blob/main/microsandbox/wipe.sh)).

## Not yet

- **Matterbridge, Fedora, OpenSearch, the browser, the code sandbox's gateway and monitoring.** The opt-in Compose
  services other than the MCP runner don't run in the sandbox yet. Use Docker for these.
- **Prebuilt images.** Each machine builds Lens itself, as `install.sh` does with Docker. Published images would make
  the first start quicker.
- **Moving an install.** A Docker install doesn't move into a sandbox yet: its database and archive stay in Docker's
  volumes.
