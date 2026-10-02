# Lens on Cloudron

The repository root is a Cloudron package: `CloudronManifest.json`, `Dockerfile.cloudron` and this folder. One app
runs SurrealDB, the API, a job worker and the web app under supervisord; only the web app is public, and it serves
the API's paths itself. Needs Cloudron 9.1 or later.

| Where | What |
|---|---|
| `/app/data/secrets.env` | secrets made on first start (session keys, database password, setup code); keep it |
| `/app/data/archive.yaml` | processing configuration, written once from `cloudron/archive.yaml`, then yours to edit |
| `/app/data/surreal` | the SurrealDB database (SurrealKV) |
| `/app/data/backup/lens.surql` | a database export, refreshed every six hours, so backups always hold a consistent copy |
| `/app/data/archive` | `data_dir`: uploads, frames, reports, model caches |
| `/app/data/media` | a folder the app may watch: put recordings here (File manager or `cloudron push`) |

Mail for password resets goes through Cloudron's mail server (the sendmail addon).

## Install on your Cloudron

With the [Cloudron CLI](https://www.npmjs.com/package/cloudron) on your computer, from a checkout of this
repository:

```bash
npm install -g cloudron
cloudron login my.example.com            # your Cloudron's dashboard address
cloudron install --location lens -f Dockerfile.cloudron
```

The CLI uploads the source and your Cloudron builds the image itself, so no registry is needed. Then open the app:
the post-install note says where the first-admin **setup code** is (the app's logs, or
`grep LENS_SETUP_CODE /app/data/secrets.env` in the web terminal).

Update to a newer checkout with `cloudron update --app lens -f Dockerfile.cloudron`; logs with
`cloudron logs --app lens -f`.

Build options go with `--build-arg`: `FULL=true` adds LibreOffice, Chromium and fonts (Office files, web pages,
emails), `EXTRAS="sensevoice voices"` adds backend extras as in `fastapi_backend/Dockerfile`. Raise the memory limit
in the app's Resources page for large models.

## Build once, install from an image

To build on another machine instead, push the image to a registry your Cloudron can pull from (a public one, or one
added under Settings → Docker registries):

```bash
docker build -f Dockerfile.cloudron -t ghcr.io/you/lens-cloudron:0.3.0 .
docker push ghcr.io/you/lens-cloudron:0.3.0
cloudron install --location lens --image ghcr.io/you/lens-cloudron:0.3.0
```

## Publish as a community app

Cloudron 9.1 and later install community apps from a `CloudronVersions.json` file at a public URL, with updates
offered as new versions are added to it.

Publishing a GitHub release builds the image and pushes it to `ghcr.io/adeelahmad/lens-cloudron:<release version>`
(and `:latest`) with the "Cloudron image" workflow; make that package public once on GitHub. Then:

```bash
cloudron versions init                                        # once: creates CloudronVersions.json
cloudron versions add --image ghcr.io/adeelahmad/lens-cloudron:0.3.0
git add CloudronVersions.json && git commit -m "Cloudron: publish 0.3.0" && git push
```

Then install it from the dashboard (App Store, community app, with the file's raw URL) or with
`cloudron install --location lens --versions-url https://raw.githubusercontent.com/adeelahmad/lense/main/CloudronVersions.json`.
For each release: bump `version` in `CloudronManifest.json`, add a `[x.y.z]` entry to `cloudron/CHANGELOG`, push a
release (which pushes the image) and run `cloudron versions add` again. The image must be public.

## Restore the database from its export

If the live database files in a backup don't open, load the export into a fresh database (web terminal):

```bash
supervisorctl -c /app/code/cloudron/supervisord.conf stop api worker surrealdb
mv /app/data/surreal /app/data/surreal.broken && mkdir /app/data/surreal && chown cloudron:cloudron /app/data/surreal
supervisorctl -c /app/code/cloudron/supervisord.conf start surrealdb
source /app/data/secrets.env
surreal import --endpoint http://127.0.0.1:8001 --user root --pass "$SURREAL_PASS" \
  --namespace archive --database main /app/data/backup/lens.surql
supervisorctl -c /app/code/cloudron/supervisord.conf start api worker
```
