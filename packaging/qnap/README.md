# Lens for QNAP NAS

A self-contained QPKG, built with [QDK](https://github.com/qnap-dev/QDK), that installs Lens from the App Center like
any other app. Lens runs in **Container Station**, but you don't set up any containers: the package carries the
Lens images (API and worker, web app) and SurrealDB, loads them on its first start, and runs the stack. Nothing is
built or downloaded on the NAS.

- **NAS:** QTS 5 or QuTS hero 5 with Container Station 3, on an Intel/AMD (`x86_64`) or ARM 64-bit (`arm_64`) model.
  4 GB of RAM or more; transcription and the full image's document conversion are heavy.
- **Space:** a few GB on the volume: the package's image files while they're loaded on the first start (they're
  deleted afterwards), the images in Container Station, and whatever your archive holds.

## Build the package

On any machine with Docker and buildx (Linux, macOS, Windows with WSL), from the repository root:

```bash
packaging/qnap/build.sh                    # Intel/AMD NAS: packaging/qnap/build/Lens_0.3.0_x86_64.qpkg
packaging/qnap/build.sh --arch arm_64      # ARM NAS:       packaging/qnap/build/Lens_0.3.0_arm_64.qpkg
```

- The version comes from `fastapi_backend/pyproject.toml` (`--version` overrides it, 10 characters at most).
- The backend image is the `full` target by default, as `make run` uses: LibreOffice and Chromium to read Office files,
  text, web pages and emails, and object detection. `--target lean` makes a much smaller package that reads PDFs
  and images only. `--extras "msg"` adds Outlook `.msg` emails (see docs/deployment.md).
- Building for the other architecture than your machine's uses QEMU. Docker Desktop has it; on Linux, once:
  `docker run --privileged --rm tonistiigi/binfmt --install arm64` (or `amd64`). It's slow; building on a machine of
  the NAS's architecture is quicker.
- The first run builds a small `lens-qdk` image with QDK in it, so QDK needn't be installed.
- `--prebuilt` packages the `lens-backend:<version>` and `lens-frontend:<version>` images already in Docker instead
  of building them (from CI, say).

## Install on the NAS

1. Install **Container Station** from the App Center and open it once, so it finishes its setup.
2. App Center > the gear icon (top right) > **Install Manually** > choose the `.qpkg` for your NAS > **Install**.
   QTS warns that the app isn't digitally signed; accept to continue. (Or, over SSH:
   `sh Lens_0.3.0_x86_64.qpkg`.)
3. The first start loads the images into Container Station, which takes a few minutes. When Lens shows as running,
   click **Open** in the App Center (or browse to `http://<NAS address>:3000`).
4. Create the first admin account with the **setup code**. It's in the QTS system log (Control Panel > System >
   System Logs, an entry from Lens saying "create the first admin account with setup code …"), in `lens.env` in the
   data folder (`LENS_SETUP_CODE`), and over SSH: `/share/CACHEDEV1_DATA/.qpkg/Lens/lens.sh setup-code` (the
   path depends on the volume you installed to: `getcfg Lens Install_Path -f /etc/config/qpkg.conf`).

The containers show up in Container Station as the application `lens` (surrealdb, backend, worker, frontend), where
you can see their logs and resource use.

## Where things are

The **data folder** is `/share/Container/lens` (the `Container` shared folder Container Station creates), or
`Lens-data` next to the app's folder if that share doesn't exist. It holds:

| Path | What |
|---|---|
| `lens.env` | Settings and secrets, written on the first start. Keep it with the rest: losing `ARCHIVE_SECRET_KEY` makes stored credentials and model API keys unreadable. |
| `surrealdb/` | The database. |
| `archive/` | Uploads, media and derived files. |

Back up the whole folder (Hybrid Backup Sync works, as it's a shared folder) with Lens stopped. Removing the app keeps
the data folder and drops the images; reinstalling picks up where it was. Delete the folder yourself to remove
everything.

Your **recordings and documents** on the NAS appear inside Lens as `/audio` (read-only), for watched folders. By
default that's the `Multimedia` share; set `AUDIO_DIR` in `lens.env` to use another folder.

## Settings

Edit `lens.env` in the data folder, then stop and start Lens in the App Center (or `lens.sh restart` over SSH).

- **Address and port.** `FRONTEND_URL` is the NAS's LAN address at install time, `http://<ip>:3000`. Change it, and
  add the host name to `ARCHIVE_ALLOWED_HOSTS`, if you reach Lens another way: a fixed host name, a reverse proxy with
  HTTPS (QTS's own reverse proxy in Control Panel > Network Access, or myQNAPcloud), or if the NAS's address changes.
  For another port set `LENS_PORT` and the port in `FRONTEND_URL` alike. Sharing and IIIF need HTTPS.
- **Mail** for password resets: the `MAIL_*` settings.
- **Models.** Lens talks to OpenAI-compatible servers; set them up in the web app. A model server on the NAS itself
  (Ollama in Container Station, say) is reachable at the NAS's LAN address, not `localhost`.
- To move the data folder: stop Lens, move the folder, then
  `setcfg Lens Data_Path /share/<folder> -f /etc/config/qpkg.conf` and start Lens.

## Over SSH

`lens.sh` in the app's folder (`getcfg Lens Install_Path -f /etc/config/qpkg.conf`):

```bash
lens.sh status              # the containers
lens.sh logs [backend]      # follow the logs (all, or one service)
lens.sh setup-code          # the first-admin setup code
lens.sh restart
```

## Upgrading

Build the new version's package and install it over the old one, as in step 2. The new images are loaded on the
next start and the old version's are removed; the data folder carries over.

## How the package is made

`build.sh` builds the images for the NAS's platform, saves them into `<arch>/images/` next to the `qpkg/` sources
(with a `manifest` naming each one), and runs QDK's `qbuild` on the lot. In the package:

- `qpkg.cfg`: requires Container Station 3, opens the web app on port 3000, gives the first start 15 minutes.
- `lens.sh`: the service script. On start it waits for Container Station's Docker, writes `lens.env` with fresh
  secrets on the first run, loads any image that isn't in Docker yet, and runs `docker compose up` on
  `docker-compose.yml` as the project `lens`. On removal it stops the stack and removes the Lens images.
- `docker-compose.yml`: the same stack as `docker-compose.prod.yml`, with the bundled images, the data folder as bind
  mounts and the web app on the NAS's port 3000.
