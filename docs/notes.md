# Notes

Every resource, entity and topic in Lens has a page of its own, and next to them sit free notes that people and the
assistant write. The assistant is the main writer and organiser; people can do everything it can.

Status: **built**: pages (free notes in a tree, a page per asset, entity, topic, collection or person), @ and #
links with backlinks (# links topics), the tree in the left navigation, the page view with the BlockSuite editor, the
model keeping titles and summaries up to date, assistant tools to find, read, write and update notes, and MCP tools to
find, read and write them ([MCP](mcp.md)), page history with restore, suggested links to what a note names, the
project or area a note fits under, images and attachments kept where Settings → Storage says, every entity's
own page at a wiki address, `/wiki/<namespace>/<Name>` ([Entity pages](#entity-pages)), and technical specs written
from transcripts by the API, MCP, the assistant and pipelines ([Specs from transcripts](#specs-from-transcripts)).
**Planned**: nesting notes under their project or area without a click, pages in the graph, wiki addresses for topics,
recordings and the other things that have pages.

## A page

| Field | What it is |
| --- | --- |
| title | One line, up to 200 characters |
| summary | One line on what the page holds: the context the assistant reads first. `summary_by` says who wrote it |
| date | The day it's about (default: the day it was written) |
| place | Where it's filed, in PARA: `project`, `area`, `resource` or `archive` |
| parent, position | Its place in the tree (free notes only) |
| about | What it's the page of, like `recording:12` or `entity:5`; empty for a free note |
| body | Markdown with mentions (below) |
| doc | The editor's own document state, if it keeps one (opaque to the server) |
| author | `person` or `assistant` |

A thing's page is made the first time someone writes on it; until then `GET /api/v1/notes/about/{kind}/{id}` returns a
draft with its name and the pages that already link to it. A page follows its asset to another namespace. When a
thing is deleted, or an entity is merged into another, its page stays as a free note at the top of the tree, so
nothing written on it is lost.

## On a note

Above the title, a note says where it is: **Notes › namespace ›** each note it's inside, every one a link. Next to that
are how many words it has, whether it's saved, and its **⋯** menu: **Add a note inside**, **Pin**, **History**,
**Download as Markdown** and **Delete** (with Undo). The text starts in line with the title. Under the text, **Inside
this note** lists the notes inside it in the tree's order, with **Add a note inside**, then the notes that link to it;
**Costs and activity** is folded at the bottom and loads when opened.

`GET /api/v1/notes/{pid}` gives `path` (the notes it's inside, from the top) and `children` (the notes inside it); the
assistant's and MCP's `read_note` give them as `path` and `notes_inside`.

## Entity pages

Every entity (a person, organisation, product, place, event, work or term) has a page of its own, named by its name
the way a wiki names pages: spaces become underscores and the rest stays as written, so Ada Lovelace in namespace
`work` is at `/wiki/work/Ada_Lovelace`. Nothing is stored for the address: it is the name. The page shows what the
entity is (its type, the other ways it's said and its description), the notes people and the assistant keep on it in
the BlockSuite editor below, every line that says it with a link to that moment, the assets and the people it comes
up with most, the entities it's mentioned together with (each a link to its own page), the same entity in other
namespaces, and similar content. **Edit** opens the panel that changes it, as on the Entities page; renaming it moves
the page to the new name.

The address is read the way analysis matches names: in any case and spacing (`/wiki/work/ada_lovelace`), and by a name
the entity had before a rename or the name of one merged into it. Those open the page and switch the address to its
own. A name with `/` or `?` in it is escaped in the address (`AC/DC` is `/wiki/work/AC%2FDC`). The entity's name on the
Entities page, in a note's `@` link, in the graph and in the RDF URI `/id/entity/<id>` all lead to its page, and
`/entities/<id>` (and an entity's page's old address, `/notes/about/entity/<id>`) does too.

`GET /api/v1/entities/{eid}` and the MCP tool `get_entity` give the page's address (`page`, `url`); the assistant's
`find_entities` gives it too. `GET /api/v1/wiki/{ns}/{address}` says which entity an address names (`id`, `path`, and
`canonical: false` when the address is another way of writing it). An address only answers for the namespace's members;
to anyone else it is not there.

## Links

The body links with mention tokens, which the editor and the assistant both write:

```text
@[Weekly call](recording:12)   @[Ada Lovelace](entity:5)   @[Plans](page:3)   #[Capsid design](topic:9)
```

`@` links resources, people and other pages; `#` links topics, the namespace's vocabulary ([Topics](topics.md)),
found by any of their labels. Links written to terms (`#[Capsids](entity:9)`) before topics existed still work.
`GET /api/v1/notes/targets?ns=&sign=&q=` offers what to link.
Links are kept as rows (`note_link`), so every page lists its backlinks; links to things that are gone or that the
reader can't see show without a name.

A note also suggests links to what its text names but doesn't link yet: the namespace's topics (by any of their
labels) and its people, organisations, places and other named things (by name or alias; a single word only when it is
written with a capital). One click adds the link on a line of its own. The assistant sees the same list as
`could_link` when it reads a note. No model is asked: it is the namespace's own vocabulary matched against the words.
`GET /api/v1/notes/{id}/suggestions`.

## Titles and summaries

With **Settings → AI assistant → Keep note titles and summaries up to date** (`ai.refine_notes`, on by default), a
note whose title or text changed is sent to the model once nobody has touched it for two minutes. The model rewrites the
one-line summary to fit the whole note, and the title only when it is empty, "Untitled" or no longer fits. A note being
typed is never sent on each keystroke, empty notes are never sent, and nothing is sent without a model configured. The
API and `lens worker` processes run this pass with the routines. Each model call, and each filing decision, counts in
the [activity ledger](activity.md) for the note (`note_page:5`) and its namespace; a note's page shows its costs and
activity at the bottom.

## Files and images

Images, attachments and other files dropped or pasted into a page are kept where **Settings → Storage** says
([Storage](storage.md)): on this machine, or on a storage connection (S3, Google Drive, Dropbox, OneDrive, SFTP, SMB,
WebDAV), encrypted with the namespace's key before they leave the machine. The editor names each by the SHA-256 of its
bytes, so the same file is kept once per page, and the server checks the name matches the bytes. Anyone who reads the
page reads its files; editors add them (`PUT /api/v1/notes/{id}/blobs?key=…`, up to `server.max_upload_mb`). Images
and PDFs show in place; anything a browser could run (HTML, SVG) only downloads. A deleted page keeps its files while it
can be put back; deleting it for good deletes them.
The assistant sees a page's files (names, types, sizes) when it reads it.

## Filing (PARA)

With **Settings → AI assistant → File notes in projects, areas, resources and archives** (`ai.organise_notes`, on by
default), free notes nobody filed are filed once their summary is written. It's a routine decision, so a decision model
takes it when one is set up (Settings → AI assistant), else the language model. When it's sure
(`decisions.act_above`) the note is filed (`place_by: assistant`); otherwise its best guess waits on the note as a
suggestion to accept with one click. What a person files is never refiled.

A note at the top of the tree that isn't a project, an area or archived also shows the project or area page it fits
under, with one click to put it inside: the one it links to, that links to it, that links the same topics and things,
or whose title words it uses (`GET /api/v1/notes/{id}/homes`, best first, with why). No model is asked and nothing
moves on its own; the assistant sees the same as `could_go_in` when it reads the note, and moves it with `update_note`.

## The assistant

The chat assistant keeps notes as its notebook: `find_notes`, `read_note`, `write_note` and `update_note` (Settings → AI
assistant lists them). It looks there first, and writes or updates a note when it learns something worth keeping, filed
in PARA and linked with @ and #. Writing needs editor access to the namespace and takes effect at once, with no
approval: notes it writes say so. It never deletes a note.

## Specs from transcripts

A voice session, a meeting or a memo becomes a technical spec, in a note linked to it. The model reads the transcript
with the **Spec from transcript** prompt template, which opens with the instruction it's built around:

> You are writing a technical spec from a voice-session transcript. Preserve every named decision, every 'no' and
> every 'not that — this', and every constraint the speaker stated. Do not paraphrase their reasoning. Flag any gap
> where the speaker said they would decide later.

The note starts with where it came from (an @ link to the asset, so the asset's page lists it) and flags what needs a
look: what the speaker left to decide later, quotes the transcript doesn't hold word for word, and, when the transcript
is longer than the model is given (`llm.max_chars`), the moment after which nothing was read. Then come the spec, in
the sections the model gives it, and four lists: **Decisions**, **No, and not that — this**, **Constraints** and
**Decide later** (as checkboxes). Each item carries the speaker's own words, linked to when they said them. Lens looks
for every quote in the transcript (case, punctuation and line breaks aside; an ellipsis may skip words): one it doesn't
find is marked *not found word for word in the transcript* and gets no link, so the model's wording is never passed
off as the speaker's. A list with nothing in it says so.

The assistant is the note's writer, in the asset's namespace, dated the day the asset was recorded. Writing the spec
again updates the same note, and its [history](#history) keeps what it said before; once that note is deleted, the
next spec is a new one.

- **API:** `POST /api/v1/recordings/{id}/spec` (with the right to process the asset) queues it as a job step, after
  what the asset's job still has to do; `{template, version}` writes it with another prompt template, such as an edited
  copy. `GET /api/v1/recordings/{id}/spec` says how it's going: `writing`, `ready` with its note and how many
  decisions, things turned down, constraints and gaps it holds, or `failed` with why. It's the asset's `spec` output
  too.
- **MCP:** `write_spec`, then `get_spec` and `read_note` ([MCP](mcp.md)).
- **The assistant:** `write_spec`, one of the Notes tools. As a model call, it asks for your approval first, with an
  estimate.
- **Pipelines:** a `spec` step writes one for each asset a pipeline runs on, such as a namespace of voice memos
  ([Pipelines and templates](processing.md#pipelines-and-templates)), and a batch run of the template writes them for
  a collection.

The template is made when Lens starts, on new installs and existing ones alike, and admins edit it on the Templates
page like any other; a new version is used from the next spec on. Its answer is JSON with `title`, `summary`, `spec`
and the lists `decisions`, `rejected`, `constraints` and `gaps`, each item with its `quote` and `time`. The transcript
reaches the model marked as data ([prompt injection](assistant.md#third-party-content-and-prompt-injection)), and web
addresses in what the model writes that the transcript doesn't hold are broken up. It's one model call, logged in the
activity ledger and held to budgets.

## History

Before each change to a page's title, summary or text, Lens keeps what the page was as a version (`note_version`, up to
100 per page). A person's edits in one sitting (ten minutes) make one version; a change by the assistant, the model
refining it, or someone else always starts a new one. **History** on a page lists them, newest first, with who changed
it; pick one to read it, and editors can restore it. Restoring keeps the page as it was too, so it can be undone.
`GET /api/v1/notes/{id}/history`, `GET .../history/{version}` and `POST .../history/{version}/restore`.

## The tree

Free notes sit in a tree in the left navigation, under the main links. The links and the tree scroll together, so a
long tree has the whole height. A row opens its note, its arrow shows the notes inside it (the tree remembers which are
open) and **+** adds a note inside. Each row's **⋯** menu, which always shows on a touch screen, adds a note inside,
renames the note in place (Enter saves, Escape leaves it as it was), moves it (**Move to…** finds the note to put it in
by name, Enter picks the first; **Move to the top**), makes a copy beside it ("Copy of …", with its text, drawings
and files), pins it ([below](#pinned-notes)), downloads it as Markdown and
deletes it with Undo. Dragging a note onto another puts it inside, and onto the Notes heading moves it to the top. The
pages of recordings and entities aren't in the tree: they open from those things.

`POST /api/v1/notes/{pid}/move` with `{parent}` moves a note (`null` for the top); `PATCH /api/v1/notes/{pid}` with
`{title}` renames it.

## Today's note

**Today** on the Notes page (or **Today's note** in Jump to, or the address `/notes/today`) opens the note for the
day in the namespace in view, made the first time it's asked for: titled with the date (like 2026-10-15) and inside a
**Daily notes** note, made too when there's none. It's the day where you are, and one note a day per namespace that
everyone there shares; rename or move it like any note. `POST /api/v1/notes/today` with `{ns, date}` (editors) gives
the same note to agents.

## Templates

Each note inside a namespace's **Templates** note is a template. **New note from a template…** in the Notes page's
**⋯** menu, or the same action in Jump to (⌘K), lists them: type to find one, Enter takes the first. The new note has the
template's title (selected, to type over), text, drawings, files, filing and view, and goes at the top of the tree.
With no templates yet, the same place makes the Templates note.

`GET /api/v1/notes/templates?ns=` lists a namespace's templates; `POST /api/v1/notes/templates` with `{ns}` gives its
Templates note, made the first time; `POST /api/v1/notes/{pid}/copy` with optional `{title, parent}` makes a new note
from any page (editors).

## Pinned notes

**Pin** in a note's ⋯ menu in the tree pins it for you: it shows at the top of the tree, under **Pinned**, and first
on the Notes page with a pin. Pins are each person's own, so pinning changes nothing for anyone else and needs only
read access; up to 50 per namespace. **Unpin** (in the menu, or the button on its row under Pinned) takes it off.
`PUT /api/v1/notes/{pid}/pin` pins and `DELETE` unpins; each page in `GET /api/v1/notes` says whether you pinned it
(`pinned`).

## Downloading as Markdown

**Download as Markdown** in a note's ⋯ menu in the tree saves it as a `.md` file, and **Download all as Markdown**
in the Notes page's ⋯ menu saves every page of the namespace as a zip of them, for Obsidian and other notes apps. Each
file starts with front matter (its title, date, where it's filed, its summary, what it's the page of), then its text:
links to other notes become `[[Title]]` wiki links and the rest the names they link to. In the zip, the notes inside a
note sit in a folder named like it, and the pages of recordings, entities and topics under `Pages of things`.
`GET /api/v1/notes/{pid}/markdown` and `GET /api/v1/notes/export?ns=` give the same files to agents.

## Deleting and putting back

**Delete** on a page deletes it at once, with no question first: the notice has **Undo**, which puts it back and opens
it. The notes inside it move up a level, and links to it show without a name while it's gone. A deleted page waits in
**Recently deleted**, at the bottom of the Notes page, for 30 days with its files and history: **Put back** returns it
where it was, with the notes that were inside it (those nobody moved since), and **Delete now** (clicked twice) deletes
it for good. After 30 days it goes for good on its own, with its files and history. A thing's page comes back as its
page, or as a free note at the top when the thing is gone or has a new page by now.

`DELETE /api/v1/notes/{pid}` deletes; `GET /api/v1/notes/deleted?ns=` lists recently deleted pages,
`POST /api/v1/notes/deleted/{pid}/restore` puts one back and `DELETE /api/v1/notes/deleted/{pid}` deletes one for good
(editors). The assistant never deletes notes.

## Finding notes

The **Notes** page lists the free notes in view, the latest changed first, each with the notes it sits inside
("In Home lab › Parts"), where it's filed and when it changed. The box at the top finds notes by the words in their
title, one-line summary or text, the pages of recordings, entities and topics too: those with the words in the title
come first, each with the words around the match, and Enter opens the first. **Projects**, **Areas**, **Resources** and
**Archive** show only the notes filed there, with how many each has. **Jump to** (⌘K or Ctrl+K) finds notes the same
way, beside recordings, pages and settings.

`GET /api/v1/notes/search?q=&ns=&place=&free=` is the same search, in every namespace you can read unless `ns` names one;
`free=true` leaves out the pages of things. The assistant's and MCP's `find_notes` give the same words around the match
(`snippet`) and the note each is inside (`inside`).

## Access

Pages belong to a namespace: anyone with a role there reads them, editors write, move and delete them. The pages of
assets also need access to the asset.

## Editor

The page view uses AFFiNE's BlockSuite editor, whose documents are Yjs CRDTs, the format OctoBase stores and syncs. A
page can be shown as a document or on the edgeless canvas (`view`), with BlockSuite's shapes, connectors, mind maps,
frames and free drawing. The editor saves its document state in `doc` next to the Markdown; the drawings live only
there. A change to the Markdown alone (the assistant's) keeps `doc` and sets `doc_stale`: the editor then replaces its
text with the Markdown and keeps the drawings. OctoBase is AGPL-3.0, so it would run as a separate service for live
co-editing rather than inside Lens.

## Refine later

Wiki addresses for topics, assets, collections and speakers, and a namespace's wiki index page; two entities whose
names differ only by `_` and a space share an address (the one with spaces wins).

Pages for people who see only some collections of a namespace; differences between versions; live co-editing through
OctoBase; pages and their links as nodes and relationships in the graph.

Specs: transcripts longer than `llm.max_chars` read in parts rather than cut; specs from a chat conversation; one spec
from several assets.
