# The graph as files

Lens shows your archive as an ordinary tree of folders and files: namespaces, collections, topics and entities are
folders, and every asset (a recording, document or image) is a few files in each folder it belongs to. Agents and
tools that already understand files can read Lens without learning its API.

| Where | Status |
| --- | --- |
| The API (`GET /api/v1/tree`, `GET /api/v1/tree/file`) and the MCP tools `list_files` and `read_file` | built |
| An S3 server (read-only, off until turned on) | in progress |
| SMB and NFS shares | in progress |

Every way in serves the same tree, built by `app/domain/filetree.py`.

## Layout

```text
<namespace>/
    namespace.json                              the namespace: its collections, topic and entity counts
    Collections/<collection>/<…>/<asset files>   every asset in its collection's folder
    Topics/<topic>/<asset files>                 the assets about a topic (accepted, not suggestions)
    Entities/<type>/<name>/<asset files>         the assets that mention an entity (hidden entities, dates and
                                                 numbers left out)
```

An asset's files are named after it, `<title> [<id>]`, so names are unique and stable:

| File | What it holds |
| --- | --- |
| `<title> [<id>].<ext>` | its own file as it was imported: audio, video, PDF, Word file, image… (`.original.txt` for an imported transcript) |
| `<title> [<id>].json` | its metadata: Dublin Core terms (`identifier`, `title`, `type`, `format`, `created`, `date`, `subject` for its topics), its collection, its entities, and the description saved on it |
| `<title> [<id>].txt` | its text: the transcript (`Speaker: …` lines) or what was read off its pages |
| `<title> [<id>].pdf` | the PDF made of a document that isn't one (a Word file, an email…) |

The same asset shows in its collection's folder, in each topic's folder and in each entity's folder. Files stored
encrypted ([Encryption](encryption.md)) are read decrypted, and a locked vault's files can't be read until one of its
owners unlocks it. Files kept on a storage connection are read from there.

Names are made safe for every file system: `/ \ : * ? " < > |` and control characters become `_`, names are cut to
120 characters, and two names in a folder that differ only in case get the second one's id added (`Talks [12]`).

## Who sees what

Exactly what the web app shows them ([Access](access.md)): an admin sees every namespace; others see the namespaces
they have a role in, and in a namespace where they only have roles on some collections, those collections, and only
the topics and entities of the assets in them. Anything else answers "not found". The tree only reads.

## The API

`GET /api/v1/tree?path=pods/Topics/Gene therapy` lists a folder: `{path, entries: [{name, folder, modified, size,
content_type}]}`. An empty path lists your namespaces. `GET /api/v1/tree/file?path=…` reads a file, with byte ranges
(`Range: bytes=0-1023`) and an `ETag`. Asking for a file as a folder (or the other way round) answers 400.

The MCP server has `list_files` and `read_file` ([MCP server](mcp.md)): `read_file` returns the text of `.txt` and
`.json` files, a page at a time, and the download link of everything else.

## Refine later

- Show supplementary files (captions, translations, attachments) beside each asset.
- Cache an asset's text and metadata so large folders list faster.
- `Speakers/<name>/` folders, and a folder of notes.
