# NSFW classification

Lens can tell which videos, documents and images have NSFW pictures in them (nudity and sexual content), with a model
that runs on your own server. It's **off** until an admin turns it on for every namespace, or a namespace's owner for
theirs. Pictures never leave the server for it, and no model provider is called, so it costs nothing per picture.

## Turning it on

- **Every namespace:** Settings → NSFW, **Classify NSFW pictures** (`nsfw.enabled`).
- **One namespace:** its page (Admin → Namespaces → the namespace → NSFW), or `PUT /api/v1/namespaces/{name}/nsfw`
  (owners). A namespace can also stay off while the rest is on, and have its own threshold and display.

Once it's on, the **nsfw** step in the standard pipeline classifies what's added from then on. Resources that were
there before are classified when someone asks: **Classify existing** on the namespace's page, `classify: true` with the
`PUT`, or `POST /api/v1/namespaces/{name}/nsfw/classify` (editors). A pipeline of your own runs it when you add the
**nsfw** step to it.

The first time a resource waits at the step, the worker fetches the model and ONNX Runtime (about 105 MB together,
[Components](components.md)); until then nothing is downloaded, so a Raspberry Pi that never turns it on stays small.

## What it does

The nsfw step scores each of a video's sampled frames (from the `shots` step), each page of a document, or the image,
from 0 (safe for work) to 1, and keeps the highest. A long video or document has at most `nsfw.max_frames` pictures
scored, spread evenly over it. A resource is **flagged** when its highest score is at least the namespace's
threshold. Changing the threshold flags or unflags what was already classified at once, without running the model
again.

The model is [AdamCodd/vit-base-nsfw-detector](https://huggingface.co/AdamCodd/vit-base-nsfw-detector) (Apache-2.0):
a Vision Transformer fine-tuned to tell NSFW from SFW pictures, as its author's 8-bit ONNX export, pinned to one
revision and checked against its SHA-256. It was trained to be strict: pictures with a lot of skin count as NSFW. It
is less sure of generated pictures. So a person can **mark a resource NSFW or not** by hand (editors), and their call
wins over the score until they hand it back to the model.

## How flagged resources show

`nsfw.display`, or the namespace's own:

| Display | In the web app |
|---|---|
| `blur` (default) | its pictures (the video, frames, faces, pages and photo search results) are blurred until someone chooses to see them |
| `hide` | left out of the Library unless you ask for them, and blurred when shown |
| `show` | shown as they are, with a label |

A flagged resource has an **NSFW** label in the Library, and its page says what was found, with **Show pictures**
and, for editors, **Not NSFW**. Its Details tab says what classification found on any classified resource, with
**Mark NSFW**. Every mark comes with Undo. Where it's on in a namespace you can read, the Library has an **NSFW**
filter: as each namespace shows them, including hidden ones, only flagged ones, or leaving them out
(`/library?namespace=…&nsfw=only` opens it so).

Blurring and hiding are display preferences, not access control: anyone who may read a resource can still open it,
see its pictures and read its classification.

## Settings

| Setting | Default | |
|---|---|---|
| `nsfw.enabled` | `false` | classify in every namespace that doesn't say otherwise |
| `nsfw.threshold` | `0.8` | flag a resource when one of its pictures scores at least this (0.5 to 0.99) |
| `nsfw.display` | `blur` | `blur`, `hide` or `show` |
| `nsfw.max_frames` | `300` | the most pictures scored per resource (1 to 5000) |

A namespace's own `enabled`, `threshold` and `display` win over these; `null` follows them again.

## API and agents

- `GET /api/v1/namespaces/{name}/nsfw`: what applies in a namespace, what it chose itself, the archive's settings, and
  how many resources are classified, flagged and not classified yet.
- `PUT /api/v1/namespaces/{name}/nsfw {enabled?, threshold?, display?, classify?}` (owners). Audited as `namespace.nsfw`.
- `POST /api/v1/namespaces/{name}/nsfw/classify` (editors): queue the nsfw step for what isn't classified yet.
- `PUT /api/v1/resources/{rid}/nsfw {flagged}` (editors): `true`, `false`, or `null` to let the score decide. Audited
  as `recording.nsfw`.
- Resources, their player and Library rows carry `nsfw`: `{flagged, score, manual, blur, display, threshold, checked,
  of, at}`, and on a resource and its player `hits`, its highest-scoring pictures with signed links. It's `null` where
  classification is off in the namespace or the resource wasn't classified.
- `GET /api/v1/resources?sensitive=only|exclude|include` filters the Library, and `GET /api/v1/resources/nsfw` says
  whether it's on in any namespace you read, how many are flagged, and how many of those are hidden. Search hits and
  photo search results carry `nsfw: true` for the ones the web app blurs.
- [MCP](mcp.md): `nsfw_settings`, `set_nsfw` (owners for a namespace, admins for the archive) and `mark_nsfw`
  (editors); `get_recording` and `list_recordings` say what's flagged.

The resource's job log says what the step found: `flagged NSFW: highest score 0.93 (threshold 0.8) over 1 page(s)`.

## Refine later

- Blur flagged pictures for visitors on public pages, embeds and IIIF, as faces can be pixelated.
- Keep the Library's NSFW filter in saved views.
- Skip sending flagged pictures to a cloud vision model (the `describe` step).
- Other models (a smaller one for a Raspberry Pi, one trained on generated pictures) to choose from.
- Scores per picture in the player, to jump to what was flagged.
