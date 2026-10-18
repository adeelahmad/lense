# Enhancing images and sound

Lens can look at each image before it's read and, when it's low quality, keep an enhanced copy that reading (OCR),
faces, objects, NSFW classification and descriptions work from instead. It can listen to each audio or video file the
same way before it's transcribed, and keep a cleaned-up copy of its sound for transcription to hear. The original
stays as it is. Both are **off** until an admin turns them on, and they run on your own server: pictures and sound
aren't sent anywhere, and no model provider is called.

## Turning it on

Settings → Enhance, **Enhance low quality images** (`enhance.images`) and **Enhance low quality sound**
(`enhance.audio`). The **enhance** step comes first in the standard pipeline, so it looks at images and recordings
added from then on. A pipeline of your own runs it when you add the **enhance** step to it; a step with **force** set
enhances everything it runs on, whatever the settings and its quality. Documents and imported transcripts skip it.

Any image or recording can also be enhanced on request, even with it off: **Enhance** in an image's Details tab, or
**Enhance and transcribe again** in a recording's (editors), `POST /api/v1/resources/{rid}/enhance`, the `enhance`
tool for agents, or the `enhance` automation action. That makes the copy and runs the rest of its pipeline again on
it. Transcribing again makes a new transcript, so the Details tab says how many corrected lines it would replace.

## Images

### What counts as low quality

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

### How it's enhanced

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

## Sound

### What counts as low quality

The step listens to the whole recording (a video's soundtrack too) at 16 kHz mono, as transcription does, in 30 ms
frames, and keeps why it sounded low quality:

| Reason | When |
|---|---|
| noisy | its loudest frames are less than `enhance.min_snr_db` above its quietest (its noise floor) |
| quiet | the level of its loud half is under `enhance.min_level_db` dBFS |
| clipped | more than 0.1% of its samples are at full scale |

A recording with none of these is left as it is (`good enough as it is (signal to noise 31 dB, level -21 dBFS, 0%
clipped)`), and a silent one is skipped. So is one whose transcript came with it, unless it's asked for, since nothing
would hear the copy.

### How it's enhanced

The engine (`enhance.audio_engine`):

| Engine | What it does |
|---|---|
| `ffmpeg` (default) | ffmpeg's own filters, nothing to fetch: `adeclip` repairs clipping, a high-pass filter takes out rumble and hum below 70 Hz, `afftdn` takes out steady noise down from the floor it measured, and a quiet recording is turned up towards -20 dBFS (as far as its peak allows) and evened out with `dynaudnorm` |
| `webrtc` | WebRTC's audio processing through [webrtc-noise-gain](https://github.com/OHF-voice/webrtc-noise-gain) (Apache-2.0; WebRTC is BSD-3-Clause): its noise suppression, high-pass filter and, for a quiet recording, automatic gain, 10 ms at a time. Its voice detector says how much of it is speech |

Asked for with nothing wrong, it gets a gentle clean-up of everything: the high-pass filter, light noise reduction
and `dynaudnorm`.

The copy keeps the original's times sample for sample, so the transcript's words land where they were said in the
original: a filter that puts its output late (`afftdn` does) is measured once with a probe of clicks and
bursts, and the copy is moved back by that much. Speakers are still told apart from the original, so voiceprints stay
comparable between recordings, and the player's waveform is the original's.

WebRTC's processing is fetched the first time the step runs with it chosen, on Linux on x86-64 or ARM64; until then,
and elsewhere, ffmpeg does it and the job log says why. Echo cancellation needs the sound of the far end of a call,
which a recording doesn't have, so neither engine does it.

## The enhanced copy

The copy is kept beside the original, a JPEG for an image and FLAC (16 kHz mono) for sound
(`data_dir/renditions/<id>-enhanced.jpg` or `.flac`), encrypted where the namespace's files are, re-keyed with them,
and deleted with the resource. Its Details tab says what was found and made (`Small, blurry · 240×180 to 960×720 by
the classic engine`, `Noisy · signal to noise 9.5 dB, level -24.1 dBFS · by ffmpeg's filters`) with **Save the
copy**. Only members of the namespace can download it; visitors to a shared resource see its pages or hear its media
as before.

Turning it off leaves copies alone until an image or a recording is processed again: then a copy the step made by
itself is removed, and one made on request is kept.

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
| `enhance.audio` | `false` | enhance low quality sound |
| `enhance.audio_engine` | `ffmpeg` | `ffmpeg` or `webrtc` |
| `enhance.min_snr_db` | `15` | noisy below this many dB of signal over noise (0 to 60) |
| `enhance.min_level_db` | `-35` | quiet below this level, dBFS (-70 to 0) |

## API and agents

- `POST /api/v1/resources/{rid}/enhance` (needs the process permission on the resource): enhance an image or a
  recording now and run its pipeline's later steps again; answers the `job`. 400 for a document or a transcript.
  Audited as `recording.enhance`.
- A resource carries `enhanced`: `{kind, low, measured, enhanced, engine, forced, at, download}`, with `scale`, `width`
  and `height` for an image and `filters` (ffmpeg) or `speech` (WebRTC) for sound. `measured` is `{width, height,
  sharpness, noise, contrast, brightness, faces, smallest_face}` for an image and `{duration, snr_db, level_db,
  floor_db, peak_db, clipped}` for sound, and `download` a signed link to the copy; `null` until the step has looked
  at it.
- `GET /api/v1/resources/{rid}/enhanced`: the copy (members of its namespace).
- [MCP](mcp.md): `enhance` (a write-scope token); `get_recording` says what the step found.
- [Automations](automations.md): the `enhance` action, on an asset.

## Refine later

- Enhance a video's keyframes and a document's scanned pages, not only images.
- A face restorer for small faces, with a permissive license.
- A speech enhancement model for heavy, changing noise (babble, music), with a permissive license.
- Enhance a video or episode imported by its address before it's downloaded (now: on request after).
- Play the enhanced sound in the player, and show the original and the copy side by side.
