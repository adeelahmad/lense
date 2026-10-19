- **Captions the browser knows about.** A video now plays its transcript as a WebVTT `<track>`, in the workspace and on
  its public page, so the browser's captions menu, full screen, picture in picture and assistive technology get it,
  with timed supplementary captions and translations as more tracks to pick. The links are signed, so they work for
  visitors and share links too. In the workspace the Captions toggle still draws the line and its speaker over the
  video, and the browser draws the track only where that can't be seen (full screen, picture in picture), so captions
  never show twice; on the public page the browser draws them once the visitor turns them on with the new
  Captions button. `GET /api/v1/recordings/<id>/player` and `GET /api/v1/public/recordings/<id>` list the tracks as
  `captions` ([Captions](docs/video.md#captions)).
