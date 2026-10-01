# Get started

## With Docker (recommended)

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
cp fastapi_backend/.env.example fastapi_backend/.env          # set ACCESS_SECRET_KEY
cp nextjs-frontend/.env.example nextjs-frontend/.env.local    # set AUTH_SECRET
make dev                                                      # docker compose up --build
```

This starts SurrealDB, the API with hot reload (<http://localhost:8000/docs>), a job worker, the web app
(<http://localhost:3000>) and MailHog for password-reset emails (<http://localhost:8025>). Pages are compiled the
first time they are visited, so the first visit to each takes a few seconds; both stacks share the same volumes, so
`make run` and `make dev` see the same archive (stop one before starting the other). On a Mac the web app's dev server
is much faster run natively than through Docker's file sharing: keep the rest in Docker and run `cd nextjs-frontend &&
pnpm install && pnpm dev` with `API_BASE_URL=http://localhost:8000` in `.env.local`.

On first start the API log prints a setup code:

```
No accounts yet. Create the first admin in the web app with setup code: …
```

Open the web app, choose **Set up**, and create the admin account with it.

Put audio under `./local-audio/podcasts` and `./local-audio/interviews` (or set `AUDIO_DIR`), or import transcripts
from the web app. Namespaces and folders are configured in `fastapi_backend/docker/archive.yaml`.

## Without Docker

You need Python 3.12 with [uv](https://docs.astral.sh/uv/), Node.js 22 with pnpm, and ffmpeg (tesseract for text on
screen in videos).

**Backend**

```bash
cd fastapi_backend
uv sync
cp .env.example .env              # set ACCESS_SECRET_KEY; unset SURREAL_URL to use the embedded database
cp archive.example.yaml archive.yaml
echo "RUN_BACKGROUND=true" >> .env  # embedded database: workers must run inside the API process
./start.sh                        # API on :8000, and a watcher that regenerates the OpenAPI schema
```

Transcription engines are optional extras: `uv sync --extra sensevoice --extra voices` (SenseVoice and voice IDs),
`--extra whisper` (faster-whisper), `--extra mlx` (Apple Silicon), `--extra pyannote`.

**Frontend**

```bash
cd nextjs-frontend
pnpm install
cp .env.example .env.local        # API_BASE_URL=http://localhost:8000, AUTH_SECRET=...
./start.sh                        # web app on :3000, regenerates the API client when openapi.json changes
```

## The `lens` command

The backend installs a `lens` command (run it with `uv run lens …`, or `docker compose exec backend lens …`):

```bash
lens init                                  # write a starter archive.yaml
lens run                                   # scan, transcribe, diarize, analyze, summarize, report
lens import podcasts episode.docx --audio episode.mp3 --speakers "SPEAKER_00=Host A,SPEAKER_01=Host B"
lens users add ana@example.com --name Ana --admin
lens users role ana@example.com podcasts editor
lens worker --steps transcribe,diarize     # a worker that only transcribes (e.g. mlx on a Mac)
lens search "capsid" --ns podcasts
lens reindex                               # after changing search.stemming
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
