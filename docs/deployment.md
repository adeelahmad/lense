# Deployment

Lens runs as containers: SurrealDB, the API, one or more workers, and the Next.js app. `docker-compose.prod.yml`
is a production-shaped starting point.

```bash
export ACCESS_SECRET_KEY=$(openssl rand -hex 32) ARCHIVE_SECRET_KEY=$(openssl rand -hex 32) \
       AUTH_SECRET=$(openssl rand -hex 32) SURREAL_PASS=$(openssl rand -hex 16) \
       FRONTEND_URL=https://lens.example.org
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml logs backend | grep "setup code"
```

## Checklist

* **HTTPS in front of the web app** (Caddy, nginx, a cloud load balancer). The web app proxies `/api/v1`, `/iiif`,
  `/embed`, `/s`, `/reports` and `/static` to the API, so only the frontend needs to be public. IIIF authorization
  requires HTTPS.
* **Allowed hosts.** Add your public host name to `server.allowed_hosts` (in the app) or `ARCHIVE_ALLOWED_HOSTS`, next
  to `backend`, the name the frontend uses inside the network.
* **Uploads through the reverse proxy.** Audio and video go up in pieces of `uploads.chunk_mb` (8 MB); let the reverse
  proxy pass request bodies at least that big (nginx: `client_max_body_size 16m;`). Finished uploads are kept in
  `data_dir/uploads`, so size the `archive-data` volume for them. See [Uploads](configuration.md#uploads).
* **Visitors' addresses, for IP groups.** Have the reverse proxy set `X-Forwarded-For`, and list the web app's address
  (in Docker, the compose network) in `server.trusted_proxies`. See [Trusted proxies](configuration.md#trusted-proxies).
* **Stable secrets.** `ACCESS_SECRET_KEY` (changing it signs everyone out), `ARCHIVE_SECRET_KEY` (changing it makes
  stored source credentials, LLM keys and everything [encrypted at rest](encryption.md) unreadable), `AUTH_SECRET`.
* **SurrealDB storage engine**: `surrealkv` (as in the compose file), RocksDB or TiKV. Not `memory`; see
  [Database](database.md).
* **Backups** of SurrealDB (`surreal export`) and of the `archive-data` volume.
* **Mail** for password resets and access requests: **Settings → Email** in the app (or `MAIL_*` in `.env`).
* **Workers.** Scale with `docker compose up -d --scale worker=3`. The images transcribe with faster-whisper on the
  CPU (SenseVoice, the default engine, is used where it is installed; elsewhere the job log notes the fallback). To
  use SenseVoice in Docker, set `EXTRAS=sensevoice` in `.env` and rebuild (`make dev`); it adds PyTorch. For GPU transcription, build with
  `EXTRAS="sensevoice voices"` and give the worker the GPU; or run workers on other machines with `SURREAL_URL`
  pointing at the database and `--steps` limited to what they can do.
* **Documents.** Docker Compose builds the full image, which reads Word, PowerPoint and spreadsheet files, text,
  Markdown, saved web pages and emails as well as PDFs and images (`LENS_TARGET=lean` builds the smaller one that
  reads only PDFs and images; a bare `docker build` makes the lean one unless given `--target full -t lens:full`): it adds LibreOffice, Chromium and fonts for most
  scripts, and ONNX Runtime with the YOLOX-s model for the objects step ([Configuration](configuration.md#objects));
  add `EXTRAS="msg"` for Outlook `.msg` emails ([Configuration](configuration.md#documents-and-images)), and
  `EXTRAS="doctr"` for docTR as the OCR engine (PyTorch: several GB;
  [Configuration](configuration.md#text-on-screen-and-scans-ocr)).
  The full image also captures web pages ([API](api.md#web-pages)), running each page's scripts in Chromium. Inside
  a container Chromium usually can't start its own sandbox (Docker's default seccomp profile doesn't allow the user
  namespaces it needs) and runs without it, so what stops a page is Lens's proxy, which lets it reach public
  addresses only. To keep Chromium's own sandbox as well, run the container with a seccomp profile that allows it
  (such as Chrome's).
* **IIIF.** Set `iiif.base_url` to the stable public address before publishing anything; identifiers are built from it.

## Cloudron

The repository is also a Cloudron package: SurrealDB, the API, a worker and the web app in one app, installed from a
checkout with `cloudron install --location lens -f Dockerfile.cloudron`. See
[cloudron/README.md](https://github.com/adeelahmad/lense/blob/main/cloudron/README.md) for data locations, updates
and publishing it as a community app.

## Frontend on Vercel

The Next.js app can be deployed to Vercel (`prod-frontend-deploy.yml`) with `API_BASE_URL` pointing at the API's
public URL; the API then needs its own HTTPS endpoint and `CORS_ORIGINS` set to the Vercel domain. The API itself does
not fit serverless functions (long-running workers, ffmpeg, large uploads, persistent connections to SurrealDB), so the
template's backend Vercel deployment was removed.
