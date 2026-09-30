# What the design needs from the API next

The web app implements the Lens Archive design against the current API. Where the design shows something the API
can't provide yet, the control is visible but disabled with a reason ("Not available yet"), or the web app works
around it (noted below). No screen shows sample data. This is the list of API work that would light those up,
grouped by area; each item names the smallest endpoint or field that would do.

## Accounts and administration

| Feature | Needed |
|---|---|
| Change your own password (today: admins via the user admin, others by reset email) | `POST /auth/password {current_password, new_password}` |
| Change your own name | `PATCH /auth/me {name}` |
| Reindex progress | a job id or status from `POST /admin/reindex` |
| Audit entries with the previous value and the request address | `before` and `ip` on audit entries |
| Health: when source credentials expire; disk split into audio, cache and database | `credentials_expire_at` per source; `disk: {audio, cache, database}` |
| How long face data is kept | a setting such as `video.face_retention_days` |

## Recordings and the library

| Feature | Needed |
|---|---|
| Saved views | `/views` (list, create, delete) |
| "Edited by me" tab | `?edited_by=me` |
| Source and language filters | `path`/`remote` and `language` in `RecordingSummary` |
| Bulk export as one file | `POST /recordings/export` returning a zip |
| Attach audio to a transcript; upload audio/video | a resumable upload endpoint (tus or S3 multipart) |
| Notes | notes create/read/update/delete |
| Split or merge turns; word highlighting | split/merge segment endpoints; word timings in the player |
| Timestamps on summary items | times in the summary schema |
| Details: codec, bitrate, loudness, checksum, size; remote source name | media probe fields; source name in the detail |
| Template report links | signed output URLs |
| Recently viewed across devices | `/me/recent` (kept in the browser today) |

## Import

| Feature | Needed |
|---|---|
| OCR for scanned PDFs | an OCR option on `/import/preview` |
| Import chosen files from a source | `POST /import/source {source, paths}`, plus "already imported" per file |
| Pick the pipeline to run after import | `pipeline` on `ImportRequest` (today: the namespace default) |
| Speaker-mapping suggestions from earlier imports | a label-suggestions endpoint |

## Search, chat and the assistant

| Feature | Needed |
|---|---|
| Facet counts over all results (today: the first 200 hits) | facets on `/search` |
| Saved searches (today: saved as filter collections, which can't hold emotion or recording filters) | a saved-search store |
| "Try …" prefix suggestions | a term-completion endpoint |
| Know whether a model is configured, and which, without being an admin | `GET /chats/capabilities` |
| Model picker | provider model list and a per-chat model |
| Scope a chat by collection (today: expanded to at most 200 recording ids) | collection ids in the chat scope |
| Tool steps and source-check verdicts when reopening a chat | return them in `GET /chats/{id}` |
| Stop an answer | a cancel endpoint |
| Retry with another model | a model override on retry |

## Speakers and graph

| Feature | Needed |
|---|---|
| "Not the same" for a suggested pair; dismiss cross-namespace suggestions | `POST /speakers/{id}/not-same`; a score and dismiss for cross-namespace matches |
| Unlink speakers | an unlink endpoint |
| Voiceprint length, words per minute, shared topics in review; last heard | speaker profile fields |
| Who merged and how many recordings moved | merge history fields |
| Node total before the cap | a count on `/graph` |

## Batch runs and collections

| Feature | Needed |
|---|---|
| Jobs of one batch (today: filtered from the 500 most recent) | `GET /jobs?batch=` |
| "Keep theirs" (skip recordings that already have an output) | a skip-existing option |
| The "Moment" column in results | per-result timestamps |
| Combine with a versioned template; PDF output | template versions and PDF in `/combine` |
| Combine when reading finishes, server-side (today: the browser remembers it) | a `combine` option on the batch |
| Collection owner names | an account name field |
| Resume a cancelled batch; batches per namespace | resume from cancelled; `namespace` on batches |

## Activity, workers and sources

| Feature | Needed |
|---|---|
| Pipeline and version per run; recording title on a job | fields on `/jobs` and `/jobs/{id}` |
| Per-step progress, ETA, inputs/outputs and times | per-step data on jobs |
| Full logs (capped at 200 lines) | `GET /jobs/{id}/log` and log lines on the event stream |
| Filter jobs by namespace with counts | `/jobs?namespace=` |
| Heartbeat during long steps (a busy worker can look silent) | heartbeat from inside long steps |
| Pause, resume, drain a worker; worker load | `/workers/{name}/pause` etc. |
| Test a source in stages (reach, sign-in, list); OAuth token expiry | staged test results; `expires_at` on sources |
| Per-file scan errors | `/watches/{id}/files?status=error` |
| Which local folders may be watched | list `sources.local_roots` for admins |

## Pipelines, templates and reports

| Feature | Needed |
|---|---|
| Steps, last run and success rate in the pipeline list (today: one request per pipeline) | summary fields on `/pipelines` |
| Dry run; run a specific version; triggers and schedules; Webhook/Notify steps | pipeline run options and step types |
| How many recordings use a template | a usage count |
| Namespace report link; per-report audio mode; share a namespace report | signed `report_url` on `/namespaces`; report options; report share links |
| Report stats for a date range (today: computed in the browser, top speakers all-time) | `/namespaces/{name}/stats?from&to` |

## Sharing and embeds

| Feature | Needed |
|---|---|
| Revoke one link; play counts; where it's embedded | per-link endpoints |
| Short `/s/…` links | a short-link route |
| Embed layout and theme | embed parameters |
| Origin check for non-admins (frame ancestors are admin-only settings) | a public "can this origin embed?" check |
| Expired or revoked links show a page, not JSON 401 | a neutral 410 page on `/embed` |

## IIIF and metadata

| Feature | Needed |
|---|---|
| IIIF Presentation 4.0 | a version option on manifests and collections |
| Choose layers per recording (today: archive-wide) | `layers` in recording metadata |
| Order a collection by series or by hand | a `series` field and `PUT /namespaces/{ns}/iiif/order` |
| CSV metadata import with a column mapping and dry run | `POST /metadata/import` |
| Publish saved collections as IIIF Collections | `/iiif/collection/{ns}/saved/{id}` and a publish flag |
| Custom fields in a namespace profile | field definitions in `PUT /namespaces/{ns}/metadata` |
| Subject lookups (Wikidata, GeoNames, LCSH) | `GET /authorities/search?source=&q=` |
| EBUCore and PBCore records | `/iiif/{rid}/ebucore.xml`, `/iiif/{rid}/pbcore.xml` |
| Import IIIF audio by reference, re-harvest from change feeds, import resources behind IIIF Auth | `audio: "reference"`, a harvest schedule, an Auth 2 client |
