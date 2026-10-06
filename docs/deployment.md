# Deployment

Lens runs as containers: SurrealDB, the API, one or more workers, and the Next.js app. `docker-compose.prod.yml`
is a production-shaped starting point. Two more services are opt-in profiles: `--profile fedora` adds a Fedora 6
repository to keep a copy of the archive in ([Fedora](fedora.md)), and `--profile opensearch` adds OpenSearch for word
search on a big archive (then choose it in Settings → Search and reindex; the default is a SQLite index in
`data_dir/search`).

```bash
export ACCESS_SECRET_KEY=$(openssl rand -hex 32) ARCHIVE_SECRET_KEY=$(openssl rand -hex 32) \
       AUTH_SECRET=$(openssl rand -hex 32) SURREAL_PASS=$(openssl rand -hex 16) \
       FRONTEND_URL=https://lens.example.org
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml logs backend | grep "setup code"
```

## Checklist

* **HTTPS in front of the web app**: [Remote access](remote-access.md) (a Cloudflare Tunnel Lens runs itself, set up in
  the app), or your own reverse proxy (Caddy, nginx, a cloud load balancer). The web app proxies `/api/v1`, `/iiif`,
  `/embed`, `/s`, `/reports`, `/static`, `/mcp`, `/id/...`, `/ns` and `/.well-known/` to the API, so only the frontend
  needs to be public. IIIF authorization requires HTTPS.
* **Allowed hosts.** Add your public host name to `server.allowed_hosts` (in the app) or `ARCHIVE_ALLOWED_HOSTS`, next
  to `backend`, the name the frontend uses inside the network.
* **Uploads through the reverse proxy.** Audio and video go up in pieces of `uploads.chunk_mb` (8 MB); let the reverse
  proxy pass request bodies at least that big (nginx: `client_max_body_size 16m;`). Finished uploads are kept in
  `data_dir/uploads`, so size the `archive-data` volume for them. See [Uploads](configuration.md#uploads).
* **Visitors' addresses, for IP groups and throttles.** The Docker Compose files trust the web app's container by name
  (`LENS_TRUSTED_PROXY_HOSTS=frontend,backend,worker`, looked up every 30 seconds), so each visitor gets their own
  sign-in throttle and IP groups see real addresses. A reverse proxy in front: have it set `X-Forwarded-For`, and list
  its address in `server.trusted_proxies`. See [Trusted proxies](configuration.md#trusted-proxies).
* **What's hardened already.** In the compose files: the database's password is random (the installer makes one), the
  web app listens on this machine only unless `LENS_BIND=0.0.0.0`, the database, API and Fedora ports are bound to
  127.0.0.1, the containers run as non-root users with `no-new-privileges`, and the web app sends `nosniff`, a
  referrer policy, a permissions policy and a frame policy (plus HSTS when reached through Cloudflare).
* **Stable secrets.** `ACCESS_SECRET_KEY` (changing it signs everyone out), `ARCHIVE_SECRET_KEY` (changing it makes
  stored source credentials, LLM keys and everything [encrypted at rest](encryption.md) unreadable), `AUTH_SECRET`.
* **SurrealDB storage engine**: `surrealkv` (as in the compose file), RocksDB or TiKV. Not `memory`; see
  [Database](database.md).
* **Backups.** `docker compose -f docker-compose.prod.yml exec backend lens backup` writes one of the database into
  `data_dir/backups` ([Database](database.md#backups)); back up the `archive-data` volume as well.
* **Mail** for sign-in links and access requests: **Settings → Email** in the app (or `MAIL_*` in `.env`).
* **Workers.** Scale with `docker compose up -d --scale worker=3`. The images carry no speech-to-text engine: a worker
  fetches the configured one (SenseVoice by default) the first time a recording needs it ([Components](components.md)),
  and cloud speech needs none. To bake one in instead, set `EXTRAS=whisper` (faster-whisper) or `EXTRAS=sensevoice`
  (adds PyTorch) in `.env` and rebuild (`make run`). For GPU transcription, build with
  `EXTRAS="sensevoice voices"` and give the worker the GPU; or run workers on other machines with `SURREAL_URL`
  pointing at the database and `--steps` limited to what they can do.
* **Documents.** Docker Compose builds the full image, which reads Word, PowerPoint and spreadsheet files, text,
  Markdown, saved web pages and emails as well as PDFs and images (`LENS_TARGET=lean` builds the smaller one that
  reads only PDFs and images; a bare `docker build` makes the lean one unless given `--target full -t lens:full`): it adds LibreOffice, Chromium and fonts for most
  scripts (ONNX Runtime and the YOLOX-s model for the objects step are fetched at run time, on first use:
  [Components](components.md));
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

The Next.js app can be deployed to Vercel with `API_BASE_URL` pointing at the API's public URL
(`prod-frontend-deploy.yml` at the repository root is a GitHub Actions workflow left from the template that does it;
copy it into `.github/workflows/` and set the `VERCEL_*` secrets to use it); the API then needs its own HTTPS endpoint
and `CORS_ORIGINS` set to the Vercel domain. The API itself does not fit serverless functions (long-running workers,
ffmpeg, large uploads, persistent connections to SurrealDB), so the template's backend Vercel deployment was removed.
