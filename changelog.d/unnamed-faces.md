- **Name the unnamed faces in a row.** Where a namespace recognises faces, Settings → People has an Unnamed faces tab
  (and a video's People tab links to it): the faces nobody has named yet, the largest group first, each with a few
  crops and the assets it's in. The first name field has focus, filled with the name Lens offers when it has one (the
  voice the face is linked to, or a named face suggested for it); Enter names it and moves on to the next, with Undo.
  A name another face already has joins that person, the same merge as "Same person". Agents do it with
  `GET /api/v1/namespaces/<ns>/faces/unnamed` and `POST /api/v1/faces/<id>/name`, or the MCP tools
  `list_unnamed_faces` and `name_face` ([Naming faces](docs/video.md#naming-faces)).
