# Dev log

Plans and progress for work in flight. Newest first.

## 2026-10-03 · Typed decisions (Jev)

Goal: a decision layer next to the LLM and the embedding model. Lens asks a System One model typed questions (yes/no
with a probability, a choice among options, a score) about its own content, and code acts on the answers with
thresholds: what's sure is applied, what isn't is left for a person. Decided with the project owner: either provider
by setting (TypeSafe's hosted Jev, or any server that speaks the same `POST /v1/systemone`), off until configured;
first features are reranking search, tagging and routing imports, moderation flags, and MCP tools that answer and
check natural-language questions.

Model:

- `decisions` settings: `enabled`, `base_url` (default `https://api.typesafe.ai/v1`), `model` (`jev-latest`),
  `api_key` (secret; left out of the request when unset, for gateways that add it), timeouts and thresholds.
  `LENS_DECISIONS_*` in the environment. A `decide.py` client: one request carries the state and named questions;
  answers come back as probabilities with a confidence. Never raises into a request path: no answer means the
  feature steps aside.
- Rerank: the top hits of a search get one yes/no each ("does this passage answer the query?"), batched; the
  probability reorders them and is shown. Hybrid ranking stays the fallback.
- Tag and route: a `classify` pipeline step (and workflow node) asks for tags from the namespace's vocabulary, the
  subtype and the collection; answers above the threshold are applied, the rest are suggestions on the resource.
- Moderation: comments (and public-facing text) get typed flags (spam, abuse, personal data); owners see a review
  queue; nothing is hidden automatically.
- MCP: `ask` (a typed question about resources or passages, answered with probabilities and the passages it rests
  on) and `check` (does this statement follow from these passages?), so an agent can validate its own answers.

Todo:

- [x] Read main: search/semantic, settings UI (workflows, comments and MCP tools: read with their slices)
- [x] `decide.py` client on TypeSafe's Python SDK (`typesafe-sdk`), settings and validation, env overrides, fake System One server for tests
- [x] Settings → Decisions in the web app: provider, key, model, test, thresholds
- [x] Rerank search (API `rerank`, relevance on hits in the web app)
- [x] MCP search uses the reranked order
- [ ] Chat retrieval uses the reranked order
- [ ] `classify` step and workflow node: tags, subtype, collection; suggestions on the resource
- [ ] Moderation flags on comments with an owners' review queue
- [x] MCP `ask` and `check` tools (offered only where `decisions.mcp` is on)
- [ ] Docs (configuration, processing, api, mcp), tests on both engines, browser checks
- [ ] Live check against hosted Jev once a key reaches this environment (2026-10-03: a request without a header is
      refused with "Must supply an API key", and no key or gateway is visible from this shell)

Left from the earlier branch `claude/oauth-search-mcp-telemetry` (not merged; main got its own OAuth, MCP and search
by meaning meanwhile): analytics per namespace and collection, and the new logo with the name "Lens". To port onto
main as their own PRs.

## 2026-10-02 · Routines

Goal: run syncs, pipelines and workflows on a schedule, including a daily LLM pass that organises the entity graph per
namespace or across all of them.

Model:

- A routine has a cron schedule (or none: by hand), a time zone, namespaces (or all) and ordered actions: sync,
  pipeline, workflow.
- Workflows get a scope. Graph workflows run over namespaces and are drawn on the same canvas: candidates → ask the
  model → filter → apply changes. Sure pairs are merged or linked, the rest proposed; everything is undoable.

Todo:

- [x] Cron schedules with time zones (no new dependency)
- [x] Routines: CRUD, run now, runs with results and logs, claimed so they run once
- [x] Scheduler thread in the API process; `lens watch` runs routines too
- [x] Graph scope for workflows; candidates, llm_judge, filter, apply_changes nodes
- [x] Graph changes: proposed / applied / dismissed / undone; undo a whole run
- [x] Seed the graph workflow and a nightly routine (off)
- [x] API tests
- [x] Web app: Routines page, run history, proposed changes review, graph nodes on the canvas
- [ ] Notify (Matterbridge/webhooks thread) when a run fails or leaves proposals
- [ ] More graph nodes: retype entities, hide noise, cluster topics
- [ ] Routing rules (shared IMAP inbox → namespaces) slot in between sync and ingest

## 2026-10-02 · Workflow canvas

Goal: design custom workflows on a canvas and attach them to pipelines.

Model:

- A namespace picks a pipeline per content type (audio, video, document, image, transcript), falling back to its
  default pipeline, then the built-in one.
- A pipeline's steps produce assets (transcript, shots, OCR text, faces...).
- Workflows are versioned node graphs that produce metadata (named outputs, custom field values). They are shared:
  a pipeline attaches any number of them, each pinned to a version and optionally with a condition, and they run
  after the pipeline's own steps.

Todo:

- [x] Read how pipelines, steps, outputs and custom fields work today
- [x] Workflow domain: graph validation (node types, ports, no cycles), create, versions, get, list
- [x] Workflow runner: input, llm, condition, pick, merge, output and field nodes, run in graph order
- [x] Entity nodes: extract (rules: built-in extractor + terms + regex), extract (LLM, structured), save entities
- [x] Pipeline graphs: asset steps and workflows as nodes, edges = runs after; ordered into steps for the job runner
- [x] Default pipeline drawn as a chain of today's steps, so nothing changes until a graph is edited
- [x] `workflow` step type in jobs, so a run records each workflow like any other step
- [x] Pipelines attach workflows as `workflow` steps (`{type: workflow, workflow, version?, when?}`), pinned when queued
- [x] Namespaces choose a pipeline per content type (`pipelines: {video: id, ...}`), reworked below into subtypes
- [x] API: `/workflows` (catalog, create, get, versions, run on a recording)
- [x] Tests for the API, validation and a run end to end
- [x] Canvas editor in the web app (pipelines and workflows): drag nodes, connect ports, node settings, I/O, save versions
- [x] Attach workflows from the pipeline editor
- [x] Content-type mapping on the Pipelines page (By content type)
- [ ] Asset converter steps (video → audio, document → images) as pipeline steps
- [ ] Workflow node types: HTTP call, template render, entity filter
- [x] Docs: processing.md section on workflows

Content types (2026-10-02, follow-up in the same PR):

- [x] Four base types (video, audio, image, text), derived from the file; transcripts, documents and web pages are text
- [x] Subtypes (the vocabulary) under a base type: label, description, pipeline, rules (extensions, filename pattern,
      length); built-in defaults, editable and removable, plus your own
- [x] A resource's subtype: chosen at import/upload or on the resource, else the first subtype whose rules match, else
      the base type's general subtype
- [x] Pipeline resolution: chosen for the run > namespace override for the subtype > subtype's pipeline > namespace
      default > standard
- [x] API: /content-types (list, create, update, delete), set a recording's subtype
- [x] Web app: Content types tab under Pipelines; per-namespace overrides by subtype; picker on the resource's Details
- [ ] Choose the content type at import and upload (today: on the resource, or recognised by rules)

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
