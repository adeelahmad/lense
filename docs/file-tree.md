# The graph as files

Lens shows its graph as an ordinary tree of folders and files: graph nodes (collections, topics, entities) are
folders, nested along the graph's links, and every asset (a recording, document or image) is a few files inside the
folders it belongs to, its metadata in a sidecar beside it. Agents and tools that already understand files can read Lens without learning its API.

| Where | Status |
| --- | --- |
| The API (`GET /api/v1/tree`, `GET /api/v1/tree/file`) and the MCP tools `list_files` and `read_file` | built |
| S3 and WebDAV servers (read-only, off until turned on) | in progress |
| SMB and NFS shares | in progress |

Every way in serves the same tree, built by `app/domain/filetree.py`.

## Layout

```text
<namespace>/
    Collections/<collection>/<collection inside it>/…   every asset, in its collection's folder
    Topics/<topic>/<narrower topic>/…                   the assets about each topic (accepted ones)
    Entities/<type>/<name>/                             the assets that mention each entity
```

A folder holds the folders under it and its assets' files side by side. The same asset shows in every folder it
belongs to, and a topic with several broader topics shows under each of them.

An asset's files are named after it, `<title> [<id>]`, so names are unique and stable:

| File | What it holds |
| --- | --- |
| `<title> [<id>].<ext>` | its own file as it was imported: audio, video, PDF, Word file, image… (`.original.txt` for an imported transcript) |
| `<title> [<id>].json` | its metadata: Dublin Core terms (`identifier`, `title`, `type`, `format`, `created`, `date`, `subject` for its topics), its collection, its entities, and the description saved on it |
| `<title> [<id>].txt` | its text: the transcript (`Speaker: …` lines) or what was read off its pages |
| `<title> [<id>].pdf` | the PDF made of a document that isn't one (a Word file, an email…) |

Files stored encrypted ([Encryption](encryption.md)) are read decrypted, and a locked vault's files can't be read until
one of its owners unlocks it. Files kept on a storage connection are read from there.

Names are made safe for every file system: `/ \ : * ? " < > |` and control characters become `_`, names are cut to
120 characters, and two names in a folder that differ only in case get the second one's id added (`Talks [12]`).

## Who sees what

Exactly what the web app shows them ([Access](access.md)): an admin sees every namespace; others see the namespaces
they have a role in, and in a namespace where they only have roles on some collections, those collections and the
topics and entities of the assets in them (with the topics broader than those). Anything else answers "not found". The tree only reads.

## The API

`GET /api/v1/tree?path=pods/Topics/Gene therapy` lists a folder: `{path, entries: [{name, folder, modified, size,
content_type}]}`. An empty path lists your namespaces. `GET /api/v1/tree/file?path=…` reads a file, with byte ranges
(`Range: bytes=0-1023`) and an `ETag`. Asking for a file as a folder (or the other way round) answers 400.

The MCP server has `list_files` and `read_file` ([MCP server](mcp.md)): `read_file` returns the text of `.txt` and
`.json` files, a page at a time, and the download link of everything else.

## Refine later

- Show supplementary files (captions, translations, attachments) beside each asset.
- Cache an asset's text and metadata so large folders list faster.
- A sidecar for each collection, topic and entity, and a folder of notes.
- Nest entities by how they relate (people under organisations) once the graph records relations between them.
