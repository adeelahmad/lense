# The graph as files

Lens shows its graph as an ordinary tree of folders and files: graph nodes (collections, topics, entities) are
folders, nested along the graph's links, and every asset (a recording, document or image) is a few files inside the
folders it belongs to, its metadata in a sidecar beside it. Agents and tools that already understand files can read
Lens without learning its API, and add to it the same way: a file copied into a collection's folder over S3 or WebDAV
is imported there ([Adding files](#adding-files)).

| Where | Status |
| --- | --- |
| The API (`GET /api/v1/tree`, `GET /api/v1/tree/file`) and the MCP tools `list_files` and `read_file` | built |
| An [S3 server](#s3) and a [WebDAV server](#webdav) (off until turned on); files added through them are imported | built |
| An [NFS share](file-shares.md) (read-only, off until turned on, allowed networks only) | built |
| An [SMB share](smb.md) (read-only, off until turned on) | built |

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
topics and entities of the assets in them (with the topics broader than those). Anything else answers "not found". The API only
reads; files are added through the [S3](#s3) and [WebDAV](#webdav) servers.

## The API

`GET /api/v1/tree?path=pods/Topics/Gene therapy` lists a folder: `{path, entries: [{name, folder, modified, size,
content_type}]}`. An empty path lists your namespaces. `GET /api/v1/tree/file?path=…` reads a file, with byte ranges
(`Range: bytes=0-1023`) and an `ETag`. Asking for a file as a folder (or the other way round) answers 400.

The MCP server has `list_files` and `read_file` ([MCP server](mcp.md)): `read_file` returns the text of `.txt` and
`.json` files, a page at a time, and the download link of everything else.

## S3

Off until an admin turns it on, in Settings, File shares (`shares.enabled`). Then Lens's worker serves the tree
over S3 on port 9555 (`shares.s3_port`; with Docker, `LENS_S3_PORT` publishes it on another port of the
host), where the web app is: this machine only unless Lens was opened to the network (`LENS_BIND`;
`LENS_SHARE_BIND` sets it apart). Each namespace you can read is a bucket; keys are the paths above.

Each person makes their own access keys on their API tokens page (File share access keys), while signed in to the web app;
API tokens can't make them. A key reads as its owner and sees exactly what they see. Its secret is shown once, kept
sealed with the server's key (S3's signatures are made with the secret itself, so it can't be hashed), expires like
an API key (Settings, API keys), stops working when its owner is disabled, and can be revoked at once. Making and
revoking keys is audited. `GET /api/v1/shares/s3` says whether the S3 and WebDAV servers are on and lists your keys;
`POST /api/v1/shares/s3/keys` and `DELETE /api/v1/shares/s3/keys/{id}` make and revoke them (admins list everyone's
with `GET /api/v1/shares/s3/keys`).

Any S3 client works with path-style addressing and any region. With rclone, the remote the web app shows when you
make a key:

```ini
[lens]
type = s3
provider = Other
access_key_id = LK…
secret_access_key = …
endpoint = http://lens.local:9555
force_path_style = true
```

then `rclone lsd lens:`, `rclone copy "lens:pods/Topics/Gene therapy" ./gene-therapy`, `rclone copy ./talks
"lens:pods/Collections/Talks"` ([Adding files](#adding-files)), or `rclone mount lens: ~/lens`.
Supported: listing buckets, listing objects (v1 and v2, by folder or recursively, a page at a time), reading objects whole or
in ranges, presigned links, and adding files: PutObject (whole, streamed with `aws-chunked` and signed chunks, or with
a checksum), multipart uploads, a key ending in `/` for a folder, a copy of a file just added onto itself (to set its
time, as rclone does), and DeleteObject(s) for files kept aside. A file's `x-amz-meta-mtime` (rclone's) dates it.
Making or deleting buckets and deleting assets answer AccessDenied; copying other objects, NotImplemented. Requests must be signed with
Signature Version 4.

The server speaks plain HTTP. On a network you don't trust, keep it on this machine or behind TLS (a reverse proxy),
since files travel in clear.

## WebDAV

Off until an admin turns it on, with S3 (Settings, File shares; `shares.webdav`, on by default once shares are).
Lens's worker serves the same tree over WebDAV on port 9556 (`shares.webdav_port`; with Docker,
`LENS_WEBDAV_PORT`), so it mounts as a drive: in Finder, Go, Connect to Server; in Windows Explorer, Map network
drive; in GNOME Files or Dolphin, `dav://lens.local:9556/`; or `rclone` with `type = webdav`.

Sign in with an access key: its id as the user name and its secret as the password. The folders at the top are your
namespaces. Listing (PROPFIND, one level at a time) and reading (GET, with ranges) work, and so does adding files
([Adding files](#adding-files)): PUT (with `Expect: 100-continue`, refused before the file is sent when it can't go
there), MKCOL for a folder, DELETE and MOVE for files kept aside, and PROPPATCH (answered, not kept), so dragging
files and folders in from Finder or Windows Explorer works. LOCK and UNLOCK answer as a class 2 server so Finder mounts
it writable, but they lock nothing. Deleting or moving assets answers 403. Eight failed sign-ins from one address within 15
minutes are refused for a while.

Like S3, it speaks plain HTTP, and Basic sign-in sends the secret with every request: keep it on this machine or
behind TLS on a network you don't trust.

## Adding files

A file copied into the tree over S3 or WebDAV is imported, as an upload in the web app is: the same checks of its type
and size (Settings, Uploads), the namespace's pipeline, and the same file twice is added once. Where it's put decides
where it goes:

| Put it in | It goes to |
| --- | --- |
| `<namespace>/Collections/<collection>/…` | that collection |
| `<namespace>/Collections/<new folder>/…` | a new collection, made with any missing ones above it (up to 8 deep), as **New collection** makes one |
| `<namespace>/`, `<namespace>/Collections/`, or a folder under `Topics/` or `Entities/` | the namespace's default collection; topics and entities come from the analysis |

Folder names match collections whatever their case. It must be yours to add to: the import permission in the
namespace, or an editor's or admin's role on the collection (making a collection takes what making one in the web app
takes). Otherwise the file is refused before it's sent where the client allows (S3's and WebDAV's `Expect:
100-continue`). Files Lens made (an asset's own file and its `.json`, `.txt` and `.pdf`) can't be written over. Each
file added is audited (`share.import`, with the protocol and path), and each collection made too.

After a file is imported, the asset shows as `<title> [<id>]` like every other; the name you gave it stays in its folder,
for you, for 10 minutes, so the copying app finds the file it just wrote and a re-sync sees it's there (with its own
time and MD5, so `rclone copy` and `rclone check` agree). Files an operating system or an app writes for itself
(`.DS_Store`, `._` files, `Thumbs.db`, `desktop.ini`, Office's lock files, `.part`, `.tmp` and other hidden or
half-written files) and empty files (Finder and Explorer make one before they write it) are kept aside for 10 minutes
after they last change, seen only by you, and never imported; renaming one to an ordinary name imports it then. They're
kept on the uploads disk, in memory per worker, so a restart forgets them.

Adding files is on whenever the shares are; turn it off in Settings, File shares (**Import files people add**,
`shares.add_files`) and they're read-only again. `GET /api/v1/shares/s3` says whether it's on (`add_files`). The
[NFS](file-shares.md) and [SMB](smb.md) shares stay read-only for now.

## Refine later

- TLS by itself for S3 and WebDAV, folders' real dates, and WebDAV locks that lock.
- Delete, rename and move assets through the shares (only files just added can be today), and copy objects over S3.
- A read-only access key, for a person who may add files but wants a key that can't.
- Keep the names files were added under past 10 minutes and across restarts; a folder copy that hits a file type Lens
  doesn't take stops in Finder.
- Show supplementary files (captions, translations, attachments) beside each asset.
- Cache an asset's text and metadata so large folders list faster.
- A sidecar for each collection, topic and entity, and a folder of notes.
- Nest entities by how they relate (people under organisations) once the graph records relations between them.
