# Processing

How recordings move through Lens: where they come from, what each step does, and how speakers, entities and the knowledge graph are built. Every step can be started from the web app, the API or the `lens` command line.

## Steps

- `scan` finds audio under each namespace's paths and fingerprints it: moved files keep their history, duplicates are skipped,
  and so are files whose recording someone deleted or moved to another namespace (at the same path, or a copy of the same
  file).
- `transcribe` uses SenseVoice, faster-whisper or mlx-whisper. A file that fails is marked and the batch carries on. For a
  document or an image it draws the pages and reads their text instead: a PDF's own text, and OCR for scans and images
  ([Documents and images](configuration.md#documents-and-images)).
- `diarize` splits genuinely two-channel files by channel, otherwise clusters voice embeddings (or uses pyannote), then
  matches voiceprints against the namespace's speakers.
- `embed` turns each line of the transcript (or block of a document's text) and each description of a page or a shot
  into a vector with a small sentence-embedding model, so that search can find them by what they mean
  ([Search by meaning](configuration.md#search-by-meaning)). It embeds only what's new or changed since it last ran.
  While search by meaning is off the step is skipped, and its job says so; it's also skipped, saying why, when
  ONNX Runtime and tokenizers aren't installed or the model can't be fetched.
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

- **Index** (`GET /api/v1/entities`):
  - Search forgives misspellings and covers aliases.
  - Filters: type, namespace, speaker, recording, date range, minimum mentions, and hidden entities.
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
  - **Change type, and hide or restore.**
  - **Merge:** with undo.
  - **Mark two entities as not the same.**
  - **Move or remove a single mention.**
  - **Link the same thing across namespaces.**
- **Merge suggestions:** same letters (ignoring case, spaces and punctuation), acronyms, one name containing the other,
  close spellings, and names that sound alike (likely transcription errors).

Curation survives re-analysis: merged names become aliases, and moved or removed mentions become per-line overrides.
Everything is audited. People who can't read a namespace never see its entities, mentions or graph nodes, and requests
about them come back as not found.

## Search

All words must appear, matched after English stemming ("exploit" also finds exploits and exploiting); "quoted phrases"
must appear as written; OR separates alternatives. Filter by namespace, speaker, emotion or recording. For archives that
aren't in English set `search.stemming: none` and run `lens reindex`. Prefix search (`expl*`) from the SQLite
version is gone; stemming covers most of what it was used for.

Where an admin switched it on, search also works **by meaning**: asked for "sailors", it finds the line about the ship
leaving the harbour, which has none of the words. The search page's Meaning switch asks for it (`semantic=true` on the
API). Passages the words find and passages that mean the same are ranked together, by `search.semantic_weight`; each
hit says whether its words matched, its meaning, or both. Filters, facets and access work the same: a passage you may
not read is never found, by words or by meaning. A corrected line is found by its old meaning no longer, and by its
new one once the embed step has run again (the correction queues it).

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

## Storage sources

Admins add sources in the app (`/api/v1/sources`): S3 or S3-compatible, Dropbox, Google Drive, OneDrive, SFTP, SMB,
WebDAV, or a folder on this machine. A watched folder maps a path on a source to a namespace, with include/exclude
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
