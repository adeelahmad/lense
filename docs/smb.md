# SMB share

Lens can serve [the file tree](file-tree.md) over SMB, the protocol Finder, Windows Explorer and Linux file managers
use for network drives. Namespaces, collections, topics and entities are folders; assets are files inside them, with
their metadata and text beside them. Everyone signs in as themselves and sees exactly what the web app shows them, and
a file they copy into a collection's folder is imported there ([Adding files](file-tree.md#adding-files)).

*Status: built.*

## Turn it on

Settings → SMB share → **Share the file tree over SMB**. It's off until an admin turns it on. The first time, a worker
fetches the SMB server (impacket, about 15 MB), then starts serving within seconds; Settings says when it's running
and shows the address.

Sign in with a **file access key** (the same keys the S3 share takes): the key's id is the user name and its secret
the password. Make one in Settings, keep the secret, and revoke the key to shut its access off: open sessions end
within 30 seconds.

| Where | How |
| --- | --- |
| macOS | Finder → Go → Connect to Server → `smb://<lens-machine>:1445/LENS` |
| Linux | `sudo mount -t cifs -o port=1445,username=<key id>,vers=2.0 //<lens-machine>/LENS /mnt/lens` (add `ro` to only read), or `smb://<lens-machine>:1445/LENS` in Files |
| Windows | Explorer only connects to port 445: install with `LENS_SMB_PORT=445` in front of the install line (kept for later upgrades), then open `\\<lens-machine>\LENS` |

| Setting | Default | What it does |
| --- | --- | --- |
| `smb.enabled` | off | Serve the share |
| `smb.add_files` | on | Import the files people copy in (**Import files people add**); off, the share is read-only. Turning it on or off restarts the share, which ends open sessions |
| `smb.allowed_networks` | private networks (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `127.0.0.0/8`, `100.64.0.0/10` for Tailscale, `fc00::/7`, `::1/128`) | Machines anywhere else are turned away before they can sign in; changes apply without a restart |
| `smb.bind`, `smb.port` | `127.0.0.1`, `4445` | Startup settings only: where the worker listens (`LENS_SMB_LISTEN`, `LENS_SMB_LISTEN_PORT`). In Docker it listens on `0.0.0.0:4445` inside the worker container |

With Docker, the worker container publishes the share on port **1445** of the host (`LENS_SMB_PORT=<port>` in front of
the install line picks another), so an SMB server the host already runs on 445 is never in the way. Like the web app, it's reachable from
this machine only unless `LENS_BIND=0.0.0.0` (what `install.sh` sets on a server); `LENS_SHARE_BIND` opens the file
shares on their own while the web app stays local.

Agents read the share's state at `GET /api/v1/settings/smb/status` and turn it on or off with
`PUT /api/v1/settings/smb` (`{"enabled": true}`), like every other setting.

## Adding files

Copy files into a collection's folder (with a file manager, `cp` onto a mount, or `smbclient`'s `put`) and each is
imported when it's closed, as an upload is: the same checks of its type and size, the namespace's pipeline,
and the same file twice is added once. A new folder under `Collections` is a new collection; a file anywhere else in a
namespace goes to its default collection. It takes the import permission (or an editor's role on the collection), as
the web app does; [Adding files](file-tree.md#adding-files) has the rest.

A file is refused as it's made when its type can't be uploaded, and cut off as it's written once it's larger than
uploads take (`uploads.max_mb`); a file that was cut off, deleted as it was closed, or still open when its client went
away isn't imported. Files the system writes for itself (`.DS_Store`, `._` files, `Thumbs.db`, `desktop.ini`, Office's
`~$` files) and files saved under a temporary name are kept aside for a while, seen only by you; renaming one to an
ordinary name imports it, which is how apps that save to a temporary file and rename it work. What Lens made can't be
written over, renamed or deleted.

## What keeps it safe

- **Off by default**, and nothing listens until an admin turns it on.
- **Adds, never changes.** A file is imported only where its owner may import, checked as an upload is. Nothing Lens
  made can be written over, renamed or deleted, and namespaces can't be made. With `smb.add_files` off, every call that
  would write, rename, delete or make a folder is refused before it reaches the tree.
- **Files arrive on Lens's disk, not in memory.** A file being written goes to the uploads folder, cut off at the
  upload size limit, and is imported or dropped when it's closed.
- **Per-person views.** A key opens its owner's view of the tree: the namespaces and collections they have a role in,
  nothing else. A disabled account or a revoked key stops working within 30 seconds, mid-session too.
- **No guests.** Anonymous and guest sessions get nothing; a share is only handed to a signed-in key.
- **Allowed networks only.** A connection from anywhere else is closed before a byte of SMB is read.
- **Hardened server.** Each connection gets its own random NTLM challenge; SMB1 is refused past the first
  negotiation (clients move on to SMB 2); the NTLM responses impacket logs are kept out of Lens's log.
- **Only the tree.** The server's file system is a stand-in that answers from the tree: no path reaches the disk.

SMB 2.0 has no encryption on the wire. Keep the share on networks you trust (or a VPN such as Tailscale), and keep its
port off the internet.

## How it works

The worker runs impacket's SMB server (pure Python; impacket's license is a modified Apache 1.1, and it's fetched only
when the share is turned on, as the `smb` extra). impacket serves folders through Python's `os` module; in Lens, its
`os` is a small stand-in (`domain/smb_share.py`) that looks files up in the tree for the signed-in key's
owner, writes files people add to the uploads folder and hands them to the importer (`domain/share_import.py`) as
they're closed, and reads files as they stream (files in vaults are decrypted on the way; files in storage connections are
fetched as they're read). One person's view of the tree is reused for a few seconds, so the many look-ups a file
manager makes after listing a folder don't each ask the database again.

## Refine later

- SMB 3 with encryption on the wire (impacket speaks SMB 2.0.2).
- Finder's Bonjour discovery, so the share shows up on its own.
- Packaging for the Synology and QNAP compose files, which don't publish the port yet.
- Delete, rename and move assets through the share (only files just added can be today).
- Free space as the uploads disk has it (impacket reports a fixed size).
