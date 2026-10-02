# Lens on Proxmox VE

A [Proxmox VE community helper script](https://community-scripts.org/docs/ct/detailed_guide) that creates a Debian 13
LXC container running Lens without Docker: SurrealDB, the API, a job worker and the web app as systemd services.

Run it in the Proxmox VE host's shell:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/adeelahmad/lense/main/proxmox/ct/lens.sh)"
```

The script uses the community-scripts engine (`community-scripts/core`) for the container, and takes its own
`ct/lens.sh` and `install/lens-install.sh` from this folder. When it finishes, open `http://<container-ip>:3000` and
create the first admin with the setup code: run `lens-setup-code` in the container.

Defaults: 4 cores, 4 GB RAM (the web app's build needs it), 20 GB disk, unprivileged, amd64 or arm64. Override them in
the script's advanced settings, or with `var_cpu=… var_ram=… var_disk=…` before the command.

## What's in the container

| | |
|---|---|
| Code | `/opt/lens`, a link to `/opt/lens-<commit>`: a shallow clone of the latest GitHub release, or of `main` while there is none |
| Settings | `/etc/lens/lens.env` (API and worker), `/etc/lens/web.env` (web app), `/etc/lens/archive.yaml` |
| Data | `/var/lib/lens` (archive data and uploads; `media/` is the folder of the starting `media` namespace), `/var/lib/surrealdb` |
| Services | `surrealdb` (127.0.0.1:8001), `lens-api` (127.0.0.1:8000), `lens-worker`, `lens-web` (port 3000) |

Only the web app listens on the network; it proxies the API's paths. The secrets in `/etc/lens/*.env` are generated
once at install and kept by updates. Logs: `journalctl -u lens-api -f` (and `lens-worker`, `lens-web`, `surrealdb`).

The container runs the lean image's tools (ffmpeg, Tesseract, poppler, rclone). To read Word, PowerPoint and
spreadsheet files, text, web pages and emails too, install what the `full` image adds
(`apt install libreoffice-writer-nogui libreoffice-calc-nogui libreoffice-impress-nogui chromium fonts-liberation2`);
see [Deployment](../docs/deployment.md) for HTTPS, allowed hosts and backups, which apply here as well.

## Updating

Run `update` in the container's console. It moves to the latest published GitHub release (the newest commit of `main`
while the repository has no release). It builds the new version beside the running one, so Lens stays up during the
build and a failed build leaves it as it was; then it stops the services, switches `/opt/lens` over, starts them again
and removes the old version. It does nothing when the container is already there. Publishing a release is all it takes
for containers to pick it up: there are no release assets to build for Proxmox. SurrealDB is upgraded when the pinned
version in `ct/lens.sh` changes.

## Testing a branch

Point the engine at a branch of this repository:

```bash
_CS_DEFAULT_URL=https://raw.githubusercontent.com/adeelahmad/lense/<branch>/proxmox \
  bash -c "$(curl -fsSL https://raw.githubusercontent.com/adeelahmad/lense/<branch>/proxmox/ct/lens.sh)"
```

This picks the scripts from that branch; the container still installs the latest release of Lens (or `main`).
