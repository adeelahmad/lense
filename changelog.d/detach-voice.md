- **Not them here.** When a voice in one asset was matched to the wrong person, Not them here on their card in the
  asset's Speakers tab (or `POST /api/v1/speakers/{sid}/detach`) gives its lines there to a new person, works out the
  first person's voice again without it, and never suggests the two as the same; Undo merges them back
  ([People and namespaces](docs/processing.md#people-and-namespaces)).
