# Processing

How recordings move through Lens: where they come from, what each step does, and how speakers, entities and the knowledge graph are built. Every step can be started from the web app, the API or the `lens` command line.

## Steps

- `scan` finds audio under each namespace's paths and fingerprints it: moved files keep their history, duplicates are skipped,
  and so are files whose recording someone deleted or moved to another namespace (at the same path, or a copy of the same
  file).
- `transcribe` uses SenseVoice, faster-whisper or mlx-whisper. When the configured engine isn't installed on a worker (or
  doesn't import there), it uses the next one that is and says so in the job log; with none at all the step fails,
  saying how to add one. A file that fails is marked and the batch carries on. For a
  document or an image it draws the pages and reads their text instead: a PDF's own text, and OCR for scans and images
  ([Documents and images](configuration.md#documents-and-images)).
- `diarize` splits genuinely two-channel files by channel, otherwise clusters voice embeddings (or uses pyannote), then
  matches voiceprints against the namespace's speakers.
- `analyze` finds entities, chapters, keywords and talk statistics; `summarize` (optional) calls any OpenAI-compatible
  server for a summary, key points, action items (with who will do them), topics, people, tone and importance, each
  key point and action item with the time of the line it comes from; `report` writes static HTML per recording and
  per namespace, with word clouds.

Every step takes `--ns`, `--limit` and `--force`; `run` does them all, and a lock stops two runs overlapping.

## Importing transcripts

Formats: .txt, .md, .markdown, .mdx, .docx, .doc, .pdf, .srt, .vtt, .json (lens, Whisper or a list of segments) and
.jsonl (SenseVoice chunk files; overlapping windows are stitched). Pasted text works too.

    lens import podcasts episode.docx --audio episode.mp3 --speakers "SPEAKER_00=Host A,SPEAKER_01=Host B"
    pbpaste | lens import notes - --title "Standup"
    lens import notes minutes.pdf --format text

In the web app, Import takes pasted text, a chosen file or one dropped on the text box, and analyses it straight away.
It also uploads audio, video, documents and images, which go through the namespace's pipeline like scanned files. A
PDF is a document (its pages, to look at and search) unless you choose Transcript (its text only). "Then run" picks
another saved pipeline for what you import.

**Audio for a transcript.** A transcript can get its audio (or video) in the web app: Import → Paste's "Attach audio…",
a transcript dropped together with the audio of the same name (`ep14-transcript.srt` and `ep14.m4a`), or "Attach audio"
on a transcript-only recording's page. The file is uploaded and becomes the recording's media; the transcript and its
speakers stay. Then these steps run, after anything the recording's job still had to do: transcribe (which keeps the
transcript and draws the waveform), diarize (speakers by voice, unless the transcript named them), shots, text on screen
and faces (video only), analyze and report. The recording takes the file's fingerprint, so scans and uploads of the
same file find it, unless another recording in the namespace has it already.

Speakers are recognised from `Name: text`, `[12:30] Name: text`, `Name (12:30): text`, Otter/Zoom/Teams exports (a
`Name  12:30` line, then what they said), `speaker|emotion|text` lines, and the speakers in SRT/VTT and JSON. Anything
else becomes paragraphs split into segments of about 40 words with estimated times. Markdown and MDX are reduced to text
first: front matter or the first heading becomes the title; imports, exports, JSX and code blocks are dropped. A PDF
imported as a transcript needs a text layer; import a scan as a document instead, and its pages are read by OCR. Named speakers are reused within the namespace; generic labels (SPEAKER_00, S1, CH0)
become new speakers.

## Speakers and namespaces

Speaker ids belong to one namespace. Per recording, `diarize.engine: auto` works like this:

- If the file is genuinely two-channel (left and right carry different voices), each channel is one speaker. That separation is exact and free.
- Otherwise, voices are clustered from embeddings. Short backchannels take the label of the nearest long turn.
- Or set the engine to `pyannote`.

Each voice gets a voiceprint (SpeechBrain ECAPA, up to `sample_seconds` of that speaker's clearest audio). The voiceprint is matched one-to-one against the namespace registry:

- At or above `match_threshold`, it reuses the existing speaker and updates the centroid.
- Between `review_threshold` and `match_threshold`, it creates a new speaker plus a suggested merge for you to confirm.
- Below that, it creates a new speaker.

You can rename and merge speakers in the web app or the CLI (`lens speakers …`), and every merge can be undone; merges
keep who made them and how many recordings moved. Speakers in different namespaces are never merged. You can link them
as the same person (and unlink them), and with `speakers.cross_namespace: suggest` the graph shows likely voice matches
as dashed edges and Speakers lists them with how alike the voices are. Saying a suggested pair is "not the same", in one
namespace or across two, removes the suggestion for good.

## Knowledge graph

Nodes are speakers and named things. Edges are:

- speakers who were in a recording together,
- who mentions what,
- what is mentioned together,
- same-person links.

`graph: shared` puts a namespace in the global graph, where named things with the same name join into one node across namespaces. `graph: isolated` keeps it to its own graph. The API takes `scope=global` or `scope=ns:<name>`.

## Entities and the graph explorer

People, organisations, products, places, events, works and topics are extracted from every transcript. Dates and
numbers are extracted too, but hidden unless asked for.

The **Entities** page in the web app lists a namespace's entities (search, type and collection filters, a Hidden tab)
and opens each in a side panel where editors rename, retype, describe and hide it.

- **Index** (`GET /api/v1/entities`):
  - Search forgives misspellings and covers aliases.
  - Filters: type, namespace, collection (and the collections inside it), speaker, recording, date range, minimum
    mentions, and hidden entities.
  - Sorts: most mentioned, most recordings, most recent, rising, and name.
  - Options: grouping by name across namespaces, a twelve-month sparkline, and facets.
- **Entity page:** its details and aliases, paged mentions (each line with the words highlighted, linking to its moment),
  a timeline by month or week and namespace, and connections (entities mentioned together, speakers, recordings).
- **Explorer:**
  - `GET /api/v1/graph/explore?focus=e12` gives the neighbourhood of an entity or speaker. Depth is one or two hops, and it
    can be filtered by type, edge kind and strength, within one namespace or all shared ones.
  - `GET /api/v1/graph/path?a=…&b=…` gives the shortest chain between two nodes, with the lines that support each link.
- **Curation** (editors of the entity's namespace):
  - **Rename:** the old name stays as an alias. It can optionally correct the words in every transcript line, with a
    dry-run preview first, then re-analysis.
  - **Change type, describe (`PATCH /api/v1/entities/{id}`), and hide or restore.**
  - **Merge:** with undo.
  - **Mark two entities as not the same.**
  - **Move or remove a single mention.**
  - **Link the same thing across namespaces.**
- **Merge suggestions:** same letters (ignoring case, spaces and punctuation), acronyms, one name containing the other,
  close spellings, and names that sound alike (likely transcription errors).

### Entity setup

Each namespace has an entity setup (Entities → Setup, `GET`/`PUT /api/v1/namespaces/{name}/entity-setup`): the types
it keeps (names of other types are left out when a recording is analysed) and a description of what it's about. Any
collection can have its own setup, which also holds for the collections inside it; a recording follows the setup of the
nearest collection that has one, else its namespace's. Editors of the namespace change its setup and any collection's;
editors of a collection change that collection's. A namespace can add entity types of its own (Entities → Types,
`/api/v1/namespaces/{name}/entity-types`), each with a description of what counts as one; a type can be deleted once no
entity has it. Changes apply to recordings analysed from then on; "Apply to analysed recordings"
(`POST /api/v1/namespaces/{name}/entity-setup/apply`) analyses the namespace's or a collection's recordings again.

A setup has a mode:

- **Self-organizing** (the default): every name the extractors find becomes an entity, and people merge, rename and
  describe them. Names of types the setup doesn't keep are left out.
- **Fixed list:** editors define the entities (Entities → Add entity, `POST /api/v1/namespaces/{name}/entities`), each
  with a type, the other ways it's said and a description, for the whole namespace or for one collection and those
  inside it. Each name found goes to the defined entity it names (by name or another way it's said, in any case; the
  defined names are looked for in the transcript too), else to **Unlabeled** when it belongs here (a type the setup
  keeps; with every type kept, anything but dates and numbers), else to **Unknown**. Unknown and Unlabeled are always
  there and can't be renamed, retyped, merged, hidden or deleted. Moving a mention to a new name in a fixed-list place
  adds that name to the list. `PATCH /api/v1/entities/{id}` sets an entity's other names and whether it is on the list;
  a defined entity nothing mentions can be deleted.

Curation survives re-analysis: merged names become aliases, and moved or removed mentions become per-line overrides.
Everything is audited. People who can't read a namespace never see its entities, mentions or graph nodes, and requests
about them come back as not found.

## Search

All words must appear, matched after English stemming ("exploit" also finds exploits and exploiting); "quoted phrases"
must appear as written; OR separates alternatives. Filter by namespace, speaker, emotion or recording. For archives that
aren't in English set `search.stemming: none` and run `lens reindex`. Prefix search (`expl*`) from the SQLite
version is gone; stemming covers most of what it was used for.

### Search by meaning

With an embedding model set up (Settings → Search, or `embeddings` in archive.yaml: see
[configuration](configuration.md#search-by-meaning)), search also finds moments about what was asked in other words:
"money worries" finds "we can't afford the rent this month". The `embed` step (in the standard pipeline, after
analyze) joins a recording's transcript lines into passages of about `embeddings.passage_chars` characters (each within
one page of a document), adds what its shots or pages are described as showing, and has the model embed each; the
vectors live in the `passage` table under SurrealDB's HNSW index. Indexing a recording again only embeds the passages
whose text changed, so a corrected line costs one request; correcting, splitting or merging lines and renaming an
entity across the transcript run the step again.

A search is matched three ways (`mode` on `GET /search`, the Match switch in the web app):

* **auto** (the default): by its words and by meaning, fused by reciprocal rank, when search by meaning is set up and
  the query has no "quoted phrases" or OR (those ask for exactly those words); else by its words.
* **keyword**: only the words, as above.
* **semantic**: only by meaning.

A passage found by meaning is shown at its line that best fits (the speaker or emotion filtered on, else the one with
most of the query's words), marked Related, with how alike it is (`similarity`, cosine). One that holds a keyword hit
adds to that hit's rank instead of showing twice. Only passages at least `embeddings.min_similarity` alike count, and
none much further than the closest one, so an unrelated query finds nothing rather than whatever is least unlike it.
Facets count the moments found by meaning too. The assistant's search tool and chat's retrieval use the same passages,
so a question finds excerpts that answer it without sharing its words.

The vectors are only comparable within one model. When the model (or `embeddings.document_prefix`) changes, the old
vectors are dropped and searches go by the words until recordings are indexed again: the **Index for search by
meaning** routine (seeded, hourly at :20) queues the embed step for up to 500 recordings not yet indexed with the
current model each time, and does nothing (no run is recorded) while search by meaning is off or everything is indexed;
Settings → Search shows how far it has got and can queue more now; `lens embed` indexes here and now. When the
embeddings server can't be reached, the step is skipped (the routine tries again later) and searches go by the words,
saying why.

## Background work

Imports, pipeline runs and folder scans return at once and run as jobs stored in SurrealDB. The server runs
`workers.inline` workers itself; more can run anywhere that reaches the database, each limited to the steps it can do.
A job whose next step a worker can't run goes back on the queue for one that can, so a Mac can transcribe with mlx while
the container does the rest. Jobs can be cancelled and retried from the failed step; a job whose worker stops
responding is retried. Workers heartbeat every 30 s, and every 15 s while they run a job however long its step takes,
with their machine's load. In Activity → Workers an admin can pause a worker (it takes no new jobs; the one it has
carries on to the end), drain it (it also hands that job back to the queue after the step it's on, so another worker
carries on, and stays paused) or resume it. `lens worker --name mac-mini` keeps its pause across restarts; the
server's own workers are named after its process, so they start afresh. Steps added to a job while it runs (attaching audio does) run after its other steps: a worker
reads the job's steps again before each step, and only finishes a job whose steps are all done.
`GET /api/v1/events` streams job progress (server-sent events). A run's whole log is kept (up to 100,000 lines) and
streams to its Activity page as it's written.

A run keeps a record of each step: when it started and finished, how long it took, how it ended (done, skipped,
failed), its last message (or why it skipped, or its error), which worker ran it, its lines of the log, and the outputs
it saved (with the template version and model that made them). A step with nothing to do for a recording skips
itself and says why: no LLM is configured, it isn't a video, the speakers came with the transcript. Retrying a run
starts the records over from the step it retries. Each finished or skipped step also adds its time to the last 25 of
its kind (template steps per template), and `GET /jobs/{jid}` turns these into how long each step usually takes on
that recording (scaled to its length for transcribe, diarize and the video steps) and about how long an active run
has left.

    lens worker --steps transcribe,diarize     # e.g. on the Mac, with SURREAL_URL pointing at the server

A worker that runs every step also scans watched folders and runs routines when they are due, so Docker and the
packages need no other process for them. One limited with `--steps`, or started with `--no-schedule`, only runs jobs.
A folder scan and a routine run are each claimed first, so several processes doing this never repeat one.

## Storage sources

Admins add sources in the app (`/api/v1/sources`): S3 or S3-compatible, Dropbox, Google Drive, OneDrive, SFTP, SMB,
WebDAV, or a folder on this machine; or an email account (IMAP) or a calendar feed (iCal), below. A watched folder maps a path on a source to a namespace, with include/exclude
patterns, audio and/or transcripts, a polling interval, how long a file must be unchanged before it is picked up, and
whether files already there are imported (backfill). New audio is queued for the full pipeline; new transcripts are
imported and analysed. Audio stays where it is: it is copied to a cache for processing and streamed from the source for
playback. A file whose recording someone deleted, or moved to another namespace, isn't imported again, even when it
changes.

Admins can also import chosen files of a source once, without watching their folder: Import → From a source, tick the
files, then Import (`POST /api/v1/import/source`). The listing marks files that are recordings already, and where.

- Credentials are encrypted in the database (AES-GCM, key from `ARCHIVE_SECRET_KEY` or `data_dir/secret.key`) and given
  to rclone in a private temporary config file per call. For Dropbox, Drive and OneDrive, paste the token from
  `rclone authorize dropbox` (or drive, onedrive); tokens rclone refreshes are saved back.
- Folders on this machine can only be watched inside `sources.local_roots`, and the rclone binary can only be set in
  archive.yaml: the web app can neither open up the server's disk nor choose what runs.

### Email (IMAP) and calendar feeds (iCal)

Two kinds of source aren't storage and don't use rclone; their messages and events are shown as files, so browsing,
importing chosen ones and watching work as above.

- **Email (IMAP)**: host, port, security (SSL/TLS, STARTTLS or none), user and password (an app password where the
  provider has them). Mailboxes are the folders, and each message is a file `<mailbox>/<uidvalidity>-<uid>.eml`,
  named by its subject (a mailbox the server rebuilds gets new names, so an old one never reads another message). A message is an email like any uploaded one: a document whose attachments are kept and made resources of
  their own, titled by its subject and dated when it was sent. On a server that can't make PDFs (no Chromium or
  LibreOffice) it comes in as text instead, without its attachments. Watching the whole account (no path) takes every
  mailbox but the bin, junk and drafts and the views of mail kept elsewhere (Gmail's All Mail, Starred and
  Important), and a message several mailboxes show (one Message-ID) becomes one resource. A watch remembers the last
  message it saw in each mailbox and asks only for newer ones. Lens only reads: mailboxes are opened read-only and
  messages fetched without marking them read.
- **Calendar feed (iCal)**: the calendar's iCal address (`https://` or `webcal://`; a user and password if it asks for
  one). The address is kept encrypted like a password and never shown again, since a private calendar's address is
  all it takes to read it. It is fetched the way web pages are captured: public addresses only (and the networks in
  `documents.web_networks`), on ports 80 and 443, and the password is never sent on to another server the calendar
  redirects to. The feed is one folder of events, each a file `<id>.ics` named by its date and title; a moved occurrence of a
  repeating event is an event of its own. An event comes in as text (its title, when and where, the organizer and
  attendees, how it repeats, and its description), dated when it starts: an all-day event on its date, wherever you
  are. Time zones are read as IANA names, Windows' names (as Outlook writes them), or the calendar's own VTIMEZONE. An event that changes (its LAST-MODIFIED,
  or its size) is read again into the resource it already is, rather than made a second one.
- `.eml` and `.ics` files in any source, and uploaded through Import, can be read as text this way too.

## Pipelines and templates

A pipeline is a named, versioned list of steps; each namespace can choose its default (otherwise: transcribe, diarize,
analyze, summarize, report). Imports, watched folders and reprocessing use the namespace's pipeline; steps that need
audio skip themselves for imported transcripts. Steps:

- `transcribe`, `diarize`, `analyze`, `summarize`, and `report` (the built-in report).
- `llm`: renders a prompt template, sends it to the configured model, checks the reply against the template's JSON
  Schema and saves it as a named output (e.g. `meeting_notes`).
- `report` with a template: renders an HTML page into the namespace's reports. These pages may not run scripts.
- `export`: renders a template to a file (Markdown, text, HTML...) and can copy it to a storage source.

Any step can carry a condition: `min_minutes`, `max_minutes`, `source` (audio or transcript), `languages`.

Templates are versioned (publish, history, diff) and rendered in a sandboxed Jinja environment. It can't reach Python
internals, caps output size, and escapes HTML in reports. Templates see `recording`, `speakers`, `segments`,
`transcript` (trimmed to `llm.max_chars`), `sections`, `entities`, `keywords`, `summary`, `stats` and `outputs`. The
summary's `key_points` and `action_items` print as their text and have `text`, `who` (empty when not said) and `t0`
(where the line they come from starts, in ms; none when not known); summaries made before they had times hold plain
strings. A fresh archive starts with three: Meeting notes (prompt), Markdown transcript (export) and One-page brief (report).
`POST /api/v1/templates/preview` renders any template, saved or not, against a recording, and with `run: true` also asks
the model.

## Workflows and the canvas

A pipeline's steps make assets (a transcript, shots, text on screen, faces); workflows turn them into metadata. A
workflow is a versioned graph drawn on a canvas (Pipelines → Workflows): it starts from the Recording node, and each
node passes what it makes along its connections.

- **Entities:** Extract entities (rules) runs the built-in extractor analyze uses, plus your terms (`Name|TYPE`) and
  regular expressions (`TYPE: pattern`); Extract entities (LLM) asks the model for structured entities and keeps,
  corrects or adds to any passed in; Merge joins lists; Save entities makes them the recording's entities (people's
  corrections kept) and redoes keywords and chapters.
- **AI and logic:** LLM prompt (a prompt template, with what came in as `{{ input }}`), Pick (a path like
  `action_items.0.text`), Condition (yes and no branches) and Merge.
- **Keeping results:** Save output (`outputs.<name>`), Set field (a custom field, kept in the metadata history) and
  Save entities.

A pipeline runs a workflow as a Workflow step, pinned to the workflow's published version when the run is queued
(`POST /api/v1/workflows/{id}/run` runs one on a recording). Pipelines can be drawn on the canvas too: a connection
means "runs after", and the graph is put in order (ties left to right) and kept as the version's steps, so runs and
Activity work as before. A pipeline saved as a list is drawn as a chain.

## Content types

Every resource is video, audio, image or text (transcripts, documents, web pages, emails and calendar events are
text), read from its file; a video file that hasn't been probed yet is told by its extension.
Under each base type is a vocabulary of content types (Pipelines → Content types): Lens starts with podcast, interview
and meeting (audio), screen-share tutorial and recorded meeting (video), photo and scanned page (image), transcript,
document, web page, email and calendar event (text), plus a general type for each base. Admins can rename and change
them, remove all but the general ones, and add their own. A default that's removed stays removed; defaults added in a
later release appear on upgrade.

A resource's content type is the one someone chose (its Details tab, `PUT /api/v1/recordings/{id}/content-type`), else
the first of its base type whose rules all match (file extensions, a pattern in the file name or title, a length),
else the general one. Patterns ignore case and see `_` as a space, so `\bcalls?\b` matches `team_call.mp3` but not
`recall.mp3`. The pipeline that runs is the one chosen for the run, else the namespace's override for the
content type, else the content type's pipeline, else the namespace default, else the standard pipeline. Content types
start without a pipeline, so nothing changes until someone sets one. Files found by a folder scan go through the same
choice as uploads.

## Routines

A routine does things on a schedule, like a cron job (Routines, admins only). It runs its actions in order over its
namespaces, or all of them:

- **Sync** scans watched folders now (all of the routine's namespaces' folders, or the ones you pick), so new files
  come in and run their pipelines.
- **Pipeline** queues a pipeline for recordings (one you pick, else the one each recording's content type gets, as
  above): new ones (since the routine last looked), unprocessed ones, or all of them.
- **Workflow** runs a workflow. One that runs on recordings is queued on them as a workflow step, pinned to its
  published version; one that organises the graph runs over the namespaces there and then.

The schedule is a five-field cron expression (minute hour day-of-month month day-of-week, e.g. `0 3 * * *`) or
`@hourly`, `@daily`, `@weekly`, `@monthly`, in a time zone; without one a routine runs only when someone presses Run
now. Every 30 seconds, `lens worker` checks (and so does the API process when it runs background work, and `lens watch`); a routine is
never started twice at once, and one started in two processes runs once. Each run keeps what every action did and a
log.

### Organising the graph

A workflow's scope is either recordings (above) or the graph. Graph workflows have their own nodes:

- **Candidates** finds pairs of entities that may be one thing, by the same rules as the merge suggestions (same
  letters, acronym, spelling, sounds alike, one name inside the other): inside each namespace (merge) or across
  namespaces whose graph is shared (link). Pairs someone said are different, and pairs already proposed, are skipped.
- **Ask the model** (`llm_judge`) shows the model each pair with lines where the names were said and asks whether
  they are the same thing, how sure it is, and which name to keep.
- **Filter** keeps the pairs that pass a test, e.g. `verdict.same` equals true.
- **Apply changes** merges or links the pairs whose confidence is at least *apply above* (at most *max apply* a run)
  and proposes the rest. Without *apply above* it only proposes.

A fresh archive has the workflow *Organise the entity graph* (both kinds of candidates, the model, then merges at 95%
or more, 25 a run) and the routine *Organise the graph every night* that runs it at 03:00 UTC, switched off. Turn it
on, or Run now with *propose only* first to see what it would do.

Every change is recorded (`GET /api/v1/graph-changes`): editors of the namespaces involved accept or dismiss
proposals (dismissing says the two are different, so the pair isn't suggested again) and undo applied changes; an
admin can undo everything a run applied at once (`POST /api/v1/routine-runs/{id}/undo`). Merges are undone exactly as
from an entity's page.
