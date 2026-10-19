- **Search by when, tag, source and language.** `GET /api/v1/search` takes `from` and `to` (days: when the asset was
  recorded, or added when its file doesn't say), `tag`, `origin` and `language`, the Library's filters of the same
  names, and keeps every kind of hit to the assets that have them; the MCP `search` tool takes `date_from` and
  `date_to` (a day, a month or a year), `tags`, `origins` and `languages`. On the search page they're compact controls
  at the top of the filter panel, empty until picked, typed in the box too (`tag:interview`, `from:2026-01-01`), shown
  as chips, kept in the link and in saved searches. A search's object filter now also keeps text on screen, file lines
  and descriptions to the assets the object is seen in, not only what was said
  ([search](docs/api.md#search)).
