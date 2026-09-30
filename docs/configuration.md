# Configuration

Lens has three layers of configuration, each for a different kind of setting.

| Layer | Where | What |
|---|---|---|
| **Environment** | `fastapi_backend/.env`, container env | secrets, database connection, CORS, mail, where `archive.yaml` is |
| **archive.yaml** | `$ARCHIVE_CONFIG` (see `archive.example.yaml`) | namespaces and their folders, processing defaults, bootstrap-only paths |
| **Settings in the app** | `GET/PUT /api/v1/settings/<section>` (admins), stored in SurrealDB | everything that can change at runtime |

Precedence for processing settings: built-in defaults (`app/domain/store.py`, `DEFAULTS`), then `archive.yaml`, then
the app, then the few environment overrides below. Secrets saved in the app (such as an LLM API key) are encrypted
with AES-GCM and are write-only: the API reports whether one is set, never its value.

## Backend environment

| Variable | Default | |
|---|---|---|
| `ACCESS_SECRET_KEY` | **required** | signs access tokens and media links (`openssl rand -hex 32`) |
| `ACCESS_TOKEN_EXPIRE_SECONDS` | `900` | access token lifetime |
| `MEDIA_URL_EXPIRE_SECONDS` | `21600` | signed media link lifetime |
| `PASSWORD_RESET_EXPIRE_MINUTES` | `60` | |
| `SURREAL_URL` | embedded | `ws://host:8000` for a server; see [Database](database.md) |
| `SURREAL_USER` / `SURREAL_PASS` / `SURREAL_NS` / `SURREAL_DB` / `SURREAL_POOL_SIZE` | `root` / `root` / `archive` / `main` / `8` | |
| `ARCHIVE_SECRET_KEY` | generated in `data_dir/secret.key` | encrypts stored credentials; keep it stable and backed up |
| `ARCHIVE_CONFIG` | `archive.yaml` | processing configuration file |
| `ARCHIVE_ALLOWED_HOSTS` | | break-glass override of allowed Host headers if a bad setting locks everyone out |
| `RUN_BACKGROUND` | follows `workers.inline` | run job workers and folder watching inside the API process |
| `LENS_SETUP_CODE` | random | fix the first-run setup code (automation) |
| `FRONTEND_URL` | `http://localhost:3000` | links in emails |
| `CORS_ORIGINS` | `["http://localhost:3000"]` | origins allowed to call the API from a browser |
| `OPENAPI_URL` | `/openapi.json` | `""` disables `/docs` and the schema |
| `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_FROM`, `MAIL_STARTTLS`, `MAIL_SSL_TLS`, `USE_CREDENTIALS`, `VALIDATE_CERTS` | | SMTP for password reset; without `MAIL_SERVER` reset links are logged |

## Frontend environment

| Variable | |
|---|---|
| `API_BASE_URL` | where the Next.js server reaches the API (`http://localhost:8000`, `http://backend:8000` in Docker) |
| `AUTH_SECRET` | encrypts the NextAuth session cookie (`npx auth secret`) |
| `AUTH_URL` | the public URL of the web app, when it can't be inferred |
| `AUTH_TRUST_HOST` | `true` behind a proxy or in Docker |

## archive.yaml

Start from `fastapi_backend/archive.example.yaml`, which documents every key. The parts that can only be set there:

* `namespaces`: folder trees scanned by `lens scan`, and whether each joins the shared knowledge graph.
* `data_dir`: reports, frames, model caches, locks and (embedded mode) the database.
* `sources.rclone` and `sources.local_roots`: which binary is run for remote storage and which local folders may be
  watched. These are bootstrap-only on purpose, so the web app can't choose what runs or open up the server's disk.
* `video.yunet_model` / `video.sface_model`: face model files.

## Settings in the app

Admins can change transcription, diarisation, voice-ID thresholds, analysis, LLM provider and key, graph, search,
reports, workers, IIIF, the assistant, video, and server options (embed frame ancestors, upload limit, allowed hosts,
session length). The API refuses an allowed-host list that leaves out the address you are using.

## Security notes

* The API checks the `Host` header against `server.allowed_hosts` (stops DNS rebinding) and sends a strict
  Content-Security-Policy; only `/embed/<id>` can be framed, and only by `server.embed_frame_ancestors`.
* Imports check the extension, cap the size (`server.max_upload_mb`), and are parsed in a temporary directory; nothing
  uploaded is executed.
* Put everything behind HTTPS before exposing it beyond one machine. IIIF authorization requires it.
