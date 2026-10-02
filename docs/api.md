# API

The API lives under `/api/v1`. Interactive docs with every request and response schema are at `/docs` (Swagger UI) and `/redoc` on the backend; the schema at `/openapi.json` generates the frontend's typed client (one class per tag, e.g. `Recordings.getRecording`).

Authenticate with `Authorization: Bearer <token>`: an access token from `POST /api/v1/auth/login`, an API token (`la_…`), or the access token of an app someone gave access to through OAuth (`lo_…`). See [Authentication](authentication.md). Errors are JSON `{"detail": …}`; validation errors answer 422. Namespaces you can't read answer 404.

Outside `/api/v1`: IIIF resources under `/iiif/…` ([IIIF](iiif.md)), the embeddable player at `/embed/<id>` (and at a share link's short address, `/s/<code>`) and stored reports at `/reports/<namespace>/…`.

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

`GET /auth/me` gives your account, your `roles` (namespace → role) and `partial`: the namespaces you have no role in
but see some collections of ([Access](access.md#collection-roles)).

## tokens

```
GET    /api/v1/tokens
GET    /api/v1/tokens/limits
POST   /api/v1/tokens
DELETE /api/v1/tokens/{token_id}
GET    /api/v1/admin/tokens
DELETE /api/v1/admin/tokens/{token_id}
```

`POST /tokens {name, scope, days?}` makes an API key (while signed in). `days` defaults to `tokens.default_days`; more
than `tokens.max_days`, or `0` (never expires) when `tokens.never_expire` is off, is a 400. `GET /tokens/limits` says
`{default_days, max_days, never_expire}`. `/admin/tokens` lists everyone's keys with their owner's `email` (admins), and
`DELETE /admin/tokens/{token_id}` revokes any of them; revoking is audited as `token.revoke`.

## oauth

```
GET    /.well-known/oauth-authorization-server
GET    /.well-known/oauth-protected-resource
POST   /api/v1/oauth/register
GET    /api/v1/oauth/authorize
POST   /api/v1/oauth/authorize
POST   /api/v1/oauth/token
POST   /api/v1/oauth/revoke
GET    /api/v1/oauth/grants
DELETE /api/v1/oauth/grants/{grant_id}
```

Lens is an OAuth 2.1 authorization server for API and MCP clients ([Authentication](authentication.md#oauth)). The MCP
server itself is at `/mcp`, outside `/api/v1` ([MCP server](mcp.md)).

* `POST /oauth/register {redirect_uris, client_name?, client_uri?, token_endpoint_auth_method?}` (RFC 7591, no sign-in,
  201) registers an app: `client_id` (`lc_…`), and a `client_secret` (`ls_…`, shown once) when it asked for
  `client_secret_post` or `client_secret_basic` instead of `none`. Redirect addresses are https, `http://localhost`
  or `127.0.0.1` (any port), or the app's own scheme; other metadata is ignored. Errors are
  `{error, error_description}`; more than 8 apps from one address in 15 minutes is a 429.
* `GET /oauth/authorize?client_id=&redirect_uri=&code_challenge=&code_challenge_method=S256&scope=` (signed in, as a
  person) answers what the consent page shows: `{client: {id, name, uri}, redirect_uri, scope, granted}`, `scope`
  being `read` or `read write` and `granted` what this app was given before. A request that can't be answered (an
  unknown app, an address it didn't register, no S256 challenge) is a 400 with the reason.
* `POST /oauth/authorize {client_id, redirect_uri, code_challenge, code_challenge_method, scope?, state?, resource?,
  approve, grant?}` answers it: `{redirect_to}`, the app's address with `code` and `state`, or with
  `error=access_denied`. `grant: "read"` gives less than was asked for.
* `POST /oauth/token` (form-encoded) with `grant_type=authorization_code`, `code`, `redirect_uri`, `code_verifier` and
  `client_id` (and `client_secret`, or HTTP Basic, for apps that have one), or with `grant_type=refresh_token` and
  `refresh_token`: `{access_token, token_type: "Bearer", expires_in, refresh_token, scope}`. Errors are
  `{error, error_description}` (`invalid_grant`, `invalid_client` with 401, `unsupported_grant_type`).
* `POST /oauth/revoke` (form-encoded: `token`, `client_id`) ends the access the token belongs to; it answers
  `{ok: true}` whether or not the token was known.
* `GET /oauth/grants` lists the apps you gave access to (`id`, `client`, `name`, `uri`, `scope`, `created_at`,
  `last_used_at`, `expires_at`); `DELETE /oauth/grants/{grant_id}` takes one's access away.

`GET /auth/me` says `via: "oauth"` for an app's token, with `scope` `read` or `write`.

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
POST   /api/v1/settings/embeddings/test
GET    /api/v1/audit
GET    /api/v1/admin/health
POST   /api/v1/admin/reindex
GET    /api/v1/admin/semantic
POST   /api/v1/admin/semantic/index
```

`POST /settings/embeddings/test` embeds one sentence with the configured embedding model (`dimension`, `ms`, or the
`error`). `GET /admin/semantic` says whether search by meaning is set up, with which model, and how many recordings
and passages are indexed with it; `POST /admin/semantic/index?limit=500` queues the embed step for recordings not yet
indexed with it (`remaining` when more are waiting).

## namespaces

```
GET    /api/v1/namespaces
POST   /api/v1/namespaces
PATCH  /api/v1/namespaces/{name}
GET    /api/v1/namespaces/{name}/wordcloud.svg
GET    /api/v1/namespaces/{name}/stats
GET    /api/v1/namespaces/{name}/ip-groups
POST   /api/v1/namespaces/{name}/ip-groups
PATCH  /api/v1/namespaces/{name}/ip-groups/{gid}
DELETE /api/v1/namespaces/{name}/ip-groups/{gid}
GET    /api/v1/namespaces/{name}/notifications
POST   /api/v1/namespaces/{name}/notifications
PATCH  /api/v1/namespaces/{name}/notifications/{tid}
DELETE /api/v1/namespaces/{name}/notifications/{tid}
POST   /api/v1/namespaces/{name}/notifications/{tid}/test
POST   /api/v1/namespaces/{name}/notifications/{tid}/secret
GET    /api/v1/namespaces/{name}/notifications/{tid}/deliveries
GET    /api/v1/namespaces/{name}/collections
POST   /api/v1/namespaces/{name}/collections
GET    /api/v1/namespaces/{name}/collections/{cid}
PATCH  /api/v1/namespaces/{name}/collections/{cid}
DELETE /api/v1/namespaces/{name}/collections/{cid}
GET    /api/v1/namespaces/{name}/collections/{cid}/members
PUT    /api/v1/namespaces/{name}/collections/{cid}/members
```

`GET /namespaces` lists the namespaces you have a role in, with their counts and your `role`, and those you see only
some collections of: `partial: true`, no `role`, counted over those collections, without the namespace-wide word
cloud and speakers.

`/namespaces/{name}/stats?from=&to=&top=` gives the Reports overview its numbers (viewers of the namespace): the
recordings made from `from` to `to` (days, `YYYY-MM-DD`, both included; leave either open), how many and how long
(`recordings`, `ms`), how many speakers are heard in them and the `top` ones by talk time in them (`top_speakers`,
default 5, at most 50, each with `talk_ms` and `recordings`), the days of the `first` and `last`, and `months`: every
calendar month from `from` (else the first recording) to `to` (else the later of today and the last recording),
empty ones included, at most the latest 240. Without a range it counts every recording; `undated` says how many
have no date, and those are never in a month or a range. Months and days are the recording dates as stored (the
time where it was recorded). 400 when `to` comes before `from`.

`/namespaces/{name}/ip-groups` lists a namespace's IP groups ([Access](access.md#ip-groups), owners), with `address`:
your address as the server sees it (`null` when it can't tell; see `server.trusted_proxies`), and on each group `here`
(your address is in it) and `chosen` (how many recordings it opens when it doesn't open `everything`). `POST` with
`{"name", "ranges", "everything"}` adds one: `ranges` are addresses or CIDR ranges, at most 100, none wider than `/8`
(IPv4) or `/16` (IPv6); names are unique in the namespace. `PATCH` changes any of them and `DELETE` removes the group.
All three answer with the list and are audited as `namespace.ip_group.create`, `.update` and `.delete`.

`/namespaces/{name}/notifications` lists a namespace's notification targets ([Notifications](notifications.md),
owners), the `events` they can get, and whether notifications are `enabled` for the server. `POST` with
`{"name", "kind", "url", "events"}` adds one: `kind` is `webhook`, `matterbridge`, `slack` or `discord`, and a
Matterbridge target also takes `gateway` (required), `username` and `token`. A webhook comes back with its signing
`secret`, this once; `POST …/{tid}/secret` makes a new one. A target's `url` comes back as its scheme and host only.
`PATCH` changes the name, events, `enabled`, the address (in full) and Matterbridge's gateway, name and token (`""`
removes it). `POST …/{tid}/test` sends a test message now and answers `{ok, code, error}`; `GET …/{tid}/deliveries`
lists the latest 50 messages with their `status` (`pending`, `sending`, `sent`, `failed`, `dropped`), `attempts`,
answer `code` and `error`. Changes are audited as `namespace.notification.create`, `.update`, `.delete` and `.secret`.

### Collections of a namespace

Every recording lives in exactly one collection of its namespace, and collections nest (at most 8 deep, 5000 per
namespace). Each namespace has a default collection, "General" when it's made, where recordings go when nobody says
where: imports, uploads, scans, watched folders and recordings moved in from another namespace. Existing recordings
were put in their namespace's General when collections came in. (Saved collections, under
[collections](#collections), are something else: lists of recordings from anywhere.)

`GET /namespaces/{name}/collections` lists them depth first, by name (viewers): each with its `parent` (`null` at the
top), `depth`, `path` (names from the top down), how many recordings it holds (`recordings`) and holds with the
collections inside it (`total`), how many collections are directly inside it (`children`), and `default`.
`POST {name, parent?, description?}` makes one (editors); a name is unique among the collections next to it, ignoring
case and repeated spaces. `PATCH` renames it, describes it, moves it inside another collection of the namespace
(`parent`; `null`: to the top; not inside itself), with the recordings and collections in it, or makes it the default
(`default: true`; `false` on the default is a 400: make another one the default instead). `DELETE` removes an empty
collection: 409 while it holds recordings or collections, or is the default, so deleting one never takes a
recording with it. Audited as `collection.create`, `collection.update` (with what changed) and `collection.delete`.
In IIIF each collection is a Collection inside its namespace's ([IIIF](iiif.md)).

People can be given a role on a collection, which holds for the ones inside it too ([Access](access.md#collection-roles)).
Each collection in the list says what you may do with it: your `role` on it (`viewer`, `editor` or `admin`; an owner
of the namespace is an admin of every collection), `can_change` (rename, describe, move or delete it and make
collections inside it: editors of the namespace, and admins of the collection) and `can_grant` (give roles on it:
owners of the namespace, and admins of the collection). Someone who sees only some collections of a namespace gets
those, starting from the ones they were given (their `path` and `depth` start there). An admin of a collection can't
make collections at the top of the namespace, move one there or into a collection they aren't an admin of, or change
the default. `GET …/{cid}/members` lists who was given a role on the collection and then who has one through a
collection it's inside (`inherited_from`); `PUT …/{cid}/members {email | account, role}` gives, changes or (`role:
null`) takes away a role, answers with the members, and is audited as `collection.member`. People with a role in the
namespace aren't listed: theirs holds everywhere in it.

## resources

A resource is what the archive holds: a recording (audio, video, or a transcript without media), a document (a PDF) or
an image ([Documents and images](#documents-and-images)). The API calls them resources: `/api/v1/resources/…` is the canonical path for everything below, and the schema and the generated
client use it (the `Resources` class). `/api/v1/recordings/…`, their address before, keeps working for existing
clients and reaches the same routes; links the server writes (signed media links, for one) may still use it. Fields
keep their names (`recording`, `RecordingSummary`). The web app's pages moved too: `/resources/<id>`, with
`/recordings/<id>` redirecting there.

```
GET    /api/v1/resources
GET    /api/v1/resources/tags
GET    /api/v1/resources/origins
GET    /api/v1/resources/languages
GET    /api/v1/resources/objects
POST   /api/v1/resources/tags
POST   /api/v1/resources/collection
GET    /api/v1/resources/{rid}
PATCH  /api/v1/resources/{rid}
DELETE /api/v1/resources/{rid}
POST   /api/v1/resources/{rid}/move
GET    /api/v1/resources/{rid}/access
PUT    /api/v1/resources/{rid}/access
GET    /api/v1/resources/{rid}/permissions
POST   /api/v1/resources/{rid}/permissions
DELETE /api/v1/resources/{rid}/permissions/{account}
GET    /api/v1/resources/{rid}/requests
POST   /api/v1/resources/{rid}/requests/{account}/approve
POST   /api/v1/resources/{rid}/requests/{account}/decline
GET    /api/v1/resources/{rid}/ip-groups
PUT    /api/v1/resources/{rid}/ip-groups/{gid}
DELETE /api/v1/resources/{rid}/ip-groups/{gid}
GET    /api/v1/resources/{rid}/player
GET    /api/v1/resources/{rid}/embed-link
GET    /api/v1/resources/{rid}/audio
GET    /api/v1/resources/{rid}/wordcloud.svg
POST   /api/v1/resources/{rid}/reprocess
POST   /api/v1/resources/{rid}/share
DELETE /api/v1/resources/{rid}/share
GET    /api/v1/resources/{rid}/shares
DELETE /api/v1/resources/{rid}/shares/{id}
GET    /api/v1/resources/{rid}/export.{fmt}
PATCH  /api/v1/resources/{rid}/segments/{idx}
POST   /api/v1/resources/{rid}/segments/{idx}/split
POST   /api/v1/resources/{rid}/segments/{idx}/merge
GET    /api/v1/resources/{rid}/edits
GET    /api/v1/resources/{rid}/outputs
```

`GET /resources` filters, sorts and pages on the server, over every recording you can read (in the namespaces you have
a role in, and in the collections you were given a role on), each with your `role` on it. Filters combine with AND;
repeat a parameter that takes several values (`?status=new&status=error`) to match any of them.

| Parameter | |
|---|---|
| `ns` | one namespace (default: every namespace you can read) |
| `collection` | a collection's id: the recordings in it and in the collections inside it |
| `q` | words that must all appear in the title, the namespace's name or a speaker's name |
| `status` | `new`, `transcribed`, `diarized`, `analyzed`, `error`, and two job states: `processing` (a job is queued or running) and `failed` (the latest job failed) |
| `attention` | `true`: only recordings that need a person (errored, latest job failed, or a voice match waiting for review) |
| `processing` | `true`: only recordings with a job queued or running |
| `speaker` | speaker ids |
| `from`, `to` | the recording date, `YYYY-MM-DD`, both days included; recordings without a date don't match |
| `min_duration`, `max_duration` | seconds: at least `min_duration`, shorter than `max_duration` |
| `media` | `audio`, `video`, `transcript` (no media), `document` or `image` |
| `access` | `public`, `restricted` or `private`: the recording's own setting, else its namespace's default |
| `featured` | `true`: only featured recordings; `false`: only the others |
| `tag` | tags, ignoring case |
| `origin` | where they came from: `source:<id>` (a connected source), `upload`, `paste`, `iiif`, `folder` (the archive's own folders) or `file` (another file imported by path) |
| `language` | language codes, ignoring case; `none` for recordings whose language isn't known |
| `object` | kinds of object seen in them (`person`, `car` …; [Objects](#objects)), ignoring case |
| `edited_by` | `me`: recordings you edited (corrected a line of the transcript, changed the catalogue record, or renamed) |
| `sort` | `date`, `title`, `duration`, `speakers`, `status` or `importance`; `-` in front for descending (default `-date`). Recordings without the value come last either way |
| `limit`, `offset` | one page (default 500 rows, at most 1000) |

The body is the page's rows; the `X-Total-Count` header says how many recordings match on all pages.

Rows also say where each recording came from (`origin`, and `origin_name`: the source's name, or e.g. "Uploaded"),
its `language` when known and the collection it lives in (`collection`, `collection_name`). `GET /resources/{rid}`
adds `collection_path`: the collections from the top of the namespace down to its own, `[{id, name}]`.
`POST /resources/collection {recordings, collection}` moves recordings into a collection of their namespace (editors
of each); a recording of another namespace is a 400 (move it to that namespace first). It answers how many `moved`;
their IIIF Manifests change (`partOf`), so harvesters see an Update. Audited as `recording.collection`.
`GET /resources/origins`, `GET /resources/languages` and `GET /resources/objects` (`ns` for one namespace) list the
origins, languages and kinds of object of the recordings you can read with how many have each, most first, for the
Library's Source, Language and Objects filters.

`PATCH /resources/{rid}/segments/{idx}` corrects a transcript line: its `text`, its `speaker` (an id in the
namespace, or `null`), or both (editors). `POST …/segments/{idx}/split {at, t?, speaker?}` splits a line in two at
`at`, a position in its text moved back to the start of the word it's in. The second part starts at `t` (ms, inside
the line) or else when its first timed word was said (or as far into the line's time as `at` is into its text), and
keeps the line's speaker unless `speaker` is sent. `POST …/segments/{idx}/merge` joins a line with the next one: both
texts (with a space between them, none in scripts written without spaces) and their words, from the first's start to
the second's end, with the first's speaker. A split or merge renumbers the lines after it, and what points at line
numbers follows: the edit history, people's entity corrections and the chapters. Each change re-analyses the
recording (`job`), is audited (`transcript.edit`, `transcript.split`, `transcript.merge`) and is kept in `GET …/edits`,
the latest first, with `kind` (`split`, `merge`, or null for a correction); a merge keeps where its second line
started (`after.at`, `after.t`) to split it there again.

A recording's `summary` (the Summarize step's, also in `GET …/player`) has `summary`, `key_points` and
`action_items`, `topics`, `people`, `sentiment` and `importance` (1–5). Key points and action items are
`{text, who?, t0?}`: `who` will do it, and `t0` is where the line they come from starts (ms), when the model said;
summaries made before they had times hold plain strings.

In `GET …/player`, a line whose words have timings from transcription carries them as `w`: `[c0, c1, t0, t1]` for
each word, a character range of its `text` and when it was said (ms). A corrected line keeps the timings of the words
it still has.

Recordings carry `tags`. `PATCH /resources/{rid}` with `{"tags": [...]}` replaces a recording's tags (editors; at most
20, 40 characters each; whitespace is collapsed and repeats are dropped, ignoring case). `POST /resources/tags` with
`{"recordings", "add", "remove"}` changes the tags of several at once (editors of each one's namespace) and says how
many changed. `GET /resources/tags` (`ns` for one namespace) lists the tags in use with how many recordings have each,
most used first.

`PATCH /resources/{rid}` with `{"title": …}` renames a recording (editors; whitespace is collapsed, at most 200
characters). It is audited as `recording.rename`; the recording's report page follows the new title and is rebuilt,
and a published recording shows up as an Update in the IIIF change feed.

`DELETE /resources/{rid}` deletes a recording (owners). Everything Lens made from it goes: its transcript and analysis
(segments, speakers' turns, chapters, entity mentions, terms, edits), video shots, text on screen, face tracks and
frames, outputs, reports and exports, shares, permissions, requests for access, and its place in IP groups, fixed
collections, chat scopes and batch runs that haven't started it. Speakers and faces only it had are removed unless
someone named them. Its waiting jobs are cancelled; while a job is running on it the answer is 409, and a worker that
finds its recording gone stops. The media file stays where it is: scans skip the same path and the same file elsewhere,
and watched folders skip the same remote file, even when it changes; importing it on purpose (the Import dialog,
`lens import`, a IIIF manifest) brings it back. A public recording shows up as a Delete in the IIIF change feed. It is
audited as `recording.delete` with its title, namespace and path.

`POST /resources/{rid}/move` with `{"namespace", "collection", "rediarize", "revoke_shares"}` moves a recording to
another namespace (owners of its namespace, editors of the new one), into `collection` there (default: the new
namespace's default collection; 404 for a collection of another namespace). It keeps its transcript, media, frames, outputs, the people given
permission on it and its share links (`revoke_shares`: they stop working). Its IIIF manifest stays as it was: what it
had from its old namespace (default access and open parts, the metadata profile's defaults) is pinned on it wherever
the new namespace would change it, kept in its metadata history and listed in `pinned`. Speakers and faces are matched
by name in the new namespace, or start there with this recording's voice and face (`rediarize`, audio only: identified
again from their voices instead); unnamed ones left with nothing in the old namespace are removed. Its entity mentions
and per-mention corrections start over: analysis runs again in the new namespace (`job`). Its report pages and exports
move to the new namespace's folders. The old namespace's scans and watched folders don't import the file again, and
its IP groups no longer open the recording. 409 when the new namespace already has the same file or a job is running
on it. Audited as `recording.move`.

Share links (editors): `POST /resources/{rid}/share` with `{"days"}` (1–3650, default 30) makes one and returns its
`id`, `token`, `embed` (`/embed/<id>?s=<token>`) and `short` (`/s/<code>`, ten characters, the same player); only their
hashes are kept, so the addresses are shown this once. `GET /resources/{rid}/shares` lists the links, newest first:
`active` (neither `revoked` nor expired), `revoked_by`/`revoked_at`, `short` (links from before short links have
none), `plays` (times its player started playing, once per page load; previews in Lens itself don't count) with
`played_at`, and `embedded_on`: the sites whose pages framed its player (`origin`, `opens`, `last_at`, most recent
first, up to 50 per link), from the browser's Referer when it reports a frame. `DELETE /resources/{rid}/shares/{id}`
revokes one link (404 if the recording has no such link), `DELETE /resources/{rid}/share` every one that still
works; both are audited as `share.revoke`. A link that doesn't work (expired, revoked, mistyped, or its recording is
gone) opens a neutral page with status 410 that says nothing about the recording.

`GET /resources/{rid}/access` says who may see a recording (members): `access` (`public`, `restricted` or
`private`), `open` (the parts a public recording opens to everyone: `media`, `transcript`, `index`), `featured`,
`inherited` (the access comes from the namespace) and the namespace's `default`. `PUT` changes any of them (owners);
`null` follows the namespace again. Changes are audited as `recording.access`, kept in the metadata history, and
announced in the IIIF change feed. [Access](access.md) explains what each level lets people do.

`/resources/{rid}/permissions` lists the people given permission on the recording (owners). `POST` with `{"email":
…}` gives it to the account with that address (404 when there is none, 400 for members of the namespace, who already
see all of it); `DELETE …/{account}` takes it away. Both answer with the list and are audited as
`recording.permission.give` and `recording.permission.take`. `/resources/{rid}/requests` lists the requests for access
(owners); approving one gives permission, declining lets the person ask again (audited as
`recording.request.approve` and `recording.request.decline`).

`/resources/{rid}/ip-groups` lists the namespace's IP groups with `opens`: whether visitors from their addresses see
all of the recording (owners). `PUT …/{gid}` opens the recording to a group that opens chosen recordings (400 for one
that opens `everything` already), `DELETE …/{gid}` closes it again (404 when it wasn't open). Both answer with the list
and are audited as `recording.ip_group.open` and `recording.ip_group.close`.

### Documents and images

A document (a PDF; a Word, PowerPoint or spreadsheet file, OpenDocument or RTF; text or Markdown; a saved web page; an
email, `.eml` or `.msg`) or an image (JPEG, PNG, TIFF, WebP, GIF or BMP) is a resource of its own: its `source` is
`document` or `image`, and so is `media_kind` in the list, with its `pages`. A document that isn't a PDF is made into
one first ([Configuration](configuration.md#documents-and-images)): the resource keeps its own file
(`GET /{rid}/media`), and the PDF it's read from is `GET /{rid}/pdf` (`primary.pdf` in its files, and `rendition`:
`{from, by}` on the resource says how it was made). Nothing a document refers to is fetched while converting it. An
email's `email` is its `subject`, `from`, `to`, `cc` and `date`; its subject becomes its title and its date when it was
made, unless someone named or dated it. Its attachments are kept as its files (role `attachment`), and those Lens can
read become resources of their own beside it, each with `attached_to: {resource, file, title}` (the email, and its
title when the caller may see it); an attachment file's `resource` is the resource it became, or the one it was already
in the namespace (`documents.attachment_resources`). Transcribing an email again keeps and makes nothing twice.
`GET /uploads/limits` says what the server can convert (`convert: {office, pages, msg}`); an upload it can't read is
refused, saying what it needs, and the files that can be transcripts too (PDFs, Word, text and Markdown) can be imported
as transcripts instead (`POST /import`). Its pipeline's transcribe step draws its
pages and reads their text: a PDF's own text, block by block with where each block is on its page (poppler's
pdftotext; pypdf without poppler, when there are no pages to look at), and by OCR (`video.ocr_engine`) the pages with
fewer than `documents.ocr_below_chars` characters of text, drawn sharper for it. An image is one page (a TIFF one per
frame), read by OCR. Diarize, shots and text on screen skip them; analyze, summarize and report run as for any text.
[Configuration](configuration.md#documents-and-images) has the settings.

The text becomes the resource's segments, one per block, so search, chat, entities, notes and corrections
(`PATCH /segments/{idx}`) work on it as on a transcript. A block's times are only a reading pace; what places it is its
page: `GET /{rid}/player` gives each segment `p` (its page, from 0) and `b` (where it is on the page, `[x, y, w, h]` as
fractions, when known), and `pages`: each page's `idx`, `width` and `height` (pixels of its image), signed links to its
`image` and its `thumb`nail (none when it couldn't be drawn), how its `text` was read (`pdf` or `ocr`), its `chars` and
its `label` (the PDF's own name for it, when it isn't its number). `media` says its `kind`, its `pages` and the first
page's size. `GET /{rid}/media` is the file itself, as a download that browsers don't run. A summary's key points and
action items cite pages: each has the `page` it comes from. The web app opens a document at a page with
`/resources/<id>?page=<n>` (from 1).

Storage sources bring documents and images in too ([sources](#sources), [imports](#imports)): their files stay on the
source, like audio and video. Where a namespace looks for faces ([Video](video.md)), the faces step looks at each page
drawn: a track's `spans`, `first_ms` and `boxes` then count pages (from 0) rather than milliseconds, and `screen_ms` is
how many pages it's on (the namespace's faces don't count those as time on screen). Published, a document is a IIIF
Manifest with a Canvas per page ([IIIF](iiif.md)), and its public page shows its pages and its text page by page
([public](#public)).

## notes

```
GET    /api/v1/resources/{rid}/notes
POST   /api/v1/resources/{rid}/notes
PATCH  /api/v1/resources/{rid}/notes/{nid}
DELETE /api/v1/resources/{rid}/notes/{nid}
```

Notes on a recording, for people with a role in its namespace (share links and signed links don't reach them). `POST`
with `text` (up to 5,000 characters) and, for a note about a moment, `t0` and `t1` (ms from the start; `t1` defaults
to `t0` and stops at the end of the recording) and the `quote` picked in the transcript (up to 1,000 characters);
without `t0` the note is about the whole recording. Anyone who can read the recording can write notes, up to 500 each
on a recording. A note is its writer's: only they see it, unless they share it (`shared: true`, which needs editor
access) with everyone who can read the recording. `GET` lists yours and the shared ones: notes about the whole
recording first, then by moment, each with its writer (`created_by`, `created_by_name`), `mine`, `can_delete` and
`edited_at` (when its text last changed). Only its writer changes a note (`PATCH` with `text` or `shared`); its writer,
or an owner of the namespace for a shared one, deletes it. Sharing, unsharing and deleting a shared note are audited
(`note.share`, `note.unshare`, `note.delete`, on the recording). Notes move with their recording and go when it's
deleted.

## comments

```
GET    /api/v1/resources/{rid}/comments
POST   /api/v1/resources/{rid}/comments
PATCH  /api/v1/resources/{rid}/comments/{cid}
DELETE /api/v1/resources/{rid}/comments/{cid}
```

Comments on a resource: a conversation everyone with a role on the resource (in its namespace, or on its collection)
reads and joins; share links and signed links don't reach them. `POST` with `text` (up to 5,000 characters) and, for
a comment about a moment or a passage, `t0` and `t1` (ms from the start; a document's pages have a reading-pace
clock, so `t0` is a place on a page; `t1` defaults to `t0` and stops at the end of the resource) and the `quote`
picked in the text (up to 1,000 characters); without `t0` the comment is about the whole resource. A comment starts
a thread; `POST` with `parent` replies on that comment's thread (a reply to a reply goes on the thread too, and a
reply is about what its thread is about, so its moment is ignored). Anyone who can read the resource can comment, up
to 1,000 each on a resource. `GET` lists them threaded: each thread (the ones about the whole resource first, then by
moment) followed by its replies, the earliest first, each with its writer (`created_by`, `created_by_name`), `mine`,
`can_resolve`, `can_delete` and `edited_at`. Only its writer changes a comment's text (`PATCH` with `text`). A thread
is resolved and reopened on its first comment (`PATCH` with `resolved`), by its writer or an editor of the resource;
the thread says who resolved it (`resolved_by`, `resolved_by_name`, `resolved_at`). Its writer, or an owner of the
resource (of its namespace, or an admin of its collection), deletes a comment; a thread's first comment takes its
replies with it. Resolving, reopening and deleting are audited (`comment.resolve`, `comment.reopen`, `comment.delete`
with whether its writer did it and how many replies went, on the resource). Comments move with their resource and go
when it's deleted.

## highlights

```
GET    /api/v1/resources/{rid}/highlights
POST   /api/v1/resources/{rid}/highlights
PATCH  /api/v1/resources/{rid}/highlights/{hid}
DELETE /api/v1/resources/{rid}/highlights/{hid}
```

Passages of a resource its editors mark in colour, for everyone with a role on it. `POST` (editors) with the passage
(`t0` and `t1`, as for comments; `t1` defaults to `t0`), the `quote` picked in the text (up to 1,000 characters), a
`colour` (`yellow`, the default, `green`, `blue` or `red`: the web app has a token for each) and a `label` of what it
marks (up to 200 characters). Up to 1,000 on a resource. `GET` lists them by passage, each with who made it
(`created_by`, `created_by_name`), `mine` and `can_edit`. Any editor of the resource changes one (`PATCH` with
`colour` or `label`; an empty label clears it) or deletes it; deleting is audited (`highlight.delete`, on the
resource). Highlights move with their resource and go when it's deleted.

## files

```
GET    /api/v1/resources/{rid}/files
POST   /api/v1/resources/{rid}/files?role=&name=&language=&label=
PATCH  /api/v1/resources/{rid}/files/{fid}
DELETE /api/v1/resources/{rid}/files/{fid}
GET    /api/v1/resources/{rid}/files/{fid}/download
GET    /api/v1/resources/{rid}/files/{fid}/lines
```

A resource's files: its primary file (`primary`: the audio or video its pipeline runs on, none for a transcript
without media) and any number of supplementary files beside it, each with a `role`, and a `language`, `label` and
`description` of its own:

| `role` | Types | What becomes of it |
|---|---|---|
| `transcript` | .txt .md .json .jsonl .srt .vtt .docx .doc .pdf | another transcript, read into lines search finds |
| `captions` | .vtt .srt | lines, and WebVTT captions in IIIF |
| `translation` | as transcripts | lines in another language |
| `index` | WebVTT or SRT chapters, JSON, OHMS XML, text whose entries start with a time | entries with a title, synopsis and keywords, and a table of contents in IIIF |
| `thumbnail` | .jpg .jpeg .png .webp .gif | the Manifest's thumbnail in IIIF |
| `attachment` | anything | a file to download |

People who can read the resource list its files (`GET`, each with a signed `download` link that works on its own, as
media links do) and read the lines of transcripts, captions, translations and indexes (`…/lines`, in order, with
`offset` and `limit` up to 1,000). A line has `t0`/`t1` in ms, which are null when its file doesn't say when its lines
are (`timed: false`, such as prose in a PDF); an index entry also has its `title`, `synopsis` and `keywords`.

Editors add a file as the raw request body (`application/octet-stream`), naming it and its role in the query, up to
`server.max_upload_mb`. 400 when its type doesn't fit the role, when it can't be read as its role (no text, more than
20,000 lines, a PDF without a text layer), or when the resource has 100 files already; 413 past the limit. `PATCH`
changes its role (reading it again), language, label or description (null clears the last three); `DELETE` deletes it
and its lines. Adding, changing and deleting are audited (`file.add`, `file.update`, `file.delete`, on the resource).
Downloads come as attachments and are never run by a browser: pages, scripts and SVG are sent as plain bytes. Files
stay with a resource that moves to another namespace and go when it's deleted.

`public` says whether everyone may download a file. A public resource opens its files with its parts
([Access](access.md#files)): transcripts, captions and translations with its transcript, indexes with its index,
thumbnails with its media. Attachments always need permission.

## imports

```
POST   /api/v1/import
POST   /api/v1/import/preview
POST   /api/v1/import/web
```

`POST /import` queues the namespace's pipeline after the import, or the saved pipeline named by `pipeline` (any of
`GET /pipelines`; 400 for one that doesn't exist, before anything is saved). Every import takes a `collection` of the
namespace to put the recordings in (default: its default collection; 404 for one of another namespace): `POST /import`,
`POST /import/source`, `POST /uploads` and `POST /import/iiif`.

`POST /api/v1/import/source {source, paths, namespace, pipeline?, collection?, documents_as?}` imports chosen files of
a storage source now, rather than watching their folder (admins, like sources; up to 500 paths, as
`GET /sources/{sid}/browse` lists them). Audio, video, documents and images stay on the source and run the namespace's
pipeline, or `pipeline`; transcripts are imported. A PDF, Word, text or Markdown file is a document unless
`documents_as` is `transcript` (its text only), or the server can't make a PDF of it. Each path gets a result: `queued` (with `recording` and `job`), `already` (the namespace has it from this
source), `skipped` (a folder, or not audio, video, a document, an image or a transcript) or `error`. Choosing a file
whose recording was deleted brings it back. Audited as `import.source`.

### Web pages

`POST /api/v1/import/web {url, namespace, title?, collection?, pipeline?}` captures a web page as a document (editors
of the namespace; admins may name a new one). Its pipeline keeps the page as it is then, as a PDF, and reads it like
any document ([Documents and images](#documents-and-images)): a link to a PDF is kept as it is, any other page is
printed by headless Chromium with its scripts run. Its `web` is `{url, final, captured_at, how}`: the address given,
the one it ended at after redirects, when it was captured, and `printed` or `pdf`. Its title is the page's own once it's
captured, unless `title` gave one. Capturing happens once: transcribing it again keeps the page it captured.

Only public addresses are reached, on ports 80 and 443: the address is checked when it's given (400, saying why: not
http(s), a user name or password in it, another port, or a host with an address that isn't public), and every request
the page makes is checked again by a proxy inside Lens, which resolves each host itself and connects only to the
address it checked; redirects are followed to http and https addresses only, each checked. Chromium has no other way
out: it looks up no names itself, reaches no D-Bus, and WebRTC, which a page's script can start, may only go through
the proxy (never UDP straight to an address). `documents.web_networks` (startup) adds networks that may be reached too, for an
intranet. Without Chromium on the server it answers 400 (`GET /uploads/limits` → `convert.web`). Audited as
`import.web`. The PDF is the resource's file (`GET /resources/{rid}/media`), named after the link
(`annual-report.pdf`) or the page's title (`harbour-news.pdf`).

### Uploads

Audio, video, documents (PDF) and images go up in pieces, so a dropped connection costs one piece, not the file. A PDF
uploaded this way is a [document](#documents-and-images); to import a PDF as a transcript (its text only), send it to
`POST /import` instead (the web app's Import asks which).

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

`POST /uploads {namespace, filename, size, title?, modified?, pipeline?, collection?, recording?}` starts one (editors of the namespace; admins
may name a new namespace, created when the upload finishes). With `recording`, a transcript-only recording, the file
becomes that recording's audio instead of a recording of its own (editors of its namespace; `namespace` can then be left
out; 409 when it has audio already; 400 for anything but audio or video). [Processing](processing.md#importing-transcripts) says what runs then. `pipeline`
runs instead of the namespace's once a new recording is made, and `collection` is where it goes (neither with
`recording`, which stays in its collection). The name loses any folders and its extension is lowercased; `modified`
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
GET    /api/v1/search/terms
GET    /api/v1/graph
GET    /api/v1/mentions
```

`GET /search?q=` finds the moments where the words are said (or shown on screen in a video, or written in a resource's
supplementary transcripts, captions, translations and indexes, or in what a model said its shots and pages show) in the
namespaces you can read, best first: every word
(English stemming), "quoted phrases" as written, `OR` between alternatives; `ns`, `speaker`, `emotion` and `recording`
narrow it, and `object` keeps to the recordings a kind of object is seen in ([Objects](#objects));
`limit`/`offset` page through it. `total` counts the moments ranked so far (`capped` when there may be more).
With `facets=true` it also counts all the matching moments, whatever the page, by namespace, speaker, emotion and
recording (`facets`: up to 50 values each, most first, and `moments`), and lists the kinds of object seen in the
recordings they're in, with how many of those recordings each is in (`objects`); past 20,000 moments the counts cover
20,000 of them (`partial`).

Each hit's `source` says where it was found: `said` (the transcript), `screen` (text on screen), `page` (a document's or an
image's text: its `page`, from 0, and its `box` on it), `object` (a kind of object, where it's first seen: its time, or
on a document's pages its `page`, with the `frame` and the `box` it's best seen in), `described` (what a shot or a page
shows, [Descriptions](#descriptions): the shot's time, or the `page`, and its `frame`) or `file` (a line of a
supplementary file: its `file`, `file_role` and `file_label`, and which `line`). A file's lines have no `t0` when the
file doesn't say when they are; the web app opens those in the resource's Files tab. A `speaker` or `emotion` filter
keeps to what was said.

`mode` says how the words are matched ([search by meaning](processing.md#search-by-meaning)): `keyword` (BM25),
`semantic` (passages an embedding model finds alike), `hybrid` (both, fused by rank) or `auto` (the default: hybrid
when search by meaning is set up and the query has no "phrases" or OR, else keyword). The reply's `mode` is how it was
matched, `semantic` whether search by meaning is set up, and `meaning` why it wasn't used when asked for. Each hit's
`match` is `words`, `meaning` (with its `similarity`, cosine, and shown at the passage's best line) or `both`.

Search has no prefix search (`interp*` looks for the word "interp"). `GET /search/terms?prefix=interp` lists whole
words said in the namespaces you can read (`ns` for one) that start with it, the most said first, with how often and
in how many recordings (`limit`, default 8, at most 20); the web app offers them as "Try …".

## speakers

```
GET    /api/v1/speakers
GET    /api/v1/speakers/{sid}/recordings
POST   /api/v1/speakers/{sid}
POST   /api/v1/speakers/{sid}/merge
POST   /api/v1/merges/{mid}/undo
POST   /api/v1/speakers/{sid}/link
POST   /api/v1/speakers/{sid}/unlink
POST   /api/v1/speakers/{sid}/not-same
```

`GET /speakers?ns=` lists the namespace's speakers (each with its suggested merges), its last merges (with `by`, the
`recordings` and `segments` that moved, and `undone_by`/`undone_at`), its links to other namespaces, and `cross`:
voices in other shared namespaces you can read that are likely the same person (`score`, best first). `not-same
{with}` says two speakers aren't the same person (one namespace or two): the suggestion goes and isn't made again.
`unlink {with}` removes a link (404 when there's none). Linking, unlinking, "not the same" and undoing a merge need
editor access to both speakers' namespaces and are audited (`speaker.link`, `speaker.unlink`, `speaker.not_same`,
`speaker.merge.undo`).

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
GET    /api/v1/resources/{rid}/metadata
PUT    /api/v1/resources/{rid}/metadata
GET    /api/v1/resources/{rid}/metadata/history
POST   /api/v1/metadata/edits/{eid}/revert
GET    /api/v1/namespaces/{name}/metadata
PUT    /api/v1/namespaces/{name}/metadata
POST   /api/v1/metadata/bulk
```

### fields

```
GET    /api/v1/namespaces/{name}/fields
POST   /api/v1/namespaces/{name}/fields
GET    /api/v1/namespaces/{name}/fields/{fid}
PATCH  /api/v1/namespaces/{name}/fields/{fid}
DELETE /api/v1/namespaces/{name}/fields/{fid}
GET    /api/v1/resources/{rid}/fields
PUT    /api/v1/resources/{rid}/fields
GET    /api/v1/namespaces/{name}/collections/{cid}/fields
PUT    /api/v1/namespaces/{name}/collections/{cid}/fields
GET    /api/v1/resources/{rid}/files/{fid}/fields
PUT    /api/v1/resources/{rid}/files/{fid}/fields
```

Custom metadata fields, defined by editors on a namespace or on one of its collections (`collection`). A field describes
the resources, the collections or the files inside where it's defined (`target`): one defined on a namespace applies
everywhere in it, one defined on a collection to what is inside that collection. Each has a `label` (unique where it's
defined, for its target), a `type` (`text`, `longtext`, `number`, `date`, `boolean`, `choice`, `choices` or `link`),
`options` for the two choice types, `help`, and whether it's `published`: published fields appear on public pages and
in IIIF metadata (Manifests for resources, Collections for collections), internal ones only in the workspace. New
fields are internal. Up to 100 fields per namespace.

Editors of the namespace define fields on it and on any of its collections; editors of a collection, on it. `PATCH`
renames a field and changes its options (400 for an option that items have chosen), help, publishing or place in the
order (`ord`); its type and target stay. `DELETE` deletes it with every value it has (`uses`, which `GET` of one field
also reports). These are audited as `field.create`, `field.update` and `field.delete`. A collection's fields go when it
is deleted.

Values: `GET …/fields` on a resource, a collection or a file lists the fields that describe it with its values;
`PUT` with `{"values": {"<field id>": value}}` sets them (null clears one; fields not named keep theirs). Values are
checked by type: text up to 500 characters (long text 5,000), a number, a date as YYYY, YYYY-MM or YYYY-MM-DD, true or
false, an option (or a list of options), an http(s) link; 400 says what doesn't fit, or names a field that doesn't
describe the item. Editors set a resource's and its files' values, those who arrange a collection (editors of the
namespace, admins of the collection) its own. A resource's values are part of its metadata history, so reverting an
edit puts them back too; saving values is audited as `fields.save`. A resource that moves keeps its values, and shows
those of the fields that describe it in its new place.

`GET /resources?field=<id>` lists the resources with a value for a field; with `value`, those whose value contains
that text (text and links, ignoring case), equals that number, starts with that date (1998, 1998-05), is true or
false, or is (or includes) that option. Saved views keep `field` and `value`.

## video

```
GET    /api/v1/resources/{rid}/media
GET    /api/v1/resources/{rid}/frames/{name}
PATCH  /api/v1/resources/{rid}/ocr/{span}
DELETE /api/v1/resources/{rid}/faces/{track}
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

`PUT /namespaces/{name}/faces/mode {mode?, purpose?, pixelate?, reprocess?}` (owners) sets the namespace's face mode
and whether the faces found are pixelated in the pictures visitors see (`pixelate`; needs faces detected; see
[Video](video.md)). `GET /namespaces/{name}/faces` says both, and the player's `faces_mode` and `faces_pixelate`
say them for a resource. `GET /resources/{rid}/frames/{name}` (frames, keyframes, face crops, a document's or an
image's pages and thumbnails) serves a request without a role in the namespace the faces found on it pixelated, where
the namespace says so; a role, or a link the API signed for a member (`full=1`), gets the picture as it is.

### Objects

The `objects` step finds the people, vehicles, animals and everyday things (the 80 kinds of the COCO dataset) on a
video's sampled frames and on a document's or an image's pages; it comes after `faces` in the standard pipeline. With
no detector on the server it's skipped, and its job says why ([Configuration](configuration.md#objects)). Each kind
found is kept once per resource, and the player's `objects` lists them, the most seen first:
`{label, spans, screen_ms, first_ms, count, score, frame, box, boxes, paged, engine}`: where it's seen (`[from, to)`
in ms, or on a document's pages page numbers from 0, with `paged`), how long or on how many pages, how often it was
found, the detector's average confidence, a signed link to the frame or page it's best seen on and where it is there,
and each place it was found (`[t, x, y, w, h, score]`, at most 500). The kinds a resource has are its `objects`, which
`GET /resources?object=` filters on; search finds them by name (`source: object`), and its `object` filter and facet
use them ([search](#search)). Moving or deleting a resource moves or deletes its objects.

### Descriptions

The `describe` step has a model that can see images, the one an admin chose as `llm.vision_model`, say in a few
sentences what each page of a document or an image shows, and each shot of a video (from its keyframe): the setting,
the people (by what they do and wear, never who they might be), the things and any text in it. It comes after
`objects` in the standard pipeline, and describes at most `llm.describe_max` pages or shots of a resource. Without an
LLM, or with no `llm.vision_model`, it's skipped, saying why; a model that can't see makes it fail with what the server
said ([Configuration](configuration.md#descriptions)). The player's `descriptions` lists them in order:
`{idx, t0, t1, text, model, frame, paged}`: the shot or page, where it starts and ends (ms; on pages, the page from 0
and the next), what the model said, which model, and a signed link to the picture it was shown. Search finds them
(`source: described`, [search](#search)). Moving or deleting a resource moves or deletes them; the step run again
replaces them.

## iiif

```
GET    /api/v1/resources/{rid}/iiif
GET    /api/v1/resources/{rid}/content-state
POST   /api/v1/import/iiif/preview
POST   /api/v1/import/iiif
```

Importing from IIIF (admins) reads a Presentation 3 Manifest, or a Collection's Manifests, also those of the
Collections inside it (at most 8 deep and 50 Collections read). The preview of a Collection lists its Manifests in
order (`items`, the first 200, each with `path`: the Collections it's in below this one), how many it found
(`total`), how many Collections it read (`collections`) and whether it stopped early (`more`). `POST /import/iiif
{url, namespace, collection?, keep_transcripts, limit, wait}` imports the first `limit` Manifests, all into
`collection` (default: the namespace's default collection).

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

A watched folder's `kinds` says what it picks up: `audio` (and video), `transcripts`, `both` of those, `documents`
(documents and images) or `all` of them, the default. The three from before documents (`audio`, `transcripts`, `both`)
read PDFs, Word and text files as transcripts, as they always did; `documents` and `all` take them as documents
(subtitles and JSON stay transcripts). `POST /watches/preview
{source, path, kinds?, include?, exclude?}` counts what a folder would pick up: `files`, `audio`, `transcripts`,
`documents` and `images`.

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
GET    /api/v1/chats/capabilities
POST   /api/v1/chats
GET    /api/v1/chats/{cid}
PATCH  /api/v1/chats/{cid}
DELETE /api/v1/chats/{cid}
POST   /api/v1/chats/{cid}/messages
POST   /api/v1/chats/{cid}/stop
POST   /api/v1/chats/{cid}/messages/{mid}/check
GET    /api/v1/approvals
POST   /api/v1/approvals/{aid}
```

`GET /chats/capabilities` tells anyone signed in whether a language model is set up (`configured`), which (`model`),
whether it uses tools (`tools`, `max_steps`), whether answers can be checked (`check`) and the `models` people may pick
(the configured one first; `llm.chat_models`, else the server's list); never the model server's address or key. A
conversation's `model` (`POST /chats`, `PATCH /chats/{cid}`; null for the configured one) answers in it, and a
question can name another (`POST /chats/{cid}/messages {content, model}`, e.g. to retry); a model that isn't offered is
a 400. Each answer records the `model` that wrote it. `POST /chats/{cid}/stop` stops the answer being written in your conversation after the piece or tool
step it's on: the stream sends `stopped`, then `done` with the saved message, whose `stopped` is true and whose
`content` is what came before (`(stopped)` when nothing had). `{stopping: false}` when nothing was being written.

A conversation's `scope` narrows what it draws on: `namespaces`, `recordings`, `collections`, `speakers`, `from` and
`to`; every key narrows it further, and an empty scope is everything you can read. `collections` are ids of
collections you can see (yours or shared); the conversation draws on their recordings as they are each time it
answers, so a filter collection's new recordings count, and a deleted collection adds none.

`GET /chats/{cid}` returns each answer with the `steps` the assistant took (`{tool, args, summary}`), its `notice` and
`error`, and its latest source `check` (`{claims, supported, verdicts, uncited}`), as they were when it was written
or checked.

## collections

Saved collections: lists of recordings from anywhere, to chat with or run batches on. A namespace's own collections,
which recordings live in, are under [namespaces](#collections-of-a-namespace).

```
GET    /api/v1/collections
POST   /api/v1/collections
GET    /api/v1/collections/{cid}
PATCH  /api/v1/collections/{cid}
DELETE /api/v1/collections/{cid}
```

## views

```
GET    /api/v1/views
POST   /api/v1/views
PATCH  /api/v1/views/{vid}
DELETE /api/v1/views/{vid}
```

Saved views of the Library: a `name` (unique among your views, ignoring case), the `namespace` it shows (`null`: every
namespace you can read) and its `state`: the tab (`all`, `attention`, `processing`), the filter box `q`, `statuses`,
a `speaker` by name (each namespace has its own speaker ids), the Library's `date` and `duration` ranges (kept as
ranges, so `30d` stays the last 30 days), `media`, `tags`, a `collection` of its namespace and `sort` (as
`GET /resources` takes it). A view is its
maker's; `shared: true` shows it to everyone with a role in its namespace (sharing needs editor access there, and a
view of every namespace can't be shared). `GET` lists yours, then the shared ones of namespaces you can read, each
with `mine` and `can_delete`; a view of a namespace you can no longer read is left out. Only its maker changes a view
(`PATCH` with `name`, `shared` or `state`); its maker, or an owner of its namespace for a shared one, deletes it. Up to
100 views each. Sharing, unsharing and deleting a shared view are audited (`view.share`, `view.unshare`,
`view.delete`).

## searches

```
GET    /api/v1/searches
POST   /api/v1/searches
PATCH  /api/v1/searches/{sid}
DELETE /api/v1/searches/{sid}
```

Saved searches: `POST` with a `name` (unique among yours), the search's `q`, and any of `namespace`, `speaker`,
`emotion` and `recording`, as `GET /search` takes them. They follow the rules of saved views (above): yours, or
`shared` with the namespace they search (editors there; a search of every namespace can't be shared); only their maker
renames or shares them (`PATCH` with `name` or `shared`); their maker, or an owner of the namespace for a shared one,
deletes them. `GET` lists yours, then the shared ones, with `speaker_name` and `recording_title` where you can read
them. Sharing, unsharing and deleting a shared one are audited (`search.share`, `search.unshare`, `search.delete`).
Saved searches are not collections: to chat with a search or run things on it, keep it as a collection too.

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
each result is a card with up to three matching `hits` (`t0` and an HTML-escaped `snippet` with `<mark>`, and the
`page` of a document's text). Restricted recordings and closed transcripts match on the title only.

`GET /public/recordings/{rid}` is a recording's public page as the caller may see it. `view` says how:

* `full`: the caller has permission, so all of it: a role in its namespace (admins have every role; `member` is
  true), permission given on the recording (`granted`), or an address in an IP group that opens it (`network`, the
  group's name).
* `public`: a public recording, for everyone else: its description (the metadata IIIF publishes) and only its open
  parts: `media` (a signed link to the audio or video, with its waveform), `transcript` (speakers and lines, and the
  transcript files to download when the transcript is open to everyone) and `chapters` (the index).
* `locked`: a restricted recording, for someone signed in without permission: its title and namespace only.

A document's or an image's media are its pages and its file: `media.kind` is `document` or `image`, `media.pages`
lists each page (`idx`, `width`, `height`, `label`, and signed links to its `image` and `thumb`), `media.url` is the
file to save and `media.poster` the first page. Its transcript is its text: each segment has its page (`p`), and the
downloads are its text (txt, md, json) without subtitles. A public document's page opens at `?page=<n>` (from 1).

Parts the caller can't use are `null` and listed in `closed`. `files` are the supplementary files the caller may
download (with signed links: those that follow an open part, or all of them with permission), and `files_closed` how
many more there are. For someone signed in without permission,
`can_request` says whether they may ask for access (something is closed to them) and `request` is their latest request.
`POST …/request` with an optional `message` asks the owners (400 when the caller already sees all of it, or when all of
it is open to everyone); asking again replaces the request. `GET /access-requests` lists the requests waiting for an
answer in the namespaces the caller owns. Recordings the caller may not see at all (restricted ones
to visitors who aren't signed in, private ones to anyone without permission) answer 404, as missing ones do.
