# Architecture

Lens is three processes around one database:

```
 browser ──► Next.js (nextjs-frontend) ──server-side, Bearer token──► FastAPI (fastapi_backend) ──► SurrealDB
    │            │  NextAuth session (encrypted cookie)                    ▲
    │            └─ rewrites /api/v1, /iiif, /embed, /reports, /static ────┘
    └── <audio>/<img> load signed media links through the same rewrites
                                                   lens worker ×N ─────────► SurrealDB (job queue)
```

| Piece | What it does |
|---|---|
| **Next.js** (`nextjs-frontend/`) | The web app. Auth.js (NextAuth v5) keeps the session; server components and server actions call the API with the session's access token through the generated, typed client. |
| **FastAPI** (`fastapi_backend/app/`) | The HTTP API under `/api/v1`, IIIF endpoints under `/iiif`, the embeddable player at `/embed/<id>` and stored reports at `/reports/...`. |
| **Workers** (`lens worker`) | Run queued jobs: transcription, diarisation, video analysis, entity extraction, LLM steps and reports. Any number, on any machine that reaches the database; each can be limited to the steps it can run (for example mlx transcription on a Mac). |
| **SurrealDB** | Everything: recordings, transcripts, speakers, the knowledge graph (as graph edges), full-text indexes, jobs, accounts and settings. |

## Backend layout

```
fastapi_backend/
  app/
    main.py              create_app(): middleware, routers; the database opens in the lifespan
    config.py            process settings from the environment (secrets, database, CORS, mail)
    cli.py               the `lens` command: batch steps, imports, users, workers
    core/
      runtime.py         Archive: configuration + database + app settings + background threads
      security.py        access tokens (JWT) and signed media links
      middleware.py      allowed hosts, security headers, IIIF CORS
    api/
      deps.py            Db, Cfg, CurrentUser, Writer, Admin*, Acl (namespace/recording access)
      media.py           signs media links in responses
      streaming.py       byte-range responses for audio and video
      v1/router.py       every /api/v1 router, one module per area in v1/routes/
      iiif.py, pages.py  IIIF protocol endpoints; embed player and reports (HTML)
    schemas/             Pydantic request and response models, one module per area
    domain/              the processing engine: store (SurrealDB), ingest, speakers, analyze,
                         entities, graph, search, video, faces, iiif, metadata, pipelines,
                         templates, llm, chat, batches, jobs, sources, settings, auth, render
  tests/                 api/ (HTTP) and domain/ (engine) tests, pytest
```

### Layers

* **Routers** (`app/api`) handle HTTP only: parse and validate input with Pydantic, check access through `deps`, call the
  domain, sign media links, shape the response. Endpoints are plain `def` functions because the domain layer is
  synchronous; FastAPI runs them in its thread pool.
* **Domain** (`app/domain`) knows nothing about HTTP. Functions take the database handle and the effective
  configuration and return plain data. It raises `ValueError` for bad input and `KeyError` for unknown ids, which
  `deps.domain_errors()` turns into 400 and 404.
* **Store** (`app/domain/store.py`) is the only place that talks to SurrealDB. See [Database](database.md).

### Configuration layers

Process settings (`app/config.py`) come from the environment only: secrets, the database connection, CORS, mail.
Processing settings (engines, thresholds, namespaces) come from built-in defaults, then `archive.yaml`, then settings
saved in the app by admins. See [Configuration](configuration.md).

## Access model

Admins can do everything. Everyone else has a role per namespace: **viewer** (read, listen, search, chat), **editor**
(import, correct, rename and merge speakers, run pipelines) or **owner** (members and namespace settings). A namespace
you have no role in behaves as if it didn't exist: its recordings, search hits, graph nodes and reports all answer 404.
`deps.Access` enforces this on every request. See [Authentication](authentication.md).

## Background work

Imports, pipeline runs and folder scans return at once and queue jobs in SurrealDB. `lens worker` processes claim jobs
atomically, heartbeat while running and hand a job back to the queue when its next step is one they can't run. A job
whose worker stops responding is retried up to `workers.max_attempts`. `GET /api/v1/events` streams job progress as
server-sent events.

In development the API can run workers in-process (`RUN_BACKGROUND=true` or `workers.inline > 0`); the Docker setups
run a separate `worker` service.

## Media

Audio, video, frames and word clouds are loaded by `<audio>`, `<video>` and `<img>` tags, which can't send an
`Authorization` header. The API therefore returns **signed links** (`?exp=…&sig=…`, HMAC over the path and expiry)
in every response that contains media, and only to callers who may read that recording. Media endpoints accept a
signed link, a share link (`?s=…`) or a bearer token. Byte ranges are supported so players can seek.

## Design decisions

* **SurrealDB, not Postgres.** The engine leans on SurrealDB's graph edges (`mentions`, `same_as`), BM25 full-text
  indexes with highlights, schemaless records for evolving pipeline output, and an embedded mode for single-machine
  installs. Moving to Postgres would have meant rewriting every query for little gain. The template's
  SQLAlchemy/Alembic/fastapi-users stack was removed; the schema is defined idempotently in `store.SCHEMA` on start.
* **The API owns identity; NextAuth owns the browser session.** Accounts, passwords (scrypt), roles, API tokens,
  share links and IIIF tokens are all in the API, because IIIF viewers, API clients and workers need them without the
  web app. NextAuth only holds the API's tokens in its encrypted session cookie.
* **No cookies on the API.** Bearer tokens only, so there is no CSRF surface on `/api/v1`. The IIIF authorization
  flow is the one exception, because the spec requires a cookie.
* **Workers out of the web process.** The prototype ran workers as threads inside the server. They now run as their
  own process so heavy transcription can't starve HTTP requests, and so they scale independently.
* **Vercel.** The template deployed the backend to Vercel's serverless functions. That doesn't fit Lens: long-running
  workers, ffmpeg/tesseract, embedded databases and large uploads. The backend deploys as a container; the frontend
  can still go to Vercel. See [Deployment](deployment.md).
