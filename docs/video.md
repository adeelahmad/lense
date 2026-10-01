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
  - People can be renamed, merged (with undo), linked to a speaker, marked "not a face" or deleted.
  - Engines: OpenCV's YuNet + SFace (`pip install -e ".[faces]"`, then point `video.yunet_model` and
    `video.sface_model` at the ONNX files from the OpenCV Zoo) or InsightFace, whose pretrained models are licensed for
    non-commercial research only.

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
    recognition mode), linked to a speaker, or removed ("Not a face"). Detect mode only offers removal.
  - **Shots:** a grid of shots.
- Everything seeks the one video, and the layout works down to phone widths.

`The prototype's end-to-end browser test (`tests/e2e_video.py`, kept for reference in the history)` runs it end to end in Chromium, against a real server with face recognition on. It
checks:
- the lanes, and seeking from shots, lanes, the transcript and the grid
- that boxes appear at the right times and switch off
- a text correction reaching the API, naming a face, linking a face to a speaker and removing one
- playback moving the playhead
- no sideways scrolling on a phone

This describes the prototype's layout; the Next.js implementation follows the design handoff.
