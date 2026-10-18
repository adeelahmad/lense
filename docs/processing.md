# Processing

How assets move through Lens: where they come from, what each step does, and how people, entities and the knowledge graph are built. Every step can be started from the web app, the API or the `lens` command line.

## Steps

- `scan` finds audio under each namespace's paths and fingerprints it: moved files keep their history, duplicates are skipped,
  and so are files whose asset someone deleted or moved to another namespace (at the same path, or a copy of the same
  file).
- `transcribe` uses SenseVoice, faster-whisper or mlx-whisper. When the configured engine isn't installed on a worker (or
  doesn't import there), it uses the next one that is and says so in the job log; with none at all the step fails,
  saying how to add one. A file that fails is marked and the batch carries on. For a
  document or an image it draws the pages and reads their text instead: a PDF's own text, and OCR for scans and images
  ([Documents and images](configuration.md#documents-and-images)).
- `diarize` splits genuinely two-channel files by channel, otherwise clusters voice embeddings (or uses pyannote), then
  matches voiceprints against the namespace's people.
- `clean` (off until Settings → Analysis → Clean up transcripts before analysis is on) tidies a transcript before
  analysis reads it: [Cleaning up transcripts](#cleaning-up-transcripts).
- `analyze` finds entities, chapters, keywords and talk statistics; `summarize` (optional) calls any OpenAI-compatible
  server for a summary, key points, action items (with who will do them), topics, people, tone and importance, each
  key point and action item with the time of the line it comes from; `report` writes static HTML per asset and
  per namespace, with word clouds.

Every step takes `--ns`, `--limit` and `--force`; `run` does them all, and a lock stops two runs overlapping.

### Your own names in transcription

Speech-to-text gets wrong the names only you use: family, colleagues, places, projects. With `transcribe.vocabulary`
on (Settings → Transcription → Expect your own names; off by default), each recording is transcribed knowing up to 60
words its namespace already knows, in this order:

- the custom vocabulary in Settings → Analysis (`analysis.gazetteer`), as written there;
- the names of the namespace's named people;
- its defined entities ([Entity setup](#entity-setup));
- the words people put in when they corrected its transcripts, the capitalised ones a line didn't have before (the
  text cleanup's own changes don't count), most often first.

Whisper (faster-whisper and mlx-whisper) and OpenAI-compatible servers get them as the prompt, a line of the names (the
diarizing models take none); Deepgram as key terms with Nova-3, or keywords with the models before it; AssemblyAI as
key terms with Universal-3 and Slam-1, or boosted words with the others. SenseVoice and ElevenLabs take no hints, so
they transcribe as before. The words go only to the engine the recording was going to anyway, and a provider's row in
the [activity ledger](activity.md) says how many it was given. Run Transcribe again on an asset to use new names.

### Cleaning up transcripts

Speech comes out of transcription as it was said, and the extractor, chapters, keywords and search all read it. With
`analysis.cleanup` on (Settings → Analysis → Clean up transcripts before analysis; off by default), the `clean` step,
which the standard pipeline runs just before `analyze`, tidies each line of a transcript with rules. The rules need no
model, fetch nothing and send nothing out:

- a word said three times or more in a row is kept once ("I I I looked" → "I looked"), and a doubled pronoun, article
  or conjunction ("the the", "I, I") once; "had had", "that that" and "very very" stay;
- "um", "uh", "erm" and "hmm" go, with the comma after them (`analysis.cleanup_fillers`, Take out "um" and "uh"; on);
- one space between words, none before a comma or a full stop, one after a comma;
- "i" becomes "I", and each sentence starts with a capital. A line that carries on the one before (same speaker, no
  full stop) keeps its lower case.
- punctuation (`analysis.cleanup_punctuation`, Put punctuation back; off): a local model, kredor/punctuate-all (MIT;
  English, German, French, Spanish, Italian, Dutch, Portuguese, Polish, Czech, Slovak, Slovenian and Bulgarian), adds
  full stops, commas and question marks after words that have none, where it is at least 70% sure. "Where a
  transcript has none" (`missing`) does it only for a transcript with fewer than one full stop in 40 words, such as
  imported captions; "In every transcript" (`always`) for every one. It keeps the marks a transcript has, and gives
  each line marks once. The model, Transformers and PyTorch (about 2 GB) are fetched the first time a clean step
  needs them ([Components](components.md)); until then, and in other languages, the rules run alone and the job's
  log says why.
- grammar (`analysis.cleanup_grammar_url`, LanguageTool server; none): a [LanguageTool](https://languagetool.org)
  server's grammar and punctuation fixes are made too ("he go" → "he goes", "a apple" → "an apple"), and only those:
  spelling, style, wording and repeated-word ("very very") suggestions are left out, and so is any fix to a word
  with a capital in it, so names stay as they were said. Start one with the compose files' `languagetool` profile
  (`docker compose --profile languagetool up -d`, about 1 GB of memory) and set `http://languagetool:8010`. The
  transcript's lines go to that address and nowhere else (no redirect is followed), so a server outside your network
  sees them; each call is in the activity ledger. When it doesn't answer, the rules' changes stand and the job's log
  says so.

These are English rules ("um" is a word in German and Portuguese), so they apply to English lines: the line's or the
asset's language when transcription gave one, else when the text reads as English. In other languages only a word
said three times in a row is kept once, and sentences start with a capital. Documents and images keep their text as
it was read.

Every line it changes is in the asset's Change history (by "text cleanup"), with Revert. A line someone corrected,
or reverted, is never cleaned again, and running the step again on cleaned text changes nothing. Turn it on for a
custom pipeline by adding the Clean up step before Analyze (with Force it runs even while the setting is off); run it
on an asset with Reprocess → Clean up, `POST /api/v1/recordings/{id}/reprocess` with `{"steps": ["clean", "analyze"]}`,
or `lens clean`. Workers whose `workers.steps` list has `analyze` run it too.

## Importing text and transcripts {#importing-transcripts}

Formats: .txt, .md, .markdown, .mdx, .docx, .doc, .pdf, .srt, .vtt, .json (lens, Whisper or a list of segments) and
.jsonl (SenseVoice chunk files; overlapping windows are stitched). Pasted text works too.

**Chat exports.** A WhatsApp chat (Export chat, the .txt), a Telegram chat (Export chat history as JSON, result.json),
a day of a Slack channel from a workspace export, or an iMessage chat from imessage-exporter's text export is read
as a chat: one line per message, credited to its sender, so the people in it join the namespace's people. When
each message was sent is kept on its line (`at`); line times are a reading pace, so a year of messages doesn't read
as a year-long recording. The chat is dated by its first message and gets the Chat export content type. A WhatsApp
or iMessage .txt uploaded with the other files is read the same way; any other text file stays a document.
`--format chat` (`"format": "chat"`) refuses a file that isn't one.

**Archives.** A zip or tar file (.zip, .tar, .tgz) uploaded or scanned is a document whose page lists what's in
it, and its files are kept as its files, as an email's attachments are: those Lens can read become resources of
their own that say which archive they came from, a chat export in it a chat and JSON in it records (so a WhatsApp
export with media is the chat and its photos). Only plain files are taken, an encrypted entry or a link is left
out, an archive inside one is kept but not opened, and reading stops at 100 files or 512 MB unpacked, so a zip bomb
can't fill the disk. What was left out, and why, is on its page. Archives get the Archive content type.

**Records.** JSON and JSON Lines that aren't a transcript (their rows have fields a transcript's lines don't, like an
order's `total`) are read as records, one line each: a JSON Lines file a record per line, a JSON file its list (or the
one list of objects it holds, such as `{"orders": [...]}`), else the object itself. A record's text is its fields,
one `name: value` per line, nested ones by their path (`address.city: Paris`), so search and the assistant see every
value. Each line says which record it is (`record`, from 0) and, for JSON Lines, where its line starts in the file
(`offset`, in bytes). The resource gets the Records content type. JSON and JSON Lines can be uploaded with the other
files too.

    lens import podcasts episode.docx --audio episode.mp3 --speakers "SPEAKER_00=Host A,SPEAKER_01=Host B"
    pbpaste | lens import notes - --title "Standup"
    lens import notes minutes.pdf --format text

In the web app, Import takes pasted text, a chosen file or one dropped on the text box, and analyses it straight away.
It also uploads audio, video, documents and images, which go through the namespace's pipeline like scanned files. A
PDF is a document (its pages, to look at and search) unless you choose to import its text only. "Then run" picks
another saved pipeline for what you import.

**Audio for a transcript.** A transcript can get its audio (or video) in the web app: Import → Paste's "Attach audio…",
a transcript dropped together with the audio of the same name (`ep14-transcript.srt` and `ep14.m4a`), or "Attach audio"
on a text-only asset's page. The file is uploaded and becomes the asset's media; the transcript and its
speakers stay. Then these steps run, after anything the asset's job still had to do: transcribe (which keeps the
transcript and draws the waveform), diarize (speakers by voice, unless the transcript named them), shots, text on screen
and faces (video only), analyze and report. The asset takes the file's fingerprint, so scans and uploads of the
same file find it, unless another asset in the namespace has it already.

Speakers are recognised from `Name: text`, `[12:30] Name: text`, `Name (12:30): text`, Otter/Zoom/Teams exports (a
`Name  12:30` line, then what they said), `speaker|emotion|text` lines, and the speakers in SRT/VTT and JSON. Anything
else becomes paragraphs split into segments of about 40 words with estimated times. Markdown and MDX are reduced to text
first: front matter or the first heading becomes the title; imports, exports, JSX and code blocks are dropped. A PDF
imported as a transcript needs a text layer; import a scan as a document instead, and its pages are read by OCR. Named speakers are matched to the namespace's people by name; generic labels (SPEAKER_00, S1, CH0)
become new people.

## People and namespaces

Person ids belong to one namespace. Per recording, `diarize.engine: auto` works like this:

- If the file is genuinely two-channel (left and right carry different voices), each channel is one speaker. That separation is exact and free.
- Otherwise, voices are clustered from embeddings. Short backchannels take the label of the nearest long turn.
- Or set the engine to `pyannote`. Its model is gated: accept its terms on Hugging Face and add a token in Settings →
  Components ([Components](components.md#settings)).

Each voice gets a voiceprint (SpeechBrain ECAPA, up to `sample_seconds` of that speaker's clearest audio). The voiceprint is matched one-to-one against the namespace registry:

- At or above `match_threshold`, it reuses the existing person and updates the centroid.
- Between `review_threshold` and `match_threshold`, it creates a new person plus a suggested merge for you to confirm.
- Below that, it creates a new person.

You can rename and merge people in the web app or the CLI (`lens speakers …`), and every merge can be undone; merges
keep who made them and how many assets moved. People in different namespaces are never merged. You can link them
as the same person (and unlink them), and with `speakers.cross_namespace: suggest` the graph shows likely voice matches
as dashed edges and People lists them with how alike the voices are. Saying a suggested pair is "not the same", in one
namespace or across two, removes the suggestion for good.

## Knowledge graph

Nodes are people and named things. Edges are:

- people who were in an asset together,
- who mentions what,
- what is mentioned together,
- same-person links.

`graph: shared` puts a namespace in the global graph, where named things with the same name join into one node across namespaces. `graph: isolated` keeps it to its own graph. The API takes `scope=global` or `scope=ns:<name>`.

## Entities and the graph explorer

People, organisations, products, places, events, works and topics are extracted from every asset's text. Dates and
numbers are extracted too, but hidden unless asked for.

**How they're found.** By rules out of the box: capitalised names, titles, organisation endings, dates and numbers,
after the entities a namespace defines. With `analysis.entities` set to `spacy` (Settings → Analysis → Entity
extraction), spaCy's small language model finds them instead, after the defined entities, which keep their names and
types where the two overlap; a lone word like "Okay" isn't taken for a name. Lens fetches spaCy (MIT) and the model
named in `analysis.spacy_model` the first time an asset needs them ([Components](components.md)): `en_core_web_sm`
(English, the default), `de_core_news_sm` (German) or `xx_ent_wiki_sm` (many languages; people, organisations and
places). spaCy's other languages' models carry GPL, LGPL or Creative Commons licenses, so Lens doesn't fetch them; one
installed by hand works. Until spaCy is there the rules find the entities and the job log says so. The workflows'
Extract entities (rules) node uses the same extractor.

**How a name gets its type.** The built-in extractor (`analysis.entities: rules`, the default) needs no model. It
reads each name and the words around it (`app/domain/name_types.py`):

- **Person:** a title (Dr Lee, President Obama) or a role before it (our CEO Maria Garcia, my colleague Tom); a
  given name first (Sarah Johnson, Maria); a full name followed by a verb of saying (Sarah Connor said); or one of the
  namespace's named speakers.
- **Organisation, event, work, place:** the word it ends or starts with (Acme Corp, University of Oxford; Web Summit,
  World Cup; Clean Air Act; Hudson River, Mount Fuji), or what it's called (a company called Lumen, an app called
  Notion, a town called Hope).
- **Built-in lists:** countries, regions, states and large cities (Place); well-known companies and institutions
  (Organisation: Google, Anthropic, NASA) and products and tools (Product: ChatGPT, Kubernetes, Excel); holidays
  (Event); days and months on their own (Date). A name with a number in it is a Product (iPhone 16).
- **Term:** anything none of these tells, such as a single unknown word (a brand or a project). A Term can be made a
  topic, or retyped on the Entities page (one at a time or ticked together). Names listed in Settings › Analysis ›
  Custom vocabulary (`Name | TYPE`) always get the type given there.

**Names the rules can't type.** When a [decision model](assistant.md#routine-choices) is set up (Jev with its key, or
Laya), analysis asks it what each new Term of a recording is, with the first two lines it's said in, and keeps the type
when it is sure (`decisions.act_above`, 0.8). Names that are already entities aren't asked again, and the short form of
a longer name in the same recording ("Johnson" of "Sarah Johnson") takes that name's type. The names of a recording go
in one call (up to 20 a call and 40 a recording; Laya, which reads less, one at a time). Each call is in Activity and
counts toward budgets, and none is made once the budget's hard limit is reached. Settings › Analysis › **Names the rules
can't type** (`analysis.typing`) chooses:

- `auto` (the default): the decision model when one is set up, else nothing; the language model is never asked.
- `model`: the decision model, else the language model, in one call per recording too.
- `rules`: no model; the names stay Terms.

A name the model isn't sure of stays a Term, and so do all of them when it can't be reached. Names a workflow's nodes
typed, and names mapped onto a fixed list ([Entity setup](#entity-setup)), aren't asked.

A name gets one type per recording, the one most of its lines give it, so "Lee" after "Dr Lee" is a person too. "And"
no longer joins two names into one ("Kenya and Nigeria" are two places). The given names are mostly the US Census
Bureau's frequent first names of 1990 (public domain); the other lists are written for Lens. With
`analysis.entities: spacy`, spaCy's types are used instead, and with
[Find places and dates](configuration.md#anytopdf) anytopdf's places join them.

Older installs get the same types once, as an upgrade step (`entity-types`, [Upgrades](database.md#upgrades)) when
they first start on this version: entities analysis left as Terms get the type these rules give them from their names
and the lines that mention them. Types people or agents set, entities on a fixed list, hidden ones, Unknown and
Unlabeled stay as they are. The changes are recorded in the graph's history as **Gave a type to** events, so they can
be rolled back there.

The **Entities** page in the web app lists a namespace's entities (search, type and collection filters, a Hidden tab).
An entity's name opens its own page, `/wiki/<namespace>/<Name>`, with its notes, every line that says it and what it
comes up with ([Entity pages](notes.md#entity-pages)); the rest of its row opens a side panel where editors rename,
retype, describe, merge and hide it.

- **Merge from the side panel:** **Merge** opens a find box; type a name and press Enter, and the entity picked is
  merged into the open one at once. Its **Maybe the same** list offers likely doubles (same letters, an acronym, a near
  spelling) with **Merge** and **Not the same**. Renaming an entity to a name another entity has offers to merge them.
- **Merge or retype many:** editors tick rows (or the whole page) and the bar at the bottom merges them into one (the
  one on the fixed list, else the most mentioned; the arrow picks another) or gives them all one type.
- Every merge and bulk type change happens at once and has **Undo** on its notice, so nothing asks to confirm.
- **From a recording:** in a recording's **Entities** tab, the pencil next to an entity opens the same side panel, so
  editors rename, retype, hide or merge it without leaving the recording.

- **Index** (`GET /api/v1/entities`):
  - Search forgives misspellings and covers aliases.
  - Filters: type, namespace, collection (and the collections inside it), person, asset, date range, minimum
    mentions, and hidden entities.
  - Sorts: most mentioned, most assets, most recent, rising, and name.
  - Options: grouping by name across namespaces, a twelve-month sparkline, and facets.
- **Entity page** (`/wiki/<namespace>/<Name>` in the web app; `GET /api/v1/wiki/{ns}/{address}` finds it by name): its
  details and aliases, its notes, paged mentions (each line with the words highlighted, linking to its moment), and
  connections (entities mentioned together, people, assets); the API also gives a timeline by month or week and
  namespace.
- **Explorer:**
  - `GET /api/v1/graph/explore?focus=e12` gives the neighbourhood of an entity or person. Depth is one or two hops, and it
    can be filtered by type, edge kind and strength, within one namespace or all shared ones.
  - `GET /api/v1/graph/path?a=…&b=…` gives the shortest chain between two nodes, with the lines that support each link.
- **Curation** (editors of the entity's namespace):
  - **Rename:** the old name stays as an alias. It can optionally correct the words in every line of text, with a
    dry-run preview first, then re-analysis.
  - **Change type, describe (`PATCH /api/v1/entities/{id}`), and hide or restore.**
  - **Merge:** with undo.
  - **Mark two entities as not the same.**
  - **Move or remove a single mention.**
  - **Link the same thing across namespaces.**
- **Merge suggestions:** same letters (ignoring case, spaces and punctuation), acronyms, one name containing the other,
  close spellings, and names that sound alike (likely transcription errors).

### Dates

With `analysis.dates` on (Settings → Analysis → Read dates; off by default), analysis reads each line for the dates it
gives and keeps them as days on the calendar, so agents and the API can ask what was said about a day, a month or a
range: `GET /api/v1/dates?start=2026-10&end=2026-10`, the MCP tool `list_dates`, the assistant's `find_dates`. Each
date keeps the words said, its first and last day and how long it is (a day, a weekend, a week, a month or a year), and
its line, linked to that moment.

- **Said relative to the day:** "yesterday", "tomorrow", "in three days", "two weeks ago", "next month", "this
  weekend" are worked out from the asset's own date (its `recorded_at`), and marked `relative`. "this Friday" is the
  coming one, "next Friday" the Friday of the week after, "last Friday" the one before. "last week" is Monday to Sunday.
- **Written out:** "3 March 2024", "March 3rd", "March 2024" (the whole month), "2026-10-20", "20/10/2026", and a year
  after a word that makes it one ("in 2024", "since 1998"). A day without its year takes the asset's year.
- **Left out rather than guessed:** a weekday on its own ("on Friday": the one before or after?), a date that reads two
  ways ("10/11/2026"), "may" and short month names in small letters ("I may", "to mar"), and a bare number like "2000
  people". Without an asset date, only dates that give their year are kept.
- The rules read English. Nothing is sent anywhere and no model is called. Turning it on reads dates as assets are
  analysed; Reprocess (Analyze) reads them for assets already there.

### Entity setup

Each namespace has an entity setup (Entities → Setup, `GET`/`PUT /api/v1/namespaces/{name}/entity-setup`): the types
it keeps (names of other types are left out when an asset is analysed) and a description of what it's about. Any
collection can have its own setup, which also holds for the collections inside it; an asset follows the setup of the
nearest collection that has one, else its namespace's. Editors of the namespace change its setup and any collection's;
editors of a collection change that collection's. A namespace can add entity types of its own (Entities → Types,
`/api/v1/namespaces/{name}/entity-types`), each with a description of what counts as one; a type can be deleted once no
entity has it. Changes apply to assets analysed from then on; "Apply to analysed assets"
(`POST /api/v1/namespaces/{name}/entity-setup/apply`) analyses the namespace's or a collection's assets again.

A setup has a mode:

- **Self-organizing** (the default): every name the extractors find becomes an entity, and people merge, rename and
  describe them. Names of types the setup doesn't keep are left out.
- **Fixed list:** editors define the entities (Entities → Add entity, `POST /api/v1/namespaces/{name}/entities`), each
  with a type, its other names and a description, for the whole namespace or for one collection and those
  inside it. Each name found goes to the defined entity it names (by name or one of its other names, in any case; the
  defined names are looked for in the text too), else to **Unlabeled** when it belongs here (a type the setup
  keeps; with every type kept, anything but dates and numbers), else to **Unknown**. Unknown and Unlabeled are always
  there and can't be renamed, retyped, merged, hidden or deleted. Moving a mention to a new name in a fixed-list place
  adds that name to the list. `PATCH /api/v1/entities/{id}` sets an entity's other names and whether it is on the list;
  a defined entity nothing mentions can be deleted.
- **Hybrid:** a few defined entities, then self-organizing. Names go to a defined entity as in the fixed list, or to
  an entity they already are; any other name of a type that belongs here becomes an entity of its own, and names of
  other types go to **Unknown**.

**Matching names** is by name (in any case, or one of an entity's other names) unless the setup says "by name, then by
description": then the names the rules can't place go to the LLM with the entities' descriptions, their other names and
what the namespace is about. In the self-organizing mode it says which described entity a new name is, if any; the name
then becomes one of that entity's other names, so the rules place it from then on. In the fixed mode it says which
defined entity a name is, or that it doesn't belong here (Unknown); in the hybrid mode it may place a name on a defined
or described entity, or say it doesn't belong here. Without a reachable LLM the rules decide. The assistant reads a namespace's setup (`entity_setup`) and proposes entity changes for approval, including
descriptions, other names and new entities on a fixed list.

Curation survives re-analysis: merged names become aliases, and moved or removed mentions become per-line overrides.
Everything is audited. People who can't read a namespace never see its entities, mentions or graph nodes, and requests
about them come back as not found.

## Search

All words must appear, matched after English stemming ("exploit" also finds exploits and exploiting); "quoted phrases"
must appear as written; OR separates alternatives; NOT, or a `-` before a word or "phrase", leaves out the lines that
have it (`budget -draft` finds budget without draft or drafts, `budget NOT "first draft"`), and a typed AND changes
nothing. Filter by namespace, person, emotion or asset. The words are
looked up in the index of `search.engine`: a SQLite FTS5 file by default, SurrealDB's own index, or OpenSearch
([configuration](configuration.md#word-search)). For archives that aren't in English set `search.stemming: none` and
run `lens reindex`. There is no prefix search: a trailing `*` (`expl*`) is ignored and the word is searched as it is;
stemming covers most of what prefixes were used for.

### Search by meaning

With an embedding model set up (Settings → Search, or `embeddings` in archive.yaml: see
[configuration](configuration.md#search-by-meaning)), search also finds moments about what was asked in other words:
"money worries" finds "we can't afford the rent this month". The `embed` step (in the standard pipeline, after
analyze) joins an asset's lines of text into passages of about `embeddings.passage_chars` characters (each within
one page of a document), adds what its shots or pages are described as showing, and has the model embed each; the
vectors live in the `passage` table under SurrealDB's HNSW index. Indexing an asset again only embeds the passages
whose text changed, so a corrected line costs one request; correcting, splitting or merging lines and renaming an
entity across the text run the step again.

A search is matched three ways (`mode` on `GET /search`, the Match switch in the web app):

* **auto** (the default): by its words and by meaning, fused by reciprocal rank, when search by meaning is set up and
  the query has no "quoted phrases", OR or NOT (those ask for exactly those words); else by its words.
* **keyword**: only the words, as above.
* **semantic**: only by meaning.

A passage found by meaning is shown at its line that best fits (the person or emotion filtered on, else the one with
most of the query's words), marked Related, with how alike it is (`similarity`, cosine). One that holds a keyword hit
adds to that hit's rank instead of showing twice. Only passages at least `embeddings.min_similarity` alike count, and
none much further than the closest one, so an unrelated query finds nothing rather than whatever is least unlike it.
Facets count the moments found by meaning too. The assistant's search tool and chat's retrieval use the same passages,
so a question finds excerpts that answer it without sharing its words.

The vectors are only comparable within one model. When the model (or `embeddings.document_prefix`) changes, the old
vectors are dropped and searches go by the words until assets are indexed again: the **Index for search by
meaning** routine (seeded, hourly at :20) queues the embed step for up to 500 assets not yet indexed with the
current model each time, and does nothing (no run is recorded) while search by meaning is off or everything is indexed;
Settings → Search shows how far it has got and can queue more now; `lens embed` indexes here and now. When the
embeddings server can't be reached, the step is skipped (the routine tries again later) and searches go by the words,
saying why.

### Similar content

The web app, the API (`GET /similar`), the assistant and the MCP server (`find_similar`) list the assets most like
a text (a sentence, a passage, a title), an asset, a topic or an entity.

Four lists are fused by reciprocal rank, so an asset alike in several ways comes first:

* **meaning**, when search by meaning is set up: the passages closest to the text (a topic's labels and definition,
  an entity's name and description), or for a resource to the centre of its own passages, so no model call is needed;
* **key words**: the text's words that the archive says in fewest recordings (from the analysis step's term counts),
  or a resource's keywords, any of them, through the word index: resources saying more of them first;
* **topics**: resources about the same topics, a topic fewer resources are about counting for more;
* **entities**: resources mentioning the same people, organisations, places and terms, the rarer counting for more.

Each result cites why: its closest passages and the lines with the shared words (each opens at that moment or page),
the words, and the topics and entities in common. Without an embeddings server (the default) the last three still
answer, and no language model is called; with one, a selected text costs one embedding request, logged in the
activity ledger.

In the web app:

* every resource has a **Similar** tab, after Entities, listing what is most like it;
* selecting text anywhere (a transcript, a page, a title, a summary, a graph label) opens a popover with the five
  resources most like the selection, each citing why, and **Find more** opens the full list at `/similar`, with
  paging and a search for the key words. The account menu's **Similar on select** switches between showing the
  popover at once and showing a small **Similar** button to click first. Text typed in a field or an editor (notes)
  opens nothing;
* a topic's and an entity's drawer, and a resource, topic or entity selected on the graph canvas, list similar
  content too.

### Finding by any attribute

The API (`POST /find`), the assistant (`find_resources`) and the MCP server (`find`) find assets by what is known
about them, one filter for all three:

* who is **seen** in them (`faces`: face identities, [Video](video.md)) and **heard** (`speakers`: people, by voice);
* the **entities** they mention and the **topics** they were accepted as about (a topic brings its narrower topics);
* their **kind** (`types`: audio, video, transcript, document, image) and **content type** (`content_types`: podcast,
  interview, scan, photo, email … [Content types](#content-types));
* **when** they were made (`date_from`, `date_to`: a day, a month or a year, so `2025` is the whole year) and how
  long they are;
* their tags, language, the kinds of object seen in them, the emotions heard, their status, collection and where they
  came from;
* and words said or written in them (`text`, matched as a search is, by meaning too when that is set up).

Filters combine with AND, the values of one filter with OR. Faces, people heard, entities and topics take an id or a
name: a name matches every face, person, entity (or one of the other ways it's said) or topic label called that in the
namespaces you can read, ignoring case, and `resolved` says what it matched. A name that matches nothing is an error
that lists the closest names, so an agent can correct itself.

Each asset found says **why** it matched, with up to three moments per reason to cite: where the words are, when the
face is on screen (or the pages it's on), the person's first lines, the lines that mention the entity, and the topics,
content type, kinds of object and emotions that matched. With `text`, the asset whose moment matches best comes
first; otherwise the newest. Nothing here calls a model, apart from `text` searching by meaning when it is set up
(one embedding request, logged in the activity ledger).

The Library list (`GET /resources`) takes the same attributes as ids: `face`, `entity`, `topic` (as it is, without its
narrower topics), `content_type` and `emotion`, besides its other filters ([API](api.md#resources)).

Planned, not built yet: these filters in the Library's filter bar and in saved collections, finding by what a photo
shows (`GET /search/photos`) inside the same filter, and ranking by more than the best matching moment.

### Classifying

The API (`POST /classify`), the assistant (`classify`) and the MCP server (`classify`) say which of some labels fits an
asset, or a piece of text, how sure that is, and every label ranked. The labels are:

* given, as a list (`["science", "sport: games and teams"]`, what a label means after `: `) or as
  `{label: what it means}`, two to 60 of them;
* or a vocabulary Lens keeps: `content_type`, the [content types](#content-types) of the asset's base type (all of
  them for text), or `topic`, its namespace's topics with their definitions and other labels (a text names the
  namespace), the most used first.

The decision model answers when it is set up (Settings → AI assistant: Jev, typesafe.ai's System One, or Laya on
the machine), else the language model, asked for JSON; `by` says which, and the call is in the
[activity ledger](activity.md), counted for the asset and its namespace. The model reads the asset's kind, file
name, length, language, tags and accepted topics, and its title, summary, the entities it mentions most and the start
of its text, marked as data so instructions in it aren't followed
([prompt injection](assistant.md#third-party-content-and-prompt-injection)). `question`
asks something of your own ("Which desk should this go to?").

An answer is `sure` at `decisions.act_above` confidence or more (0.8 by default). Nothing changes unless `apply` is
set, by an editor, and the answer is sure: then the asset's content type is set (as choosing it in its Details tab
does), the topic is suggested for it, waiting for someone to accept it (one it already has, waits for or was dismissed
from stays as it is), or the label given is added as a tag (at most 40 characters). `applied` says which, and it is
in the audit log. The MCP and assistant tools never apply; the assistant can then suggest the topic (`suggest_topic`).

Workflows classify too, with a Classify node ([Workflows and the canvas](#workflows-and-the-canvas)), so a pipeline
can classify every asset it runs on. Planned, not built yet: classifying many assets at once from the API, and labels
with examples.

## Background work

Imports, pipeline runs and folder scans return at once and run as jobs stored in SurrealDB. The server runs
`workers.inline` workers itself; more can run anywhere that reaches the database, each limited to the steps it can do.
A job whose next step a worker can't run goes back on the queue for one that can, so a Mac can transcribe with mlx while
the container does the rest. Jobs can be cancelled and retried from the failed step; a job whose worker stops
responding is retried. In Activity the queue is handled as a whole too: **Clear queue** cancels every queued and held
run you may process (in the namespace in view), asking first, and its message can undo it; under **Queued** the runs
show in the order workers take them, and dragging one by its grip (or Alt+↑ and Alt+↓ on the grip, or Move to top
and Move to bottom in its menu) reorders the queue; **Retry failed** retries every failed run, one per asset, at the
back of the queue, and can be undone too. Agents do the same through [the API](api.md#jobs) and the MCP tools `runs`,
`clear_queue`, `move_run` and `retry_runs`.
Workers heartbeat every 30 s, and every 15 s while they run a job however long its step takes, with their machine's
load. In Activity → Workers an admin can pause a worker (it takes no new jobs; the one it has
carries on to the end), drain it (it also hands that job back to the queue after the step it's on, so another worker
carries on, and stays paused) or resume it. `lens worker --name mac-mini` keeps its pause across restarts; the
server's own workers are named after its process, so they start afresh. Steps added to a job while it runs (attaching audio does) run after its other steps: a worker
reads the job's steps again before each step, and only finishes a job whose steps are all done.
`GET /api/v1/events` streams job progress (server-sent events). A run's whole log is kept (up to 100,000 lines) and
streams to its Activity page as it's written.

A run keeps a record of each step: when it started and finished, how long it took, how it ended (done, skipped,
failed), its last message (or why it skipped, or its error), which worker ran it, its lines of the log, and the outputs
it saved (with the template version and model that made them). A step with nothing to do for an asset skips
itself and says why: no LLM is configured, it isn't a video, the speakers came with the transcript. Retrying a run
starts the records over from the step it retries. Each finished or skipped step also adds its time to the last 25 of
its kind (template steps per template), and `GET /jobs/{jid}` turns these into how long each step usually takes on
that asset (scaled to its length for transcribe, diarize and the video steps) and about how long an active run
has left.

    lens worker --steps transcribe,diarize     # e.g. on the Mac, with SURREAL_URL pointing at the server

A worker that runs every step also scans watched folders and runs routines when they are due, so Docker and the
packages need no other process for them. One limited with `--steps`, or started with `--no-schedule`, only runs jobs.
A folder scan and a routine run are each claimed first, so several processes doing this never repeat one.

## Storage sources

Admins add sources in the app (`/api/v1/sources`): S3 or S3-compatible, Dropbox, Google Drive, OneDrive, SFTP, SMB,
WebDAV, or a folder on this machine; or an email account (IMAP, or Gmail and Google Workspace), a calendar feed (iCal),
Google Calendar or a [news feed](#news-feeds-rss-and-atom) (RSS or Atom), below. A watched folder maps a path on a source to a namespace, with include/exclude
patterns, audio and/or transcripts, a polling interval, how long a file must be unchanged before it is picked up, and
whether files already there are imported (backfill). New audio is queued for the full pipeline; new transcripts are
imported and analysed. Audio stays where it is: it is copied to a cache for processing and streamed from the source for
playback. A file whose asset someone deleted, or moved to another namespace, isn't imported again, even when it
changes.

Admins can also import chosen files of a source once, without watching their folder: Import → From a source, tick the
files, then Import (`POST /api/v1/import/source`). The listing marks files that are assets already, and where.

- Credentials are encrypted in the database (AES-GCM, key from `ARCHIVE_SECRET_KEY` or `data_dir/secret.key`) and given
  to rclone in a private temporary config file per call; tokens rclone refreshes are saved back.
- **Dropbox, OneDrive, and Google Drive as a Google account sign in through rclone on the Lens server**, with nothing
  to install elsewhere and no app of your own: Sign in with Dropbox (or Microsoft, Google) opens the service's page in
  a new tab, which asks you to allow rclone, the tool Lens connects with. Afterwards the service sends that tab to
  `http://127.0.0.1:53682/?state=…&code=…`, where `rclone authorize` waits on the server. When your browser runs on
  the same machine as Lens that reaches rclone and the dialog carries on by itself; otherwise the page doesn't load:
  copy its address from the address bar and paste it into the dialog. Lens passes only that address's state, code and
  error on to rclone, and only when the state is this sign-in's. The connection is then made and tested (OneDrive's
  own drive filled in), and the dialog goes on to pick a folder. One sign-in runs at a time (rclone listens on one
  port), each for ten minutes. A connection whose token stopped working shows Sign in again. Pasting the token
  `rclone authorize dropbox` (or drive, onedrive) prints on another computer still works: Paste a token from rclone
  instead.
- Agents sign in through rclone the same way: `POST /api/v1/sources/rclone-signin` with `{"type": "dropbox"}` (or
  `{"source": <id>}`, and `params`) answers `{id, url, port}`; the person opens `url`, and the agent posts the address
  they end on to `POST /api/v1/sources/rclone-signin/{id}` (`{"address": …}`), which answers `{connected: <id>}`
  (400: not this sign-in's address, still waiting; 410: cancelled, refused or ran out). `DELETE` stops it. Audited as
  `source.signin`.
- **Google Drive can connect with Google** as Google Calendar does: when Lens has a Google OAuth client (the Google
  sign-in provider's, or an earlier Google connection's), a new Google account connection uses it, so **Connect with
  Google** saves the connection, opens Google's consent screen in this browser and comes back with the account
  connected (its address shows on the connection). Read only asks Google for `drive.readonly`, Full access for all of
  Drive. With no client Lens signs in through rclone; Connect through your own Google app instead takes a web
  application client of a Google Cloud project with the Drive API turned on (the form shows the redirect URI to
  register). The connection keeps that client with its token (`client_id`, and the secret encrypted) so rclone renews
  the token with it; a token from rclone (signing in through rclone, or pasted) drops it. A connection whose sign-in
  stopped working shows **Connect Google account** on its row. As for Google Calendar, connecting happens in the web
  app (`POST /api/v1/sources/{id}/google`).
- Google Drive can also sign in as a service account: choose Service account and upload its JSON key file
  (`service_account_credentials`). Share the folders Lens should read with the account's address, or give a shared
  drive ID (`team_drive`); in Google Workspace, `impersonate` reads a user's own Drive through domain-wide delegation.
- To import from one Drive folder only, set Root folder (`root_folder_id`): paste the folder's link from the address
  bar (`https://drive.google.com/drive/folders/<id>`, `open?id=<id>`) or its id; Lens keeps the id. The shared drive
  takes its link the same way. A file's link is refused with a note to paste its folder's.
- A rate-limited connection (Google Drive throttling an account) still works: its check shows it working, slowly;
  rclone backs off and retries (Drive gets 10 low-level retries); and a file a scan couldn't fetch waits for the next
  scan, as do the rest of that scan's files, rather than failing for good.
- Folders on this machine can only be watched inside `sources.local_roots`, and the rclone binary can only be set in
  archive.yaml: the web app can neither open up the server's disk nor choose what runs.

### Email (IMAP), calendar feeds (iCal) and Google Calendar

These kinds of source aren't storage and don't use rclone; their messages and events are shown as files, so browsing,
importing chosen ones and watching work as above. [News feeds](#news-feeds-rss-and-atom) work the same way.

- **Email (IMAP)**: host, port, security (SSL/TLS, STARTTLS or none), user and password (an app password where the
  provider has them). Mailboxes are the folders, and each message is a file `<mailbox>/<uidvalidity>-<uid>.eml`,
  named by its subject (a mailbox the server rebuilds gets new names, so an old one never reads another message). A message is an email like any uploaded one: a document whose attachments are kept and made resources of
  their own, titled by its subject and dated when it was sent. On a server that can't make PDFs (no Chromium or
  LibreOffice) it comes in as text instead, without its attachments. Watching the whole account (no path) takes every
  mailbox but the bin, junk and drafts and the views of mail kept elsewhere (Gmail's All Mail, Starred and
  Important), and a message several mailboxes show (one Message-ID) becomes one resource. A watch remembers the last
  message it saw in each mailbox and asks only for newer ones. Lens only reads: mailboxes are opened read-only and
  messages fetched without marking them read.
- **Routing rules** (email watches only): a shared inbox can feed several namespaces. Each rule has conditions on who
  sent a message (`from`), who it was for (`to`: To, Cc, and the address the inbox received it at, so mail to an alias
  like support@ is told apart), its `subject`, its mailing list (`list`, the List-Id) or its `mailbox`, and sends
  matching messages to a namespace, or skips them. Rules are tried in order and the first whose conditions all match
  decides; a message no rule matches goes to the watch's own namespace, as it does with no rules. Patterns ignore case;
  one without `*` or `?` matches anywhere in the text (`acme.com`), one with them must match all of it
  (`*@acme.com`), and a condition can list several patterns, any of which may match. A rule names a namespace that
  exists (rules never create one); mail for a namespace deleted since goes to the watch's. Rules apply to messages that
  arrive after they're saved. `POST /api/v1/watches/routes/preview` shows where the latest messages would go before
  saving, and a scan's stats count the messages `routed` elsewhere.
- **Gmail and Google Workspace**: see [below](#gmail-and-google-workspace).
- **Calendar feed (iCal)**: the calendar's iCal address (`https://` or `webcal://`; a user and password if it asks for
  one). The address is kept encrypted like a password and never shown again, since a private calendar's address is
  all it takes to read it. It is fetched the way web pages are captured: public addresses only (and the networks in
  `documents.web_networks`), on ports 80 and 443, and the password is never sent on to another server the calendar
  redirects to. The feed is one folder of events, each a file `<id>.ics` named by its date and title; a moved occurrence of a
  repeating event is an event of its own. An event comes in as text (its title, when and where, the organizer and
  attendees, how it repeats, and its description), dated when it starts: an all-day event on its date, wherever you
  are. Time zones are read as IANA names, Windows' names (as Outlook writes them), or the calendar's own VTIMEZONE. An event that changes (its LAST-MODIFIED,
  or its size) is read again into the resource it already is, rather than made a second one.
- **Google Calendar** (`gcal`): read through Google's Calendar API with read-only access (`calendar.readonly`), so
  nothing in the calendar is changed. Each calendar is a folder and each event a file `<calendar id>/<event id>.ics`,
  named by its date and title, and read as text exactly as an iCal feed's events are; it also says which occurrences
  of a repeating event were cancelled and the event's video call. A changed occurrence of a repeating event is an event
  of its own, as Google keeps it; working-location entries ("Home", "Office") are left out. It signs in one of two ways,
  each kept encrypted like a password and never shown again:

    - **Google account** (`token`): **Connect with Google** in the connection form saves the connection and opens
      Google's consent screen in this browser. Once you allow read-only calendar access, Google sends you back to
      Sources with the account connected (its address shows on the connection) and its calendars open, to pick one to
      watch. It goes through an OAuth client of a Google Cloud project with the Calendar API turned on: the Google
      sign-in provider's (Settings › Sign-in) when there is one, else an earlier Google connection's, else one you
      enter in the form: a web application client whose authorized redirect URIs include
      `<your Lens address>/api/v1/auth/external/google/callback` (the address Google sign-in uses too; the form shows
      it). While the client's OAuth consent screen is in Testing, Google ends its refresh tokens after 7 days and warns
      that the app isn't verified; publish it to keep the connection. A connection whose sign-in stopped working shows
      **Connect Google account** on its row. Connecting happens in the web app (`POST /api/v1/sources/{id}/google`
      returns the consent address, and refuses API tokens), because the consent itself is a person's and comes back to
      the browser that asked. What is kept is the "authorized user" JSON (`client_id`, `client_secret`,
      `refresh_token`), which an agent or a person can also save directly as the `token` (Or paste an authorized user
      JSON): `gcloud auth
      application-default login --client-id-file=client.json --scopes=https://www.googleapis.com/auth/calendar.readonly,https://www.googleapis.com/auth/cloud-platform`
      writes one (to `application_default_credentials.json` in gcloud's folder).
    - **Service account** (`service_account_credentials`, its .json key file). On its own it reads the calendars shared
      with its address: share them, and give their IDs in `calendars` (Google Calendar › Settings › Integrate
      calendar). In Google Workspace it can read a user's calendars instead (`impersonate`), once an admin gave it
      domain-wide delegation for the `calendar.readonly` scope (Admin console › Security › API controls).

    `calendars` (IDs, comma separated) limits the source to those; empty, it reads every calendar in the account's
    list, and watching the whole account (no path) leaves out Google's own (holidays, birthdays, week numbers). Events
    that ended more than `days_back` days ago (365) are left out; empty takes every event. A watch lists each calendar
    again every poll (`events.list`, 2,500 events a call). Every call to Google is a row in the activity ledger
    (`google.token`, `google.calendar`); Google throttling the account shows as working, slowly, and a scan it
    interrupts is tried again at the next poll. The token endpoint is always Google's own, whatever a key file says. Without a Google Cloud project,
    a Google calendar's secret address in iCal format (Settings › Integrate calendar) works as a calendar feed.

    **The assistant** (`write`): "Only reads" (empty, the default), or "Also adds events, after your yes" (`yes`),
    which lets an admin's [assistant](assistant.md#mail-calendars-and-files) add an event once the person approves it,
    with all of it shown first. That asks Google for `calendar.events` too, so a connected account connects again
    (Connect again), a service account needs the calendars shared with it with "Make changes to events", and in
    Workspace an admin allows `calendar.events` for it as well. Adding an event is a row in the activity ledger
    (`google.calendar`, call `events.insert`) and in the audit log (`calendar.add`).
- `.eml` and `.ics` files in any source, and uploaded through Import, can be read as text this way too.

### News feeds (RSS and Atom)

A news feed (type `rss`) is a source too: on the Sources page (Sensors › Files, email and calendars), add a News feed,
then watch it. Watching a feed subscribes to it: each new post becomes a text resource of the namespace, titled and
dated by the post, so answers cite it and its link. An agent does both in one step: `subscribe_feed` over
[MCP](mcp.md) or the assistant's tool of that name (admins) takes the address and a namespace, reads the feed first and
keeps it only if it works; `news_feeds` lists what's followed and `unsubscribe_feed` stops one.

- **Address**: the feed's own (`https://`, or `feed://`), or a page of the site: when the page names its feed
  (`<link rel="alternate" type="application/rss+xml">`, as blogs and news sites do), the first feed it names is read.
  A user and password if the feed asks for one. The address is kept encrypted like a password and never shown again,
  since a private feed's address often carries its key. It is fetched as calendar feeds are: public addresses only
  (and the networks in `documents.web_networks`), on ports 80 and 443, and the password is never sent to another
  server, including one that a page names as its feed's.
- **Posts**: the feed is one folder of posts, each a file `<id>.atom` named by its date and title. RSS (0.9x, 1.0,
  2.0) and Atom (0.3, 1.0) are read by [feedparser](https://github.com/kurtmckee/feedparser) (BSD), which drops
  scripts, styles and event handlers from the posts' HTML and makes their links absolute. Whatever the feed, each post
  is kept as an Atom entry document of its own (RFC 4287): its title, the post's page, enclosures (a podcast's audio),
  when it was published and updated, its authors, categories, summary and content, and the feed's title and site
  (never its address). It comes in as text: the title, its link, who wrote it, when, the feed, its categories and
  enclosures, then its text. Its content type is Feed post, so a namespace can give feed posts a pipeline of their own
  (Settings › Content types).
- **Automations**: each new post is a `feed_post.received` activity (a kind of `asset.added`) with the post's
  address, site, feed, author, date and categories, so an automation can start on posts from one feed, author or
  category ([Automations](automations.md#kinds)). What the feed wrote is marked as outside text.
- **New posts only, unless you backfill**: as for any watch, a new watch on a feed takes the posts published after it
  starts; turn on backfill to bring in those the feed lists already. The newest 500 posts of a feed are listed.
- **Light on the network**: a feed read is reused for a minute, then asked for again with its `ETag` and
  `Last-Modified`, so a feed that hasn't changed costs one small request. A post is known by its id (an RSS item's
  `guid`), so a feed that reorders or retitles its posts doesn't bring them in twice; a post whose text changes is read
  again into the resource it already is. A post the feed drops later keeps its resource and its file.
- `.atom` files uploaded through Import (a single post, or a whole Atom feed) are read as text the same way.

Refine later: JSON Feed (feedparser reads RSS and Atom only); an option to capture each post's whole page, for feeds
that carry only a summary; podcast feeds bringing in each episode's audio to transcribe; OPML import of a feed list;
subscribing in one step (address and namespace) on the Sources page, as agents can.

### Gmail and Google Workspace

A Gmail or Google Workspace mailbox (type `gmail`) is read through Google's Gmail API, read only (`gmail.readonly`),
or over IMAP (`via: imap`). For a personal Gmail account, an email (IMAP) connection with an app password works too,
with nothing to set up at Google. A Gmail source signs in one of two ways:

- **Google account** (`token`): **Connect with Google** in the connection form, as for
  [Google Calendar](#email-imap-calendar-feeds-ical-and-google-calendar): Google's consent screen asks to read your mail
  (read only; all of Gmail to read over IMAP, since Google has no read-only scope for it), then sends you back to
  Sources with the account connected, its address as the source's `user`, and its labels open. Turn on the Gmail API in
  the OAuth client's Google Cloud project and add the Gmail scope to its consent screen. Google reviews apps that read
  Gmail before people outside your Workspace domain can use them without a warning: while the consent screen is in
  Testing, it works for its test users and Google ends their sign-ins after seven days. Unticking the mail permission on
  Google's page leaves the source unconnected, and says so. An agent saves the `token` directly instead (in the form:
  Or paste an authorized user JSON): `gcloud auth application-default login --client-id-file=client.json --scopes=https://www.googleapis.com/auth/gmail.readonly,https://www.googleapis.com/auth/cloud-platform`
  writes one; enter the account's address as `user` to read over IMAP.
- **A service account** (Google Workspace). Upload the service account's JSON key and enter the address of the person
  whose mail Lens reads; nobody signs in. A Workspace admin lets the account's client id use
  `https://www.googleapis.com/auth/gmail.readonly` (or `https://mail.google.com/` to read over IMAP) under Admin
  console › Security › API controls › Domain-wide delegation. One source reads one person's mailbox.

Labels are the folders: the inbox, sent, starred, important, the inbox's categories (Primary, Social, Promotions,
Updates, Forums) and your own labels. A message is a file `<label id>/<message id>.eml`, read as Gmail keeps it, so it
becomes an email document as an IMAP message does. Watching the whole mailbox (no path) takes everything but spam, the
bin, drafts and chats, each message once. A watch follows Gmail's history: a scan asks only for what arrived since the
last one (and, on a label, messages the label was put on since), and a watch that doesn't take what's there already
reads nothing old at all. Messages are taken as soon as Gmail lists them, with no wait for them to settle. When Gmail
no longer keeps the history a watch points into (it keeps about a week), the watch reads what arrived from an hour
before its last scan on, and skips what it already has. Browsing a label shows its newest 200 messages; Import → From a
source takes any message chosen. Routing rules work as for IMAP, and a message's `mailbox` is its labels.

Over IMAP, a Gmail source works as an IMAP account does, at imap.gmail.com with the Google sign-in (XOAUTH2). That
needs Google's all-of-Gmail scope, since Google has no read-only one for IMAP; Lens still only reads. Changing `via`
starts the source's watches again as new ones (taking what's there only if they backfill), since the two name folders
and messages differently: check their folders. Each access token Google gives, each listing and each message read is a
row in the activity ledger (`google.token`, `gmail.list`, `gmail.read`); none costs anything.

**The assistant** (`write`): "Only reads" (empty, the default), or "Also sends mail and saves drafts, after your yes"
(`yes`), which lets an admin's [assistant](assistant.md#mail-calendars-and-files) send a message or save a draft once
the person approves it, having read the whole message. Through the Gmail API that asks Google for `gmail.compose` too,
so a connected account connects again (Connect again), and a Workspace admin allows
`https://www.googleapis.com/auth/gmail.compose` for a service account as well; over IMAP, all of Gmail already covers
it. Lens sends through the Gmail API either way. Each message sent or draft saved is a row in the activity ledger
(`gmail.send`, `gmail.draft`) and in the audit log (`mail.send`, `mail.draft`).

## Pipelines and templates

A pipeline is a named, versioned list of steps; each namespace can choose its default (otherwise: transcribe, diarize,
analyze, summarize, report). Imports, watched folders and reprocessing use the namespace's pipeline; steps that need
audio skip themselves for imported text. Steps:

- `transcribe`, `diarize`, `clean` ([Cleaning up transcripts](#cleaning-up-transcripts)), `analyze`, `summarize`, and
  `report` (the built-in report).
- `llm`: renders a prompt template, sends it to the configured model, checks the reply against the template's JSON
  Schema and saves it as a named output (e.g. `meeting_notes`).
- `spec`: writes a technical spec from the transcript into a note linked to the asset, with the Spec from transcript
  template unless the step names another prompt template ([Specs from transcripts](notes.md#specs-from-transcripts)).
- `report` with a template: renders an HTML page into the namespace's reports. These pages may not run scripts.
- `export`: renders a template to a file (Markdown, text, HTML...) and can copy it to a storage source.
- `convert`: makes new assets from one, a video's soundtrack as audio or a document's pages as images
  ([Converting assets](#converting-assets)).

Any step can carry a condition: `min_minutes`, `max_minutes`, `source` (audio or transcript), `languages`.

Templates are versioned (publish, history, diff) and rendered in a sandboxed Jinja environment. It can't reach Python
internals, caps output size, and escapes HTML in reports. Templates see `recording`, `speakers`, `segments`,
`transcript` (trimmed to `llm.max_chars`), `sections`, `entities`, `keywords`, `summary`, `stats` and `outputs`. The
summary's `key_points` and `action_items` print as their text and have `text`, `who` (empty when not given) and `t0`
(where the line they come from starts, in ms; none when not known); summaries made before they had times hold plain
strings. A fresh archive starts with three: Meeting notes (prompt), Markdown transcript (export) and One-page brief (report).
Every install, new or not, also has Spec from transcript (prompt), which the `spec` step uses.
`POST /api/v1/templates/preview` renders any template, saved or not, against an asset, and with `run: true` also asks
the model.

New pipelines, workflows, templates and routines don't need a name typed: the web app guesses one (from a pipeline's
steps, what a workflow runs on, a template's kind, a routine's schedule and actions), numbered when it's taken
("Prompt template 2"). The pencil next to the name on its page renames it later, as does `PATCH` on its address.

### Choosing the converter for a Read step

A Read step makes an Office file, email, web page or photographed page into a PDF before it reads it. Which program
does that is the server's choice, `documents.converter` in Settings → Documents ([anytopdf](configuration.md#anytopdf)),
unless the step names its own: `{"type": "read", "converter": "anytopdf"}` makes every document and image in that
pipeline into a PDF with anytopdf (flattening a photographed page first), `"lens"` uses Lens's own converters only, and
`"auto"` uses anytopdf only for what Lens can't convert. Other pipelines keep the server's choice. In the pipeline
editor it's the Read step's **Make PDFs with** setting. Where a step asks for anytopdf but it isn't installed here and
no conversion node is set, Lens converts the file itself and the run's log says so.

### Converting assets

A Convert step makes new assets from the one it runs on. Default pipelines have none; add one in the pipeline editor
or on the canvas (Step library → Convert).

- **To audio** (`{"type": "convert", "to": "audio"}`): a video's soundtrack becomes an audio asset, titled
  "<the video's title> (audio)". It is m4a unless `format` says `mp3`, `opus` or `flac`; AAC sound in m4a (or MP3
  sound in mp3) is copied as it is, not encoded again. Audio, documents and videos with no sound are skipped.
- **To images** (`{"type": "convert", "to": "images"}`): each page the Read step drew becomes an image asset,
  titled "<title>, page N", the same JPEG the asset's page shows (documents.page_pixels on its longest side). Put a
  Read step before it; without drawn pages it skips. A multi-page TIFF's frames work too; at most 200 pages are made
  into images from one document.

New assets are kept in the same namespace and collection, encrypted where its files are, and each says which asset
(and page) it was made from: `converted_from` on `GET /api/v1/recordings/{id}`, and a link on its page. They then run
the pipeline their content type gives them, as an upload does; `pipeline` names another pipeline by its id, or
`"none"` keeps them as they are until a pipeline is run on them. Running the step again keeps what it made before;
`force` makes their files again. An asset a Convert step made isn't converted again, so a pipeline can't feed itself,
and deleting it deletes its file.

## Workflows and the canvas

A pipeline's steps make derived files (a transcript or a page's text, shots, text on screen, faces); workflows turn them into metadata. A
workflow is a versioned graph drawn on a canvas (Pipelines → Workflows): it starts from the Asset node, and each
node passes what it makes along its connections.

- **Entities:** Extract entities (rules) runs the extractor analyze uses (the rules, or spaCy), plus your terms (`Name|TYPE`) and
  regular expressions (`TYPE: pattern`); Extract entities (LLM) asks the model for structured entities and keeps,
  corrects or adds to any passed in; Merge joins lists; Entity filter keeps the entities you want (only some types,
  without others, without names on a stop list, names at least so long or matching a pattern, or said at least so
  many times; anything in the list that isn't an entity is left out); Save entities makes them the asset's entities
  (people's corrections kept) and redoes keywords and chapters.
- **AI and logic:** LLM prompt (a prompt template, with what came in as `{{ input }}`), Classify (which of your
  labels, the content types or the namespace's topics fits the asset, or what came in as text; it passes on `choice`,
  `confidence`, `sure` and `ranked`, so a Condition or Switch can branch on them; [classifying](#classifying)), Pick
  (a path like `action_items.0.text`), Condition (yes and no branches; one test, or a
  [match tree](automations.md#match-trees) of several) and Merge.
- **Web:** HTTP call sends GET, POST, PUT, PATCH or DELETE to a web address and passes on the answer (`status`, `ok`,
  `url`, `content_type`, `body`, and `json` when it is JSON, up to 200,000 characters), so a Condition can branch on
  `ok` and a Pick or template can read the JSON. The address, header values and body are templates (what came in is
  `{{ input }}`: `https://api.example.com/search?q={{ input | urlencode }}`), but the scheme, host and port are written
  out, so what flows through a workflow never decides where it goes. It goes out as the assistant's web tools do:
  through Lens's network guard, to public addresses on ports 80 and 443 only (or the private networks an admin allowed
  in Settings → Documents), every redirect checked again. Headers that carry credentials (Authorization, cookies, API
  keys and tokens) are sealed with the server's key when the workflow is saved, bound to that header and that host, and
  shown as `••••`; a redirect to another server doesn't get them. A 4xx or 5xx answer is passed on with `ok` false; an
  address that can't be reached fails the node. Each call is a `workflow.http` row in the [activity ledger](activity.md)
  (the host and method, never what was sent). A try on the canvas sends a GET but only says what it would send for the
  other methods. Asset and graph workflows have it; automations call web addresses with their own action, which asks
  first ([actions](automations.md#actions-and-intents)). Refine later: credentials passed in through a custom node's parameter
  aren't sealed, and a sealed header is bound to its host rather than to one workflow.
- **Keeping results:** Save output (`outputs.<name>`), Set field (a custom field, kept in the metadata history), Save
  entities, and a Classify node set to act on a sure answer (it sets the content type, suggests the topic or adds the
  tag; a try on the canvas only says what it would do).

A pipeline runs a workflow as a Workflow step, pinned to the workflow's published version when the run is queued
(`POST /api/v1/workflows/{id}/run` runs one on an asset). Pipelines can be drawn on the canvas too: a connection
means "runs after", and the graph is put in order (ties left to right) and kept as the version's steps, so runs and
Activity work as before. A pipeline saved as a list is drawn as a chain.

An automation's workflow (scope `event`) is drawn on the same canvas, from What happened (the run) to Action and
Decide nodes, with every action in the catalog listed in the palette and its settings drawn from the action's params
([On the canvas](automations.md#on-the-canvas)).

## Content types

Every resource is video, audio, image or text (transcripts, documents, web pages, emails and calendar events are
text), read from its file; a video file that hasn't been probed yet is told by its extension.
Under each base type is a vocabulary of content types (Pipelines → Content types): Lens starts with podcast, interview
and meeting (audio), screen-share tutorial and recorded meeting (video), photo and scanned page (image), chat export,
records, transcript, document, web page, email and calendar event (text), plus a general type for each base. Admins can rename and change
them, remove all but the general ones, and add their own. A default that's removed stays removed; defaults added in a
later release appear on upgrade.

A resource's content type is the one someone chose (when uploading or importing it, in its Details tab, or with
`PUT /api/v1/recordings/{id}/content-type`), else
the first of its base type whose rules all match (file extensions, a pattern in the file name or title, a length,
what Lens found the file holds: `forms: [chat]` or `[records]`), else the general one. One recognised by what the file holds comes
before one recognised by its name, so a Telegram result.json is a chat export, not a transcript. Patterns ignore case and see `_` as a space, so `\bcalls?\b` matches `team_call.mp3` but not
`recall.mp3`. The pipeline that runs is the one chosen for the run, else the namespace's override for the
content type, else the content type's pipeline, else the namespace default, else the standard pipeline. Content types
start without a pipeline, so nothing changes until someone sets one. Files found by a folder scan go through the same
choice as uploads. Choosing it at import (**What it is** beside each file, pasted text or web page in Import;
`content_type` on `POST /uploads`, `POST /import`, `POST /import/web` and `/web/links`; or telling the assistant what
attached files are) means the right pipeline runs the first time, not after a change and a rerun. Left as
**Recognised automatically**, it's recognised as above.

## Routines

A routine does things on a schedule, like a cron job (Routines, admins only). It runs its actions in order over its
namespaces, or all of them:

- **Sync** scans watched folders now (all of the routine's namespaces' folders, or the ones you pick), so new files
  come in and run their pipelines.
- **Pipeline** queues a pipeline for assets (one you pick, else the one each asset's content type gets, as
  above): new ones (since the routine last looked), unprocessed ones, or all of them.
- **Workflow** runs a workflow. One that runs on assets is queued on them as a workflow step, pinned to its
  published version; one that organises the graph runs over the namespaces there and then.

The schedule is a five-field cron expression (minute hour day-of-month month day-of-week, e.g. `0 3 * * *`) or
`@hourly`, `@daily`, `@weekly`, `@monthly`, in a time zone; without one a routine runs only when someone presses Run
now. Every 30 seconds, `lens worker` checks (and so does the API process when it runs background work, and `lens watch`); a routine is
never started twice at once, and one started in two processes runs once. Each run keeps what every action did and a
log. A run that fails, or leaves graph changes to review, can be sent to a chat room through
Matterbridge or to a webhook: pick `routine.failed` or `routine.proposed` for a namespace's target
([Notifications](notifications.md#events)).

The [Schedules](schedules.md) page (Automate › Schedules) lists every routine next to the automations' schedules and
the backup, to switch, re-time and run each from one place; the Routines page and New routine open from its top.

### Organising the graph

A workflow's scope is assets (above), the graph, or an event: an automation's workflow, run by its automation
([The event workflow scope](automations.md#the-event-workflow-scope)). Graph workflows have their own nodes:

- **Find what to tidy** (`candidates`) finds, by rules and without a model, what *Look for* says:
  - **Look-alikes** (merge, link): pairs of entities that may be one thing, by the same rules as the merge
    suggestions (same letters, acronym, spelling, sounds alike, one name inside the other), inside each namespace
    (merge) or across namespaces whose graph is shared (link). Pairs someone marked as different are skipped.
  - **Wrong types** (retype): entities whose type the naming rules read differently now, from the name itself (a
    title, an ending like *Labs* or *Inc*, a known place or organisation: 90% for a term, 80% for another type) or from
    most of the lines that say it, at least two (up to 90%). An entity is typed by its first asset; this catches a
    term that later lines show is a person. Types a person or an agent set, entities on a fixed list and hidden ones
    are left alone, and so are types the namespace doesn't keep.
  - **Noise** (hide): entities with no letters or one letter, made of filler words (*Okay so*; not acronyms like
    *US*), or a phrase rather than a name (95%; filler words are 70% when the entity has a type other than Term), and
    terms said only once (50%, below the default 70%). Entities someone showed again are left alone.
  - **Topics to group** (cluster): a namespace's topics that go under a broader topic: those whose labels end in
    another topic's label (*Machine learning* and *Deep learning* under *Learning*, 85%), and three or more with no
    broader topic that end in the same word, under a new topic for it (*Sales calls*, *Support calls* and *Team
    calls* under *Calls*, 70% for three, up to 85%). Topics have no types, so *Only these types* doesn't apply.
  Anything already proposed, dismissed or undone is skipped too, so a run never redoes what someone took back.
- **Ask the model** (`llm_judge`) shows the model each candidate with lines where the names appear and asks, a kind
  at a time: whether a pair is the same thing and which name to keep, what type a name is (from the namespace's
  types), whether it is noise, or whether a group's topics go under its broader topic (with a better label for a new
  one, and which topics to leave out); and how sure it is. Each gets a `verdict`, with `agree` true when the change
  should be made (and, for pairs, `same`).
- **Filter** keeps the candidates that pass a test, e.g. `verdict.agree` (or `verdict.same`) equals true.
- **Apply changes** merges, links, retypes, hides or groups the candidates whose confidence (the model's, else the rules') is
  at least *apply above* (at most *max apply* a run) and proposes the rest. Without *apply above* it only proposes. A
  candidate the model said no to is skipped.

A fresh archive has the workflow *Organise the entity graph* (both kinds of candidates, the model, then merges at 95%
or more, 25 a run) and the routine *Organise the graph every night* that runs it at 03:00 UTC, switched off. Turn it
on, or Run now with *propose only* first to see what it would do.

Every change is recorded (`GET /api/v1/graph-changes`), and shows in Routines › Proposed changes: editors of the
namespaces involved accept or dismiss proposals (dismissing a pair says the two are different; dismissing a type or a
hide or a group leaves things as they are; neither is suggested again), choose another type when accepting a retype
(`to`) or another label for a group's new broader topic (`label`), and undo applied changes; an admin can undo everything a run applied at once (`POST /api/v1/routine-runs/{id}/undo`).
Merges are undone exactly as from an entity's page; a type or a hide someone has changed since stays as they left it.
Undoing a group takes its topics out from under the broader topic, and deletes that topic when the group made it and
nothing uses it since (a recording, a narrower or related topic).
Agents do the same through the API, and `undo_graph_change` over MCP. To tidy types, noise and topics, add a workflow
that looks for them (no built-in workflow does, so nothing changes until you add one).

### Publishing from the graph

Graph workflows also publish, so a routine fills a [feed](feeds.md) or makes a [podcast episode](podcasts.md) on a
schedule. Their nodes:

- **Select assets** passes on the resources in the routine's namespaces (or the *namespaces* named), newest first,
  *limit* at most (50, up to 500): those that came in *since* the routine's last run (the default; on its first run,
  the past week), or in the past *day*, *week* or *month*, or *all* of them; matching *q* (words in the title),
  *topics*, *entities* and *status*. Podcast episodes are left out unless *include episodes* is on, so an episode about
  the week's resources isn't a source for the next one.
- **Select activities** passes on the [timeline](timeline.md)'s activities in those namespaces, of the *kinds* named
  (with their descendants), *since* the same choices, matching *q* and a *where* match tree, newest first, *limit* at
  most (50, up to 200). Only what everyone who reads the namespace sees, never someone's own.
- **Publish to feed** adds what comes in to the *feed*: resources and episodes, activities, or text (a template
  node's output becomes a text item, with the node's *title*). What's in the feed already isn't added again, so a
  routine that runs again adds only what's new; what the feed's readers couldn't read is refused and said in the run's
  log. The feed's namespace is one the routine covers.
- **Podcast episode** makes an episode about the resources that come in (*max sources*, 10, of those with text; none
  under *min sources*), with the *title*, *prompt*, *length* and *style* of [Podcasts](podcasts.md), placed in the
  *namespace* named, else the *feed*'s, else where an admin's episode goes; a source its readers couldn't read is left
  out. With a *feed*, the episode is added to it and listed once it's made.

A run's result counts what was `published` and the `episodes` made, and its log says what each node did. On the
canvas the nodes are under Data and Keep in a graph workflow's palette, the feed is picked from a list, and the canvas
says what to fix before Publish (a feed to choose, a limit over the most). Try on the canvas runs a graph workflow over
the namespaces you pick and says what each node would do, without publishing. So
Select assets → Podcast episode (feed *Weekly*, length 10) on a routine that runs every Monday makes an episode about
the week's new resources and puts it in your podcast app; Select activities (kinds `asset.added`) → Publish to feed
puts the timeline in your feed reader.
