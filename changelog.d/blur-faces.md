- **Blur faces for sharing.** A video's People tab has **Blur faces for sharing**: every face found is ticked, and
  Enter makes a copy of the video with those faces blurred, by ffmpeg over the faces' tracks, in the background as a
  heavy step (quiet processing hours apply), with its progress shown. The copy is kept with the video's files, the
  original stays as it is, and an owner can have visitors (public pages, embeds, share links, IIIF, feeds) play the
  copy instead. Nothing changes for a video until someone asks. Agents use `GET` and
  `POST /api/v1/recordings/{id}/blur-faces` (all, unnamed or chosen faces), `PUT .../blur-faces/visitors`, and the
  MCP tools `blur_faces`, `get_face_blur` and `set_face_blur_for_visitors`
  ([Blurring faces for sharing](docs/video.md#blurring-faces-for-sharing)).
