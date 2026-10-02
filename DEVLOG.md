# Dev log

## 2026-10-02: Docker stack check (`make dev`, `make run`)

Both stacks brought up on a Linux host (Docker 29.6, Compose 5.3); setup, login and 21 app pages walked in Chromium on
each; every container's log read. Folded in the Docker fixes from running `make dev` on an Apple Silicon Mac with
Colima (first made on the OAuth branch), so there is one Docker change.

**Healthy.** SurrealDB, the API, the worker and the mail catcher start clean on both stacks. The browser walk found no
API errors and no failed requests; the only 404s were index URLs that don't exist (`/entities`, `/resources`,
`/recordings` only have `[id]` pages).

**Fixed**

- **`make run` then `docker compose up` ran the production images.** Both compose files build under the same project,
  so the production build overwrote the dev images (`<project>-frontend`, `<project>-backend`); the dev frontend then
  ran `next start` with no build and crashed ("Could not find a production build"). `make dev` hid it by rebuilding
  every time. The production images are `lens-backend` and `lens-frontend`, tagged `${LENS_VERSION:-prod}`.
- **A crashed dev server left its container "Up".** In Docker, `start.sh` (API and web app) stops when either the
  server or the watcher stops, so `docker compose ps` shows it exited and the log says why. Outside Docker it waits
  for both (`wait -n` needs bash 4.3; macOS ships 3.2).
- **Containers ignored `docker stop`** and were killed ten seconds later (exit 137): as a container's first process,
  bash and Python ignore TERM unless they handle it. `start.sh` traps TERM and INT and stops its children; `lens
  worker` and `lens watch` stop on TERM as on Ctrl-C.
- **The dev stack didn't get `ARCHIVE_SECRET_KEY`.** `make` writes it to the root `.env` so Docker and native runs
  read the same stored credentials, but the dev compose file never passed it on; the API and worker fell back to
  `data_dir/secret.key`. The root `.env` is now an optional `env_file`, before `fastapi_backend/.env` (which wins).
  Credentials a dev stack stored before need entering again (CHANGELOG says how to keep them).
- **No arm64 mail catcher.** MailHog publishes amd64 only; the dev stack uses Mailpit (same ports, now on 127.0.0.1).
- **Dependencies didn't follow the image.** `node_modules` and the venv were named volumes that a rebuild leaves
  alone (the web app ran Next 16.0.8 against a 16.3.6 lockfile); they're anonymous volumes that `make dev` renews.
- `make` finds `docker compose` or the standalone `docker-compose`. The Next dev server no longer writes `AGENTS.md`
  and `CLAUDE.md` into `nextjs-frontend/` (`agentRules: false`), and `caniuse-lite` is current.

**Not changed, worth knowing**

- **The dev frontend needs a checkout uid 1000 can write to.** `user: node` plus the source bind mount means `next
  dev` writes `.next/` and `next-env.d.ts` as uid 1000. Fine on Docker Desktop and Colima (macOS) and for a normal Linux
  user; a root-owned clone fails with `EACCES` (`chown -R 1000:1000 nextjs-frontend`).
- React warns "Encountered a script tag while rendering React component" on not-found pages in dev: the root layout's
  theme script (`app/layout.tsx`). It still runs on server render; dev-only console noise.
- SurrealDB warns "existing root users were found" on every start after the first: `SURREAL_PASS` only applies when
  the volume is created, as the `.env` comment says.

**Sandbox only (not repo issues):** Docker Hub rate-limited pulls (429), `ghcr.io` blobs and the Debian mirrors were
blocked by that environment's network policy, so images came from `mirror.gcr.io` and the backend was built without
its apt packages (no ffmpeg/tesseract/LibreOffice), on the lean target. Processing jobs weren't exercised.
