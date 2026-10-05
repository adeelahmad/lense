# Where Lens keeps its files

Status: **built**: the file store (this machine, or any storage connection through rclone, optionally wrapped in rclone
crypt), its settings, a check, the assistant's `file_storage` tool, Settings → Storage and the setup wizard's choice,
and notes' images and attachments ([Notes](notes.md#files-and-images)). **Planned**: moving uploads and recordings' media there too, and a downloadable
storage key.

The files Lens makes its own, starting with notes' attachments, go where **Settings → Storage** (the `files` section)
says. The setup wizard's Storage step asks the same: on this machine, or on a storage connection, which can be added
right there (the same dialog as Sources) and is checked before it's saved.

| Setting | |
|---|---|
| `store` | `local`: `data_dir/objects` on this machine (the default). `connection`: a storage connection |
| `connection` | the id of a storage connection (on the Sources page, `/sources`): S3 or S3-compatible (AWS, MinIO, Backblaze, R2 ...), Google Drive, Dropbox, OneDrive, SFTP, SMB, WebDAV, or a folder on this machine inside `sources.local_roots` |
| `folder` | where on it: a path; for S3, the bucket and a path (`my-bucket/lens`); for a folder on this machine, its full path |
| `crypt` | also wrap it in an [rclone crypt](https://rclone.org/crypt/) remote, which hides the names and sizes of what is kept there |

Everything goes through rclone, so any remote it reaches works the same way. A file going to a connection is
encrypted with its namespace's key ([Encryption](encryption.md)) before it leaves the machine: the remote only ever
holds ciphertext, whether `crypt` is on or not. crypt's password is made by Lens the first time it's needed and kept
sealed like any setting's secret: there's nothing to type or remember.

Each file records where it went, so changing these settings never strands what is already stored: older files are read
from wherever they were written, and new ones go to the new place.

`POST /api/v1/settings/files/test` writes a small file there, reads it back and removes it: what is saved, or a
`{store, connection, folder, crypt}` given in the body, to check before saving. The assistant (for admins) has
`file_storage`, which says where files go and which connections could take them, and checks it with `check`; it
changes it with `change_settings files`, which always waits for approval. Agents connected over [MCP](mcp.md) have
the same as `file_storage` and `set_file_storage` (admins only; the change is checked before it's saved).
