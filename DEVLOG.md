# Dev log

Plans and progress for work in flight. Newest first.

## 2026-10-04 · Activity history and budgets

Goal (Adeel): every change and every call coming in or going out is tracked, with its cost, down to the resource, so
each resource has an activity history. Pipelines, workflows and routines can be capped at a budget, and before a run
(or something larger) the assistant looks at where the budget stands and whether the run should go ahead.

Model:

- One ledger, `activity`: a row per call in (API requests that change something), call out (model, embeddings,
  decision model, webhooks, web tools) and run (a job, a routine run). Each row names every resource it touched
  (`routine:3`, `routine_run:12`, `job:40`, `recording:7`, `pipeline:2`, `workflow:4`, `chat:9`, `space:1`,
  `account:1`), with tokens, estimated cost (the prices in Settings), duration and outcome; never prompt or reply text.
  Who a call is for comes from the work it runs in (a scope), so a model call inside a job counts for the job, its
  recording, pipeline, namespace and the routine that queued it.
- A resource's history is its ledger rows plus its audit log entries. On by default, nothing to set up; kept
  `activity.keep_days` (365). Reads (GET) aren't logged unless `activity.reads` is on.
- Budgets are off unless set: a budget on a routine, pipeline, workflow or namespace caps cost (USD) and/or tokens per
  run, day, week or month. Before a routine run or a job, Lens estimates the run from past runs and checks what is
  left. Over budget, it asks you (the default: the run waits for your pick), skips, or lets the assistant weigh it (the
  decision model; unsure means ask). Doing nothing changes nothing: a waiting run stays waiting.

Todo:

- [x] Activity ledger: scopes, model/embedding/decision/webhook/web-tool calls, changing API requests, job and routine
      run totals, per-resource history and cost summary API, retention sweep
- [ ] Activity in the web app: a history panel on routines, pipelines, workflows and recordings; Settings → Activity
      and costs with totals by resource
- [ ] Cost in every view (Adeel): every resource, entity and view shows what it cost and its budget; a figure that
      isn't exact (no price for a model, tokens the server didn't report, a run's forecast) is marked as an estimate
      (`≈`, with why on hover). The API says `exact` / `estimate` per figure
- [x] Budgets: set per routine, pipeline, workflow or namespace; spent and estimate; checked before routine runs and
      jobs; over budget waits for your pick (run once, skip), or the assistant decides
- [ ] Budgets in the web app: set a budget, see where it stands, approve or skip a waiting run
- [x] Periodic check: warn at 80% and 100% of a budget, once per period (on the budget and in its history)
- [x] Local model costs (Adeel): price per model by tokens, by time (per hour) or off; off by default
- [ ] Budget warnings through notifications, and the assistant answering where budgets stand

Refine later: compute time as a cost (CPU seconds × a rate for local models), per-person budgets, forecasting from
schedules (a routine's next runs this period), budget alerts in the weekly digest.

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
- [ ] Explorer canvas: drag nodes, pan and zoom, pinch and long-press on touch; one-click layouts (force, BFS tree,
      DFS tree, radial) and reset; a custom route through picked nodes; right-click menu for parents, children,
      ancestors, descendants, neighbours and paths
- [ ] Questions in plain language: the question becomes Cypher (shown, editable), the answer lights up on the canvas
- [ ] Assistant and MCP tools: graph schema, query, related, paths; proposing changes behind an approval
- [ ] Topics as a controlled vocabulary (SKOS), apart from entities (authority records); asked Adeel when

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
