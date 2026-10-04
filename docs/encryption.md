# Encryption at rest

Lens encrypts with AES-256-GCM. Each namespace has its own random data key, and that key is only ever stored
*wrapped*, meaning encrypted by one or more key-encryption keys. Wrapped keys are kept in the `data_key` table.

| Wrapper | Where its key comes from | What it allows |
| --- | --- | --- |
| `server` | HKDF-SHA-256 of the server's secret (`ARCHIVE_SECRET_KEY`, or `data_dir/secret.key`) | Background work (transcription, embeddings, search, routines, the assistant) reads the namespace without anyone signed in |
| `passkey:<credential>` | HKDF-SHA-256 of the passkey's WebAuthn PRF output for the namespace | A vault opens when one of its owners unlocks it with that passkey |

A namespace whose `server` wrapper is removed is a **vault** (see [Vaults](#vaults)).

Encrypted files are written in 64 KiB chunks, so audio and video can still seek. The last chunk is marked, so a file
that has been cut short, edited or reordered fails to open instead of reading as something else. Key rotation adds a
new key version, and older files stay readable because each file records the version it was written with. Removing a
namespace's `data_key` row makes everything encrypted with it unreadable.

What is encrypted:

* Source credentials, notification secrets and model API keys, with the server key. This was already the case before
  encryption at rest.
* With `encryption.files` on, the files Lens keeps under its data folder, each with its namespace's key: uploads,
  email attachments (as files and as resources of their own), captured web pages, IIIF imports and resources'
  supplementary files. A new archive turns this on at its first start. An archive that already has files keeps its
  setting until you run `lens encrypt`, which turns it on and encrypts what is there (`lens encrypt --off` turns it
  back). The command can be stopped and run again. Changing the setting in the app (Settings) only affects files that
  arrive from then on.

How encrypted files are used:

* Players stream them with seeking: only the chunks a byte range touches are decrypted. Downloads are decrypted as
  they're sent.
* Tools that need a path (ffmpeg, pdftoppm, LibreOffice, Tesseract) get a plain working copy under
  `data_dir/tmp/work`, readable only by Lens. The copy is removed once it has gone unused for `encryption.work_minutes`
  (30 by default).
* Fingerprints and sizes are those of the plain file, so the same file uploaded or scanned again is still recognised.

Not encrypted yet, and planned: what Lens makes from the files (page images and frames, PDF renditions, reports,
exports), the cache of files from storage sources, and moving a recording's files to its new namespace's key (until
then a moved file still opens with the key of the namespace it came from).

What isn't encrypted by Lens: the SurrealDB database itself, including transcripts and the indexes that full-text
and semantic search need, and your own folders that Lens scans (it only reads them). Put the data volume on an
encrypted disk (LUKS, FileVault, BitLocker or an encrypted ZFS dataset) to cover these.

## Vaults

A vault is a namespace only its owners' passkeys open: the server keeps nothing that opens its files on its own.
Owners turn one on in Admin › Namespaces › the namespace › **Vault** with "Lock to my passkey".

* **How a passkey opens it.** Unlocking is a passkey prompt that also asks the passkey, through the WebAuthn PRF
  extension, for a secret made from a salt for that namespace (`sha256("lens-vault:<id>")`). Only that passkey can
  make the same secret again. The browser sends it beside the signed answer, Lens derives the key-encryption key from
  it with HKDF-SHA-256, unwraps the data key, and keeps neither. Recent phones, computers, password managers and
  security keys support PRF; a passkey that doesn't is refused with a message saying so.
* **Unlocked for a while.** A vault stays open in the API process for `encryption.vault_minutes` (60 by default)
  after each unlock, or until someone presses "Lock now". Locked, its files don't play or download (the API answers
  423), and its queued work waits; it runs once the vault is unlocked again. Workers running in a separate process
  never get the key, so they skip vault jobs.
* **More than one passkey.** While it's open, owners add their passkeys with "Add one of my passkeys". The last
  passkey that opens a vault can't be removed, from the vault or from the account.
* **No other way in.** There is no recovery code, password or admin override. If every passkey that opens a vault is
  lost, its files can't be recovered by anyone. Add a second passkey (a phone or a security key) right away.
* **Its files are always encrypted.** Turning a namespace into a vault encrypts its files that aren't yet (when
  `encryption.files` is off), in the background, and new ones are encrypted as they're stored. Files in folders Lens
  scans stay as they are.
* **Turning it back.** "Make it ordinary again" (while it's open) wraps the key for the server again and drops the
  passkey wrappers. Its files stay encrypted, now with a key the server can open.

A vault covers its files. Its transcripts, search indexes and other details are in the database like every other
namespace's (see above).

Keep `ARCHIVE_SECRET_KEY` (or `secret.key`) safe and backed up separately from the data. Without it, nothing
encrypted with the server key can be read.
