# Notes

Every resource, entity and topic in Lens has a page of its own, and next to them sit free notes that people and the
assistant write. The assistant is the main writer and organiser; people can do everything it can.

Status: **built**: pages (free notes in a tree, a page per recording, entity, collection or speaker), @ and # links
with backlinks, the tree in the left navigation, the page view with the BlockSuite editor, and the model keeping
titles and summaries up to date. **Planned**: assistant and MCP tools, a routine that files notes (PARA and the
ontology), attachments on encrypted object storage, pages in the graph, # links to SKOS topics.

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
draft with its name and the pages that already link to it. A page follows its recording to another namespace. When a
thing is deleted, or an entity is merged into another, its page stays as a free note at the top of the tree, so
nothing written on it is lost.

## Links

The body links with mention tokens, which the editor and the assistant both write:

```text
@[Weekly call](recording:12)   @[Ada Lovelace](entity:5)   @[Plans](page:3)   #[Capsids](entity:9)
```

`@` links resources, people and other pages; `#` links topics (entities of type Topic, until topics become a SKOS
vocabulary of their own; see [the graph](graph.md)). `GET /api/v1/notes/targets?ns=&sign=&q=` offers what to link.
Links are kept as rows (`note_link`), so every page lists its backlinks; links to things that are gone or that the
reader can't see show without a name.

## Titles and summaries

With **Settings → Assistant → Keep note titles and summaries up to date** (`ai.refine_notes`, on by default), a note
whose title or text changed is sent to the model once nobody has touched it for two minutes. The model rewrites the
one-line summary to fit the whole note, and the title only when it is empty, "Untitled" or no longer fits. A note being
typed is never sent on each keystroke, empty notes are never sent, and nothing is sent without a model configured. The
API and `lens worker` processes run this pass with the routines. Model calls are measured by telemetry; their costs go
to the activity ledger once it exists.

## Access

Pages belong to a namespace: anyone with a role there reads them, editors write, move and delete them. The pages of
recordings also need access to the recording.

## Editor

The page view uses AFFiNE's BlockSuite editor, whose documents are Yjs CRDTs, the format OctoBase stores and syncs.
The editor saves its document state in `doc` next to the Markdown; a change to the Markdown alone (the assistant's)
drops `doc`, so the editor rebuilds it from the Markdown. OctoBase is AGPL-3.0, so it would run as a separate service
for live co-editing rather than inside Lens.

## Refine later

Pages for people who see only some collections of a namespace; page history and undo; attachments; live co-editing
through OctoBase; pages and their links as nodes and relationships in the graph.
