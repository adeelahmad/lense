# Dev log

Plans and progress for work in flight. Newest first.

## 2026-10-18 · Secrets from Vaultwarden

Adeel: a secrets manager, Vaultwarden first, with others to come. A secret field in Settings can hold a reference
(`vaultwarden://OpenAI/password`) instead of the secret; `secret_providers.py` reads it when the settings are built,
keeps it in memory for `secrets.cache_minutes` and reads it again in the background, and anything that shows settings
shows the reference, never the value. The built-in sealed store stays the default. Vaultwarden is read with
python-vaultwarden (Apache-2.0), fetched as a component once an address is set: the Bitwarden CLI is GPL and needs
Node, and Bitwarden's SDK isn't permissively licensed and reads only its Secrets Manager product. Lens reads one
folder of its own vault account. Tested against a fake Vaultwarden that encrypts with the library's own crypto.

Next: Settings › Secrets in the web app, with a picker for the vault's items beside every secret field.

Refine later: references in sources' credentials, MCP servers' environments and sign-in providers; checking a value
read from the vault the way a typed one is checked; a custom CA for a self-hosted vault; an environment variable for
the master password; more providers (1Password, HashiCorp Vault, Infisical).

## 2026-10-17 · Read any chat message aloud

Adeel: play any message in the chat through the TTS model. A speaker button on every question and answer reads it by
`POST /voice/speak` (so the provider layer, the voice picker's voice and the activity ledger are voice mode's), one
message at a time, in pieces of at most 1,500 characters (Deepgram's limit is 2,000; `speech.speak` cuts at 4,000),
with the next piece asked for while one plays. Audio is kept in the page by text, the 32 most recent pieces.

Refine later: keep audio across reloads (encrypted on the server, or the browser's Cache Storage); a shorter first
piece so long answers start sooner; voice mode reading answers through the same pieces; pause and resume rather than
stop; the speaker button in an asset's Chat tab; drop kept audio when the voice is changed.

## 2026-10-17 · Dates read as days

Adeel: dates extraction. `dates.py` reads the dates each line gives (written out, numeric, and relative to the day)
into `date_said` as first and last days, worked out from the asset's `recorded_at`, when Settings → Analysis → Read
dates is on. `GET /api/v1/dates`, MCP `list_dates` and the assistant's `find_dates` find them by day or range with
their lines. English rules in Lens's own code rather than dateparser: dateparser couldn't read "next Tuesday" or "this
Friday", and its own search over a line finds dates in ordinary words.

Refine later: DATE entities named by their day, so "next Tuesday" and "20 October" in two assets meet in the graph;
other languages' month and day names; a Dates view in the web app (a calendar of what was said); times of day.

## 2026-10-17 · Entities found by spaCy

Adeel: NLP libraries for named entities. Settings → Analysis → Entity extraction → spaCy existed but needed spaCy
installed by hand; it's now a component fetched the first time an asset needs it (the `spacy` extra and a pinned,
checksummed model wheel: English, German or the multi-language one, all MIT). The defined entities come first, spaCy's
names fill in around them, and the rules stand in until spaCy is there. Workflows' rules node uses the same extractor.

Refine later: pick the model by the transcript's language; spaCy's medium and large English models (MIT too, 40 to
560 MB) as options; run spaCy over a recording's lines in one batch.

## 2026-10-17 · Cleaning up transcripts before analysis

Adeel: a text cleaning step before entity extraction and transcript processing, from a punctuation and grammar
script he pasted. The `clean` step (cleanup.py) runs before `analyze` in the standard pipeline, off until Settings →
Analysis turns it on: rules take out fillers and stutters and tidy spacing and capitals, each change kept in the edit
history with Revert. The script's alternating "Speaker 1 / Speaker 2" labels aren't carried over: Lens tells voices
apart by diarization, or keeps the labels a transcript came with.

Next: punctuation restored by a local model (the script's deepmultilingualpunctuation, fetched on first use), grammar
from a LanguageTool server, spaCy fetched for named entities, and dates found and normalised.

Refine later: revert a whole cleanup at once; cleanup rules for other languages' fillers; per-namespace switch.

## 2026-10-17 · Describing pictures with Moondream on this machine

Moondream is the third describe engine (`llm.describe_engine` `moondream`), opt-in, through Hugging Face transformers
(Apache-2.0) and PyTorch (BSD), the `moondream` extra fetched on first use. Both models' code comes with their
weights (`trust_remote_code`), pinned to a revision; both caption with the same call (`caption(image,
length="normal")`). `llm.moondream_model` picks Moondream 3 Preview (the default when Moondream is chosen, since that's
what was asked for) or Moondream 2. `moondream.cannot_run` keeps 19 GB from being fetched where Moondream 3 can't run
(no NVIDIA GPU with 20 GB, no Mac with 32 GB) and says so in the step's log and in `picture_descriptions`.

Checked here: Moondream 2 at the pinned revision loads with the locked transformers and describes a photo well ("A
vintage light blue Volkswagen Beetle is parked parallel to a yellow building with a wooden door …"), but took 3 to 6
minutes a picture on four shared CPU cores in float32: Florence-2 base took 3 s. Moondream 3 Preview wasn't
run here (no GPU); its code at the pinned revision was read: the same caption call, with flex attention. The opt-in
test (`LENS_TEST_MOONDREAM=1`) fetches PyTorch's CPU build, transformers and Moondream 2 through the component and
describes a red dot. It first wrote "and, (, (, (…": with `OMP_THREAD_LIMIT` below PyTorch's thread count (the test
suite sets it to 1), PyTorch's CPU kernels write nonsense, so `_load` now caps PyTorch's threads at that limit.

Refine later: Moondream's query skill with what the picture is ("page 3 of …"), as the provider's model is told; its
`compile()` on a GPU; bfloat16 on CPUs that have it; Moondream 3 on a GPU in CI or on a maintainer's machine; a
quantised Moondream 2 for small machines.

## 2026-10-17 · Describing pictures with Florence-2 on this machine

Adeel: use Moondream 3 and/or Microsoft Florence for visual descriptions. Florence-2 (MIT) is now a describe engine
(`llm.describe_engine` `florence-2`): its 8-bit ONNX export runs on ONNX Runtime on the CPU, fetched on first use,
pinned and hashed, base or large. It runs a Raspberry Pi's way (no PyTorch, about 280 MB for base) and needs no model
provider. Checked on two photos here: base takes about 3 s a picture on four CPU cores, large about 9 s, and both say
plainly what each shows.

Moondream 3 comes next as an opt-in engine. Its licences aren't open: Moondream 3 Preview's weights are BSL 1.1 (with
a production use grant; Apache-2.0 two years after release) and Moondream 3.1's are the Moondream Model License; 3.1
runs only on Photon, whose kernels (`kestrel-kernels`) are proprietary and licensed only under a separate agreement, so
Lens won't install Photon. Moondream 3 Preview runs through Hugging Face transformers (permissive) on an NVIDIA GPU or
a Mac with enough memory (about 19 GB of weights): not on a Pi.

Refine later: beam search (the model's own default is three beams; greedy with no repeated trigrams matches it on the
photos tried); Florence-2's OCR task beside the OCR step; a GPU (CUDA or CoreML execution provider) when there is one;
the Q4 exports for a smaller download; describing only the pictures NSFW classification didn't flag.

## 2026-10-15 · Entity types beyond Term

Adeel: "every entity comes as term? why!!". The default rules extractor typed a capitalised name PERSON only after a
title, ORG only before Inc/Corp-like words and PRODUCT with a digit; everything else (people, places, most
organisations) was TERM. `name_types.py` now types names from built-in lists (given names, places, well-known
organisations and products, holidays), the words a name starts or ends with, roles and "called" phrases before it,
verbs of saying after it and the namespace's named speakers; a recording gives each name one type. Upgrade step
`entity-types` retypes old analysis-made Terms, skipping types people or agents set (graph history and audit log).

Then the decision model: `analysis.typing` (`auto` by default: Jev or Laya when set up, never the language model;
`model` lets the language model answer; `rules` none) asks about a recording's new Terms in one `decide.choose_all`
call, with two lines each, and keeps answers above `decisions.act_above`.

Refine later: an action that asks the model about existing Terms (API, MCP, a button on the Entities page; next PR);
offer the namespace's own types to the model too; ask about names in the hybrid mode's new entities; place, name and brand lists for more languages and regions (Custom vocabulary in
Settings covers one install's own names); type existing entities again when a later recording gives better evidence
(needs a marker for types people set, kept out of the versioned rows); "Google Cloud"-like names (a known company,
then a word) as products.

## 2026-10-15 · The assistant knows Lens itself

Adeel: the assistant had no clue about the platform, its capabilities or general asks like "what is the MCP
address"; ground it in the docs through an internal tool. `lens_help` (app/domain/manual.py) answers from two things:
this server's facts (version, the web app, MCP and API addresses as the person uses them, what is turned on, their
access; admins also get each setting's page) and Lens's docs, cut at their headings into an in-memory SQLite FTS5
index (the word index's engine), found by words, with the docs' contents. Both are cited ([n]: "This Lens server",
"Docs · MCP server › Connecting", opening the heading on GitHub). The same tool is on the MCP server, and chat rooms
list its sources. The Docker Compose files mount the install's `docs/` read-only at `/docs`; Cloudron's image copies
them.

Refine later: the docs inside the NAS packages' images (Synology, QNAP build from fastapi_backend/ only, so they have
the server's facts and the online link); an in-app docs page so citations open the installed version instead of
GitHub's main branch; docs found by meaning too when search by meaning is on; docs passages for models that can't call
tools (search-and-answer only reads the archive); the README's overview in containers (its symlink points outside
docs/).

Found while landing it (the clicks survey hung after its first chat answer, one run in six): a reader leaving a
streamed answer before it ends leaves its generator to the garbage collector, which closes it on whatever thread is
running, mid-query, and the answer's cleanup (`on.end()`) then asked the database for the one embedded connection that
same thread held: a self-deadlock that froze the whole server. `store.DB.conn` now gives a thread the connection it
already holds. Refine later: the cleanup of a streamed answer (and, in a long conversation, its summary's model call)
still runs on the thread that happens to collect it, the event loop's included; hand it to a worker instead.

## 2026-10-14 · Long recordings in pieces a speech provider takes

Adeel: large audio files are segmented automatically to each transcription provider's limits. `speech.Engine` cuts a
recording longer than `speech.{provider}_max_minutes` at the quietest tenth of a second in the half minute before the
limit, halves a piece whose encoded file is over `speech.{provider}_max_mb` (again at a pause), sends each piece and
joins the segments on the recording's timeline. Defaults follow each provider's published limits
(docs/speech-providers.md, Long recordings); they are in Settings → Speech providers. This replaces the ten-minute
parts only OpenAI-compatible servers had.

Refine later: link a new voice heard in two pieces into one speaker by voiceprint (today it shows as two until merged);
decode and send a very long recording a piece at a time instead of holding all its samples in memory; send pieces in
parallel for providers that allow it; overlap pieces a little and drop the words heard twice, for a cut with no pause
near it.

## 2026-10-14 · MCP servers that run as a program

Admins add a stdio MCP server (`command`, `args`, `env`, as in Claude's `.mcp.json`) as an `mcp` extension, and a
plugin's stdio servers come along when an admin installs it. They run in the MCP runner (`mcp-runner/`, the opt-in
`mcp` Compose profile), a container with none of Lens's data or keys, reached over HTTP with a token on a volume only
it and the backend share. Recorded in the threat model.

Refine later: turn the runner on from Settings through the machine agent, as monitoring does, instead of
`COMPOSE_PROFILES=mcp` in `.env`; the runner on installs that don't use Docker (microsandbox, Cloudron, NAS packages);
plugin servers that run a program from the plugin's own folder; an egress allow-list for the runner's network.

## 2026-10-10 · Pasted transcripts fetch no speech model

Found by the clicks survey: on a fresh install, importing a pasted transcript queued the standard pipeline from
transcribe, so the worker fetched PyTorch and SenseVoice (about 1 GB) before analysing it, only for transcribe to skip.
`jobs.for_source` now leaves transcribe and diarize out for a `transcript` source, as it does for documents and images.

Refine later: transcript jobs queued before the upgrade still wait for that fetch once (a migration step would back up
the whole database for a queue that drains on its own); a run of only transcribe or diarize on a transcript still
queues them, to say why they skip, and may fetch the model; a batch of only diarize over documents fails its enqueue.

## 2026-10-10 · The assistant in your browser

Goal (Adeel): chat with the assistant from the browser extension, and let it do things by using the browser, like the
Claude browser extension: "check my email and import the important attachments".

Model (docs/browser-assistant.md): the clipper extension (extensions/browser) gets a side panel. It signs in with
OAuth (PKCE, `identity.launchWebAuthFlow`, nothing to copy), chats through `/chats` like the web app, and connects a
browser session (fastapi_backend/app/domain/browser.py). While it's connected, a question from it gets the
`browser_` tools; each action is a `browser_action` row the side panel takes from its event stream, carries out and
answers, and the tool waits for it in the database (any process, like stopping an answer). The extension guards what
the browser does: sites the person allowed, a yes before anything consequential, no passwords or card numbers, an
action log. Page text reaches the model as tool results (third-party text, untrusted.py once #302 is in).

PRs:

- [x] Server: sessions, actions, the browser tools, `ai.browser` and `ai.browser_steps`, the API for agents
- [x] Extension: side panel chat, OAuth sign-in (the Edge smoke test signs in through a stand-in consent page)
- [x] Extension: browser control (read with refs, click, type, keys, download into the conversation), site
      permissions, asking first, the action log (the Edge smoke test drives each against a stand-in Lens)
- [x] After reading outside text (#309's `wary()`): addresses to open or download and text to type that the person
      didn't write go with `confirm`, and the side panel asks first, on allowed sites too

Refine later: the browser from WhatsApp/Slack while the side panel is open (a setting, off); keep-alives in the chat
stream while an action waits; browser tools over MCP; real input events (Chrome's debugger) for sites that ignore
synthetic ones; asking about the page or selection from the right-click menu; the model and namespaces picker in the
side panel; webmail attachments behind a download button, and files redirected to another site; frames; Safari's
default tab; Lens checking that the extension asked (an extension older than `confirm` doesn't).

## 2026-10-10 · Similar content

Goal (Adeel): a Similar tab on every resource, and selecting text anywhere (a resource's text or title, notes, topics,
entities, the graph canvas) shows similar content in a popover, with a way to find more.

Model (fastapi_backend/app/domain/similar.py, docs/processing.md#similar-content): one list for a text, a resource, a
topic or an entity, fused by reciprocal rank from four: meaning (passages near the text, or near the centre of a
resource's passages: no model call for a resource), key words (the text's words the `term` table says are rare, or
`analyze.keywords`, OR'ed through the word index), shared topics and shared entities (rarer ones weigh more). Each item
cites up to three moments. `GET /similar`, assistant and MCP `find_similar`. Without embeddings (the default) the
other three answer, with no model call.

PRs: #296 the API, assistant and MCP tool; then the web app (the Similar tab, the selection popover and the full
Similar page at `/similar`, and similar content for topics, entities and graph nodes).

Refine later:
- key words of a text are single words and two-word phrases; stems beyond plurals (designing/design) don't match;
- a resource not indexed for meaning is compared by its title and keywords (one embedding request), not its text;
- the centre of a long resource's passages blurs what its parts are about; comparing a few of its passages may be
  sharper;
- entities in another namespace with the same name count only through the words, not as the same entity;
- no "not similar" feedback yet, and nothing is cached between requests beyond the query vectors.
- selecting text in an editor (notes, the document editor) opens nothing, since selecting there is for editing; a
  "Similar" item in the editor's own menu would cover it;

## 2026-10-10 · More voices for spoken answers

Goal (Adeel): read answers and podcasts aloud with ElevenLabs, macOS `say`, Google Gemini TTS or StyleTTS2, with
the voice picked in Settings. ElevenLabs and Deepgram were already there (#116); the providers stay shared with
podcasts through `speech.speak()`.

Todo:

- [x] Gemini TTS and macOS `say` as text-to-speech providers; `GET /voice/voices` lists each provider's voices;
      `POST /voice/speak` takes a voice to try
- [x] StyleTTS2 as an opt-in local service (`styletts2/`, Compose profile `styletts2`; voices are clips in a volume)
- [x] Settings → AI assistant → Voice: pick a voice from the list and hear it; Gemini in Speech providers

Refine later: pick a second `say` voice for podcast hosts from what the Mac has; Gemini's multi-speaker mode for a
whole podcast in one call; ElevenLabs voice settings (stability, style); cache the voice lists; upload StyleTTS2 voice
clips from Settings (or use a speaker's own voiceprint clips); start the StyleTTS2 service from Settings; GPU build.

## 2026-10-10 · Podcasts from picked knowledge

Goal (Adeel): pick documents, search results, topics, notes or graph concepts and get a two-host, Recast-style
learning episode about them, every claim cited to a real source, connected to the rest of the library.

Model (fastapi_backend/app/domain/podcasts.py, docs/podcasts.md): an episode is a recording (its script lines are
segments spoken by the hosts) plus `podcast:<rid>` for the request, excerpts, outline, cited lines and fact-check log.
One job on that recording runs podcast steps (gather, plan, write, check, publish) and then analyze, embed, summarize.
Citations are segment runs `{recording, idx0, idx1}`, which exist without embeddings. Prompts are prompt templates
with role podcast.<stage>.

Rulings (Adeel, 2026-10-10, recommended defaults accepted): episodes go in a `podcasts` namespace only when its readers
can read every source namespace and no source is in a vault, else in the sources' namespace; OpenAI-compatible TTS by
default, ElevenLabs opt-in, script-only without TTS; 5/10/20 min presets, 30 max; rewrite an unsupported line once,
then drop; episodes are never cited as evidence by later episodes (memory only); MCP may queue an episode; on by
default; stale citations are marked, never auto-regenerated.

Todo:

- [x] Backend script pipeline: selection (recordings, passages), plan, write, fact-check, publish as a transcript
- [x] Audio: per-line TTS with two voices, stitch with pauses, loudness, store as the recording's media
- [x] REST API and MCP `create_podcast` / `get_podcast`
- [x] Web: Create podcast dialog (Library selection, resource menu), /podcasts list, episode page with progress,
  player, citations, sources and fact-check log
- [ ] Phase 2: cross-page selection tray (search hits, topics, notes, graph), context expansion, connections
- [ ] Phase 3: episode memory, script editing and re-render, episode→source graph edges, stale citations

Watch out: an episode's speakers are named after the hosts, so they are the same speakers across episodes in a
namespace.

## 2026-10-05 · Threat model

Goal (Adeel): write down Lens's security contract, meaning what it assumes, guarantees and leaves to whoever runs it,
so a security report can be routed against something written. Modeled at 5f799f2; produced with the threat-model
plugin (1.2.1).

Rulings from Adeel (2026-10), recorded in the model as maintainer claims:

- Exposure to the internet behind HTTPS is supported. Only the instance admin is trusted; an admin is equivalent to
  code on the host. Owners, editors, viewers, tokens, MCP agents and the assistant acting for them are not.
- URLs only an admin can set (model server, sources, IIIF import, Fedora, telemetry) are trusted input. SSRF is in
  scope for URLs a non-admin sets (web import, extension `http` tools, notification targets), and for the guards Lens
  documents on admin URLs (iCal, the telemetry endpoint).
- Shipped defaults (plain HTTP, SurrealDB root/root) are supported on one machine only. `docker-compose.yml` and
  `make dev` are dev-only.
- Downloaded binaries and models are trusted to HTTPS and their publisher, with no further integrity claim.

Todo:

- [x] Recon, surface pass over 4 families, waves 1–2 of questions
- [x] Draft `threat-model.md`; backtest against 36 real security commits plus 81 synthesized reports (no fixed item
      closes); revise
- [x] `threat-model.yaml` and `threat-model.json`; link from SECURITY.md
- [ ] Waves 3–11 of §1.18 (47 open questions), then promote answers to maintainer claims
- [x] Align SECURITY.md's SSRF example ("a model server") with the admin-URL ruling (YAML hash refreshed;
      the plugin's validator was not available in the session that made this edit, so re-run it)

Watch out: the prose is canonical. After any edit to `threat-model.md`, regenerate the YAML and JSON and run
`ci/validate_threat_model.py --base origin/main` (CI runs it on every change to these files and fails a prose change
that changes neither companion). The YAML no longer holds a hash of the prose, which made every pair of threat-model
pull requests conflict (#507).

## 2026-10-04 · Graph history: versions, replay, rollback

Goal (Adeel): make the graph replayable and versioned.

Model (fastapi_backend/app/domain/graph_history.py, docs/graph-history.md):

- Every change to the curated entity graph is one append-only `graph_event`: its number is the graph's version. It
  says when, who (a person, a routine, the assistant, analysis), through what (web app, API token, OAuth app,
  assistant, MCP, routine, analysis), why, and for every record it touched (entity, alias, cross-namespace link,
  "not the same" pair) the row before and after.
- Built on what is there: graph changes (organize.py: proposed, applied, dismissed, undone) and merges (entity_merge)
  are recorded through the same entity functions, and their events point back at them.
- As of: the graph at any version, rebuilt by walking back from today's rows. Diff between two versions. An entity's
  history. Versions can be named ("before the cleanup").
- Rollback to a version: a preview first, then later changes are undone as new events, so a rollback can itself be
  undone. Merges are undone with their mentions.
- Replay: checkpoints (the graph at a version) plus the events after them rebuild the graph; verify compares the
  replay with the live rows and records any difference as an event, so nothing stays unrecorded.
- Not versioned here: mentions (analysis makes them from transcripts, whose edits have their own history). As-of views
  use today's mention counts.

Todo:

- [x] Event log with who, through what and why; every curation path records (entity page, assistant, MCP, routines
      and graph changes, analysis, the entity map)
- [x] Versions API: list, the graph as of a version, diff, an entity's history, named versions
- [x] Rollback to a version, with a preview
- [x] Checkpoints, replay and verify (`lens graph verify`, nightly checkpoint)
- [x] The graph API and Cypher as of a version (`as_of`); the graph cache is keyed on the graph's version
- [x] An as-of picker in the explorer (with the graph explorer thread)
- [x] Web app: a history timeline with diffs and rollback (Routines › History; an entity's History button)
- [x] Graph events in each resource's activity history (an entity's, a namespace's, a recording's analysis)

Refine later: speakers (same-person links, names) in the history; a mention-level history.



## 2026-10-04 · Activity history and budgets

Goal (Adeel): every change and every call coming in or going out is tracked, with its cost, down to the resource, so
each resource has an activity history. Pipelines, workflows and routines can be capped at a budget, and before a run
(or something larger) the assistant looks at where the budget stands and whether the run should go ahead.

Model:

- One ledger, `activity`: a row per call in (API requests that change something), call out (model, embeddings,
  decision model, webhooks, web tools) and run (a job, a routine run). Each row names every resource it touched
  (`routine:3`, `routine_run:12`, `job:40`, `recording:7`, `pipeline:2`, `workflow:4`, `chat:9`, `space:1`,
  `account:1`), with tokens, estimated cost (the prices in Settings), duration and outcome; never prompt or reply text.
  Who a call is for comes from the work it runs in (a scope), so a model call inside a job counts for the job, its
  recording, pipeline, namespace and the routine that queued it.
- A resource's history is its ledger rows plus its audit log entries. On by default, nothing to set up; kept
  `activity.keep_days` (365). Reads (GET) aren't logged unless `activity.reads` is on.
- Budgets are off unless set: a budget on a routine, pipeline, workflow or namespace caps cost (USD) and/or tokens per
  run, day, week or month. Before a routine run or a job, Lens estimates the run from past runs and checks what is
  left. Over budget, it asks you (the default: the run waits for your pick), skips, or lets the assistant weigh it (the
  decision model; unsure means ask). Doing nothing changes nothing: a waiting run stays waiting.

Todo:

- [x] Activity ledger: scopes, model/embedding/decision/webhook/web-tool calls, changing API requests, job and routine
      run totals, per-resource history and cost summary API, retention sweep
- [ ] Activity in the web app: a history panel on routines, pipelines, workflows and recordings; Settings → Activity
      and costs with totals by resource
- [ ] Cost in every view (Adeel): every resource, entity and view shows what it cost and its budget; a figure that
      isn't exact (no price for a model, tokens the server didn't report, a run's forecast) is marked as an estimate
      (`≈`, with why on hover). The API says `exact` / `estimate` per figure
- [x] Budgets: set per routine, pipeline, workflow or namespace; spent and estimate; checked before routine runs and
      jobs; over budget waits for your pick (run once, skip), or the assistant decides
- [ ] Budgets in the web app: set a budget, see where it stands, approve or skip a waiting run
- [x] Periodic check: warn at 80% and 100% of a budget, once per period (on the budget and in its history)
- [x] Local model costs (Adeel): price per model by tokens, by time (per hour) or off; off by default
- [ ] Budget warnings through notifications, and the assistant answering where budgets stand

Refine later: compute time as a cost (CPU seconds × a rate for local models), per-person budgets, forecasting from
schedules (a routine's next runs this period), budget alerts in the weekly digest.
## 2026-10-04 · Local decision models: Laya on MLX

Goal (Adeel): routine decisions can run on the machine itself with the Laya typed decision models on MLX
(github.com/mizorewww/laya-mlx; aac6fef/laya-mlx, aac6fef/laya-multilingual-mlx, aac6fef/laya-typed-decisions-mlx),
beside Jev. Jev stays the default: with nothing configured, decisions behave exactly as before.

Laya takes the same questions as Jev's System One (choice, score, noul) and answers in the same shape, so it is a
third engine behind decide.choose, falling back to the language model the way Jev does. MLX runs only on Apple
Silicon, so Lens in Docker on a Mac (a Linux VM) can't run it in the container: there it reaches a Laya server on the
Mac (`lens decide-server`), which speaks System One's API.

Todo:

- [x] Engine "laya" in decide.py: in this process on Apple Silicon, or a Laya server's address; Jev and auto unchanged
- [x] Settings decisions.laya_model (the three models) and decisions.laya_url; "not available here" instead of failing
- [x] Lens installs laya-mlx (extra `laya`) and downloads the chosen model itself (Settings → Components)
- [x] `lens decide-server`: System One's API over Laya on a Mac, for Lens in Docker
- [x] Status and test endpoints; Settings → AI assistant shows the engine, model, availability and a Test button
- [ ] Log decision calls in the cost ledger once "Activity history and budgets" lands
- Refine later: install.sh starts the decide server on a Mac host by itself; score and noul questions for callers;
  Laya's router (language detection picks the multilingual model); shortlisting for large option sets
## 2026-10-04 · Cloud speech providers and local GGUF models

Goal (Adeel): transcription, speaker separation and emotion stay local by default (SenseVoice and friends, unchanged),
but each voice task can be sent to a provider instead: an OpenAI-compatible Whisper endpoint, ElevenLabs (speech to
text and text to speech), AssemblyAI and Deepgram (every capability that fits). Every service takes a custom base URL,
for proxies and compatible servers. Also: run a local LLM with llama.cpp from a list of GGUF models this machine can run,
downloaded from Hugging Face.

Model:

- Settings → Speech providers (`speech` section): per service a base URL, a model and an API key (sealed like every
  other key). The defaults point at each vendor; change the URL for a proxy or a compatible server.
- `transcribe.engine` gains openai, elevenlabs, assemblyai, deepgram. Their transcripts carry the provider's speaker
  labels, language, audio events and sentiment (as emotion); `diarize.engine` gains provider, and auto uses those
  labels when a transcript has them. Voice IDs across recordings still come from the local voiceprints.
- Voice chat: `voice.stt` picks its own engine (default: the transcription engine); `voice.tts_provider` adds
  ElevenLabs and Deepgram Aura next to the OpenAI-compatible speech server.
- Provider calls go through the activity ledger once it lands (seconds of audio per call).
- Local LLM: a catalog of GGUF chat models with the memory each needs; the ones this machine can run are offered,
  downloaded from Hugging Face into data_dir/models/gguf, and served by llama.cpp's server, which becomes the LLM
  provider.

Todo:

- [x] Speech providers: settings, OpenAI-compatible, ElevenLabs, AssemblyAI and Deepgram transcription with speakers,
      language, events and sentiment; provider speaker labels in speaker separation
- [x] Voice chat: own speech-to-text engine; ElevenLabs and Deepgram text to speech
- [x] Settings → Speech providers in the web app; new engines in Transcription and Speaker separation
- [x] Local LLM: GGUF catalog filtered by this machine, download from Hugging Face, llama.cpp server as the provider

Refine later: per-minute prices for provider calls in the ledger; AssemblyAI and Deepgram summaries, chapters and
entities as optional imports next to Lens's own analysis; several endpoints per service; speaker labels kept across
chunks of long OpenAI-compatible transcriptions; CUDA and Vulkan builds of llama.cpp; local embedding models.

## 2026-10-04 · Topics: a controlled vocabulary apart from entities

Goal (Adeel): entities and topics are mixed (a topic is an entity of type TERM, which is also the fallback type for
anything unclassified). Separate them: topics become a SKOS controlled vocabulary per namespace (preferred and
alternative labels, a definition, broader, narrower and related topics), and recordings are about topics. Entities
stay the named things (people, organisations, places...) and later become authority records. Decided: after the graph
PRs (done, #111 #121).

Todo:

- [x] Vocabulary in the backend: topics with labels, definition, broader/narrower/related; recordings about topics;
      create, edit, merge, delete; turn a TERM entity into a topic (and back, by deleting the topic); API with viewer
      reads and editor changes; topics in the property graph (Topic, ABOUT, NARROWER, RELATED) and in RDF as SKOS
- [x] Topics page: the tree of broader and narrower topics, a topic's recordings, edit and merge; a recording's topics
- [x] Analysis suggests topics (summary topics and keywords matched to the vocabulary; new ones as suggestions to
      accept); a namespace can keep its vocabulary fixed or open, as entities do
- [x] # links in notes point at topics; assistant and MCP tools for topics

Refine later: shared vocabularies across namespaces and imported schemes (LCSH, Wikidata) with exactMatch; entities as
authority records (variant names, external identifiers); topic history and undo for merges; a namespace setting
to accept analysis suggestions without asking, or to stop offering new topics; matching labels of three or more words
in transcripts (only summaries match them now).

## 2026-10-04 · anytopdf as the conversion engine (not started: wait for Adeel's go)

Goal (Adeel): use the sister project [anytopdf-rs](https://github.com/adeelahmad/anytopdf-rs) (README on its sprint2
branch) to turn any source into one searchable, cited PDF: OCR, captions, transcripts, keyframes and metadata as an
invisible text layer, with an embedded manifest, chunks, source anchors and provenance. Do not start until Adeel says go.

Why it fits: one static Rust binary (Linux x86_64 and arm64 musl, so a Raspberry Pi) that Lens can download as a
component, instead of LibreOffice and Chromium in the full image; provenance per fact matches "trusted, cited memory".

Todo (when started):

- [ ] Wait for anytopdf's JSON output (`--json`, schemas, exit codes) and `extract --json` manifest to land
- [ ] anytopdf as a component Lens installs itself (settings: auto, on, off), with its version recorded
- [ ] A pipeline / workflow node "Make evidence PDF": inputs a recording or resource, outputs the PDF as a rendition
      and its manifest chunks and anchors as Lens chunks (citations jump to time span, box or byte range)
- [ ] Use it where Lens has no converter first (images, captions, mixed folders); keep LibreOffice and Chromium as
      the default for Office, HTML and email until anytopdf reads them (its roadmap: PDF, HTML, EML, Office)
- [ ] Reuse Lens's own OCR and transcripts as sidecars rather than running them twice
- [ ] Run it with no network, size and time caps, plugins off unless an admin allows them (its plugins run with full
      user rights until its sandbox lands)

Refine later: anytopdf's intake channels (IMAP, webhooks, watched folders) and printing overlap Lens's sources and
sensors; decide which side owns them. MCP server mode could be an extension.
## 2026-10-04 · Notes: a page for everything

Goal (Adeel): every resource, entity and topic has its own page (its note), next to free notes written by people or
the assistant. The assistant is the main writer and organiser; people can do everything it can. Notes link to anything
with @ (resources, people, entities) and # (topics). Free notes sit in a tree in the left navigation, like Notion;
pages of resources are opened from those resources. docs/notes.md.

Model: a `page` has a title, a one-line summary (the context the assistant reads; refined by the AI when switched
on), a date, a place (PARA: project, area, resource, archive), a parent for the tree, the thing it's about (if any), a
body and who wrote it (a person or the assistant). Links found in the body are kept as edges, with backlinks, and show
in the graph.

Todo:

- [x] Pages: create, read, change, move in the tree, delete; a page per resource made on first open
- [x] @ and # mentions: links and backlinks, a search to pick what to link
- [x] Web app: tree explorer in the left navigation, page view with title, summary, date, place and backlinks; the
      page of a recording or entity reached from its detail view
- [x] Editor: BlockSuite (AFFiNE's block editor on Yjs documents, what OctoBase stores) behind one component; plain
      text kept for search and the assistant
- [x] AI title and summary refinement (ai.refine_notes, on by default, can be switched off)
- [x] Costs of refinement and filing to the activity ledger (`note_page:id` and its namespace; shown on the page)
- [x] Assistant tools: find, read, write and update notes (at once, no approval: notes are its notebook)
- [x] MCP tools to find, read and write notes (find_notes, read_note, write_note)
- [x] Page history (note_version): a version before each change, one per sitting, restore that is itself undoable
- [x] Self-organising, first step: notes nobody filed are filed in PARA by a decision model (or the LLM), with a
      suggestion when it isn't sure (ai.organise_notes)
- [x] Self-organising: suggested links to the topics and named things a note mentions (no model; one click; the
  assistant sees them as could_link)
- [x] Self-organising: the project or area a top-level note fits under, from shared links and title words (no model;
  one click; the assistant sees them as could_go_in)
- [ ] Self-organising, next: nest notes without a click when the fit is clear (decision model, like PARA filing)
- [x] # topics move to the SKOS topics once the graph thread splits them from entities (#135)
- [x] Attachments on a page, encrypted through the keyring (in the file store)
- [x] The file store (blobs.py, docs/storage.md): this machine or any storage connection through rclone (S3, Drive,
      Dropbox, OneDrive, SFTP, SMB, WebDAV, a folder), keyring-encrypted before it leaves, optional rclone crypt; the
      `files` settings, a check, the assistant's file_storage tool. Adeel said go 2026-10-05, with rclone and crypt
- [x] Settings → Storage and the setup wizard step (pick or add a connection, folder, crypt, check); new installs choose
- [x] Attachments on notes in the file store: BlockSuite's blob source keeps images and files there (note_file)
- [ ] Moving uploads and recordings' media to the file store (pipelines need local copies: a cache)
- [ ] A downloadable storage key (the server secret; opens every namespace that isn't a passkey vault)
- [x] File storage over MCP (admins): file_storage, set_file_storage (checked before saving)

## 2026-10-04 · The graph, end to end

Goal (Adeel): make the graph the one focus and nail it: a human explorer canvas, agent queries with rights, questions
in plain language, and a graph tool for the assistant. Scope is one namespace or every shared one. docs/graph.md.

Decision: agents query in Cypher (openCypher, the basis of ISO GQL), run by Lens's own engine over a per-caller
projection, read-only; changes are proposed graph changes. Not Gremlin (needs a JVM server, models write it worse),
not raw SurrealQL (lock-in, reaches any table), SPARQL stays over the RDF view.

Todo:

- [x] Property graph: namespaces, collections, recordings, speakers, entities; hierarchy and association relationships
- [x] Walk it: parents, children, ancestors, descendants, neighbours, every path / shortest paths
- [x] Read-only Cypher engine with a step and time budget; API with read (any token) and change (write + editor) rights
- [x] Explorer canvas: drag nodes, pan and zoom, pinch and long-press on touch; one-click layouts (force, BFS tree,
      DFS tree, radial) and reset; a custom route through picked nodes; right-click menu for parents, children,
      ancestors, descendants, neighbours and paths
- [x] Questions in plain language: the question becomes Cypher (shown, editable), the answer lights up on the canvas
- [x] Assistant and MCP tools: graph schema, query, related, paths; proposing changes behind an approval
- [ ] Topics as a controlled vocabulary (SKOS), apart from entities (authority records): started, see "Topics" above

## 2026-10-03 · Assistant extensions: tools, skills, hooks, plugins

Goal: the assistant can be extended to the same level by code, the canvas, voice or plain chat.

Model (fastapi_backend/app/domain/extensions.py, /api/v1/extensions):

- One registry, four kinds: tool (params, effect read/change, body prompt/http, later code and canvas graph), skill
  (when + instructions, read with `use_skill`), hook (message, before_tool, after_tool, answer: add context, block a
  tool, call a tool), plugin (a bundle of the three, shared and switched off as one).
- Versioned and shared like custom nodes (private / namespace / everyone); a version records how it was made (code,
  canvas, chat). Change tools wait for approval like the built-in ones; `ai.extensions` turns them all off.
- Manifests as code: Markdown with frontmatter (body = skill instructions or a prompt tool's prompt) or YAML/JSON.

Todo:

- [x] Registry, versions, sharing, manifests in and out, check without saving, try a tool
- [x] Prompt and web tools, skills, hooks in the chat tool loop; approvals for change tools
- [x] Code tools: Python in a process of its own (admins write them; time, CPU, memory limits, no secrets, own folder, no network unless allowed; stops when its owner is no longer an admin). Refine later: a container or gVisor sandbox for non-admins, pip packages per tool
- [x] Canvas: a `tool` workflow scope whose arg nodes are the tool's parameters (ask_model, call_tool nodes)
- [x] Canvas editor for tool graphs in the web app (Extensions → Draw a tool)
- [x] Authoring from chat and voice: the assistant drafts and saves extensions behind an approval card
- [x] Extensions page in the web app: list, manifest editor, switch on/off, share, try, versions
- [ ] `lens ext` CLI (push/pull manifests)

Refine later: hook-called tools don't show as steps; web tools reach ports 80/443 only and have no secret
references yet; extensions aren't offered over MCP yet; marketplace on top of plugins.

## 2026-10-02 · Routines

Goal: run syncs, pipelines and workflows on a schedule, including a daily LLM pass that organises the entity graph per
namespace or across all of them.

Model:

- A routine has a cron schedule (or none: by hand), a time zone, namespaces (or all) and ordered actions: sync,
  pipeline, workflow.
- Workflows get a scope. Graph workflows run over namespaces and are drawn on the same canvas: candidates → ask the
  model → filter → apply changes. Sure pairs are merged or linked, the rest proposed; everything is undoable.

Todo:

- [x] Cron schedules with time zones (no new dependency)
- [x] Routines: CRUD, run now, runs with results and logs, claimed so they run once
- [x] Scheduler thread in the API process; `lens watch` runs routines too
- [x] Graph scope for workflows; candidates, llm_judge, filter, apply_changes nodes
- [x] Graph changes: proposed / applied / dismissed / undone; undo a whole run
- [x] Seed the graph workflow and a nightly routine (off)
- [x] API tests
- [x] Web app: Routines page, run history, proposed changes review, graph nodes on the canvas
- [ ] Notify (Matterbridge/webhooks thread) when a run fails or leaves proposals
- [ ] More graph nodes: retype entities, hide noise, cluster topics
- [ ] Routing rules (shared IMAP inbox → namespaces) slot in between sync and ingest

## 2026-10-02 · Workflow canvas

Goal: design custom workflows on a canvas and attach them to pipelines.

Model:

- A namespace picks a pipeline per content type (audio, video, document, image, transcript), falling back to its
  default pipeline, then the built-in one.
- A pipeline's steps produce assets (transcript, shots, OCR text, faces...).
- Workflows are versioned node graphs that produce metadata (named outputs, custom field values). They are shared:
  a pipeline attaches any number of them, each pinned to a version and optionally with a condition, and they run
  after the pipeline's own steps.

Todo:

- [x] Read how pipelines, steps, outputs and custom fields work today
- [x] Workflow domain: graph validation (node types, ports, no cycles), create, versions, get, list
- [x] Workflow runner: input, llm, condition, pick, merge, output and field nodes, run in graph order
- [x] Entity nodes: extract (rules: built-in extractor + terms + regex), extract (LLM, structured), save entities
- [x] Pipeline graphs: asset steps and workflows as nodes, edges = runs after; ordered into steps for the job runner
- [x] Default pipeline drawn as a chain of today's steps, so nothing changes until a graph is edited
- [x] `workflow` step type in jobs, so a run records each workflow like any other step
- [x] Pipelines attach workflows as `workflow` steps (`{type: workflow, workflow, version?, when?}`), pinned when queued
- [x] Namespaces choose a pipeline per content type (`pipelines: {video: id, ...}`), reworked below into subtypes
- [x] API: `/workflows` (catalog, create, get, versions, run on a recording)
- [x] Tests for the API, validation and a run end to end
- [x] Canvas editor in the web app (pipelines and workflows): drag nodes, connect ports, node settings, I/O, save versions
- [x] Attach workflows from the pipeline editor
- [x] Content-type mapping on the Pipelines page (By content type)
- [x] Asset converter steps (video → audio, document → images) as pipeline steps (the convert step, conversions.py)
- [ ] Workflow node types: HTTP call, template render, entity filter
- [x] Docs: processing.md section on workflows

Content types (2026-10-02, follow-up in the same PR):

- [x] Four base types (video, audio, image, text), derived from the file; transcripts, documents and web pages are text
- [x] Subtypes (the vocabulary) under a base type: label, description, pipeline, rules (extensions, filename pattern,
      length); built-in defaults, editable and removable, plus your own
- [x] A resource's subtype: chosen at import/upload or on the resource, else the first subtype whose rules match, else
      the base type's general subtype
- [x] Pipeline resolution: chosen for the run > namespace override for the subtype > subtype's pipeline > namespace
      default > standard
- [x] API: /content-types (list, create, update, delete), set a recording's subtype
- [x] Web app: Content types tab under Pipelines; per-namespace overrides by subtype; picker on the resource's Details
- [ ] Choose the content type at import and upload (today: on the resource, or recognised by rules)

## 2026-10-02: Docker stack check (`make dev`, `make run`)

Both stacks brought up on a Linux host (Docker 29.6, Compose 5.3); setup, login and 21 app pages walked in Chromium on
each; every container's log read. Folded in the Docker fixes from running `make dev` on an Apple Silicon Mac with
Colima (first made on the OAuth branch), so there is one Docker change.

**Healthy.** SurrealDB, the API, the worker and the mail catcher start clean on both stacks. The browser walk found no
API errors and no failed requests; the only 404s were index URLs that don't exist (`/entities`, `/resources`,
`/recordings` only have `[id]` pages).

**Fixed**

- **`make run` then `docker compose up` ran the production images.** Both compose files build under the same project,
  so the production build overwrote the dev images (`<project>-frontend`, `<project>-backend`); the dev frontend then
  ran `next start` with no build and crashed ("Could not find a production build"). `make dev` hid it by rebuilding
  every time. The production images are `lens-backend` and `lens-frontend`, tagged `${LENS_VERSION:-prod}`.
- **A crashed dev server left its container "Up".** In Docker, `start.sh` (API and web app) stops when either the
  server or the watcher stops, so `docker compose ps` shows it exited and the log says why. Outside Docker it waits
  for both (`wait -n` needs bash 4.3; macOS ships 3.2).
- **Containers ignored `docker stop`** and were killed ten seconds later (exit 137): as a container's first process,
  bash and Python ignore TERM unless they handle it. `start.sh` traps TERM and INT and stops its children; `lens
  worker` and `lens watch` stop on TERM as on Ctrl-C.
- **The dev stack didn't get `ARCHIVE_SECRET_KEY`.** `make` writes it to the root `.env` so Docker and native runs
  read the same stored credentials, but the dev compose file never passed it on; the API and worker fell back to
  `data_dir/secret.key`. The root `.env` is now an optional `env_file`, before `fastapi_backend/.env` (which wins).
  Credentials a dev stack stored before need entering again (CHANGELOG says how to keep them).
- **No arm64 mail catcher.** MailHog publishes amd64 only; the dev stack uses Mailpit (same ports, now on 127.0.0.1).
- **Dependencies didn't follow the image.** `node_modules` and the venv were named volumes that a rebuild leaves
  alone (the web app ran Next 16.0.8 against a 16.3.6 lockfile); they're anonymous volumes that `make dev` renews.
- `make` finds `docker compose` or the standalone `docker-compose`. The Next dev server no longer writes `AGENTS.md`
  and `CLAUDE.md` into `nextjs-frontend/` (`agentRules: false`), and `caniuse-lite` is current.

**Not changed, worth knowing**

- **The dev frontend needs a checkout uid 1000 can write to.** `user: node` plus the source bind mount means `next
  dev` writes `.next/` and `next-env.d.ts` as uid 1000. Fine on Docker Desktop and Colima (macOS) and for a normal Linux
  user; a root-owned clone fails with `EACCES` (`chown -R 1000:1000 nextjs-frontend`).
- React warns "Encountered a script tag while rendering React component" on not-found pages in dev: the root layout's
  theme script (`app/layout.tsx`). It still runs on server render; dev-only console noise.
- SurrealDB warns "existing root users were found" on every start after the first: `SURREAL_PASS` only applies when
  the volume is created, as the `.env` comment says.

**Sandbox only (not repo issues):** Docker Hub rate-limited pulls (429), `ghcr.io` blobs and the Debian mirrors were
blocked by that environment's network policy, so images came from `mirror.gcr.io` and the backend was built without
its apt packages (no ffmpeg/tesseract/LibreOffice), on the lean target. Processing jobs weren't exercised.
