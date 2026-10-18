# NFS share

Lens can show the archive as folders and files ([the file tree](file-tree.md)) to other machines, so a file manager,
a media player or a script reads it like any other share: namespaces, collections, topics and entities are folders,
and assets are files inside them.

## NFS

Settings → NFS share → **Share the file tree over NFS**. It's off until you turn it on, and then a worker starts
serving within seconds. A file copied into a collection's folder is imported there, as the share's account
([Adding files](#adding-files)); turn **Import files people add** off and the share is read-only.

Mount it on Linux (Settings shows the command with your port filled in; add `ro` to only read):

```sh
sudo mount -t nfs -o port=12049,mountport=12049,tcp,vers=3 <lens-machine>:/ /mnt/lens
```

On macOS: `sudo mount -t nfs -o port=12049,mountport=12049,tcp,vers=3,resvport <lens-machine>:/ /Volumes/lens`.

| Setting | Default | What it does |
| --- | --- | --- |
| `nfs.enabled` | off | Serve the share |
| `nfs.account` | the first admin | Whose view of the tree the share shows: it holds exactly what that person sees in the web app, and files written to it are added as that person |
| `nfs.add_files` | on | Import the files written to the share (**Import files people add**); off, rclone serves it read-only |
| `nfs.allowed_networks` | private networks (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `127.0.0.0/8`, `fc00::/7`, `::1/128`) | Machines anywhere else are turned away before they read anything; changes apply without a restart |
| `nfs.port` | 2049 | The port inside Lens (with Docker, inside the worker container) |

With Docker, the worker container publishes the share on port **12049** of the host (`LENS_NFS_PORT` in `.env` picks
another), so an NFS server the host already runs on 2049 is never in the way. Like the web app, it's reachable from
this machine only unless `LENS_BIND=0.0.0.0` (what `install.sh` sets on a server); `LENS_SHARE_BIND` opens the share
on its own while the web app stays local.

### NFS has no login

NFS (version 3) trusts the network: there is no password, passkey or token. So Lens:

- serves nothing until an admin turns it on;
- adds files only as one account may (`nfs.account`), checked as uploads are, and never writes over, renames or
  deletes what Lens made; with `nfs.add_files` off it serves the tree read-only, so nothing reaching the share can
  change the archive;
- shows one account's view (`nfs.account`), never more than that person may see; pick an account that sees only what
  the share should show (a member of just the namespaces or collections to share), not an admin, when the network has
  people who shouldn't see everything, or add to everything;
- turns away every client outside `nfs.allowed_networks`, and counts them in Settings.

Anyone on an allowed network can read the share and add files to it as its account, and an address on the local
network can be spoofed, so list only your own networks, and keep the port off the internet.

### Adding files

Copy files into a collection's folder (`cp` onto the mount, or a file manager) and each is imported as an upload is,
as the share's account: the same checks of its type and size, the namespace's pipeline, and the same file twice is
added once. A file anywhere else in a namespace goes to its default collection, and one written inside a new folder
under `Collections` makes that collection ([Adding files](file-tree.md#adding-files) has the rest).

rclone keeps a file being written in a cache on Lens's disk (`data_dir/cache/nfs`) and puts it to Lens a couple of
seconds after it's closed, so it shows up in Lens a moment later. NFS has no way to tell the client that Lens turned a
file down after it was written, so a file Lens can't take (its type, its size) shows in Settings → NFS share's log,
and rclone tries it again later. An empty new folder lasts as long as rclone remembers it; it becomes a
collection when a file is written into it.

### How it works

A worker runs `rclone serve nfs` (rclone is in Lens's image already; MIT licensed; `--read-only` while
`nfs.add_files` is off) over Lens's own S3
server (a copy on 127.0.0.1 inside the worker, so the S3 share itself needn't be on), signed in with an S3 key
made for `nfs.account`, which is revoked when the share stops. rclone listens on 127.0.0.1 inside the container; a small
relay in Lens (`domain/share_relay.py`) holds the published port and hands rclone only the connections from allowed
networks. One worker serves it at a time (a lease on `app_service:nfs`), and Settings shows which one, whose view it
shows, how many connections it turned away, and rclone's last lines when something goes wrong. Agents read the same
state at `GET /api/v1/settings/nfs/status` and change it with `PUT /api/v1/settings/nfs`.

### Refine later

- NFS version 4 and Kerberos (sec=krb5) for a share with a real login.
- A share per namespace or collection, each with its own account and networks.
- Packaging for the Synology and QNAP compose files, which don't publish the port yet.
- Tell the client when Lens turns a written file down, and make empty new folders collections at once.
