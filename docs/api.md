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
PATCH  /api/v1/auth/me
POST   /api/v1/auth/password
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
GET    /api/v1/namespaces/{name}/ip-groups
POST   /api/v1/namespaces/{name}/ip-groups
PATCH  /api/v1/namespaces/{name}/ip-groups/{gid}
DELETE /api/v1/namespaces/{name}/ip-groups/{gid}
```

`/namespaces/{name}/ip-groups` lists a namespace's IP groups ([Access](access.md#ip-groups), owners), with `address`:
your address as the server sees it (`null` when it can't tell; see `server.trusted_proxies`), and on each group `here`
(your address is in it) and `chosen` (how many recordings it opens when it doesn't open `everything`). `POST` with
`{"name", "ranges", "everything"}` adds one: `ranges` are addresses or CIDR ranges, at most 100, none wider than `/8`
(IPv4) or `/16` (IPv6); names are unique in the namespace. `PATCH` changes any of them and `DELETE` removes the group.
All three answer with the list and are audited as `namespace.ip_group.create`, `.update` and `.delete`.

## recordings

```
GET    /api/v1/recordings
GET    /api/v1/recordings/tags
POST   /api/v1/recordings/tags
GET    /api/v1/recordings/{rid}
PATCH  /api/v1/recordings/{rid}
DELETE /api/v1/recordings/{rid}
POST   /api/v1/recordings/{rid}/move
GET    /api/v1/recordings/{rid}/access
PUT    /api/v1/recordings/{rid}/access
GET    /api/v1/recordings/{rid}/permissions
POST   /api/v1/recordings/{rid}/permissions
DELETE /api/v1/recordings/{rid}/permissions/{account}
GET    /api/v1/recordings/{rid}/requests
POST   /api/v1/recordings/{rid}/requests/{account}/approve
POST   /api/v1/recordings/{rid}/requests/{account}/decline
GET    /api/v1/recordings/{rid}/ip-groups
PUT    /api/v1/recordings/{rid}/ip-groups/{gid}
DELETE /api/v1/recordings/{rid}/ip-groups/{gid}
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
| `access` | `public`, `restricted` or `private`: the recording's own setting, else its namespace's default |
| `featured` | `true`: only featured recordings; `false`: only the others |
| `tag` | tags, ignoring case |
| `sort` | `date`, `title`, `duration`, `speakers`, `status` or `importance`; `-` in front for descending (default `-date`). Recordings without the value come last either way |
| `limit`, `offset` | one page (default 500 rows, at most 1000) |

The body is the page's rows; the `X-Total-Count` header says how many recordings match on all pages.

Recordings carry `tags`. `PATCH /recordings/{rid}` with `{"tags": [...]}` replaces a recording's tags (editors; at most
20, 40 characters each; whitespace is collapsed and repeats are dropped, ignoring case). `POST /recordings/tags` with
`{"recordings", "add", "remove"}` changes the tags of several at once (editors of each one's namespace) and says how
many changed. `GET /recordings/tags` (`ns` for one namespace) lists the tags in use with how many recordings have each,
most used first.

`PATCH /recordings/{rid}` with `{"title": …}` renames a recording (editors; whitespace is collapsed, at most 200
characters). It is audited as `recording.rename`; the recording's report page follows the new title and is rebuilt,
and a published recording shows up as an Update in the IIIF change feed.

`DELETE /recordings/{rid}` deletes a recording (owners). Everything Lens made from it goes: its transcript and analysis
(segments, speakers' turns, chapters, entity mentions, terms, edits), video shots, text on screen, face tracks and
frames, outputs, reports and exports, shares, permissions, requests for access, and its place in IP groups, fixed
collections, chat scopes and batch runs that haven't started it. Speakers and faces only it had are removed unless
someone named them. Its waiting jobs are cancelled; while a job is running on it the answer is 409, and a worker that
finds its recording gone stops. The media file stays where it is: scans skip the same path and the same file elsewhere,
and watched folders skip the same remote file, even when it changes; importing it on purpose (the Import dialog,
`lens import`, a IIIF manifest) brings it back. A public recording shows up as a Delete in the IIIF change feed. It is
audited as `recording.delete` with its title, namespace and path.

`POST /recordings/{rid}/move` with `{"namespace", "rediarize", "revoke_shares"}` moves a recording to another namespace
(owners of its namespace, editors of the new one). It keeps its transcript, media, frames, outputs, the people given
permission on it and its share links (`revoke_shares`: they stop working). Its IIIF manifest stays as it was: what it
had from its old namespace (default access and open parts, the metadata profile's defaults) is pinned on it wherever
the new namespace would change it, kept in its metadata history and listed in `pinned`. Speakers and faces are matched
by name in the new namespace, or start there with this recording's voice and face (`rediarize`, audio only: identified
again from their voices instead); unnamed ones left with nothing in the old namespace are removed. Its entity mentions
and per-mention corrections start over: analysis runs again in the new namespace (`job`). Its report pages and exports
move to the new namespace's folders. The old namespace's scans and watched folders don't import the file again, and
its IP groups no longer open the recording. 409 when the new namespace already has the same file or a job is running
on it. Audited as `recording.move`.

`GET /recordings/{rid}/access` says who may see a recording (members): `access` (`public`, `restricted` or
`private`), `open` (the parts a public recording opens to everyone: `media`, `transcript`, `index`), `featured`,
`inherited` (the access comes from the namespace) and the namespace's `default`. `PUT` changes any of them (owners);
`null` follows the namespace again. Changes are audited as `recording.access`, kept in the metadata history, and
announced in the IIIF change feed. [Access](access.md) explains what each level lets people do.

`/recordings/{rid}/permissions` lists the people given permission on the recording (owners). `POST` with `{"email":
…}` gives it to the account with that address (404 when there is none, 400 for members of the namespace, who already
see all of it); `DELETE …/{account}` takes it away. Both answer with the list and are audited as
`recording.permission.give` and `recording.permission.take`. `/recordings/{rid}/requests` lists the requests for access
(owners); approving one gives permission, declining lets the person ask again (audited as
`recording.request.approve` and `recording.request.decline`).

`/recordings/{rid}/ip-groups` lists the namespace's IP groups with `opens`: whether visitors from their addresses see
all of the recording (owners). `PUT …/{gid}` opens the recording to a group that opens chosen recordings (400 for one
that opens `everything` already), `DELETE …/{gid}` closes it again (404 when it wasn't open). Both answer with the list
and are audited as `recording.ip_group.open` and `recording.ip_group.close`.

## imports

```
POST   /api/v1/import
POST   /api/v1/import/preview
```

`POST /import` queues the namespace's pipeline after the import, or the saved pipeline named by `pipeline` (any of
`GET /pipelines`; 400 for one that doesn't exist, before anything is saved).

`POST /api/v1/import/source {source, paths, namespace, pipeline?}` imports chosen files of a storage source now,
rather than watching their folder (admins, like sources; up to 500 paths, as `GET /sources/{sid}/browse` lists them).
Audio and video stay on the source and run the namespace's pipeline, or `pipeline`; transcripts are imported. Each
path gets a result: `queued` (with `recording` and `job`), `already` (the namespace has it from this source),
`skipped` (a folder, or not audio, video or a transcript) or `error`. Choosing a file whose recording was deleted
brings it back. Audited as `import.source`.

### Uploads

Audio and video go up in pieces, so a dropped connection costs one piece, not the file.

```
GET    /api/v1/uploads/limits
POST   /api/v1/uploads
GET    /api/v1/uploads
GET    /api/v1/uploads/{uid}
PUT    /api/v1/uploads/{uid}?offset=
DELETE /api/v1/uploads/{uid}
```

`GET /limits` tells anyone signed in what can be uploaded: the types (`uploads.extensions`), the largest file
(`uploads.max_mb`), the piece size the web app sends (`uploads.chunk_mb`) and the largest transcript file for
`POST /import` (`server.max_upload_mb`).

`POST /uploads {namespace, filename, size, title?, modified?, pipeline?, recording?}` starts one (editors of the namespace; admins
may name a new namespace, created when the upload finishes). With `recording`, a transcript-only recording, the file
becomes that recording's audio instead of a recording of its own (editors of its namespace; `namespace` can then be left
out; 409 when it has audio already). [Processing](processing.md#importing-transcripts) says what runs then. `pipeline`
runs instead of the namespace's once a new recording is made (not with `recording`). The name loses any folders and its extension is lowercased; `modified`
(the file's last-modified time in milliseconds) dates the recording when its name doesn't. 400 for a type not in
`uploads.extensions`, 413 over `uploads.max_mb`, 507 when the server's disk can't hold it with 512 MB to spare.

`PUT /uploads/{uid}?offset=` sends the next piece as the raw request body (`application/octet-stream`), starting at
`offset`: the upload's `offset` is how many bytes have arrived. Pieces stream to a partial file under
`data_dir/uploads/.partial`; a piece that breaks off is dropped whole, so after a dropped connection the client gets the
upload and sends from its `offset`. 409 when `offset` isn't where the upload has got to, or while another piece of it
is arriving. The piece with the last byte moves the file to `data_dir/uploads/<namespace>/<uid>/<name>`, makes it a
recording and queues the namespace's pipeline (or attaches it, adding the steps that need media to the recording's job):
the answer has `state: "done"`, `recording` and `job`. When the namespace
already has the same file (uploaded, scanned or imported before), no second recording is made: `duplicate` is true and
`recording` is that one, which gets its media back if it had lost it. Audited as `upload` (with `attached` in the
detail). Sending the last piece again
answers the same.

`GET /uploads` lists your unfinished uploads, newest first: choosing the same file for the same namespace again carries
on from its `offset`. Only the person uploading (and admins) can see, send to or `DELETE` an upload; `DELETE` throws away
what arrived. An upload nobody has sent a piece to for `uploads.expire_hours` is removed with its partial file. Deleting a
recording later leaves its uploaded file in place, like any other media file.

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
GET    /api/v1/jobs/{jid}/log
POST   /api/v1/jobs/steps/{step}
POST   /api/v1/jobs/{jid}/cancel
POST   /api/v1/jobs/{jid}/retry
GET    /api/v1/workers
POST   /api/v1/workers/{name}/pause
POST   /api/v1/workers/{name}/drain
POST   /api/v1/workers/{name}/resume
GET    /api/v1/events
```

`GET /workers` (admins) lists every worker that has checked in: the steps it runs, its `heartbeat_at` (every 30 s,
every 15 s while it runs a job), `current` job, `paused`/`draining` (with `paused_by`, `paused_at`), its machine's
`load` (1-minute load average per CPU) and `cpus`, and `steps_last_hour` (steps it finished, done or skipped). `pause`
stops it taking new jobs; `drain` also hands the job it has back to the queue after the step it's on (it stays paused);
`resume` undoes either. Each returns the worker, is for admins, and is audited as `worker.<action>`.

`GET /jobs` lists the newest jobs (`limit`, up to 2,000) in the namespaces you can read, filtered by `status`
(comma separated), `recording`, `batch` (a batch run's jobs) and `namespace` (404 when you can't read it). `counts`
gives the jobs of each status and `namespaces` the jobs in each namespace, both over `namespace` and `batch` but any
status, so a client can show its filters' counts while one is applied.

Every job names its recording (`title`) and the pipeline it runs with the version it pinned (`pipeline`:
`{id, version, name}`; `{name: "Standard"}` for the standard steps, null when the steps were chosen directly).
`GET /jobs/{jid}` adds how each step went, `step_runs` (one per step, null until it runs): `started_at`, `finished_at`,
`seconds`, `outcome` (`running`, `done`, `skipped`, `failed`), `note` (its last message, why it skipped, or its error),
`worker`, `log_from`/`log_to` (its lines of the whole log) and `outputs` (`[{key, template, version, model}]`, saved as
`outputs.<key>`). It also adds `estimates`, how long each step usually takes on this recording in seconds (from the
last 25 runs of that step or template; null with none), and while the job is queued or running `eta_seconds`.

A run keeps its whole log (up to 100,000 lines); the job itself carries its last 200 lines (`log`) and how many there
are (`log_total`). `GET /jobs/{jid}/log?after=&limit=` pages through it: `{start, lines, total, more}`, up to 5,000
lines a page (runs from before whole logs were kept have their last 200 lines).

`GET /events` streams `job` events: one per job change in the namespaces you can read, from `since`. With
`logs=<jid>` it follows that one job, and each batch of its new lines comes as a `log` event
`{job, start, lines}`: `start` numbers the first line (the first event carries up to the last 200), so a client can
tell an overlap from a gap and fetch the gap with `GET /jobs/{jid}/log`. Lines go out about every 2 seconds.

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

`GET /sources/{sid}/browse?path=` lists a folder of a source; each file says which recordings it is already
(`imported: [{recording, namespace}]`).

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

## public

What visitors see ([Access](access.md)). No sign-in is needed; send a token and people with permission see more, and
so do visitors whose address is in an IP group.

```
GET    /api/v1/public/home
GET    /api/v1/public/collections/{name}
GET    /api/v1/public/search
GET    /api/v1/public/recordings/{rid}
POST   /api/v1/public/recordings/{rid}/request
GET    /api/v1/access-requests
```

`GET /public/home` lists the featured public recordings (for everyone, members too) and the collections the caller
sees anything in, with how many of their recordings they see (`network` names the IP group that opens all of one to
the caller's address). `GET /public/collections/{name}` is a collection's page:
the namespace's description and the recordings the caller sees there, newest first (`limit`, `offset`, and `total` on
all pages). Each recording is a card with the caller's `view` of it; `locked` cards carry the title only. A poster
frame comes only with media the caller may play. A collection with nothing for the caller answers 404.

`GET /public/search?q=` (`limit`, `offset`) finds the recordings the caller sees by their title, and by the lines of
the transcripts they may read (the same query syntax as `/search`: words, "phrases", OR). Title matches come first;
each result is a card with up to three matching `hits` (`t0` and an HTML-escaped `snippet` with `<mark>`). Restricted
recordings and closed transcripts match on the title only.

`GET /public/recordings/{rid}` is a recording's public page as the caller may see it. `view` says how:

* `full`: the caller has permission, so all of it: a role in its namespace (admins have every role; `member` is
  true), permission given on the recording (`granted`), or an address in an IP group that opens it (`network`, the
  group's name).
* `public`: a public recording, for everyone else: its description (the metadata IIIF publishes) and only its open
  parts: `media` (a signed link to the audio or video, with its waveform), `transcript` (speakers and lines, and the
  transcript files to download when the transcript is open to everyone) and `chapters` (the index).
* `locked`: a restricted recording, for someone signed in without permission: its title and namespace only.

Parts the caller can't use are `null` and listed in `closed`. For someone signed in without permission,
`can_request` says whether they may ask for access (something is closed to them) and `request` is their latest request.
`POST …/request` with an optional `message` asks the owners (400 when the caller already sees all of it, or when all of
it is open to everyone); asking again replaces the request. `GET /access-requests` lists the requests waiting for an
answer in the namespaces the caller owns. Recordings the caller may not see at all (restricted ones
to visitors who aren't signed in, private ones to anyone without permission) answer 404, as missing ones do.
