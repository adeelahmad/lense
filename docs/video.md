# Video

Video files (mp4, mov, mkv, webm, m4v, avi) go through the same pipeline as audio. The transcript comes from the
soundtrack, and three more steps look at the picture. Each step skips itself for audio-only recordings.

- `shots`: ffmpeg finds scene cuts, saves a keyframe for each shot, and samples a frame every `video.sample_seconds`.
  Cuts between slides that share one template are subtle, so lower `video.scene_threshold` for slide decks.
- `ocr`: reads the text on screen from the sampled frames. The same line on consecutive frames becomes one span, with
  the time it's visible and where it sits on the frame.
  - Engines: Tesseract (installed in the Docker image), Apple Vision on a Mac (`pip install -e ".[mac-ocr]"`, run it
    in a worker on the Mac), or RapidOCR.
  - On-screen text is searchable (results say whether a hit was said or shown), cited by chat, and correctable
    (`PATCH /api/v1/resources/<id>/ocr/<span>`).
- `faces`: detects faces in the sampled frames, and on the pages of documents and images, where a face's track says
  which pages it's on rather than its time on screen ([API](api.md#documents-and-images)).
  - Where a namespace allows it, faces are grouped per recording and matched against that namespace's people, the way
    voices are: auto-match, review, or new.
  - When a face is on screen while one speaker talks, the app suggests they're the same person.
  - Faces can be renamed, merged (with undo), linked to a person in People, marked "not a face" or deleted, and the
    ones nobody has named yet are [named in a row](#naming-faces).
  - Engines: OpenCV's YuNet + SFace (`pip install -e ".[faces]"`, then point `video.yunet_model` and
    `video.sface_model` at the ONNX files from the OpenCV Zoo), InsightFace, whose pretrained models are licensed for
    non-commercial research only, or `anytopdf`: [anytopdf](configuration.md#anytopdf)'s plugins find faces with
    their built-in YuNet and describe them with the same SFace file, so it needs neither OpenCV nor Python packages
    (Lens fetches anytopdf and SFace on first use). Its faces match ones OpenCV found, so switching keeps the people a
    namespace knows. It looks at up to 50 frames or pages per run, sandboxed and offline; anytopdf's own face index
    is thrown away after each run, since who someone is stays Lens's.

Faces are biometric data, so they are off by default. A namespace owner chooses:

- `detect`: boxes and screen time, but no identities and no face descriptors kept.
- `recognize`: identities. This requires a stated purpose, which is recorded with who set it.

`PUT /api/v1/namespaces/<ns>/faces/mode` sets the mode; with `reprocess` it queues the faces step for the namespace's
videos, documents and images.

- Leaving recognition deletes the namespace's face descriptors.
- Turning faces off deletes all face data.
- Owners can delete one person's face data or a whole namespace's.
- Every change is audited.
- Face crops follow the recording's access rules, and faces never go into IIIF unless `video.publish_faces` is on.
- An owner can have the faces found **pixelated for visitors** (`pixelate` on the same call): in the pictures a
  request without a role in the namespace gets (public pages, embeds, share links, IIIF), each face found becomes a
  few blocks, a margin around it too, on frames, keyframes, pages and thumbnails. Members see the pictures as they are,
  signed in or through the links the API signs for them (marked `full=1`, signed with the rest). It goes by the
  faces found, so it needs detection on; turning faces off turns it off too. Faces missed by the detector, and tracks
  an editor marked as not a face, aren't pixelated.

A published video recording becomes a IIIF Video on a Canvas with its width, height and duration. It gets a thumbnail,
a "Text on screen" annotation layer targeting `#xywh=…&t=…`, and its shots as a Range.

## Playable copies

Phones and cameras write files many browsers can't play: HEVC video in a .mov, AVI, MPEG transport streams, AMR voice
memos, Windows Media. Turn on Settings › Video › Playable copies (`video.playable_copies`, off by default, since
transcoding a long video keeps a small machine busy for a while) and the transcribe step makes one copy of such a
recording's media, even when its transcript came with it:

- video becomes H.264 at most `video.playable_height` lines high (360, 480, 720 or 1080; 720 by default) with AAC sound
  in MP4, and sound alone becomes AAC in MP4;
- the copy is kept in `data_dir/playable/`, encrypted like the rest when the namespace is, and deleted with the
  recording;
- the player (`/api/v1/recordings/<id>/audio`) gets the copy, while `/api/v1/recordings/<id>/media`, IIIF and feeds
  still give the file itself;
- a file browsers already play (H.264, VP8, VP9 or AV1 video; AAC, MP3, Opus, Vorbis, FLAC or PCM sound, in MP4,
  WebM, Ogg, MP3, FLAC or WAV) gets no copy.

Recordings transcribed before the setting was on get their copy when their transcribe step runs again.

## Naming faces

In recognize mode, the faces nobody has named yet wait in **Settings → People → Unnamed faces** (the tab shows only
where faces are recognised; a video's People tab links to it while any are unnamed). The largest group comes first:
the face in the most assets, then the longest on screen. Each shows a few crops, how often it's seen and the assets
it's in.

- The first face's name field has focus, filled with the name Lens offers when it has one (the voice the face is
  linked to, or a named face or voice suggested for it) and selected, so typing replaces it.
- Enter names it and moves on to the next. The notice that follows has Undo.
- A name another face of the namespace already has (any case) joins that person: it's the same merge as "Same
  person", and Undo takes it back. Any other name becomes the face's own, everywhere in the namespace.
- Naming needs the curate permission (editors); members who can't see the reason on the button.

Agents do the same with the MCP tools `list_unnamed_faces` and `name_face`, or `GET
/api/v1/namespaces/<ns>/faces/unnamed` and `POST /api/v1/faces/<id>/name` ([API](api.md#video)).

## The video recording page

Video recordings open in their own layout.

- **The video:** yellow boxes mark text on screen and blue, labelled boxes mark faces. Both can be switched off.
- **Timeline:** lanes for shots (with their frames), speakers, text on screen and each person on screen (up to eight),
  with a playhead. Clicking any block or anywhere on a lane seeks the video.
- **Tabs:**
  - **Transcript:** the synced transcript, index and find.
  - **On screen:** every piece of text read from the screen, with its frame and a Correct button. Corrections feed
    search, and the machine reading is kept.
  - **People:** each face with time on screen and first appearance. Faces can be named (across the namespace in
    recognition mode), linked to a person, or removed ("Not a face"). Detect mode only offers removal.
  - **Shots:** a grid of shots.
- Everything seeks the one video, and the layout works down to phone widths.

## Captions

A video plays its text as WebVTT `<track>`s, so the browser's own captions menu, full screen and picture in picture
show them and assistive technology can read them, in the workspace and on its public page alike:

- the transcript comes first, as `kind="captions"` in the recording's language when it's known, and is the default
  while captions are on;
- then each supplementary transcript, captions or translation that says when its lines are
  ([files](api.md#files)), in the order they were added; a translation is `kind="subtitles"`. Pick them from the
  browser's captions menu.

The tracks are IIIF's WebVTT (`/iiif/<id>/transcript.vtt`, `/iiif/<id>/files/<file>.vtt`), with links signed for as long
as media links last, because a `<track>` can't send the Authorization header. `GET /api/v1/recordings/<id>/player`
lists them as `captions`, and so does `GET /api/v1/public/recordings/<id>`, with only what that visitor may read and
only when they may play the media. A share link (`?s=`) gets the transcript's track; supplementary files stay with
the namespace's members, as they do in the Files tab.

Captions show once, never twice:

- **Workspace (the video recording page):** the Captions toggle (`C`) draws the line and its speaker over the video, as
  before. Its track stays loaded but undrawn while that overlay shows, and the browser draws it when the video is full
  screen or picture in picture, where the overlay can't be seen. Turning Captions off turns the track off too.
- **Public page:** there is no overlay, so the browser draws the captions. They're off when the page opens, as before,
  and the Captions button under the video turns them on.

Refine later: the embeddable player (`/embed/<id>`, `/s/<code>`) plays sound beside its transcript, without a video,
so it has no tracks; Lens's own controls have no menu to pick a translation's track (the browser's menu does, where it
shows its own controls); and a share link's player could offer the supplementary tracks its link opens.

`The prototype's end-to-end browser test (`tests/e2e_video.py`, kept for reference in the history)` runs it end to end in Chromium, against a real server with face recognition on. It
checks:
- the lanes, and seeking from shots, lanes, the transcript and the grid
- that boxes appear at the right times and switch off
- a text correction reaching the API, naming a face, linking a face to a person and removing one
- playback moving the playhead
- no sideways scrolling on a phone

This describes the prototype's layout; the Next.js implementation follows the design handoff.
