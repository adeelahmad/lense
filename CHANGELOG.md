# Changelog

The backend (`fastapi_backend`) and the frontend (`nextjs-frontend`) are versioned together.

## Unreleased

- **Fix (security): only the server's own media links are signed.** Text shaped like a media link
  (`/api/v1/recordings/12/audio`) came back signed: titles and transcript lines in API responses, and anything in the
  embed and report pages, including the transcript data inside them. Someone who could rename a recording or correct
  its transcript could get a working link to the audio, video or frames of any recording, in namespaces they have no
  role in. Responses now sign only the fields that hold links; the embed page signs only its recording's links, and a
  report page only those of its namespace's recordings.
- **Library: filters, sorting and counts on the server.** The Library's filters, tabs and sorting now look at every
  recording you can read, not just the rows loaded so far, and the page no longer says it can't.
  `GET /api/v1/recordings` takes `q`, `status` (the recording statuses plus the job states `processing` and `failed`),
  `attention`, `processing`, `speaker`, `from`/`to`, `min_duration`/`max_duration`, `media` and `sort`, and says how
  many recordings match in the `X-Total-Count` response header (documented in `docs/api.md`).
    - The body is still the list of rows, so existing API clients keep working; the total is a header rather than a
      new envelope for that reason (it is exposed to cross-origin browsers too).
    - `from`/`to` are days (both included) and durations are seconds, so the API doesn't bake in the web app's ranges.
    - `speaker` takes speaker ids. Speakers belong to one namespace, so the web app's speaker filter lists everyone
      who speaks in the namespaces in scope by name and sends every id with that name.
    - The Needs attention and Processing tab counts come from the server too.
- **Whole job logs.** A run's Activity page shows its whole log, streaming as it's written, and downloads it all; it
  was the last 200 lines. Runs keep up to 100,000 lines in a new `job_log` table, written in chunks every couple of
  seconds; the job still carries its last 200 (`log`) and now how many there are (`log_total`).
  `GET /api/v1/jobs/{jid}/log?after=&limit=` pages through them, and `GET /api/v1/events?logs=<jid>` follows one job
  with `log` events `{job, start, lines}` (docs/api.md). Runs from before this keep their last 200 lines.
- **Custom fields.** Editors define their own metadata fields on a namespace or on a collection, for the resources,
  the collections or the files inside it: text, long text, number, date, yes/no, one or several of a list, or a link.
  Decided with the project owner: fields live where they're defined and apply to everything inside; each is published
  (public pages and IIIF) or internal (the workspace only), and new fields are internal.
    - Values are checked by type and saved only where someone may edit the item. A resource's values are part of its
      metadata history, so a revert puts them back; deleting a field deletes its values (after saying how many).
      Defining, changing and deleting fields and saving values are audited.
    - Web app: Manage collections → Fields of the namespace…, and Fields… and Describe… on each collection; the
      recording page's Metadata tab (and the metadata page) and a file's Fields… take the values; the Library filters
      by a field (Field chip), and saved views keep it.
    - API: `/api/v1/namespaces/{name}/fields` and the `fields` of resources, collections and files; `field`/`value` on
      `GET /api/v1/resources` (docs/api.md#fields). Answers "Custom fields in a namespace profile" (docs/backend-gaps.md).
- **Files: transcripts, captions, translations, indexes, thumbnails and attachments beside a resource.** A
  resource has its primary file (the audio or video its pipeline runs on) and now any number of supplementary files,
  each with a role, a language, a label and a description. Decided with the project owner: typed files, parsed.
    - Transcripts, captions and translations (text, Markdown, JSON, SRT, WebVTT, Word, PDF) and indexes (WebVTT or SRT
      chapters, JSON, OHMS XML, or lines that start with a time) are read into lines that search finds next to what
      was said and shown on screen (`source: "file"`). Lines play from their times; a file without times opens on its
      line instead.
    - The recording page's Files tab (under More) lists the primary file and the others to download, shows their
      lines, and lets editors add, describe, re-role and delete files. Adding, changing and deleting are audited
      (`file.add`, `file.update`, `file.delete`).
    - A public resource opens its files with its parts (decided with the project owner): transcripts, captions and
      translations with its transcript, indexes with its index, thumbnails with its media; attachments always need
      permission. The public page lists the ones a visitor may download.
    - IIIF lists every file as a download (closed ones behind the Authorization Flow), timed transcripts, captions and
      translations as WebVTT captions, indexes as tables of contents, and a thumbnail as the Manifest's.
    - API: `/api/v1/resources/{rid}/files` (list, add as the raw body, change, delete, download, lines), up to
      `server.max_upload_mb` each (docs/api.md#files). Files are kept in `data_dir/files/<resource>/`; they stay when a
      resource moves and go when it's deleted.
- **Resources: `/api/v1/resources` names what the archive holds.** The API's recordings are now its resources: every
  `/api/v1/recordings/…` path is published as `/api/v1/resources/…`, the schema's tag and the generated client's
  class are `resources`/`Resources`, and the web app's pages are at `/resources/<id>`. Decided with the project owner:
  the old names stay as aliases. `/api/v1/recordings/…` reaches the same routes for existing clients and scripts, and
  `/recordings/<id>` in the web app redirects (keeping `?t=`). Fields keep their names (`recording`, and the
  `Recording…` types), and signed media links the server writes keep their form (docs/api.md#resources).
- **Roles on collections.** People can be given a role on a collection (viewer, editor or admin), which holds for
  the collections inside it too and adds to their namespace role. Someone without a role in the namespace sees just
  those collections: their recordings in the Library, search and the recordings' pages; the namespace's own pages
  (Speakers, Graph, Reports, Chat, Activity) stay with its members and say so. An admin of a collection runs it like an
  owner: its people, its sub-collections, and owner actions on its recordings. Decided with the project owner.
    - In the Library: Collection → Manage collections → People… gives, changes and takes away roles; each collection
      offers only what you may do with it. A namespace seen in part shows "Some" in the namespace picker.
    - API: `GET/PUT /namespaces/{name}/collections/{cid}/members` (owners of the namespace, admins of the collection;
      audited as `collection.member`); collections say your `role`, `can_change` and `can_grant`; recordings and
      Library rows carry your `role` on each; `GET /namespaces` lists namespaces seen in part (`partial`) and
      `GET /auth/me` names them; search, the recording's runs and entities, notes and IIIF follow (docs/access.md,
      docs/api.md).
- **Collections inside namespaces.** Every recording now lives in one collection of its namespace, and collections
  nest. Each namespace starts with "General", its default, which took every existing recording. The Library filters by
  collection (with the ones inside it) and its Manage collections dialog makes, renames, moves, describes and deletes
  them and picks the default; the bulk bar's Collection moves recordings into one. A recording's breadcrumb shows where
  it lives and its ⋯ menu moves it. Imports, uploads, source imports and IIIF imports choose a collection, and saved
  views keep one. Decided with the project owner: one home per recording, nesting, saved collections stay as they are
  (lists of recordings from anywhere).
    - `GET/POST /api/v1/namespaces/{name}/collections`, `GET/PATCH/DELETE …/{cid}` (editors change them; audited as
      `collection.create`, `.update`, `.delete`), `POST /api/v1/recordings/collection` (audited as
      `recording.collection`), and `collection` on `GET /recordings`, recordings, imports, uploads and moves
      (docs/api.md). A collection is deleted only when empty and not the default, so it never takes recordings with
      it.
    - **IIIF: a namespace's Collection now lists its collections**, not its Manifests: each collection is a
      Collection at `/iiif/collection/<namespace>/<id>` holding the collections inside it and then its Manifests, and
      a Manifest's `partOf` is its collection (docs/iiif.md). Harvesters that only read Manifests at the namespace's
      level need to follow the Collections inside it. Importing from IIIF does: it now finds the Manifests of nested
      Collections, so one Lens can still import another's namespace.
- **Fix: Escape in a menu inside a dialog closed the dialog too.** Menus used an older copy of the layering that
  dialogs use, so each thought it was on top; `@radix-ui/react-dropdown-menu` is now 2.1.24, which shares the
  dialogs' copy, and Escape closes only the menu.
- **Admins decide how long API keys last.** Settings → API keys sets how long a new key lasts (90 days unless
  changed), the most it may last (365) and whether keys may never expire (not by default; until now anyone could
  make one that never expires). The API tokens page offers what's allowed. The same section lists everyone's keys, and
  an admin can revoke any of them.
    - `tokens.default_days`, `tokens.max_days`, `tokens.never_expire` (docs/configuration.md); `POST /api/v1/tokens`
      leaves out `days` for the default, and answers 400 past the limits. `GET /api/v1/tokens/limits` says them.
    - `GET /api/v1/admin/tokens`, `DELETE /api/v1/admin/tokens/{id}` (admins). Revoking a key, yours or anyone's, is
      audited as `token.revoke`. Keys made before a change keep their expiry.
- **Summaries: key points and action items say when.** The Summarize step now gives key points as well, and each key
  point and action item comes with the time of the line it's from (and who will do an action item, when said); the
  Summary tab shows the time, which plays from there, as do the report page and the embedded player.
    - The summary's `key_points` and `action_items` are `{text, who?, t0?}` (`t0` in ms; docs/api.md); the model is
      asked to cite each line's time, and a cited time becomes the start of the line shown with it. Summaries made
      before this keep plain strings, which everything still reads.
    - In templates, these items print as their text, so templates written for strings keep working; `.who` and `.t0`
      are there for new ones (docs/processing.md).
- **Split and join transcript lines; the word being said lights up.** In Edit, a line splits at the cursor
  (Shift+Enter, or Split here), optionally giving the rest to another speaker (Split, the rest is…), and joins the next
  line (Join with next line, or Delete at its end; Backspace at its start joins the line above). Undo takes them back
  like other corrections, and the change history lists them. When transcription timed the words, the one being said
  is highlighted as the recording plays.
    - `POST /api/v1/recordings/{rid}/segments/{idx}/split {at, t?, speaker?}` and `…/merge` (docs/api.md). The lines
      after it are renumbered, and the edit history, entity corrections and chapters follow; the words keep their
      timings; Analyze runs again, as after a correction. Audited as `transcript.split` and `transcript.merge`;
      `GET …/edits` gives each change's `kind`, in the order made even within a second.
    - Lines in `GET …/player` carry their timed words as `w` (character ranges of the text, with times), so a
      corrected line keeps the timings of the words it still has. The highlight uses the browser's CSS Custom
      Highlight API; browsers without it keep the line's tint.
- **Notes on recordings.** Select words in the transcript and choose Add note, or write one in the new Notes tab about
  where the player is or about the whole recording. Notes are yours; editors can share theirs with everyone who can
  read the recording (decided with the project owner, like saved views). Turns with notes get a mark that opens the
  tab, and each note's time plays from there.
    - `GET`/`POST /api/v1/recordings/{rid}/notes`, `PATCH`/`DELETE …/notes/{nid}` (new `note` table; docs/api.md):
      text, an optional moment (`t0`, `t1` in ms) and quote, and `shared`. Only its writer changes a note; its writer,
      or an owner of the namespace for a shared one, deletes it. Sharing, unsharing and deleting a shared note are
      audited.
    - Notes move with their recording and go when it's deleted.
- **Chat: draw on collections as they are.** Scoping a conversation to a collection keeps the collection, not a copy
  of its first 200 recordings: the assistant reads its recordings each time it answers, so a filter collection's new
  recordings count. Several collections can be picked; the scope chip names them.
    - Chat scopes take `collections` (ids of collections you can see, yours or shared; docs/api.md). They narrow it
      like the other keys, also for the assistant's tools; a deleted collection adds nothing.
    - `/chat?collection=<id>` (a collection's Chat) starts a conversation scoped to it.
- **Search: "Try …" whole words for a prefix.** Searching `interp*` finds nothing (there's no prefix search); the page
  now offers the words said in your namespaces that start with it ("Try interpretability or interpreter"), and picking
  one searches for it. `GET /api/v1/search/terms?prefix=` lists them, the most said first (docs/api.md).
- **Search: saved searches keep every filter.** Save keeps a search's words and all its filters (namespace, speaker,
  emotion, recording) in Saved searches; they were kept as filter collections, which can't hold emotion or recording.
  Like saved views, they're yours, and editors can share one with the namespace it searches; the panel lists yours,
  then the shared ones, each with a Delete for whoever may delete it.
    - `GET/POST /api/v1/searches`, `PATCH/DELETE /api/v1/searches/{sid}` (docs/api.md), kept with saved views as
      their own kind. Sharing, unsharing and deleting a shared one are audited.
    - "Also keep it as a collection" in the Save dialog still makes the collection, to chat with it or run things on
      it. Searches saved before this are collections and stay in Collections.
- **Search: filter counts over every match.** The Namespace, Speaker, Emotion and Recording counts next to the
  results cover every moment the words match, not the first 200 the page had loaded.
  `GET /api/v1/search?facets=true` returns them (`facets`, docs/api.md), counted on the server up to 20,000 moments;
  past that the panel says the counts cover 20,000 of them.
- **Library: Edited by me, and Source and Language filters.** The Edited by me tab lists the recordings you corrected
  (a line of the transcript), catalogued (the metadata record) or renamed, with a count like the other tabs. The
  Source filter picks where recordings came from: each connected source by name, Uploaded, Pasted text, IIIF imports,
  Archive folders (`lens scan`) and Imported files; the Language filter picks their languages, or "Not known".
    - `GET /api/v1/recordings` takes `edited_by=me`, `origin` and `language`, and its rows carry `origin`,
      `origin_name` and `language` (not the raw `path`/`remote`); `GET /api/v1/recordings/origins` and
      `GET /api/v1/recordings/languages` count them for the filters (docs/api.md).
    - Saved views keep the new tab and both filters.
- **Library: saved views.** Save view keeps what the Library shows (its namespace, tab, filters and sort) under a
  name, and Views brings it back. Views are yours; editors can share one with its namespace, and everyone with a role
  there then sees it (decided with the project owner). Only its maker changes a view; its maker or an owner of the
  namespace deletes a shared one.
    - `GET/POST /api/v1/views`, `PATCH/DELETE /api/v1/views/{vid}` (docs/api.md), in a new `saved_view` table. Date
      and length filters are kept as the Library's ranges, so "last 30 days" stays the last 30 days; the speaker is
      kept by name and found again in the view's namespace.
    - In Views, your own views can take what the Library shows now, and be shared or unshared. Sharing, unsharing and
      deleting a shared view are audited.
- **Reports: a namespace's numbers come from the server, for the range you pick.** The overview's recordings, hours,
  speakers, months and top speakers come from a new `GET /api/v1/namespaces/{name}/stats?from=&to=` (docs/api.md);
  the page used to load every recording of the namespace (up to 10,000) and count in the browser. Top speakers are
  now the range's, by talk time in its recordings; they were all-time. The table of recording reports asks for the
  range's latest 25.
    - `from`/`to` are days, both included, like the Library's date filter; months run from the range's start (or the
      first recording) to its end (or this month), empty ones included.
    - Recordings without a date count only in All time, and in no month; the page says so when there are some.
- **Share links: revoke one, see plays and where it's embedded, short addresses, and a page for dead links.** The
  Share dialog lists each link with how often its player was played and the sites that embed it, and revokes one
  link (Revoke…) as well as all of them. A new link comes with a short address, `/s/<code>`, which opens the same
  player and can be framed like it. An expired, revoked or mistyped link opens a neutral "This link isn't available"
  page with status 410 instead of a JSON 401, in the frame of a host page too.
    - `DELETE /api/v1/recordings/{rid}/shares/{id}` revokes one link; `POST .../share` returns `id` and `short`;
      `GET .../shares` adds `revoked`, `revoked_by`, `revoked_at`, `short`, `plays`, `played_at` and `embedded_on`
      (docs/api.md). Revoking is audited as `share.revoke` with the link (or how many links).
    - A play is the shared player starting to play, counted once per page load; the page reports it to
      `POST /embed/{rid}/played`. The sites come from the Referer's origin when the browser says the player is in a
      frame (a new `share_embed` table, up to 50 sites per link). Lens's own pages, such as the embed builder's
      preview, count neither.
    - Short codes are ten characters without look-alikes, and only their hashes are kept, like the token's; links
      made before this have none. `/s` is proxied by the web app like `/embed`.
    - The 410 page is the same whatever was wrong with the link, so it never tells whether a recording exists; a
      signed embed link that expired or was tampered with gets it too.
- **Speakers: "not the same", unlink, and who merged.** The review queue's Not the same works: the pair is dropped
  and never suggested again. Other namespaces lists likely matching voices with how alike they are (it read them
  off the graph, without a score), each with Not the same and Link, and linked speakers can be unlinked. Merge
  history says who merged, how many recordings moved, and who undid it.
    - `POST /api/v1/speakers/{sid}/not-same {with}` and `POST /api/v1/speakers/{sid}/unlink {with}`; `GET
      /api/v1/speakers` adds `cross` (`score`) and merge `by`, `recordings`, `segments`, `undone_by`, `undone_at`
      (docs/api.md). Pairs said to be different are kept in a new `not_same` table and left out of suggestions and
      of the graph's "maybe the same voice" edges.
    - Linking, unlinking, "not the same" and undoing a merge are audited (`speaker.link`, `speaker.unlink`,
      `speaker.not_same`, `speaker.merge.undo`).
- **Choose the model in Chat, and try another.** The model chip under the question box becomes a menu when there's a
  choice: the conversation then answers with the model you pick. Each answer offers Try another model, which asks
  the same question again with a different one, and says which model wrote it.
    - Which models: a new setting, `llm.chat_models` (Settings → LLM provider), lists them; left empty, whatever the
      model server lists (`GET /models`, kept a minute). The configured model is always offered and stays the
      default (docs/configuration.md).
    - `GET /api/v1/chats/capabilities` adds `models`; conversations take and return `model` (`POST /chats`,
      `PATCH /chats/{cid}`, null for the configured one); `POST /chats/{cid}/messages` takes `model` for one answer;
      messages carry the `model` that wrote them. A model that isn't offered is a 400; a conversation whose model is no
      longer offered answers with the configured one (docs/api.md).
- **Reopened conversations keep how answers were made.** An answer now keeps the tools the assistant used, any
  notice, the error when there was no answer, and its source check; reopening a conversation (or reloading) showed
  only the text. `GET /api/v1/chats/{cid}` returns `steps`, `notice`, `error` and `check` on each message
  (docs/api.md); answers from before this have none.
- **Stop an answer, and know the model, without being an admin.** Stop in Chat (and in a recording's Chat tab) now
  asks the server to end the answer after the piece or tool step it's on, and keeps what came before in the
  conversation, marked stopped; it used to stop reading, leaving the question without an answer and the model
  working. Everyone sees whether a language model is set up, and which, before asking; only admins could.
    - `GET /api/v1/chats/capabilities` (anyone signed in: `configured`, `model`, `tools`, `max_steps`, `check`; not
      the server's address or key) and `POST /api/v1/chats/{cid}/stop` (`{stopping}`); the answer stream sends
      `stopped` before `done`, and saved messages carry `stopped` (docs/api.md).
    - The stop is a flag on the conversation that the answer checks between pieces (every half second at most) and
      tool steps, so it works whichever server process is answering. A model call already under way finishes first;
      the web app stops reading after 15 s if the server hasn't ended the answer by then.
- **Runs of one namespace or one batch, from the server.** Activity's namespace filter asks the server, so the status
  counts and the 200 rows shown are that namespace's (they were the newest 200 of every namespace, filtered in the
  browser), and its menu says how many runs each namespace has. A batch run's page lists all of its jobs; it showed
  those among the 500 newest jobs of the archive. `GET /api/v1/jobs` takes `batch` and `namespace`, and returns
  `namespaces` (jobs per namespace) next to `counts`; `limit` goes up to 2,000 (docs/api.md). Live updates keep each
  filtered list to its own jobs.
- **Pause, drain and resume workers.** Activity → Workers lets admins pause a worker (it takes no new runs; the one
  it has carries on to the end), drain it (it also hands that run back to the queue after the step it's on, so another
  worker carries on, and stays paused) and resume it. Each card shows the machine's CPU load and the steps the worker
  finished in the last hour. A run waiting only on paused workers says so.
    - `POST /api/v1/workers/{name}/pause|drain|resume` (admins, audited as `worker.<action>`); `GET /api/v1/workers`
      adds `paused`, `draining`, `paused_by`, `paused_at`, `load`, `cpus` and `steps_last_hour` (docs/api.md).
    - A pause lives on the worker's record, so `lens worker --name …` stays paused across restarts; the server's own
      workers are named after its process and start afresh.
    - Draining an idle worker just pauses it; a drain that arrives as its run ends doesn't outlive the run.
- **Fix: a busy worker looked silent.** Workers only heartbeat between runs, so one on a long step (a long
  transcription) showed as silent. They now heartbeat every 15 s while they run, whatever the step.
- **What each step of a run did.** A run's Activity page shows, for each step, when it started and finished, how long
  it took, how it ended, its last message, which worker ran it, the outputs it saved (with the template version and
  model that made them) and exactly its own lines of the log; while a run is active, how long each step usually takes
  and about how long the run has left. Runs name their pipeline and the version they pinned, in the list too, and
  Activity's Pipeline filter works. The recording's History reads the same records.
    - Jobs carry `pipeline` and `title` everywhere; `GET /api/v1/jobs/{jid}` adds `step_runs`, `estimates` and
      `eta_seconds` (docs/api.md). Runs from before this are read from their log, as before.
    - A step with nothing to do now says it skipped, and why (`<step> skipped: no LLM is configured`), instead of
      logging a skip as a success; transcribe, diarize, summarize and the video steps do.
    - Estimates come from the last 25 times each kind of step ran, kept in a new `step_stat` table: per template for
      template steps (the namespace's report pages and a report template are timed apart), and per minute of
      recording for transcribe, diarize and the video steps.
    - Retrying a run starts its step records over from the step it retries; a run that fails because its worker went
      silent records that on the step, and now has a finish time.
- **Fix: History squeezed its runs.** With several runs, the recording's History tab shrank each run's card until
  its steps were hidden.
- **Fix: opening a SurrealDB server database could fail.** Opening a connection to a server creates the database if
  it's new, and two processes doing that at once (workers starting together, or tests in parallel) could hit a write
  conflict and stop with "cannot open SurrealDB". Opening now retries a conflict, as queries already did.
- **Import chosen files from a source.** Import → From a source lets admins tick files and import them now, into a
  namespace and with the pipeline of their choice, instead of only watching the folder. The listing marks files that
  are recordings already, and where. `POST /api/v1/import/source {source, paths, namespace, pipeline?}` answers per
  file (queued, already, skipped or error) and is audited as `import.source`; `GET /api/v1/sources/{sid}/browse`
  gives each file's `imported`. A file chosen on purpose comes back even if its recording was deleted.
- **Choose the pipeline at import.** Import's "Then run" picks any saved pipeline instead of the namespace's, for
  uploaded transcripts, pasted text and audio or video uploads. `POST /api/v1/import` and `POST /api/v1/uploads` take
  `pipeline` (400 for one that doesn't exist, before anything is saved); attaching audio to a transcript runs its own
  steps, so it doesn't take one.
- **Attach audio to a transcript.** A transcript-only recording gets its audio or video from its page ("Attach audio",
  also in the ⋯ menu on phones), from Import → Paste ("Attach audio…"), or by dropping a transcript together with the
  audio of the same name on Import → Upload, which now makes one recording of the pair instead of two. The file goes up
  in pieces like any upload (`POST /api/v1/uploads` with `recording`) and becomes the recording's media: the transcript
  and its speakers stay, the waveform is drawn, speakers are told apart by voice when the transcript didn't name them,
  and video gets shots, text on screen and faces (docs/processing.md).
    - The steps that need media are added to the recording's job when it has one (the import's, say), after what it
      still has to do. Workers now read a job's steps again before each step and only finish a job whose steps are
      all done, so steps added while it runs aren't missed.
- **Upload audio and video.** Import → Upload takes audio and video as well as transcripts. Files go up in pieces
  (`uploads.chunk_mb`, 8 MB), each written straight to disk on the server; a piece that fails is sent again, an upload
  can be paused and resumed, and choosing the same file again after a reload carries on where it stopped. When the
  last piece arrives the file becomes a recording and the namespace's pipeline is queued; a file the namespace already
  has finds that recording instead of making a second one. `GET/POST /api/v1/uploads`, `GET/PUT/DELETE
  /api/v1/uploads/{uid}` and `GET /api/v1/uploads/limits` (docs/api.md); audited as `upload`.
    - New settings (Settings → Uploads, docs/configuration.md): `uploads.max_mb` (4096), `uploads.extensions` (the
      types folder scans import), `uploads.chunk_mb` and `uploads.expire_hours` (24: unfinished uploads are then
      deleted). The transcript limit, `server.max_upload_mb`, is unchanged.
    - Uploaded files are kept in `data_dir/uploads/<namespace>/`, and an upload is refused (507) when it would leave
      the disk less than 512 MB free. Deleting a recording leaves its uploaded file, as with any media file.
    - The Import page reads its limits from the server for everyone, not only admins.
- **Your own name and password.** Everyone changes their own name on the Profile page, and their password with their
  current one: `PATCH /api/v1/auth/me` and `POST /api/v1/auth/password` (signed in, not with an API token; wrong
  guesses are throttled like sign-ins; audited as `password.change`). A new password ends your other sessions and
  keeps this one; your API tokens and this device stay signed in. The reset link stays for a forgotten password.
- **Tags on recordings.** Editors tag recordings from the Library's bulk bar (add tags, or take off ones they have);
  the Library filters by tags and shows them in a Tags column. `PATCH /api/v1/recordings/{rid}` takes `tags`,
  `POST /api/v1/recordings/tags` changes several at once, `GET /api/v1/recordings/tags` lists the tags in use, and
  `GET /api/v1/recordings?tag=` filters (any of the tags, ignoring case) (`docs/api.md`).
- **Move recordings to another namespace.** Owners move recordings from the Library's bulk bar to a namespace they
  edit: `POST /api/v1/recordings/{rid}/move`, audited as `recording.move` (`docs/api.md`).
    - They keep their transcripts, media, outputs, permissions and share links; a checkbox stops the share links
      working.
    - Their IIIF manifests stay as they were: the access, open parts and metadata defaults they had from their old
      namespace are pinned on them wherever the new one would change them (kept in their metadata history).
    - Speakers and faces are matched by name in the new namespace, or start there with each recording's voice and
      face; a checkbox identifies them again from their voices instead (audio only). Analysis runs again there, so
      entity mentions (and corrections to them) start over.
    - Report pages and exports move to the new namespace's folders; the old namespace's scans and watched folders don't
      import the files again, and its IP groups no longer open them. 409 when the new namespace already has the same
      file, or while a job is running.
- **Delete recordings.** Owners delete recordings from the Library's bulk bar, after a confirmation that lists them;
  `DELETE /api/v1/recordings/{rid}`, audited as `recording.delete` (`docs/api.md`).
    - Everything Lens made from a recording goes with it: transcript, analysis, frames, outputs and reports, shares,
      permissions and requests for access, and its place in IP groups, fixed collections, chat scopes and batch runs
      that haven't reached it. Unnamed speakers and faces only it had go too; named ones stay.
    - The media file stays where it is, and isn't imported again: scans skip the same path or the same file elsewhere,
      watched folders skip the same remote file. Importing it on purpose brings it back.
    - Its waiting jobs are cancelled; a running job holds the delete back (409), and a worker whose recording is gone
      stops. A deleted recording's failed jobs can't be retried, batch runs continue without it, and undoing an entity
      merge no longer brings back mentions in deleted recordings. Public recordings show up as a Delete in IIIF change
      discovery; the namespace's report overview is rewritten in the background.
- **Rename recordings.** Editors rename a recording from the pencil next to its title (or ⋯ → Rename on a phone):
  `PATCH /api/v1/recordings/{rid}` with `title`, audited as `recording.rename`. The report page is renamed with it,
  so its link keeps working, and a report job rewrites the title inside; IIIF harvesters see an Update.
- **Access: public, restricted or private** (after Aviary's roles and permissions matrix; `docs/access.md`). Every
  recording is public, restricted or private, following its namespace's default unless it sets its own; a public one
  chooses which parts anyone may use (media, transcript, index) and can be featured. This replaces the four IIIF-only
  access levels.
    - **Converted on first start, once:** `public` stays public; `transcript` becomes public with the media closed;
      `signed-in` becomes restricted; `private` stays private. Namespace defaults convert the same way. Restricted
      recordings aren't published in IIIF (the old `signed-in` ones were listed), so the conversion announces a
      Delete for each in the change feed.
    - **Publishing is for owners**, as the web app already said: changing a recording's access, open parts or
      featured flag needs the owner role, through `PUT /api/v1/recordings/{rid}/access`, metadata edits, bulk edits
      and reverts alike (editors got a 403 only from the web app before). Changes are audited as `recording.access`.
    - `GET /api/v1/recordings/{rid}/access`; the recording list and detail carry `access`, `open` and `featured`,
      and the list filters by `access` and `featured`.
    - The web app shows a recording's access next to its status (click it to change it), marks public and
      restricted recordings in the Library, and the IIIF panel, metadata editor, metadata profile and Publish dialog
      use the new setting.
- **A recording's public page**, `/explore/recordings/<id>`, for anyone, signed in or not (Aviary's resource detail
  page). Visitors see a public recording's description and its open parts: the player, the transcript with find and
  downloads, the chapters; closed parts say who can open them. Signed-in people without permission see a restricted
  recording's title behind a lock; members see all of it, with a link back to the workspace. Restricted and private
  recordings are "not available" to everyone else. Owners copy the link from the Access dialog.
  `GET /api/v1/public/recordings/{rid}` answers with only what the caller may use (`docs/api.md`).
- **Explore: the public home page and collections** (Aviary's home and collection splash pages). `/explore` shows
  the featured public recordings, to everyone, and the collections the visitor can browse; a collection's page shows
  its description and recordings: public ones for everyone, restricted ones behind a lock ("content locked") for
  signed-in people, all of them for members. The sign-in page links there. `GET /api/v1/public/home` and
  `GET /api/v1/public/collections/{name}`.
- **Explore: search** (Aviary's search results page). `/explore/search` finds recordings by their title, and by what's
  said in the transcripts the visitor may read, with the matching lines; each line opens the recording at that moment
  (`?t=`). Restricted recordings (for signed-in people, locked) and closed transcripts match on the title only, so a
  search never reveals what they say. A search box sits on the home page and in the header.
  `GET /api/v1/public/search`; `search()` can be limited to a set of recordings.
- **Permission on a recording** (Aviary's "registered user with view permission"). Owners give a person with an
  account permission on one recording from its Access dialog, by email address, and take it away there (audited).
  They then see all of it on the pages for visitors and in IIIF, whatever its access; private recordings are listed
  for them, and the home page shows what's shared with them. `GET`/`POST /api/v1/recordings/{rid}/permissions`,
  `DELETE /api/v1/recordings/{rid}/permissions/{account}`.
    - Fix: IIIF resources of a recording that isn't public now also open with the IIIF access cookie, and the
      signed links the auth probe hands out work for them (they answered 404, so viewers couldn't play them).
- **Asking for access** (Aviary's "request access"). Signed-in people without permission ask a recording's owners for
  access from its page: to a public recording's closed parts, or to a restricted one, with an optional message; the
  page shows where their request stands. Owners hear of it by email (when mail is set up), in the recording's Access
  dialog and on Home under Needs attention, and approve (which gives permission) or decline, audited.
  `POST /api/v1/public/recordings/{rid}/request`, `GET /api/v1/recordings/{rid}/requests`,
  `POST …/requests/{account}/approve|decline`, `GET /api/v1/access-requests`.
- **IP groups** (Aviary's "public user with view permission in an IP group"). Owners name address ranges on their
  namespace's page (a reading room, a campus) that open every recording in the namespace, or the ones chosen in each
  recording's Access dialog. Visitors from those addresses see what a group opens without signing in: in collections,
  search, a recording's page ("You're connecting from Reading room") and IIIF, the Authorization Flow's probe
  included. Groups and choices are audited. `/api/v1/namespaces/{name}/ip-groups` (list with your address as the
  server sees it, add, change, delete) and `/api/v1/recordings/{rid}/ip-groups` (open or close one recording).
    - The visitor's address comes from `X-Forwarded-For` only when the request arrives from a trusted proxy: the new
      `server.trusted_proxies` setting (Settings → Access & embedding; default this machine). A proxy that isn't
      trusted, or the web app asking on its own behalf, counts for no group. Behind the web app, a reverse proxy that
      sets the header is needed, or visitors could claim any address (`docs/configuration.md`, Trusted proxies).
    - Server-side page titles for visitors pass their address on, so a reading room sees the right titles.

Tooling:

- `make openapi`, the dev watcher and the pre-commit hook write the same client: the generator reads its input as an
  absolute path now (given a bare `openapi.json`, it had baked that name into the client's `baseUrl` type).

## 0.3.0 <small>September 30, 2026</small> {id="0.3.0"}

The web app implements the Lens Archive design (built on the Aladdin design system).

- **Foundations:** design tokens with a dark theme and speaker and emotion palettes, self-hosted fonts (DM Sans,
  Source Serif 4 for transcripts, JetBrains Mono), and the design's components: buttons that explain why they're
  disabled, status, role, speaker, emotion and job-step chips, secret fields, tables with sorting and paging, dialogs,
  drawers, toasts, banners, the four-colour step loop and verdict cards.
- **App shell:** collapsible navigation, namespace switcher, ⌘K palette, live activity drawer, account menu with
  appearance and keyboard shortcuts.
- **Screens:** Home, Library, Import, Reports; the audio and video recording pages with the player; Search; Chat with
  citations, tool steps and approvals; Speakers; Graph; Batch runs and Collections; Activity; Sources; Pipelines and
  Templates; Sharing and Embed; Settings; Admin (people, members, audit, health); IIIF metadata and publishing;
  sign-in, first-run setup, password reset, API tokens, and signed-out-mid-task recovery.
- Every screen follows the role rule (actions you can't take are disabled with a reason; namespaces you have no role
  in never appear), has loading, empty and error states, and works in dark mode and at phone width.
- The web app forwards API, media, embed, IIIF and report paths at request time, so one build works against any
  API address.
- Features the design shows but the API doesn't support yet are disabled with a reason and listed in
  `docs/backend-gaps.md`.

Backend:

- Fix: on the embedded engine, search silently missed most segments after a restart (the full-text index lost
  entries on reopen). The index is now repaired once per process before the first search.
- Fix: IIIF "Open in" viewer links can be saved in Settings (only http(s) URLs are accepted).
- The job event stream opens immediately.

Tooling:

- CI reports coverage in each run's summary and keeps the reports as artifacts (Coveralls needs a paid plan for
  private repositories). The pnpm version comes from the frontend's `package.json`.
- pre-commit installs only the dev tools (the processing extras include macOS-only packages), and every hook passes:
  the backend is formatted with Ruff throughout, the frontend with Prettier at 120 columns.

## 0.2.0 <small>September 30, 2026</small> {id="0.2.0"}

Lens moves from a single-process prototype to a platform on the Next.js FastAPI template.

- **Backend**
    - The prototype's processing engine now lives in `app/domain`, unchanged in behaviour.
    - The API is split into routers under `/api/v1` with Pydantic request and response models, so the OpenAPI schema
      and the frontend's typed client cover every endpoint.
    - Authentication: short-lived JWT access tokens and rotating refresh tokens (with reuse detection) for NextAuth,
      replacing session cookies and CSRF tokens; password reset by email; API tokens unchanged.
    - Media is served through signed links, since `<audio>` and `<img>` can't send bearer tokens.
    - SurrealDB: a connection pool for servers, and automatic retries of write conflicts.
    - Workers run as their own process (`lens worker`); the API can still run them inline for development.
    - Removed the template's Postgres, SQLAlchemy, Alembic, fastapi-users and Vercel backend deployment.
- **Frontend**
    - NextAuth (Auth.js v5) with a credentials provider backed by the API, token refresh, first-run setup and password
      reset. Public registration is gone: admins invite people.
    - A typed API client wired to the session, and rewrites so media and IIIF are served from the web app's origin.
- **Operations**
    - Docker Compose for development (SurrealDB, API, worker, web app, MailHog) and a production-shaped compose file.
    - CI runs lint, type checks, an OpenAPI drift check and the tests against embedded SurrealDB and a SurrealDB server.
