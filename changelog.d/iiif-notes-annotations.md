- **Notes and comments in IIIF, as W3C Web Annotations.** With `notes` added to the published annotation layers
  (Settings, IIIF; off by default), an asset's IIIF Manifest lists its shared notes and its comments as an
  AnnotationPage (`/iiif/<id>/annotations/notes`): each note or thread `commenting` on its moment or on the whole
  asset, each reply `replying` to its thread, with who wrote it and when; on a document, per page. Only people with a
  role on the asset get it, through their sign-in or the IIIF access cookie: the public, share links and signed links
  never do, and unshared notes are never in it ([IIIF](docs/iiif.md)).
