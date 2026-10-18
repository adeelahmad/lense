- **A downloads switch for each asset.** Its owners can turn downloads off in its Access dialog or with `PUT
  /api/v1/resources/<id>/access {"downloads": false}`. Then everyone else who sees it still plays it, sees its pages
  and reads its text, but saving its own file, the PDF made of it or its supplementary files answers 403 with the
  reason; the web app disables those download buttons and says why, the file tree and IIIF manifests leave the files
  out, and owners keep saving. It's on for every asset until someone turns it off. It isn't copy protection: what
  plays can still be recorded ([Downloads](docs/access.md#downloads)).
