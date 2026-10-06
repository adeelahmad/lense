# Configuration

Lens has three layers of configuration, each for a different kind of setting.

| Layer | Where | What |
|---|---|---|
| **Environment** | `fastapi_backend/.env`, container env | secrets, database connection, CORS, mail, where `archive.yaml` is |
| **archive.yaml** | `$ARCHIVE_CONFIG` (see `archive.example.yaml`) | namespaces and their folders, processing defaults, bootstrap-only paths |
| **Settings in the app** | `GET/PUT /api/v1/settings/<section>` (admins), stored in SurrealDB | everything that can change at runtime |

Precedence for processing settings: built-in defaults (`app/domain/store.py`, `DEFAULTS`), then `archive.yaml`, then
the app, then the few environment overrides below. Secrets saved in the app (such as an LLM API key) are encrypted
with AES-GCM and are write-only: the API reports whether one is set, never its value.

## Backend environment

| Variable | Default | |
|---|---|---|
| `ACCESS_SECRET_KEY` | **required** | signs access tokens and media links (`openssl rand -hex 32`) |
| `ACCESS_TOKEN_EXPIRE_SECONDS` | `900` | access token lifetime |
| `MEDIA_URL_EXPIRE_SECONDS` | `21600` | signed media link lifetime |
| `PASSWORD_RESET_EXPIRE_MINUTES` | `60` | |
| `SURREAL_URL` | embedded | `ws://host:8000` for a server; see [Database](database.md) |
| `SURREAL_USER` / `SURREAL_PASS` / `SURREAL_NS` / `SURREAL_DB` / `SURREAL_POOL_SIZE` | `root` / `root` / `archive` / `main` / `8` | |
| `ARCHIVE_SECRET_KEY` | generated in `data_dir/secret.key` | encrypts stored credentials; keep it stable and backed up |
| `ARCHIVE_CONFIG` | `archive.yaml` | processing configuration file |
| `ARCHIVE_ALLOWED_HOSTS` | | break-glass override of allowed Host headers if a bad setting locks everyone out |
| `RUN_BACKGROUND` | follows `workers.inline` | run job workers and folder watching inside the API process |
| `LENS_SETUP_CODE` | random | fix the first-run setup code (automation) |
| `LENS_ADMIN_EMAIL` / `LENS_ADMIN_NAME` | | create the first admin at startup, with no setup code; the log prints a sign-in link for adding their passkey ([First-run setup](#first-run-setup)) |
| `LENS_ADMIN_PASSWORD` | | give that admin a password instead (passwords stay on) |
| `LENS_NAMESPACE` | | create the first namespace at startup, while there is none |
| `LENS_LLM_BASE_URL` / `LENS_LLM_MODEL` / `LENS_LLM_API_KEY` / `LENS_LLM_VISION_MODEL` | | the model provider; wins over Settings, which show these locked |
| `LENS_SETUP_WIZARD` | | `off`: never show the setup wizard |
| `LENS_TELEMETRY` / `LENS_TELEMETRY_ENDPOINT` / `LENS_TELEMETRY_HEADERS` | off | opt-in OpenTelemetry traces and metrics, sent only to this OTLP/HTTP endpoint ([Telemetry](telemetry.md)); `LENS_TELEMETRY=off` keeps it off whatever Settings say |
| `FRONTEND_URL` | `http://localhost:3000` | the web app's address as people use it: links in emails, and where apps send people to sign in ([OAuth](authentication.md#oauth)) |
| `CORS_ORIGINS` | `["http://localhost:3000"]` | origins allowed to call the API from a browser |
| `OPENAPI_URL` | `/openapi.json` | `""` disables `/docs` and the schema |
| `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_FROM`, `MAIL_STARTTLS`, `MAIL_SSL_TLS`, `USE_CREDENTIALS`, `VALIDATE_CERTS` | | SMTP for password reset and for telling owners about requests for access. Usually set in the app instead (**Settings → Email**, with **Send a test email**); set here, they win and show locked there. Without a server the links are logged |

## First-run setup

A fresh install (no accounts when the API first starts) walks its first admin through setup in the web app:

1. **Admin account**, with the one-time setup code from the log (`make setup-code`), so a stranger who finds a new
   public server can't claim it. The admin signs in with a passkey ([Authentication](authentication.md#signing-in-passkeys)).
   Skipped when `LENS_ADMIN_EMAIL` creates the admin at startup; then open the sign-in link the log prints (or sign
   in with `LENS_ADMIN_PASSWORD`, if you set one).
2. **Namespace**: the first one, with its knowledge graph shared or isolated. Already done when archive.yaml or
   `LENS_NAMESPACE` names namespaces, or an install script made one.
3. **Model provider**: an OpenAI-compatible server (OpenAI, Ollama, llama.cpp, LM Studio, vLLM), its model and key,
   with a test.
4. **Storage**: where data lives (shown; set in archive.yaml and `SURREAL_URL`), where Lens keeps its own files (this
   machine or a storage connection, [Storage](storage.md)), the largest upload, and optionally a folder inside
   `sources.local_roots` to watch.
5. **Apps and AI**: whether apps and AI assistants (Claude, ChatGPT, Cursor and other MCP clients) may sign people in
   with their Lens account ([OAuth](authentication.md#oauth)), on unless turned off, how long their tokens last, and
   the MCP server's address to add to an assistant.
6. **Telemetry**: off unless chosen ([Telemetry](telemetry.md)).

Every step can be skipped, and the whole wizard too; all of it stays in Settings. Values from the environment win and
show locked. Installs that already had accounts never see the wizard. The API side is `GET /api/v1/setup`,
`POST /api/v1/setup/namespace`, `PUT /api/v1/setup/llm`, `GET /api/v1/setup/llm/detect` (model servers running on
this machine or the Docker host, with their models), `PUT /api/v1/setup/storage`, `PUT /api/v1/setup/oauth`,
`PUT /api/v1/setup/telemetry` and `POST /api/v1/setup/finish`
(admins); `GET /api/v1/auth/status` says whether it is still pending (`wizard_pending`).

## Frontend environment

| Variable | |
|---|---|
| `API_BASE_URL` | where the Next.js server reaches the API (`http://localhost:8000`, `http://backend:8000` in Docker) |
| `AUTH_SECRET` | encrypts the NextAuth session cookie (`npx auth secret`) |
| `AUTH_URL` | pins sign-in to one public URL of the web app. Leave it unset (the Docker Compose files do) so sign-in follows the address the browser is on: its LAN name, https:// address or Cloudflare tunnel |
| `AUTH_TRUST_HOST` | `true` behind a proxy or in Docker |
| `TRUST_PROXY_HEADERS` | `true` when a reverse proxy in front of the web app sets `X-Forwarded-Host`, `-Proto` and `-For`: they're passed on to the API, which names that address in OAuth discovery and reads the visitor's address for IP groups and throttles. Off, the web app reports the `Host` the browser sent and the address it was reached from |
| `LENS_WEB_PROXY_HOSTS` | hosts whose `X-Forwarded-For` the web app passes on with `TRUST_PROXY_HEADERS` off (`backend,worker` in the Docker Compose files, for the Cloudflare tunnel) |

## archive.yaml

Start from `fastapi_backend/archive.example.yaml`, which documents every key. The parts that can only be set there:

* `namespaces`: folder trees scanned by `lens scan`, and whether each joins the shared knowledge graph.
* `data_dir`: reports, frames, model caches, locks and (embedded mode) the database.
* `sources.rclone` and `sources.local_roots`: which binary is run for remote storage and which local folders may be
  watched. These are bootstrap-only on purpose, so the web app can't choose what runs or open up the server's disk.
* `video.yunet_model` / `video.sface_model`: face model files.
* `video.yolox_model` / `video.ultralytics_model`: the object detector's model ([Objects](#objects)).

## Settings in the app

Admins can change, in Settings: transcription, speaker separation, speech providers, voice IDs, analysis, the LLM
provider and key, a local model, the AI assistant, search (word search and search by meaning), reports and graph,
video, OCR, faces and objects, workers, sign-in, components, access and embedding (embed frame ancestors, transcript
upload limit, allowed hosts, trusted proxies, session length), remote access, email, chat rooms, notifications,
telemetry, the Fedora repository, sensors, uploads, storage, documents, API keys, and IIIF and metadata. The API
refuses an allowed-host list that leaves out the address you are using.

## Word search

Searching by the words goes through the index of `search.engine` (Settings → Search → Word search; how each works is
in [Database](database.md#schema)):

| Setting | Default | |
|---|---|---|
| `search.engine` | `sqlite` | `sqlite`: a SQLite FTS5 file per database in `data_dir/search/`. `surrealdb`: SurrealDB's own full-text index, which needs several times the memory. `opensearch`: an OpenSearch (or Elasticsearch-compatible) cluster |
| `search.opensearch_url` | empty | the cluster's address, such as `http://search.lan:9200`; with `opensearch` and no address, the built-in index is used |
| `search.opensearch_user` / `opensearch_password` | empty / none | for a cluster that asks for them; the password is stored encrypted |
| `search.opensearch_verify` | true | whether its certificate is checked; turn it off only for a self-signed one on your own network |
| `search.stemming` | `english` | `none` for archives that aren't in English |

After changing the engine or stemming, run `lens reindex` (or Settings → Search → Reindex now).

## Search by meaning

Passages of transcripts, pages and descriptions are embedded by an OpenAI-compatible embeddings server (`POST
/embeddings`), so search can find moments by meaning ([how it works](processing.md#search-by-meaning)). By default it
asks the LLM provider's server (`llm.base_url`, with its key) for `nomic-embed-text`, a small model that runs offline
in Ollama (`ollama pull nomic-embed-text`); set `embeddings.base_url` to use another server, such as Ollama next to
LM Studio, or llama.cpp's `llama-server --embeddings`. Without a server, search goes by the words as before.

```yaml
embeddings:
  base_url: http://localhost:11434/v1   # null: the LLM provider's
  model: nomic-embed-text
```

| Setting | Default | |
|---|---|---|
| `embeddings.enabled` | true | off: no passages are embedded, and searches go by the words |
| `embeddings.base_url` | null | the embeddings server; null: the LLM provider's (and its key). `LENS_EMBED_BASE_URL` sets it |
| `embeddings.model` | nomic-embed-text | the embedding model (`LENS_EMBED_MODEL`); e.g. mxbai-embed-large, bge-m3, all-minilm, text-embedding-3-small |
| `embeddings.api_key` / `api_key_env` | none | a key for the embeddings server, stored encrypted (`LENS_EMBED_API_KEY`), or the variable that holds it |
| `embeddings.min_similarity` | null | how alike (cosine, 0–1) a passage must be to count; null: what suits the model (0.52 for nomic-embed-text) |
| `embeddings.neighbours` | 40 | passages found per search, 5–500 (more when a page of results needs them) |
| `embeddings.passage_chars` | 800 | how long passages are, 200–4000 characters |
| `embeddings.batch_size` | 32 | passages sent per request, 1–256 |
| `embeddings.timeout` | 60 | seconds per request (a search waits at most 15) |
| `embeddings.query_prefix` / `document_prefix` | null | what searches and passages start with; null: what the model wants (`search_query: ` and `search_document: ` for nomic-embed-text, an instruction for mxbai and bge, `query: ` / `passage: ` for e5) |

Changing the model drops the stored vectors (another model's can't be compared) until recordings are indexed again. When the server fails to index (it's down, or
doesn't have the model), indexing jobs skip for ten minutes rather than each waiting on it, and the hourly routine
waits too; **Test** in Settings → Search, **Index now**, or `lens embed` try again at once.

## Chat models

People can choose the model a conversation uses, and ask a question again with another (Retry with another model).
`llm.chat_models` (Settings → LLM provider) lists the models they may pick, besides `llm.model`, which is always
offered and stays the default. Left empty, they may pick whatever the model server lists (`GET /models`, kept for a
minute); on providers that list many models, or charge by model, list the ones you want offered.

## Descriptions

The describe step ([API](api.md#descriptions)) sends each page of a document or an image, and each shot's keyframe of
a video (at most 1024 pixels on a side), to a model that can see images, and keeps what it says each shows, for
search and for people who can't see them. It uses the LLM provider's server (Settings → LLM provider) with a model you
choose there as one that can see images: OpenAI's, or a vision model on LM Studio, Ollama or vLLM (LLaVA, Qwen-VL,
Llama 3.2 Vision, Gemma 3 …). Lens can't tell which models see, so none is chosen until you choose one. Each page or
shot is a request, so a long document costs as many: `llm.describe_max` caps how many of a resource are described.

| Setting | Default | |
|---|---|---|
| `llm.vision_model` | none | the model that describes pages and shots; none: the describe step is skipped |
| `llm.describe_max` | 50 | pages or shots of a resource described at most, 1–1000 (in order: the rest aren't) |

## Uploads

Audio, video, documents (PDF, Office and OpenDocument files, text, Markdown, saved web pages and emails) and images
uploaded in the web app (Import → Upload) go up in pieces
([API](api.md#uploads)). Settings → Uploads:

| Setting | Default | |
|---|---|---|
| `uploads.max_mb` | 4096 | the largest file, in MB |
| `uploads.extensions` | the types folder scans import, documents and images | which types can be uploaded: any of `.m4a .mp3 .wav .flac .ogg .opus .aac .amr .aif .aiff .wma .mp4 .m4v .mov .mkv .webm .avi .mpg .mpeg .3gp .pdf .doc .docx .odt .rtf .ppt .pptx .odp .xls .xlsx .ods .txt .text .md .markdown .mdx .html .htm .eml .msg .jpg .jpeg .png .tif .tiff .webp .gif .bmp`. A list saved before documents and images came leaves them out until they're added |
| `uploads.chunk_mb` | 8 | how much the web app sends per request, 1–64. Keep it below the request-body limit of any proxy in front of the web app (nginx's `client_max_body_size` is 1 MB unless set) |
| `uploads.expire_hours` | 24 | how long an unfinished upload waits for its next piece before what arrived is deleted, 1–720 |

Pieces are written straight to `data_dir/uploads/.partial`, never held in memory; finished files are kept in
`data_dir/uploads/<namespace>/`, so give `data_dir` room for them (an upload is refused when it would leave less than
512 MB free). Transcript files have their own limit, `server.max_upload_mb` (Settings → Access & embedding), which
also caps a resource's supplementary files ([API](api.md#files)), kept in `data_dir/files/<resource>/`; watched
folders have none.

## Documents and images

A PDF uploaded as a document, or an image, has its pages drawn and read when its pipeline runs ([API](api.md#documents-and-images)).
Drawing pages and reading their text needs poppler-utils (`pdftoppm`, `pdftotext`, `pdfinfo`; in the Docker image);
without it a PDF's text is read by pypdf and it has no pages to look at. Pages without text are read by the OCR engine of
`video.ocr_engine` in the languages of `video.ocr_languages` (Tesseract is in the Docker image); without one, scans and
images have no text.

Other documents are made into PDFs first ([API](api.md#documents-and-images)): Word, PowerPoint and spreadsheet files,
OpenDocument and RTF by LibreOffice; text, Markdown, saved web pages (HTML) and emails by Chromium printing a page Lens
makes of them, or by LibreOffice where there's no Chromium. Outlook `.msg` emails also need the `msg` extra
(`pip install -e ".[msg]"`; extract-msg is GPL-3.0). The `lens:full` Docker image has LibreOffice and Chromium
([Deployment](deployment.md)); without them, PDFs and images are read as before, and files that can be transcripts
(Word, text, Markdown) are imported as transcripts. Settings → Documents:

| Setting | Default | |
|---|---|---|
| `documents.page_pixels` | 2000 | the longest side of a page's image, in pixels, 800–6000 |
| `documents.thumb_pixels` | 360 | the longest side of its thumbnail, 120–800 |
| `documents.ocr_below_chars` | 25 | a page with fewer characters of text than this is read by OCR, 0–5000 (0: never) |
| `documents.max_pages` | 2000 | the most pages of one document that are drawn and read, 1–50000 |
| `documents.convert_seconds` | 300 | how long making one PDF may take before its job fails, 10–3600 seconds |
| `documents.attachment_resources` | true | whether an email's attachments that Lens can read (documents, images, audio, video, emails) also become resources of their own; they're kept as its files either way |
| `documents.converter` | `auto` | who makes the PDFs: `auto` uses [anytopdf](#anytopdf) only for what LibreOffice and Chromium can't do here, `anytopdf` uses it for every document and image (and fetches it), `lens` never uses it |
| `documents.anytopdf_url` | none | a conversion node: another machine running `anytopdf queue serve` and `anytopdf queue work`, such as `https://convert.home:8640`. Set, it converts instead of the program here |
| `documents.anytopdf_token` | none | the node's bearer token (its `ANYTOPDF_QUEUE_TOKEN`), kept encrypted |

Set at startup only (the config file; the web app can't choose what the server runs):

| Setting | Default | |
|---|---|---|
| `documents.soffice` | `soffice` or `libreoffice` on PATH | LibreOffice |
| `documents.chromium` | the first of `chromium`, `chromium-browser`, `google-chrome`, `google-chrome-stable`, `chrome` on PATH | Chromium or Chrome (a headless shell works too); it also captures web pages ([API](api.md#web-pages)) |
| `documents.anytopdf` | the one Lens downloaded, else `anytopdf` on PATH | the anytopdf program |
| `documents.web_networks` | `[]` | networks (CIDR, such as `10.20.0.0/16`) that web pages and calendar feeds may be fetched from besides the public internet: for an intranet, or a calendar server at home; loopback and cloud metadata addresses stay out unless listed. `LENS_WEB_NETWORKS` in `.env` (comma-separated) adds to it, for Docker and the packages |

### anytopdf

[anytopdf](https://github.com/adeelahmad/anytopdf-rs) (MIT or Apache-2.0) is Lens's sister project: one static program
that makes documents, photos and media into searchable PDFs. Lens uses it as a converter, two ways:

- **Here.** With `documents.converter` set to `anytopdf`, Lens downloads release 0.3.0 for this machine on first use
  (Linux x86-64 and arm64, macOS), checks it against the release's checksum and keeps only the program in
  `data_dir/models/anytopdf-0.3.0/`. It makes the PDF of text, Markdown, web pages and emails from the same cleaned page
  Lens would print with Chromium, so a server without Chromium reads them, and it reads images too: a photographed page
  is found, straightened and flattened before OCR. Office files still need LibreOffice beside it.
- **On a conversion node.** With `documents.anytopdf_url` and its token set, every conversion goes to that machine as
  a job (only the file, named `document.<type>`), and Lens fetches the PDF. The node has LibreOffice and whatever else
  it needs, so a small server (a Raspberry Pi) reads Word, PowerPoint and spreadsheet files with nothing installed.
  Run it behind TLS off loopback (`anytopdf queue serve --tls-cert … --tls-key …`).

It runs with no network, no runtime plugins and no config file of its own, and its PDF has no provenance page and
holds only the document's text, so what Lens reads is what the document says. Faces, objects and speech stay Lens's
own steps. The resource's rendition says `anytopdf` made it.

Neither may reach anything while converting: Chromium goes through a proxy inside Lens that serves the page and refuses
every other request (the page also allows no scripts), and LibreOffice is given a proxy address that isn't there.
Chromium runs with its sandbox where it can, and without it as root or where the container lacks what the sandbox
needs; it's given no D-Bus to reach (it would wait on the services it asks there). The pages are JPEGs in `data_dir/frames/<resource>/` (a page of about 300 KB at the default size) and the PDF
made of a document is `data_dir/renditions/<resource>.pdf`; both go when the resource does. Workers listed in
`workers.steps` run them as part of `transcribe`.

## Text on screen and scans (OCR)

The OCR engine of `video.ocr_engine` reads text on a video's sampled frames (the ocr step) and on a document's pages
without text and on images (when they're transcribed, [above](#documents-and-images)):

* `auto`, the default: Apple Vision on a Mac worker, else Tesseract, else RapidOCR, whichever is there first.
* `tesseract`: Tesseract (in the Docker images), in the languages of `video.ocr_languages`.
* `apple-vision`: macOS's own (`pip install -e ".[mac-ocr]"`), for workers on a Mac.
* `rapidocr`: RapidOCR on ONNX Runtime (`pip install -e ".[rapidocr]"`).
* `doctr`: docTR (Apache-2.0), good on scans and photos of text; it reads the Latin alphabet. It runs on PyTorch:
  `pip install -e ".[doctr]"`, or `--build-arg EXTRAS="doctr"` for a Docker image (several GB). Its two models
  (`fast_base` and `crnn_vgg16_bn`, about 130 MB) are fetched the first time it reads, into `DOCTR_CACHE_DIR`
  (`~/.cache/doctr`); where the server can't fetch them, put them there from docTR's GitHub releases.
* `none`: nothing is read.

Without an engine (one that isn't installed, or models that can't be loaded) the ocr step is skipped and a document's
scans aren't read; the job says why, and so does a video's Text on screen tab. Settings → Video, OCR, faces and objects:

| Setting | Default | |
|---|---|---|
| `video.ocr_engine` | `auto` | `auto`, `tesseract`, `apple-vision`, `rapidocr`, `doctr` or `none` |
| `video.ocr_languages` | `eng` | Tesseract's language codes, one per line |
| `video.ocr_min_confidence` | 60 | lines on frames read with less confidence (0–100) are left out |

## Objects

The objects step finds the people, vehicles, animals and everyday things (the 80 kinds of the COCO dataset) on a
video's sampled frames and on a document's or an image's pages ([API](api.md#objects)), with the engine of
`video.object_engine`:

* `yolox`, the default: YOLOX on ONNX Runtime, both Apache-2.0. It needs ONNX Runtime (`pip install -e ".[objects]"`)
  and a YOLOX `.onnx` model as YOLOX's releases publish them. Lens fetches both, with YOLOX-s, the first time
  a job needs them ([Components](components.md)), and also finds a model put in `/opt/lens/models`.
* `ultralytics`: Ultralytics YOLO (`pip install ultralytics`). It's AGPL-3.0: a server that lets others use it must
  offer them its source, so it's in no image and no extra.
* `off`.

Without one the step is skipped, and its job says why. Settings → Video, OCR, faces and objects:

| Setting | Default | |
|---|---|---|
| `video.object_engine` | `yolox` | `yolox`, `ultralytics` or `off` |
| `video.object_min_score` | 0.4 | how sure the detector must be to keep what it found, 0.05–0.95 |

Set at startup only:

| Setting | Default | |
|---|---|---|
| `video.yolox_model` | the first `yolox*.onnx` in `/opt/lens/models` or the data folder's models | a YOLOX model: YOLOX-s (35 MB, the one fetched), or YOLOX-Nano and YOLOX-Tiny (smaller and faster, less sure) |
| `video.ultralytics_model` | `yolov8n.pt` | Ultralytics weights, a file or a name Ultralytics downloads |

On a CPU, YOLOX-s takes about a tenth of a second a frame. What's found is kept per kind and resource: where it's seen
and its boxes on each frame or page (at most 500).

## API keys

How long the API keys people make for scripts and other apps last ([Authentication](authentication.md#api-tokens)).
Settings → API keys:

| Setting | Default | |
|---|---|---|
| `tokens.default_days` | 90 | how long a new key lasts when its maker doesn't say, 1–3650 days (at most `tokens.max_days`) |
| `tokens.max_days` | 365 | the longest a key may last, 1–3650 days |
| `tokens.never_expire` | false | whether keys may be made that never expire |
| `tokens.oauth_enabled` | true | whether apps and AI assistants may sign people in with their Lens account ([OAuth](authentication.md#oauth)); off, discovery and registration are gone and apps' tokens stop working until it is back on, while people can still see and revoke the apps they allowed |
| `tokens.oauth_access_minutes` | 60 | how long the access token of an app someone signed in to lasts ([OAuth](authentication.md#oauth)), 5–1440 minutes; the app renews it by itself |
| `tokens.oauth_refresh_days` | 30 | how long such an app stays signed in after it last renewed its access, 1–3650 days (at most `tokens.max_days`) |

The limits apply to keys made after a change: keys made before keep their expiry; apps get the new lifetimes the
next time they renew. Settings → API keys also lists
everyone's keys (whose, what scope, when they expire and were last used), and an admin can revoke any of them; that's
audited as `token.revoke`.

## Notifications

Where namespaces may send notifications ([Notifications](notifications.md)). Settings → Notifications:

| Setting | Default | |
|---|---|---|
| `notifications.enabled` | true | off: nothing is sent, and what happens meanwhile isn't sent later |
| `notifications.networks` | `[]` | private networks targets may be in (`192.168.1.0/24`, `172.16.0.0/12` for Docker); without one, public addresses only |
| `notifications.app_url` | null | the web app's address, for links in messages; null uses `FRONTEND_URL` |
| `notifications.poll_seconds` | 5 | how often the notifier looks for news, 1–3600 |
| `notifications.max_attempts` | 6 | how many times a message is tried, 1–20 |

## Trusted proxies

IP groups ([Access](access.md#ip-groups)) match the visitor's address. The server takes it from the connection or,
when the connection comes from a trusted proxy, from the `X-Forwarded-For` header that proxy sends, reading from the
right past other trusted proxies. `server.trusted_proxies` (in the app: Settings → Access & embedding) lists them, as
addresses or CIDR ranges. The default trusts this machine (`127.0.0.0/8` and `::1`), which suits the web app and the
API on one machine.

* **List the web app.** The browser reaches the API through the web app, which tells it the address it was reached
  from in `X-Forwarded-For`. A visitor's own `X-Forwarded-For` is dropped, so they can't choose it; the web app
  passes one on only from a reverse proxy (`TRUST_PROXY_HEADERS=true`) or from the hosts in `LENS_WEB_PROXY_HOSTS`
  (the Cloudflare tunnel's containers). This is done by `lens-server.js`, which the production image starts; `next
  dev` and `next start` pass the header on unchanged. The Docker Compose files list the web app for you:
  `LENS_TRUSTED_PROXY_HOSTS=frontend,backend,worker` trusts those containers by name (their addresses, looked up
  every 30 seconds, follow them when they're recreated; `backend` and `worker` run the Cloudflare tunnel). Elsewhere,
  list the web app's address here, or name its host in `LENS_TRUSTED_PROXY_HOSTS`. Sign-in throttles follow the same
  address, so without it everyone behind the web app shares one.
* **Behind a reverse proxy, set `TRUST_PROXY_HEADERS=true` on the web app** and have the proxy set
  `X-Forwarded-For`: nginx with `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;`, or Caddy, which does by
  default. The proxy adds the real address last, and the server reads that one. Without the setting, every visitor
  shows up as the proxy's address.
* A request from an address that isn't trusted but carries `X-Forwarded-For` counts for no IP group, and neither does
  one from a trusted proxy that forwards nothing (the web app asking on its own behalf). A namespace's IP groups show
  your address as the server sees it, or say it can't tell.
* The API server's own proxy handling (uvicorn's `FORWARDED_ALLOW_IPS`, `127.0.0.1` unless set) decides the address in
  its logs; IP groups follow `server.trusted_proxies` either way.

## Security notes

* The API checks the `Host` header against `server.allowed_hosts` (stops DNS rebinding) and sends a strict
  Content-Security-Policy; only the player (`/embed/<id>`, `/s/<code>`) can be framed, and only by
  `server.embed_frame_ancestors`.
* Imports check the extension, cap the size (`server.max_upload_mb`), and are parsed in a temporary directory; nothing
  uploaded is executed. Uploads accept only the types in `uploads.extensions` (each is served back as audio or video, or
  as a download; documents and images only as downloads, and as page images Lens drew), under a name with no folders
  in it.
* Put everything behind HTTPS before exposing it beyond one machine. IIIF authorization requires it.
