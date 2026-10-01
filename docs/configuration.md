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
| `FRONTEND_URL` | `http://localhost:3000` | links in emails |
| `CORS_ORIGINS` | `["http://localhost:3000"]` | origins allowed to call the API from a browser |
| `OPENAPI_URL` | `/openapi.json` | `""` disables `/docs` and the schema |
| `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_FROM`, `MAIL_STARTTLS`, `MAIL_SSL_TLS`, `USE_CREDENTIALS`, `VALIDATE_CERTS` | | SMTP for password reset and for telling owners about requests for access; without `MAIL_SERVER` the links are logged |

## Frontend environment

| Variable | |
|---|---|
| `API_BASE_URL` | where the Next.js server reaches the API (`http://localhost:8000`, `http://backend:8000` in Docker) |
| `AUTH_SECRET` | encrypts the NextAuth session cookie (`npx auth secret`) |
| `AUTH_URL` | the public URL of the web app, when it can't be inferred |
| `AUTH_TRUST_HOST` | `true` behind a proxy or in Docker |

## archive.yaml

Start from `fastapi_backend/archive.example.yaml`, which documents every key. The parts that can only be set there:

* `namespaces`: folder trees scanned by `lens scan`, and whether each joins the shared knowledge graph.
* `data_dir`: reports, frames, model caches, locks and (embedded mode) the database.
* `sources.rclone` and `sources.local_roots`: which binary is run for remote storage and which local folders may be
  watched. These are bootstrap-only on purpose, so the web app can't choose what runs or open up the server's disk.
* `video.yunet_model` / `video.sface_model`: face model files.
* `video.yolox_model` / `video.ultralytics_model`: the object detector's model ([Objects](#objects)).

## Settings in the app

Admins can change transcription, diarisation, voice-ID thresholds, analysis, LLM provider and key, graph, search,
reports, workers, IIIF, the assistant, video, uploads, documents and images, and server options (embed frame ancestors, transcript upload
limit, allowed hosts, trusted proxies, session length). The API refuses an allowed-host list that leaves out the address
you are using.

## Chat models

People can choose the model a conversation uses, and ask a question again with another (Retry with another model).
`llm.chat_models` (Settings → LLM provider) lists the models they may pick, besides `llm.model`, which is always
offered and stays the default. Left empty, they may pick whatever the model server lists (`GET /models`, kept for a
minute); on providers that list many models, or charge by model, list the ones you want offered.

## Uploads

Audio, video, documents (PDF) and images uploaded in the web app (Import → Upload) go up in pieces
([API](api.md#uploads)). Settings → Uploads:

| Setting | Default | |
|---|---|---|
| `uploads.max_mb` | 4096 | the largest file, in MB |
| `uploads.extensions` | the types folder scans import, PDFs and images | which types can be uploaded: any of `.m4a .mp3 .wav .flac .ogg .opus .aac .amr .aif .aiff .wma .mp4 .m4v .mov .mkv .webm .avi .mpg .mpeg .3gp .pdf .jpg .jpeg .png .tif .tiff .webp .gif .bmp`. A list saved before documents and images came leaves them out until they're added |
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

Set at startup only (the config file; the web app can't choose what the server runs):

| Setting | Default | |
|---|---|---|
| `documents.soffice` | `soffice` or `libreoffice` on PATH | LibreOffice |
| `documents.chromium` | the first of `chromium`, `chromium-browser`, `google-chrome`, `google-chrome-stable`, `chrome` on PATH | Chromium or Chrome (a headless shell works too); it also captures web pages ([API](api.md#web-pages)) |
| `documents.web_networks` | `[]` | networks (CIDR, such as `10.20.0.0/16`) that web pages may be captured from besides the public internet: for an intranet; loopback and cloud metadata addresses stay out unless listed |

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
  and a YOLOX `.onnx` model as YOLOX's releases publish them. The `lens:full` image has YOLOX-s in `/opt/lens/models`,
  where Lens finds it without being told ([Deployment](deployment.md)).
* `ultralytics`: Ultralytics YOLO (`pip install ultralytics`). It's AGPL-3.0: a server that lets others use it must
  offer them its source, so it's in no image and no extra.
* `off`.

Without one the step is skipped, and its job says why. Settings → Video:

| Setting | Default | |
|---|---|---|
| `video.object_engine` | `yolox` | `yolox`, `ultralytics` or `off` |
| `video.object_min_score` | 0.4 | how sure the detector must be to keep what it found, 0.05–0.95 |

Set at startup only:

| Setting | Default | |
|---|---|---|
| `video.yolox_model` | the first `yolox*.onnx` in `/opt/lens/models` | a YOLOX model: YOLOX-s (35 MB, the image's), or YOLOX-Nano and YOLOX-Tiny (smaller and faster, less sure) |
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

The limits apply to keys made after a change: keys made before keep their expiry. Settings → API keys also lists
everyone's keys (whose, what scope, when they expire and were last used), and an admin can revoke any of them; that's
audited as `token.revoke`.

## Trusted proxies

IP groups ([Access](access.md#ip-groups)) match the visitor's address. The server takes it from the connection or,
when the connection comes from a trusted proxy, from the `X-Forwarded-For` header that proxy sends, reading from the
right past other trusted proxies. `server.trusted_proxies` (in the app: Settings → Access & embedding) lists them, as
addresses or CIDR ranges. The default trusts this machine (`127.0.0.0/8` and `::1`), which suits the web app and the
API on one machine.

* **List the web app.** The browser reaches the API through the web app, which passes on the `X-Forwarded-For` it
  received. In Docker, list the compose network: `docker network inspect` shows its subnet, and `172.16.0.0/12` covers
  Docker's default address pools.
* **Put a reverse proxy in front of the web app that sets `X-Forwarded-For`**: nginx with
  `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;`, or Caddy, which does by default. The web app can't
  tell a header a visitor made up from one a proxy set; the reverse proxy adds the real address last, and the server
  reads that one. Without it, a visitor can claim any address.
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
