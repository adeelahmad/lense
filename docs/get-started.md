# Get started

## In one line

```bash
curl -fsSL https://raw.githubusercontent.com/adeelahmad/lense/main/install.sh | sh
```

On Linux or a Mac, this installs Docker if it's missing (get.docker.com on Linux; OrbStack with Homebrew on a Mac,
and Homebrew too if needed), after listing what it will install and asking (only at a terminal; `LENS_YES=1` skips
the question),
gets Lens into `~/lens`, writes the secrets once, builds and starts the stack, and opens the setup page with the setup
code already filled in. On a server without a desktop, Lens is reachable from the network and the link uses the
server's address. Run the same line again to update: the secrets, the database and the archive are kept. Settings go
before `sh`: `LENS_DIR`, `LENS_PORT`, `LENS_PUBLIC` (`1`: from the network, `0`: this machine only), `LENS_TARGET`
(`lean` for the smaller image) and `LENS_REF` (a branch or tag) change the defaults; the top of `install.sh` lists
them. If Lens doesn't answer in time (`LENS_WAIT`, in seconds: 10 minutes with Docker, 30 in a microsandbox), the
installer stops waiting and shows the last lines Lens logged and why.

Where it can (Linux with KVM, or a Mac with Apple silicon), a first install asks whether to run Lens in Docker (the
default) or in a [microsandbox](microsandbox.md) microVM, which needs no Docker; `LENS_BACKEND=microsandbox` chooses
it without asking.

## The web installer

On a first install run at a terminal (on Linux or a Mac with Python 3.8 or later, which nearly all have), the line
above opens an installer in your browser instead of asking anything in the terminal:

1. **This computer**: the system, memory, free disk, whether Docker is there and running, and whether microsandbox can
   run.
2. **How it runs**: Docker or [microsandbox](microsandbox.md), the full or lean edition (lean is picked on a computer
   with less than 4 GB of memory, such as a Raspberry Pi), the folder, the port, and whether other devices on the
   network may reach it. It lists what it will install; when that needs administrator rights, it asks for your
   password here, keeps it in memory for the install and hands it to `sudo` (it is never written down).
3. **AI model**: model servers already running on the computer (Ollama, LM Studio, llama.cpp, vLLM, LocalAI) with their
   models, or a provider (OpenAI, Anthropic, Google Gemini, OpenRouter, Groq, Mistral or any OpenAI-compatible server)
   with its API key; **Test** lists the models. It can be skipped and set in Lens later.
4. **Extras**, all optional: the first namespace, search by meaning (an embeddings server), the typesafe.ai decision
   model's key, cloud transcription and its key, email (SMTP), remote access through a Cloudflare tunnel, anytopdf,
   and telemetry.
5. **Review**, then **Install**: it runs `install.sh` with those answers and shows its progress (and the full output
   under **Details**). If something fails, it says what, and **Try again** runs it again (what's done stays done).
6. **Open Lens** goes to the setup page with the setup code filled in, where you create the admin account with a
   passkey.

The answers that are settings go to Lens as `LENS_SETUP_SETTINGS`, which it saves once at its first start as if they
were made in the app, so they stay editable in Settings (unlike the `LENS_LLM_*` variables, which lock them); the line
is taken out of `.env` again once Lens is running. On a server without a desktop, the installer answers on the
network and the terminal prints its link, which carries a one-time key. Only someone with the link can use it.

`LENS_WIZARD=0` asks in the terminal as before, and `LENS_WIZARD=1` opens the installer for an existing install too
(to change how it runs; its data, secrets and accounts stay). Runs without a terminal (scripts, CI) and runs with
`LENS_YES=1` or `LENS_BACKEND` set never open it.

### Double-click instead

Each release carries launchers that do the same without a terminal; download the one for your system from the
[latest release](https://github.com/adeelahmad/lense/releases/latest):

| System | File | First time |
|---|---|---|
| macOS | `Install-Lens-macOS.zip` (unzip it: **Install Lens.app**) | it isn't notarized yet, so right-click it and choose **Open**, then **Open** again |
| Windows | `Install-Lens-Windows.cmd` | if SmartScreen warns, **More info**, then **Run anyway** |
| Linux | `Install-Lens-Linux.desktop` | right-click it and choose **Allow Launching** (it needs `curl` and Python 3) |

They open the web installer in the browser; if something stops them before it opens (no internet, no Python), they
say so in a window. On a Mac without Apple's Command Line Tools (which bring Python), it asks macOS to install them
first, then you open it again.

### Windows

```powershell
irm https://raw.githubusercontent.com/adeelahmad/lense/main/install.ps1 | iex
```

or the Windows launcher above. It installs Python 3 for your account if it's missing (winget, else python.org's
installer), then opens the same web installer. On Windows Lens runs in Docker Desktop: the installer installs it with
winget if it's missing (Windows asks for permission), turns on WSL when Docker needs it (Windows may then need a
restart; open the installer again afterwards and it carries on), gets the code into `%USERPROFILE%\lens`, writes the
secrets once, builds and starts Lens and opens its setup page. Running it again updates in place. Lens starts again
whenever Docker Desktop does. microsandbox isn't available on Windows. Windows support is new: CI checks it with a
stand-in Docker, not yet with a real Docker Desktop install.

## With Docker and make

You need Docker with Compose, and `make`.

```bash
make run          # build once, run everything: http://localhost:3000
make setup-code   # the first-admin setup code, printed by the API on its first start
```

`make run` builds the images (the full backend image, which reads Office files, web pages and emails too; `make run
LENS_TARGET=lean` for the smaller one), writes a `.env` with fresh secrets if there is none, and starts SurrealDB, the
API, a job worker and the web app with `docker-compose.prod.yml`: the web app is built ahead of time, so every page is
instant. `make stop` stops it; `make logs` follows it. The database and the archive live in Docker volumes and survive
rebuilds.

To work on the code, run the hot-reload stack instead:

```bash
make dev          # docker compose up --build --renew-anon-volumes
```

`make` uses `docker compose`, or the standalone `docker-compose` where that's what is installed (Colima, Podman);
`make dev DOCKER_COMPOSE="podman compose"` chooses another. After a dependency changes (`uv.lock`,
`pnpm-lock.yaml`), `make dev` rebuilds the images and starts from their packages; `make docker-up` starts what's
there without rebuilding.

This starts SurrealDB, the API with hot reload (<http://localhost:8000/docs>), a job worker, the web app
(<http://localhost:3000>) and Mailpit, which catches the emails Lens sends, mostly sign-in links
(<http://localhost:8025>). Pages are compiled the first time they are visited, so the first visit to each takes a few
seconds; both stacks share the same volumes, so `make run` and `make dev` see the same archive (stop one before
starting the other). On a Mac the web app's dev server is much faster run natively than through Docker's file sharing:
keep the rest in Docker and run `cd nextjs-frontend && pnpm install && pnpm dev` with
`API_BASE_URL=http://localhost:8000` in `.env.local`.

On first start the API log prints a setup code, and a link that fills it in (`make setup-code` shows the line):

```
No accounts yet. Create the first admin in the web app with setup code: … (or open http://localhost:3000/setup?code=…, which fills it in)
```
 Open the link (or the web app, and enter the code) and create the admin account. A short wizard then asks for the first
namespace, the model provider, storage, apps and AI, and telemetry. Once a model is set, **Finish with defaults** skips
the rest; change any of it later in Settings ([Configuration](configuration.md#first-run-setup) lists the `.env` values
that answer it instead).

After the first sign-in, a short welcome card points to the two places most people come back for: the assistant, which
you can ask straight from the card, and **Reports**, with the charts of what is in the archive. Asking, opening
Reports, skipping or closing the card all count as seen; **Welcome tour** in the account menu opens it again. On Home,
type a question and press Enter to send it to the assistant ([Chat](assistant.md)). With only one namespace, it is
picked for you everywhere.

Put audio under `./local-audio/podcasts` and `./local-audio/interviews` (or set `AUDIO_DIR`), or import transcripts
from the web app. Namespaces and folders are configured in `fastapi_backend/docker/archive.yaml`.

## Without Docker

You need Python 3.12 with [uv](https://docs.astral.sh/uv/), Node.js 22 with pnpm, and ffmpeg (tesseract for text on
screen in videos).

**Backend**

```bash
cd fastapi_backend && uv sync && cd ..
make start-backend                # API on :8000, and a watcher that regenerates the OpenAPI schema
```

`make start-backend` writes `fastapi_backend/.env` (the embedded database under `data_dir`, workers inside the API
process, the secrets of the root `.env`) and `fastapi_backend/archive.yaml` (from `archive.example.yaml`) when they
are missing; `.env.example` lists everything else you can set. To use the Docker stack's database instead, set
`SURREAL_URL=ws://localhost:8001`.

Transcription engines are optional extras: `uv sync --extra sensevoice --extra voices` (SenseVoice and voice IDs),
`--extra whisper` (faster-whisper), `--extra mlx` (Apple Silicon), `--extra pyannote`.

**Frontend**

```bash
cd nextjs-frontend && pnpm install && cd ..
make start-frontend               # web app on :3000, regenerates the API client when openapi.json changes
```

`make start-frontend` writes `nextjs-frontend/.env.local` (`API_BASE_URL=http://localhost:8000`, the root `.env`'s
`AUTH_SECRET`) when it is missing. `make env` writes every `.env` file at once; none is ever overwritten.

## The `lens` command

The backend installs a `lens` command (run it with `uv run lens …`, or `docker compose exec backend lens …`):

```bash
lens init                                  # write a starter archive.yaml
lens run                                   # scan, transcribe, diarize, analyze, embed, summarize, report
lens import podcasts episode.docx --audio episode.mp3 --speakers "SPEAKER_00=Host A,SPEAKER_01=Host B"
lens users add ana@example.com --name Ana --admin
lens users role ana@example.com podcasts editor
lens worker --steps transcribe,diarize     # a worker that only transcribes (e.g. mlx on a Mac)
lens search "capsid" --ns podcasts
lens search "money worries" --mode semantic   # by meaning (with an embedding model; see configuration.md)
lens embed                                 # index what isn't yet searchable by meaning
lens reindex                               # after changing search.engine or search.stemming
lens migrations                            # database upgrades: run, pending or failed
lens backup                                # back the database up into <data_dir>/backups
```

Docker on macOS can't use the Apple GPU. Keep the database and API in Docker and run a native worker against the same
database (the compose file publishes SurrealDB on `127.0.0.1:8001`):

```bash
SURREAL_URL=ws://127.0.0.1:8001 SURREAL_PASS=root uv run lens worker --steps transcribe,diarize
```

Native workers store Mac paths, so map them for the container: `audio.path_map: {"/Users/you/Audio": "/audio"}`.

## Keeping the frontend client in sync

The API's OpenAPI schema generates the frontend's typed client. `start.sh` in both projects watches for changes; to do
it by hand:

```bash
cd fastapi_backend && uv run python -m commands.generate_openapi_schema   # writes ../nextjs-frontend/openapi.json
cd ../nextjs-frontend && pnpm generate-client
```

CI fails if `openapi.json` is out of date.
