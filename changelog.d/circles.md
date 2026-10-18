- **Circles of trust.** Put the people close to you in circles of your own (Immediate family, Close family, Friends,
  Colleagues are suggested) and share an activity from your timeline or a resource with a circle and a note: each of
  them gets it in their own timeline as `share.received`, which their automations can follow, and you get
  `share.sent`. *With the item* also gives them permission on that one resource (it needs `publish`); undo takes the
  share back everywhere, with the permissions it gave. Nothing in a vault can be shared. Works through
  `/api/v1/circles`, MCP (`list_circles`, `save_circle`, `share_with_circle`, `undo_share`) and the assistant, which
  asks first ([Circles of trust](docs/circles.md)).
