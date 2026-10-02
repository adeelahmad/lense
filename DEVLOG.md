# Dev log

## 2026-10-02: Docker stack check (`make dev`, `make run`)

Brought up both stacks on a Linux host (Docker 29.6, Compose 5.3), walked setup, login and 21 app pages in Chromium on
each, and read every container's log.

**Healthy.** SurrealDB, the API, the worker and MailHog start clean on both stacks. The browser walk found no API
errors and no failed requests on either; the only 404s were index URLs that don't exist (`/entities`, `/resources`,
`/recordings` only have `[id]` pages).

**Fixed**

- **`make run` then `docker compose up` ran the production images.** Both compose files build under the same project,
  so the production build overwrote the dev images (`<project>-frontend`, `<project>-backend`). The dev frontend then
  ran `next start` with no build and crashed ("Could not find a production build"); the dev backend lost its dev
  tools. `make dev` hid it by rebuilding every time. The production images are now `lens-backend:prod` and
  `lens-frontend:prod`. Checked: prod up, down, then plain `docker compose up` runs the dev images.
- **A crashed dev server left its container "Up".** `start.sh` (API and web app) kept the watcher in the foreground,
  so when `fastapi dev` or `next dev` died the container stayed up serving nothing. In Docker the scripts now stop
  when either process stops, so `docker compose ps` shows it exited and the log says why. Running without Docker is
  unchanged (`wait -n` needs bash 4.3, and macOS ships 3.2). Checked by killing each server.
- **The dev stack didn't get `ARCHIVE_SECRET_KEY`.** `make` writes it to the root `.env` so Docker and native runs read
  the same stored credentials, but `docker-compose.yml` never passed it to the API or worker, which fell back to a key
  in `data_dir/secret.key`. The root `.env` is now an optional `env_file` before `fastapi_backend/.env` (which still
  wins). **Heads-up:** credentials already stored by the dev stack were encrypted with the volume's `secret.key`; after
  this change they need re-entering (or set `ARCHIVE_SECRET_KEY` to that file's contents).

**Not changed, worth knowing**

- **The dev frontend can't start from a checkout node can't write to.** `user: node` (uid 1000) plus the source
  bind mount means `next dev` needs `nextjs-frontend/` writable by uid 1000 (`.next/`, `next-env.d.ts`). Fine on
  Docker Desktop (macOS) and for a normal Linux user; a root-owned clone fails with `EACCES`. Fix there:
  `chown -R 1000:1000 nextjs-frontend`.
- React warns "Encountered a script tag while rendering React component" on not-found pages in dev: the root layout's
  theme script (`app/layout.tsx`). It still runs on server render; dev-only console noise.
- `caniuse-lite` is 24 months old (Browserslist warning in the dev log): `pnpm up caniuse-lite` when convenient.
- SurrealDB warns "existing root users were found" on every start after the first: `SURREAL_PASS` only applies when
  the volume is created, as the `.env` comment says.

**Sandbox only (not repo issues):** Docker Hub rate-limited pulls (429), `ghcr.io` blobs and the Debian mirrors are
blocked by this environment's network policy, so images were pulled from `mirror.gcr.io` and the backend was built
without its apt packages (no ffmpeg/tesseract/LibreOffice), on the lean target. Processing jobs weren't exercised.
