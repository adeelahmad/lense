# What the design needs from the API next

The web app is built against the current API. Where the design shows something the API
can't provide yet, the control is visible but disabled with a reason ("Not available yet"), or the web app works
around it (noted below). No screen shows sample data. This is the list of API work that would light those up,
grouped by area; each item names the smallest endpoint or field that would do.

## Accounts and administration

| Feature | Needed |
|---|---|
| Reindex progress | a job id or status from `POST /admin/reindex` |
| Audit entries with the previous value and the request address | `before` and `ip` on audit entries |
| Health: when source credentials expire; disk split into audio, cache and database | `credentials_expire_at` per source; `disk: {audio, cache, database}` |
| How long face data is kept | a setting such as `video.face_retention_days` |

## Recordings and the library

| Feature | Needed |
|---|---|
| Bulk export as one file | `POST /recordings/export` returning a zip |
| Details: codec, bitrate, loudness, checksum, size; remote source name | media probe fields; source name in the detail |
| Template report links | signed output URLs |
| Recently viewed across devices | `/me/recent` (kept in the browser today) |

## Import

| Feature | Needed |
|---|---|
| Speaker-mapping suggestions from earlier imports | a label-suggestions endpoint |

## Search, chat and the assistant

| Feature | Needed |
|---|---|

## Speakers and graph

| Feature | Needed |
|---|---|
| Voiceprint length, words per minute, shared topics in review; last heard | speaker profile fields |
| Node total before the cap | a count on `/graph` |

## Batch runs and collections

| Feature | Needed |
|---|---|
| "Keep theirs" (skip recordings that already have an output) | a skip-existing option |
| The "Moment" column in results | per-result timestamps |
| Combine with a versioned template; PDF output | template versions and PDF in `/combine` |
| Combine when reading finishes, server-side (today: the browser remembers it) | a `combine` option on the batch |
| Collection owner names | an account name field |
| Resume a cancelled batch; batches per namespace | resume from cancelled; `namespace` on batches |

## Activity, workers and sources

| Feature | Needed |
|---|---|
| Test a source in stages (reach, sign-in, list); OAuth token expiry | staged test results; `expires_at` on sources |
| Per-file scan errors | `/watches/{id}/files?status=error` |
| Which local folders may be watched | done: `storage.local_roots` in `GET /api/v1/setup` (admins) |

## Pipelines, templates and reports

| Feature | Needed |
|---|---|
| Steps, last run and success rate in the pipeline list (today: one request per pipeline) | summary fields on `/pipelines` |
| Dry run; run a specific version; triggers and schedules; Webhook/Notify steps | pipeline run options and step types |
| How many recordings use a template | a usage count |
| Namespace report link; per-report audio mode; share a namespace report | signed `report_url` on `/namespaces`; report options; report share links |

## Sharing and embeds

| Feature | Needed |
|---|---|
| Embed layout and theme | embed parameters |
| Origin check for non-admins (frame ancestors are admin-only settings) | a public "can this origin embed?" check |

## IIIF and metadata

| Feature | Needed |
|---|---|
| IIIF Presentation 4.0 | a version option on manifests and collections |
| Choose layers per recording (today: archive-wide) | `layers` in recording metadata |
| Order a collection by series or by hand | a `series` field and `PUT /namespaces/{ns}/iiif/order` |
| CSV metadata import with a column mapping and dry run | `POST /metadata/import` |
| Publish saved collections as IIIF Collections | `/iiif/collection/{ns}/saved/{id}` and a publish flag |
| Subject lookups (Wikidata, GeoNames, LCSH) | `GET /authorities/search?source=&q=` |
| EBUCore and PBCore records | `/iiif/{rid}/ebucore.xml`, `/iiif/{rid}/pbcore.xml` |
| Import IIIF audio by reference, re-harvest from change feeds, import resources behind IIIF Auth | `audio: "reference"`, a harvest schedule, an Auth 2 client |
