# API

The API lives under `/api/v1`. Interactive docs with every request and response schema are at `/docs` (Swagger UI) and `/redoc` on the backend; the schema at `/openapi.json` generates the frontend's typed client (one class per tag, e.g. `Recordings.getRecording`).

Authenticate with `Authorization: Bearer <token>`: an access token from `POST /api/v1/auth/login`, or an API token (`la_…`). See [Authentication](authentication.md). Errors are JSON `{"detail": …}`; validation errors answer 422. Namespaces you can't read answer 404.

Outside `/api/v1`: IIIF resources under `/iiif/…` ([IIIF](iiif.md)), the embeddable player at `/embed/<id>` and stored reports at `/reports/<namespace>/…`.

## auth

```
GET    /api/v1/auth/status
POST   /api/v1/auth/setup
POST   /api/v1/auth/login
POST   /api/v1/auth/refresh
POST   /api/v1/auth/logout
GET    /api/v1/auth/me
POST   /api/v1/auth/password/forgot
POST   /api/v1/auth/password/reset
```

## tokens

```
GET    /api/v1/tokens
POST   /api/v1/tokens
DELETE /api/v1/tokens/{token_id}
```

## users

```
GET    /api/v1/users
POST   /api/v1/users
PATCH  /api/v1/users/{uid}
GET    /api/v1/namespaces/{name}/members
PUT    /api/v1/namespaces/{name}/members
```

## admin

```
GET    /api/v1/settings
PUT    /api/v1/settings/{section}
POST   /api/v1/settings/llm/test
GET    /api/v1/audit
GET    /api/v1/admin/health
POST   /api/v1/admin/reindex
```

## namespaces

```
GET    /api/v1/namespaces
POST   /api/v1/namespaces
PATCH  /api/v1/namespaces/{name}
GET    /api/v1/namespaces/{name}/wordcloud.svg
```

## recordings

```
GET    /api/v1/recordings
GET    /api/v1/recordings/{rid}
GET    /api/v1/recordings/{rid}/player
GET    /api/v1/recordings/{rid}/embed-link
GET    /api/v1/recordings/{rid}/audio
GET    /api/v1/recordings/{rid}/wordcloud.svg
POST   /api/v1/recordings/{rid}/reprocess
POST   /api/v1/recordings/{rid}/share
DELETE /api/v1/recordings/{rid}/share
GET    /api/v1/recordings/{rid}/shares
GET    /api/v1/recordings/{rid}/export.{fmt}
PATCH  /api/v1/recordings/{rid}/segments/{idx}
GET    /api/v1/recordings/{rid}/edits
GET    /api/v1/recordings/{rid}/outputs
```

`GET /recordings` filters, sorts and pages on the server, over every recording you can read. Filters combine with AND;
repeat a parameter that takes several values (`?status=new&status=error`) to match any of them.

| Parameter | |
|---|---|
| `ns` | one namespace (default: every namespace you can read) |
| `q` | words that must all appear in the title, the namespace's name or a speaker's name |
| `status` | `new`, `transcribed`, `diarized`, `analyzed`, `error`, and two job states: `processing` (a job is queued or running) and `failed` (the latest job failed) |
| `attention` | `true`: only recordings that need a person (errored, latest job failed, or a voice match waiting for review) |
| `processing` | `true`: only recordings with a job queued or running |
| `speaker` | speaker ids |
| `from`, `to` | the recording date, `YYYY-MM-DD`, both days included; recordings without a date don't match |
| `min_duration`, `max_duration` | seconds: at least `min_duration`, shorter than `max_duration` |
| `media` | `audio`, `video` or `transcript` (no media) |
| `sort` | `date`, `title`, `duration`, `speakers`, `status` or `importance`; `-` in front for descending (default `-date`). Recordings without the value come last either way |
| `limit`, `offset` | one page (default 500 rows, at most 1000) |

The body is the page's rows; the `X-Total-Count` header says how many recordings match on all pages.

## imports

```
POST   /api/v1/import
POST   /api/v1/import/preview
```

## search

```
GET    /api/v1/search
GET    /api/v1/graph
GET    /api/v1/mentions
```

## speakers

```
GET    /api/v1/speakers
GET    /api/v1/speakers/{sid}/recordings
POST   /api/v1/speakers/{sid}
POST   /api/v1/speakers/{sid}/merge
POST   /api/v1/merges/{mid}/undo
POST   /api/v1/speakers/{sid}/link
```

## entities

```
GET    /api/v1/entities
GET    /api/v1/entities/types
GET    /api/v1/entities/suggestions
GET    /api/v1/entities/merges
GET    /api/v1/entities/timeline
POST   /api/v1/entities/retype
POST   /api/v1/entities/merge
POST   /api/v1/entities/merges/{mid}/undo
POST   /api/v1/entities/not-same
GET    /api/v1/entities/{eid}
GET    /api/v1/entities/{eid}/mentions
GET    /api/v1/entities/{eid}/connections
POST   /api/v1/entities/{eid}/rename
POST   /api/v1/entities/{eid}/hide
POST   /api/v1/entities/{eid}/link
DELETE /api/v1/entities/{eid}/link/{other}
POST   /api/v1/mentions/{mention}/move
GET    /api/v1/graph/explore
GET    /api/v1/graph/path
```

## metadata

```
GET    /api/v1/recordings/{rid}/metadata
PUT    /api/v1/recordings/{rid}/metadata
GET    /api/v1/recordings/{rid}/metadata/history
POST   /api/v1/metadata/edits/{eid}/revert
GET    /api/v1/namespaces/{name}/metadata
PUT    /api/v1/namespaces/{name}/metadata
POST   /api/v1/metadata/bulk
```

## video

```
GET    /api/v1/recordings/{rid}/media
GET    /api/v1/recordings/{rid}/frames/{name}
PATCH  /api/v1/recordings/{rid}/ocr/{span}
DELETE /api/v1/recordings/{rid}/faces/{track}
GET    /api/v1/namespaces/{name}/faces
DELETE /api/v1/namespaces/{name}/faces
PUT    /api/v1/namespaces/{name}/faces/mode
POST   /api/v1/faces/merges/{mid}/undo
POST   /api/v1/faces/{fid}
DELETE /api/v1/faces/{fid}
POST   /api/v1/faces/{fid}/merge
POST   /api/v1/faces/{fid}/speaker
POST   /api/v1/faces/{fid}/dismiss
```

## iiif

```
GET    /api/v1/recordings/{rid}/iiif
GET    /api/v1/recordings/{rid}/content-state
POST   /api/v1/import/iiif/preview
POST   /api/v1/import/iiif
```

## jobs

```
GET    /api/v1/jobs
POST   /api/v1/jobs
GET    /api/v1/jobs/{jid}
POST   /api/v1/jobs/steps/{step}
POST   /api/v1/jobs/{jid}/cancel
POST   /api/v1/jobs/{jid}/retry
GET    /api/v1/workers
GET    /api/v1/events
```

## sources

```
GET    /api/v1/sources/backends
GET    /api/v1/sources
POST   /api/v1/sources
PATCH  /api/v1/sources/{sid}
DELETE /api/v1/sources/{sid}
POST   /api/v1/sources/{sid}/test
GET    /api/v1/sources/{sid}/browse
GET    /api/v1/watches
POST   /api/v1/watches
POST   /api/v1/watches/preview
PATCH  /api/v1/watches/{wid}
DELETE /api/v1/watches/{wid}
POST   /api/v1/watches/{wid}/scan
```

## templates

```
GET    /api/v1/templates
POST   /api/v1/templates
POST   /api/v1/templates/preview
GET    /api/v1/templates/{tid}
POST   /api/v1/templates/{tid}/versions
GET    /api/v1/templates/{tid}/diff
```

## pipelines

```
GET    /api/v1/pipelines
POST   /api/v1/pipelines
GET    /api/v1/pipelines/{pid}
POST   /api/v1/pipelines/{pid}/versions
POST   /api/v1/pipelines/{pid}/run
```

## chats

```
GET    /api/v1/chats
POST   /api/v1/chats
GET    /api/v1/chats/{cid}
PATCH  /api/v1/chats/{cid}
DELETE /api/v1/chats/{cid}
POST   /api/v1/chats/{cid}/messages
POST   /api/v1/chats/{cid}/messages/{mid}/check
GET    /api/v1/approvals
POST   /api/v1/approvals/{aid}
```

## collections

```
GET    /api/v1/collections
POST   /api/v1/collections
GET    /api/v1/collections/{cid}
PATCH  /api/v1/collections/{cid}
DELETE /api/v1/collections/{cid}
```

## batches

```
POST   /api/v1/batches/estimate
GET    /api/v1/batches
POST   /api/v1/batches
GET    /api/v1/batches/{bid}
POST   /api/v1/batches/{bid}/combine
POST   /api/v1/batches/{bid}/{action}
GET    /api/v1/batches/{bid}/results
GET    /api/v1/batches/{bid}/results.{fmt}
```
