# Encryption at rest

Lens encrypts with AES-256-GCM. Each namespace has its own random data key, and that key is only ever stored
*wrapped*, meaning encrypted by one or more key-encryption keys. Wrapped keys are kept in the `data_key` table.

| Wrapper | Where its key comes from | What it allows |
| --- | --- | --- |
| `server` | HKDF-SHA-256 of the server's secret (`ARCHIVE_SECRET_KEY`, or `data_dir/secret.key`) | Background work (transcription, embeddings, search, routines, the assistant) reads the namespace without anyone signed in |
| `passkey:<credential>` | The passkey's WebAuthn PRF output (coming with passkey vaults) | The namespace opens when its owner signs in with that passkey |

A namespace whose `server` wrapper is removed is a **vault**. Only one of its passkeys can open it, and its contents
are processed only while it is unlocked. Register more than one passkey for a vault: if every passkey that opens it
is lost, its data can't be recovered by anyone.

Encrypted files are written in 64 KiB chunks, so audio and video can still seek. The last chunk is marked, so a file
that has been cut short, edited or reordered fails to open instead of reading as something else. Key rotation adds a
new key version, and older files stay readable because each file records the version it was written with. Removing a
namespace's `data_key` row makes everything encrypted with it unreadable.

What is encrypted so far:

* Source credentials, notification secrets and model API keys, with the server key. This was already the case before
  encryption at rest.
* The keyring and file format above. Files Lens stores under its data folder are next, along with a `lens encrypt`
  command for installs that already have data.

What isn't encrypted by Lens: the SurrealDB database itself, including transcripts and the indexes that full-text
and semantic search need, and your own folders that Lens scans (it only reads them). Put the data volume on an
encrypted disk (LUKS, FileVault, BitLocker or an encrypted ZFS dataset) to cover these.

Keep `ARCHIVE_SECRET_KEY` (or `secret.key`) safe and backed up separately from the data. Without it, nothing
encrypted with the server key can be read.
