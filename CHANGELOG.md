# Changelog

The backend (`fastapi_backend`) and the frontend (`nextjs-frontend`) are versioned together.

## Unreleased

- **Fix (security): only the server's own media links are signed.** Text shaped like a media link
  (`/api/v1/recordings/12/audio`) came back signed: titles and transcript lines in API responses, and anything in the
  embed and report pages, including the transcript data inside them. Someone who could rename a recording or correct
  its transcript could get a working link to the audio, video or frames of any recording, in namespaces they have no
  role in. Responses now sign only the fields that hold links; the embed page signs only its recording's links, and a
  report page only those of its namespace's recordings.
- **Library: filters, sorting and counts on the server.** The Library's filters, tabs and sorting now look at every
  recording you can read, not just the rows loaded so far, and the page no longer says it can't.
  `GET /api/v1/recordings` takes `q`, `status` (the recording statuses plus the job states `processing` and `failed`),
  `attention`, `processing`, `speaker`, `from`/`to`, `min_duration`/`max_duration`, `media` and `sort`, and says how
  many recordings match in the `X-Total-Count` response header (documented in `docs/api.md`).
    - The body is still the list of rows, so existing API clients keep working; the total is a header rather than a
      new envelope for that reason (it is exposed to cross-origin browsers too).
    - `from`/`to` are days (both included) and durations are seconds, so the API doesn't bake in the web app's ranges.
    - `speaker` takes speaker ids. Speakers belong to one namespace, so the web app's speaker filter lists everyone
      who speaks in the namespaces in scope by name and sends every id with that name.
    - The Needs attention and Processing tab counts come from the server too.
- **Delete recordings.** Owners delete recordings from the Library's bulk bar, after a confirmation that lists them;
  `DELETE /api/v1/recordings/{rid}`, audited as `recording.delete` (`docs/api.md`).
    - Everything Lens made from a recording goes with it: transcript, analysis, frames, outputs and reports, shares,
      permissions and requests for access, and its place in IP groups, fixed collections, chat scopes and batch runs
      that haven't reached it. Unnamed speakers and faces only it had go too; named ones stay.
    - The media file stays where it is, and isn't imported again: scans skip the same path or the same file elsewhere,
      watched folders skip the same remote file. Importing it on purpose brings it back.
    - Its waiting jobs are cancelled; a running job holds the delete back (409), and a worker whose recording is gone
      stops. A deleted recording's failed jobs can't be retried, batch runs continue without it, and undoing an entity
      merge no longer brings back mentions in deleted recordings. Public recordings show up as a Delete in IIIF change
      discovery; the namespace's report overview is rewritten in the background.
- **Rename recordings.** Editors rename a recording from the pencil next to its title (or ⋯ → Rename on a phone):
  `PATCH /api/v1/recordings/{rid}` with `title`, audited as `recording.rename`. The report page is renamed with it,
  so its link keeps working, and a report job rewrites the title inside; IIIF harvesters see an Update.
- **Access: public, restricted or private** (after Aviary's roles and permissions matrix; `docs/access.md`). Every
  recording is public, restricted or private, following its namespace's default unless it sets its own; a public one
  chooses which parts anyone may use (media, transcript, index) and can be featured. This replaces the four IIIF-only
  access levels.
    - **Converted on first start, once:** `public` stays public; `transcript` becomes public with the media closed;
      `signed-in` becomes restricted; `private` stays private. Namespace defaults convert the same way. Restricted
      recordings aren't published in IIIF (the old `signed-in` ones were listed), so the conversion announces a
      Delete for each in the change feed.
    - **Publishing is for owners**, as the web app already said: changing a recording's access, open parts or
      featured flag needs the owner role, through `PUT /api/v1/recordings/{rid}/access`, metadata edits, bulk edits
      and reverts alike (editors got a 403 only from the web app before). Changes are audited as `recording.access`.
    - `GET /api/v1/recordings/{rid}/access`; the recording list and detail carry `access`, `open` and `featured`,
      and the list filters by `access` and `featured`.
    - The web app shows a recording's access next to its status (click it to change it), marks public and
      restricted recordings in the Library, and the IIIF panel, metadata editor, metadata profile and Publish dialog
      use the new setting.
- **A recording's public page**, `/explore/recordings/<id>`, for anyone, signed in or not (Aviary's resource detail
  page). Visitors see a public recording's description and its open parts: the player, the transcript with find and
  downloads, the chapters; closed parts say who can open them. Signed-in people without permission see a restricted
  recording's title behind a lock; members see all of it, with a link back to the workspace. Restricted and private
  recordings are "not available" to everyone else. Owners copy the link from the Access dialog.
  `GET /api/v1/public/recordings/{rid}` answers with only what the caller may use (`docs/api.md`).
- **Explore: the public home page and collections** (Aviary's home and collection splash pages). `/explore` shows
  the featured public recordings, to everyone, and the collections the visitor can browse; a collection's page shows
  its description and recordings: public ones for everyone, restricted ones behind a lock ("content locked") for
  signed-in people, all of them for members. The sign-in page links there. `GET /api/v1/public/home` and
  `GET /api/v1/public/collections/{name}`.
- **Explore: search** (Aviary's search results page). `/explore/search` finds recordings by their title, and by what's
  said in the transcripts the visitor may read, with the matching lines; each line opens the recording at that moment
  (`?t=`). Restricted recordings (for signed-in people, locked) and closed transcripts match on the title only, so a
  search never reveals what they say. A search box sits on the home page and in the header.
  `GET /api/v1/public/search`; `search()` can be limited to a set of recordings.
- **Permission on a recording** (Aviary's "registered user with view permission"). Owners give a person with an
  account permission on one recording from its Access dialog, by email address, and take it away there (audited).
  They then see all of it on the pages for visitors and in IIIF, whatever its access; private recordings are listed
  for them, and the home page shows what's shared with them. `GET`/`POST /api/v1/recordings/{rid}/permissions`,
  `DELETE /api/v1/recordings/{rid}/permissions/{account}`.
    - Fix: IIIF resources of a recording that isn't public now also open with the IIIF access cookie, and the
      signed links the auth probe hands out work for them (they answered 404, so viewers couldn't play them).
- **Asking for access** (Aviary's "request access"). Signed-in people without permission ask a recording's owners for
  access from its page: to a public recording's closed parts, or to a restricted one, with an optional message; the
  page shows where their request stands. Owners hear of it by email (when mail is set up), in the recording's Access
  dialog and on Home under Needs attention, and approve (which gives permission) or decline, audited.
  `POST /api/v1/public/recordings/{rid}/request`, `GET /api/v1/recordings/{rid}/requests`,
  `POST …/requests/{account}/approve|decline`, `GET /api/v1/access-requests`.
- **IP groups** (Aviary's "public user with view permission in an IP group"). Owners name address ranges on their
  namespace's page (a reading room, a campus) that open every recording in the namespace, or the ones chosen in each
  recording's Access dialog. Visitors from those addresses see what a group opens without signing in: in collections,
  search, a recording's page ("You're connecting from Reading room") and IIIF, the Authorization Flow's probe
  included. Groups and choices are audited. `/api/v1/namespaces/{name}/ip-groups` (list with your address as the
  server sees it, add, change, delete) and `/api/v1/recordings/{rid}/ip-groups` (open or close one recording).
    - The visitor's address comes from `X-Forwarded-For` only when the request arrives from a trusted proxy: the new
      `server.trusted_proxies` setting (Settings → Access & embedding; default this machine). A proxy that isn't
      trusted, or the web app asking on its own behalf, counts for no group. Behind the web app, a reverse proxy that
      sets the header is needed, or visitors could claim any address (`docs/configuration.md`, Trusted proxies).
    - Server-side page titles for visitors pass their address on, so a reading room sees the right titles.

Tooling:

- `make openapi`, the dev watcher and the pre-commit hook write the same client: the generator reads its input as an
  absolute path now (given a bare `openapi.json`, it had baked that name into the client's `baseUrl` type).

## 0.3.0 <small>September 30, 2026</small> {id="0.3.0"}

The web app implements the Lens Archive design (built on the Aladdin design system).

- **Foundations:** design tokens with a dark theme and speaker and emotion palettes, self-hosted fonts (DM Sans,
  Source Serif 4 for transcripts, JetBrains Mono), and the design's components: buttons that explain why they're
  disabled, status, role, speaker, emotion and job-step chips, secret fields, tables with sorting and paging, dialogs,
  drawers, toasts, banners, the four-colour step loop and verdict cards.
- **App shell:** collapsible navigation, namespace switcher, ⌘K palette, live activity drawer, account menu with
  appearance and keyboard shortcuts.
- **Screens:** Home, Library, Import, Reports; the audio and video recording pages with the player; Search; Chat with
  citations, tool steps and approvals; Speakers; Graph; Batch runs and Collections; Activity; Sources; Pipelines and
  Templates; Sharing and Embed; Settings; Admin (people, members, audit, health); IIIF metadata and publishing;
  sign-in, first-run setup, password reset, API tokens, and signed-out-mid-task recovery.
- Every screen follows the role rule (actions you can't take are disabled with a reason; namespaces you have no role
  in never appear), has loading, empty and error states, and works in dark mode and at phone width.
- The web app forwards API, media, embed, IIIF and report paths at request time, so one build works against any
  API address.
- Features the design shows but the API doesn't support yet are disabled with a reason and listed in
  `docs/backend-gaps.md`.

Backend:

- Fix: on the embedded engine, search silently missed most segments after a restart (the full-text index lost
  entries on reopen). The index is now repaired once per process before the first search.
- Fix: IIIF "Open in" viewer links can be saved in Settings (only http(s) URLs are accepted).
- The job event stream opens immediately.

Tooling:

- CI reports coverage in each run's summary and keeps the reports as artifacts (Coveralls needs a paid plan for
  private repositories). The pnpm version comes from the frontend's `package.json`.
- pre-commit installs only the dev tools (the processing extras include macOS-only packages), and every hook passes:
  the backend is formatted with Ruff throughout, the frontend with Prettier at 120 columns.

## 0.2.0 <small>September 30, 2026</small> {id="0.2.0"}

Lens moves from a single-process prototype to a platform on the Next.js FastAPI template.

- **Backend**
    - The prototype's processing engine now lives in `app/domain`, unchanged in behaviour.
    - The API is split into routers under `/api/v1` with Pydantic request and response models, so the OpenAPI schema
      and the frontend's typed client cover every endpoint.
    - Authentication: short-lived JWT access tokens and rotating refresh tokens (with reuse detection) for NextAuth,
      replacing session cookies and CSRF tokens; password reset by email; API tokens unchanged.
    - Media is served through signed links, since `<audio>` and `<img>` can't send bearer tokens.
    - SurrealDB: a connection pool for servers, and automatic retries of write conflicts.
    - Workers run as their own process (`lens worker`); the API can still run them inline for development.
    - Removed the template's Postgres, SQLAlchemy, Alembic, fastapi-users and Vercel backend deployment.
- **Frontend**
    - NextAuth (Auth.js v5) with a credentials provider backed by the API, token refresh, first-run setup and password
      reset. Public registration is gone: admins invite people.
    - A typed API client wired to the session, and rewrites so media and IIIF are served from the web app's origin.
- **Operations**
    - Docker Compose for development (SurrealDB, API, worker, web app, MailHog) and a production-shaped compose file.
    - CI runs lint, type checks, an OpenAPI drift check and the tests against embedded SurrealDB and a SurrealDB server.
