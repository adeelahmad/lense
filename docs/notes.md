# Notes

Every resource, entity and topic in Lens has a page of its own, and next to them sit free notes that people and the
assistant write. The assistant is the main writer and organiser; people can do everything it can.

Status: **built**: pages (free notes in a tree, a page per asset, entity, topic, collection or person), @ and #
links with backlinks (# links topics), the tree in the left navigation, the page view with the BlockSuite editor, the
model keeping titles and summaries up to date, assistant tools to find, read, write and update notes, and MCP tools to
find, read and write them ([MCP](mcp.md)), page history with restore, suggested links to what a note names, the
project or area a note fits under, images and attachments kept where Settings → Storage says, and every entity's
own page at a wiki address, `/wiki/<namespace>/<Name>` ([Entity pages](#entity-pages)).
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
and PDFs show in place; anything a browser could run (HTML, SVG) only downloads. Deleting the page deletes its files.
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

## History

Before each change to a page's title, summary or text, Lens keeps what the page was as a version (`note_version`, up to
100 per page). A person's edits in one sitting (ten minutes) make one version; a change by the assistant, the model
refining it, or someone else always starts a new one. **History** on a page lists them, newest first, with who changed
it; pick one to read it, and editors can restore it. Restoring keeps the page as it was too, so it can be undone.
`GET /api/v1/notes/{id}/history`, `GET .../history/{version}` and `POST .../history/{version}/restore`.

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
