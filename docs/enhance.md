# Enhancing images

Lens can look at each image before it's read and, when it's low quality, keep an enhanced copy that reading (OCR),
faces, objects, NSFW classification and descriptions work from instead. The original stays as it is. It's **off**
until an admin turns it on, and it runs on your own server: pictures aren't sent anywhere, and no model provider is
called.

Audio isn't enhanced yet.

## Turning it on

Settings → Enhance, **Enhance low quality images** (`enhance.images`). The **enhance** step comes first in the
standard pipeline, so it looks at images added from then on. A pipeline of your own runs it when you add the
**enhance** step to it; a step with **force** set enhances every image it runs on, whatever the settings and its
quality.

Any image can also be enhanced on request, even with it off: **Enhance** in its Details tab (editors),
`POST /api/v1/resources/{rid}/enhance`, the `enhance` tool for agents, or the `enhance` automation action. That makes
the copy and runs the rest of its pipeline again on it.

## What counts as low quality

The step measures the image and keeps why it looked low quality:

| Reason | When |
|---|---|
| small | its shorter side is under `enhance.min_side` pixels |
| blurry | its sharpness (the variance of its Laplacian, where it's sharpest) is under `enhance.min_sharpness` |
| noisy | its noise, estimated where it's plainest so edges and texture don't count, is over 6 levels out of 255 |
| low contrast | the spread of its tones is under 30 levels out of 255: flat, too dark or too bright |
| faces too small | a face on it is under `enhance.min_face_px` pixels tall, so recognition has little to go on |

Faces are looked for only where the namespace finds faces (its faces mode isn't off), with the face engine in
Settings → Video. With **Only images with faces** (`enhance.only_faces`), an image with no face is left alone.

An image with none of these is left as it is, and its job log says so: `good enough as it is (900×760, sharpness
312, noise 1.8, contrast 61)`.

## How it's enhanced

A small image, or one with small faces, is made bigger: enough to bring its shorter side to `enhance.min_side` and
its smallest face to `enhance.min_face_px`, at most `enhance.max_scale` times and never past 4000 pixels a side. The
engine (`enhance.image_engine`):

| Engine | What it does |
|---|---|
| `classic` (default) | Pillow, nothing to fetch: a noisy image is smoothed with a median filter, enlarged with Lanczos, a dull one stretched to its full range of tones, and a soft or enlarged one sharpened (unsharp mask) |
| `realesrgan` | [Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN) general x4v3 (BSD-3-Clause, about 5 MB, as Qualcomm AI Hub exports it to ONNX), run with ONNX Runtime on the CPU in tiles. It enlarges four times while taking out blur, noise and JPEG blocks, and the result is brought to the size wanted |

Real-ESRGAN and ONNX Runtime are fetched the first time the step runs with it chosen ([Components](components.md));
until then nothing is downloaded, so a Raspberry Pi that keeps `classic` stays small. An image over 1.5 megapixels is
enhanced by `classic` instead, so a small machine isn't swamped, and the job log says why.

## The enhanced copy

The copy is a JPEG kept beside the original (`data_dir/renditions/<id>-enhanced.jpg`), encrypted where the
namespace's files are, re-keyed with them, and deleted with the resource. Its Details tab says what was found and
made (`Small, blurry · 240×180 to 960×720 by the classic engine`) with **Save the copy**. Only members of the
namespace can download it; visitors to a shared resource see its pages as before.

Turning it off leaves copies alone until an image is processed again: then a copy the step made by itself is removed,
and one made on request is kept.

## Settings

| Setting | Default | |
|---|---|---|
| `enhance.images` | `false` | enhance low quality images |
| `enhance.image_engine` | `classic` | `classic` or `realesrgan` |
| `enhance.only_faces` | `false` | only images with a face |
| `enhance.min_side` | `720` | small below this many pixels on the shorter side (64 to 4000) |
| `enhance.min_sharpness` | `100` | blurry below this sharpness (0 to 10000; 0 never counts one as blurry) |
| `enhance.min_face_px` | `64` | faces smaller than this many pixels tall are too small (0 to 1024; 0 turns it off) |
| `enhance.max_scale` | `4` | enlarge at most this many times (1 to 4) |

## API and agents

- `POST /api/v1/resources/{rid}/enhance` (needs the process permission on the resource): enhance an image now and run
  its pipeline's later steps again; answers the `job`. 400 for anything but an image. Audited as `recording.enhance`.
- A resource carries `enhanced`: `{kind, low, measured, enhanced, engine, scale, width, height, forced, at, download}`,
  where `measured` is `{width, height, sharpness, noise, contrast, brightness, faces, smallest_face}` and `download` a
  signed link to the copy; `null` until the step has looked at it.
- `GET /api/v1/resources/{rid}/enhanced`: the copy (members of its namespace).
- [MCP](mcp.md): `enhance` (a write-scope token); `get_recording` says what the step found.
- [Automations](automations.md): the `enhance` action, on an asset.

## Refine later

- Enhance audio before it's transcribed: noise, level and clipping.
- Enhance a video's keyframes and a document's scanned pages, not only images.
- A face restorer for small faces, with a permissive license.
- Show the original and the copy side by side in the asset page.
