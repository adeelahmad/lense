# Dev log

Plans and progress for work in flight. Newest first.

## 2026-10-04 · Graph history: versions, replay, rollback

Goal (Adeel): make the graph replayable and versioned.

Model (fastapi_backend/app/domain/graph_history.py, docs/graph-history.md):

- Every change to the curated entity graph is one append-only `graph_event`: its number is the graph's version. It
  says when, who (a person, a routine, the assistant, analysis), through what (web app, API token, OAuth app,
  assistant, MCP, routine, analysis), why, and for every record it touched (entity, alias, cross-namespace link,
  "not the same" pair) the row before and after.
- Built on what is there: graph changes (organize.py: proposed, applied, dismissed, undone) and merges (entity_merge)
  are recorded through the same entity functions, and their events point back at them.
- As of: the graph at any version, rebuilt by walking back from today's rows. Diff between two versions. An entity's
  history. Versions can be named ("before the cleanup").
- Rollback to a version: a preview first, then later changes are undone as new events, so a rollback can itself be
  undone. Merges are undone with their mentions.
- Replay: checkpoints (the graph at a version) plus the events after them rebuild the graph; verify compares the
  replay with the live rows and records any difference as an event, so nothing stays unrecorded.
- Not versioned here: mentions (analysis makes them from transcripts, whose edits have their own history). As-of views
  use today's mention counts.

Todo:

- [x] Event log with who, through what and why; every curation path records (entity page, assistant, MCP, routines
      and graph changes, analysis, the entity map)
- [x] Versions API: list, the graph as of a version, diff, an entity's history, named versions
- [x] Rollback to a version, with a preview
- [x] Checkpoints, replay and verify (`lens graph verify`, nightly checkpoint)
- [x] The graph API and Cypher as of a version (`as_of`); the graph cache is keyed on the graph's version
- [ ] An as-of picker in the explorer (with the graph explorer thread)
- [x] Web app: a history timeline with diffs and rollback (Routines › History; an entity's History button)
- [ ] Graph events in each resource's activity history (with the activity history thread)

Refine later: speakers (same-person links, names) in the history; a mention-level history.

## 2026-10-04 · anytopdf as the conversion engine (not started: wait for Adeel's go)

Goal (Adeel): use the sister project [anytopdf-rs](https://github.com/adeelahmad/anytopdf-rs) (README on its sprint2
branch) to turn any source into one searchable, cited PDF: OCR, captions, transcripts, keyframes and metadata as an
invisible text layer, with an embedded manifest, chunks, source anchors and provenance. Do not start until Adeel says go.

Why it fits: one static Rust binary (Linux x86_64 and arm64 musl, so a Raspberry Pi) that Lens can download as a
component, instead of LibreOffice and Chromium in the full image; provenance per fact matches "trusted, cited memory".

Todo (when started):

- [ ] Wait for anytopdf's JSON output (`--json`, schemas, exit codes) and `extract --json` manifest to land
- [ ] anytopdf as a component Lens installs itself (settings: auto, on, off), with its version recorded
- [ ] A pipeline / workflow node "Make evidence PDF": inputs a recording or resource, outputs the PDF as a rendition
      and its manifest chunks and anchors as Lens chunks (citations jump to time span, box or byte range)
- [ ] Use it where Lens has no converter first (images, captions, mixed folders); keep LibreOffice and Chromium as
      the default for Office, HTML and email until anytopdf reads them (its roadmap: PDF, HTML, EML, Office)
- [ ] Reuse Lens's own OCR and transcripts as sidecars rather than running them twice
- [ ] Run it with no network, size and time caps, plugins off unless an admin allows them (its plugins run with full
      user rights until its sandbox lands)

Refine later: anytopdf's intake channels (IMAP, webhooks, watched folders) and printing overlap Lens's sources and
sensors; decide which side owns them. MCP server mode could be an extension.
## 2026-10-04 · Notes: a page for everything

Goal (Adeel): every resource, entity and topic has its own page (its note), next to free notes written by people or
the assistant. The assistant is the main writer and organiser; people can do everything it can. Notes link to anything
with @ (resources, people, entities) and # (topics). Free notes sit in a tree in the left navigation, like Notion;
pages of resources are opened from those resources. docs/notes.md.

Model: a `page` has a title, a one-line summary (the context the assistant reads; refined by the AI when switched
on), a date, a place (PARA: project, area, resource, archive), a parent for the tree, the thing it's about (if any), a
body and who wrote it (a person or the assistant). Links found in the body are kept as edges, with backlinks, and show
in the graph.

Todo:

- [x] Pages: create, read, change, move in the tree, delete; a page per resource made on first open
- [x] @ and # mentions: links and backlinks, a search to pick what to link
- [ ] Web app: tree explorer in the left navigation, page view with title, summary, date, place and backlinks; the
      page of a recording or entity reached from its detail view
- [ ] Editor: BlockSuite (AFFiNE's block editor, OctoBase/Yjs documents) behind one component; plain text kept for
      search and the assistant. Waits on Adeel's link for the exact editor
- [ ] AI title and summary refinement (notes.refine, on by default, can be switched off); costs to the activity ledger
- [ ] Assistant and MCP tools: find, read, write, link and file notes; changes by the assistant are undoable
- [ ] Self-organising: a routine files notes into PARA and links them to entities and topics, proposing what it isn't
      sure of
- [ ] # topics move to the SKOS topics once the graph thread splits them from entities
- [ ] Attachments on a page, encrypted through the keyring
- [ ] Object storage (S3, or an rclone remote served as S3) in the setup wizard, no local storage; a downloadable
      256-bit storage key. Design proposal waiting on Adeel (changes existing installs)

## 2026-10-04 · The graph, end to end

Goal (Adeel): make the graph the one focus and nail it: a human explorer canvas, agent queries with rights, questions
in plain language, and a graph tool for the assistant. Scope is one namespace or every shared one. docs/graph.md.

Decision: agents query in Cypher (openCypher, the basis of ISO GQL), run by Lens's own engine over a per-caller
projection, read-only; changes are proposed graph changes. Not Gremlin (needs a JVM server, models write it worse),
not raw SurrealQL (lock-in, reaches any table), SPARQL stays over the RDF view.

Todo:

- [x] Property graph: namespaces, collections, recordings, speakers, entities; hierarchy and association relationships
- [x] Walk it: parents, children, ancestors, descendants, neighbours, every path / shortest paths
- [x] Read-only Cypher engine with a step and time budget; API with read (any token) and change (write + editor) rights
- [x] Explorer canvas: drag nodes, pan and zoom, pinch and long-press on touch; one-click layouts (force, BFS tree,
      DFS tree, radial) and reset; a custom route through picked nodes; right-click menu for parents, children,
      ancestors, descendants, neighbours and paths
- [x] Questions in plain language: the question becomes Cypher (shown, editable), the answer lights up on the canvas
- [x] Assistant and MCP tools: graph schema, query, related, paths; proposing changes behind an approval
- [ ] Topics as a controlled vocabulary (SKOS), apart from entities (authority records); next, after the graph PRs (Adeel)

## 2026-10-03 · Assistant extensions: tools, skills, hooks, plugins

Goal: the assistant can be extended to the same level by code, the canvas, voice or plain chat.

Model (fastapi_backend/app/domain/extensions.py, /api/v1/extensions):

- One registry, four kinds: tool (params, effect read/change, body prompt/http, later code and canvas graph), skill
  (when + instructions, read with `use_skill`), hook (message, before_tool, after_tool, answer: add context, block a
  tool, call a tool), plugin (a bundle of the three, shared and switched off as one).
- Versioned and shared like custom nodes (private / namespace / everyone); a version records how it was made (code,
  canvas, chat). Change tools wait for approval like the built-in ones; `ai.extensions` turns them all off.
- Manifests as code: Markdown with frontmatter (body = skill instructions or a prompt tool's prompt) or YAML/JSON.

Todo:

- [x] Registry, versions, sharing, manifests in and out, check without saving, try a tool
- [x] Prompt and web tools, skills, hooks in the chat tool loop; approvals for change tools
- [x] Code tools: Python in a process of its own (admins write them; time, CPU, memory limits, no secrets, own folder, no network unless allowed; stops when its owner is no longer an admin). Refine later: a container or gVisor sandbox for non-admins, pip packages per tool
- [x] Canvas: a `tool` workflow scope whose arg nodes are the tool's parameters (ask_model, call_tool nodes)
- [x] Canvas editor for tool graphs in the web app (Extensions → Draw a tool)
- [x] Authoring from chat and voice: the assistant drafts and saves extensions behind an approval card
- [x] Extensions page in the web app: list, manifest editor, switch on/off, share, try, versions
- [ ] `lens ext` CLI (push/pull manifests)

Refine later: hook-called tools don't show as steps; web tools reach ports 80/443 only and have no secret
references yet; extensions aren't offered over MCP yet; marketplace on top of plugins.

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
