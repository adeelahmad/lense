# Dev log

Plans and progress for work in flight. Newest first.

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
