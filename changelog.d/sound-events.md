- **Search finds moments by the sounds in them.** Laughter, applause, music and the other sounds a speech provider
  hears are a search filter like emotion: type `event:laughter` (any case; `music` is the BGM tag), pick one under
  Sounds in the filters, or pass `event` to `GET /api/v1/search`, a saved search or the MCP `search` tool. Each hit
  says its sound, and the facets count them (`events`), leaving plain speech out ([API](docs/api.md#search),
  [search](docs/processing.md#search)).
