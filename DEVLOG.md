# Dev log

Plans and progress for work in flight. Newest first.

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
- [ ] Workflow domain: graph validation (node types, ports, no cycles), create, versions, get, list
- [ ] Workflow runner: input, llm, condition, pick, output and field nodes, run in graph order
- [ ] `workflow` step type in jobs, so a run records each workflow like any other step
- [ ] Pipelines attach workflows (`workflows: [{workflow, version?, when?}]`), appended as steps when resolved
- [ ] Namespaces choose a pipeline per content type (`pipelines: {video: id, ...}`)
- [ ] API: `/workflows` (catalog, create, get, versions, run on a recording)
- [ ] Tests for the API, validation and a run end to end
- [ ] Canvas editor in the web app: drag nodes, connect ports, edit node settings, save versions
- [ ] Attach workflows from the pipeline editor
- [ ] Content-type mapping in the namespace settings UI
- [ ] Asset converter steps (video → audio, document → images) as pipeline steps
- [ ] Workflow node types: HTTP call, template render, entity filter
- [ ] Docs: processing.md section on workflows
