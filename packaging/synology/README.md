# Lens for Synology DSM

A self-contained Synology package (`.spk`). Install it in Package Center and Lens runs: the package carries the
images for the API and worker, the web app and SurrealDB, and has Container Manager run them as a project called
`lens`. Nothing is pulled from a registry and nothing is set up by hand.

Needs DSM 7.2.1 or later with Container Manager (the package installs it if it's missing), on an x86_64 model or a
64-bit ARM model that Container Manager supports.

## Build

On any machine with Docker (and buildx, which Docker Desktop and current Docker Engine include):

```bash
packaging/synology/build.sh                    # x86_64, most Plus and Value models
ARCH=armv8 packaging/synology/build.sh         # 64-bit ARM models (cross-builds: needs QEMU/binfmt on x86)
LENS_TARGET=full packaging/synology/build.sh   # with LibreOffice and Chromium, to read Office files and web pages
```

The package lands in `dist/synology/lens-<version>-<build>-<arch>.spk`. Or run the **Synology package** workflow from
the repository's Actions tab and download the `.spk` from the run.

To find your NAS's architecture: Control Panel > Info Center shows the CPU; Intel and AMD are `x86_64`, Realtek
RTD1296/RTD1619 and Marvell Armada 37xx are `armv8`.

## Install

1. Package Center > **Manual Install** > choose the `.spk` > Next. Accept the third-party package warning.
2. In the wizard, pick the web port (3000 unless something else uses it), the address people will open Lens at
   (leave it blank for `http://<NAS address>:<port>`), and a setup code.
3. When it's done, open Lens from the DSM main menu (or the address above), and create the first admin account with
   the setup code.

The package creates a shared folder `lens` (or reuses one with that name):

| Folder | What's in it |
| --- | --- |
| `lens/db` | The SurrealDB database |
| `lens/archive` | Uploads, media and everything Lens derives from them |
| `lens/audio/podcasts`, `lens/audio/interviews` | Recordings Lens can scan into the two starting namespaces |
| `lens/lens.conf` | Secrets and the wizard's answers, kept with the data so a reinstall still reads it |

Back up the whole `lens` folder (Hyper Backup) with Lens stopped. Uninstalling the package leaves the folder alone.

## Upgrade

Build with a higher `BUILD` (or a new Lens version) and Manual Install the new `.spk` over the old one. The data, the
secrets and the wizard's answers are kept; Container Manager loads the new images and recreates the containers.
The previous version's images stay in Container Manager > Image until you delete them.

```bash
BUILD=0002 packaging/synology/build.sh
```

## How it works

The package uses DSM's `docker-project` resource (Container Manager 1432+, DSM 7.2.1+): `conf/resource` points
Container Manager at `compose/compose.yaml` inside the package and at the bundled image tarball, which it loads before
creating the project. Container Manager starts and stops the project with the package. The package's own scripts run
as an unprivileged package user, as DSM 7 requires; they only write the compose file.

- `scripts/postinst` resolves the `lens` shared folder, writes `lens.conf` (secrets generated once, never
  overwritten) and renders `compose/compose.yaml` and the DSM menu entry (`ui/config`) from their templates.
- `package/compose/compose.yaml.in` is `docker-compose.prod.yml` with built images instead of build contexts, the
  shared folder instead of named volumes, and a one-shot `permissions` container that hands `lens/archive` to the
  API's user (uid 1000).
- The API uses the image's `docker/archive.yaml`, so it starts with the `podcasts` and `interviews` namespaces.

## Troubleshooting

- **Logs:** Container Manager > Project > `lens` > the container > Log. The API's log names the setup code too.
- **Port in use:** reinstall and pick another port; the data stays.
- **Behind a reverse proxy (HTTPS):** give its address in the wizard, so sign-in redirects and emailed links use it;
  Control Panel > Login Portal > Advanced > Reverse Proxy can forward it to `localhost:<port>`.
