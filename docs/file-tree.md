# The graph as files

Lens shows its graph as an ordinary tree of folders and files: graph nodes (collections, topics, entities) are
folders, nested along the graph's links, and every asset (a recording, document or image) is a few files inside the
folders it belongs to, its metadata in a sidecar beside it. Agents and tools that already understand files can read
Lens without learning its API.

| Where | Status |
| --- | --- |
| The API (`GET /api/v1/tree`, `GET /api/v1/tree/file`) and the MCP tools `list_files` and `read_file` | built |
| An [S3 server](#s3) and a [WebDAV server](#webdav) (read-only, off until turned on) | built |
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
topics and entities of the assets in them (with the topics broader than those). Anything else answers "not found". The tree only reads.

## The API

`GET /api/v1/tree?path=pods/Topics/Gene therapy` lists a folder: `{path, entries: [{name, folder, modified, size,
content_type}]}`. An empty path lists your namespaces. `GET /api/v1/tree/file?path=…` reads a file, with byte ranges
(`Range: bytes=0-1023`) and an `ETag`. Asking for a file as a folder (or the other way round) answers 400.

The MCP server has `list_files` and `read_file` ([MCP server](mcp.md)): `read_file` returns the text of `.txt` and
`.json` files, a page at a time, and the download link of everything else.

## S3

Off until an admin turns it on, in Settings, File shares (`shares.enabled`). Then Lens's worker serves the tree
read-only over S3 on port 9555 (`shares.s3_port`; with Docker, `LENS_S3_PORT` publishes it on another port of the
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

then `rclone lsd lens:`, `rclone copy "lens:pods/Topics/Gene therapy" ./gene-therapy`, or `rclone mount lens: ~/lens`.
Supported: listing buckets, listing objects (v1 and v2, by folder or recursively, a page at a time), reading objects whole or
in ranges, and presigned links. Anything that writes answers AccessDenied. Requests must be signed with Signature
Version 4.

The server speaks plain HTTP. On a network you don't trust, keep it on this machine or behind TLS (a reverse proxy),
since files travel in clear.

## WebDAV

Off until an admin turns it on, with S3 (Settings, File shares; `shares.webdav`, on by default once shares are).
Lens's worker serves the same tree read-only over WebDAV on port 9556 (`shares.webdav_port`; with Docker,
`LENS_WEBDAV_PORT`), so it mounts as a drive: in Finder, Go, Connect to Server; in Windows Explorer, Map network
drive; in GNOME Files or Dolphin, `dav://lens.local:9556/`; or `rclone` with `type = webdav`.

Sign in with an access key: its id as the user name and its secret as the password. The folders at the top are your
namespaces. Listing (PROPFIND, one level at a time) and reading (GET, with ranges) work; anything that writes answers
403. Eight failed sign-ins from one address within 15 minutes are refused for a while.

Like S3, it speaks plain HTTP, and Basic sign-in sends the secret with every request: keep it on this machine or
behind TLS on a network you don't trust.

## At the web app's address

The web app can relay both shares at its own address: S3 at `/s3` and WebDAV at `/dav`. They then work wherever the web
app opens, including through [Remote access](remote-access.md) and [HTTPS](https.md), which serve the web app over
`https://` and reach nothing else. It's off until an admin turns on **At the web app's address too** in Settings, File
shares (`shares.remote`), on top of the shares themselves; until then requests that come that way are turned away
before anyone signs in, and the shares' own ports work as before.

Once it's on, the dialog that shows a new access key gives these addresses:

```ini
[lens]
type = s3
provider = Other
access_key_id = LK…
secret_access_key = …
endpoint = https://lens.example.com/s3
force_path_style = true
```

and `https://lens.example.com/dav/` for WebDAV (Finder, Windows Explorer, `davs://lens.example.com/dav/` in GNOME Files
or Dolphin, rclone's `type = webdav`). The S3 endpoint has a path, so it needs a client that takes one: rclone, the AWS
CLI and boto3 do.

How it works: the web app hands requests under `/s3` and `/dav` to the worker's share servers before Next.js sees them
(`nextjs-frontend/lens-shares.js`; the servers are at `LENS_S3_URL` and `LENS_WEBDAV_URL`, set in the Docker Compose
files, else this machine's ports 9555 and 9556). The path and `Host` stay as the client sent them, since S3 clients
sign both, and `X-Forwarded-Prefix` tells the share server the request came through the web app. Failed WebDAV
sign-ins are counted per visitor, from `X-Forwarded-For` as the web app and the proxies Lens trusts report it
(Settings, Access & embedding, Trusted proxies). Signing in is the same as at the ports: a person's own access key,
read-only.

## Refine later

- TLS by itself for S3 and WebDAV, folders' real dates, and WebDAV locks for clients that insist on them.
- Virtual-host S3 addressing at the web app's address, for clients that can't take an endpoint with a path (MinIO's
  `mc`).
- Show supplementary files (captions, translations, attachments) beside each asset.
- Cache an asset's text and metadata so large folders list faster.
- A sidecar for each collection, topic and entity, and a folder of notes.
- Nest entities by how they relate (people under organisations) once the graph records relations between them.
