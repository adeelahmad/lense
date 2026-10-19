- **Correct an asset's language, with its region.** The language transcription hears stays on the asset, and editors
  can set or correct it as a BCP 47 tag with a region when it matters (`en-GB` or `en-US`, `pt-BR` or `pt-PT`) in the
  asset's Details tab, with `PATCH /api/v1/resources/<id> {"language": "en-gb"}` or the MCP tool `set_language`. The
  tag is checked and written the usual way (`en-GB`, `zh-Hant-TW`), the IIIF manifest and Dublin Core record give it,
  and transcribing the asset again keeps a person's language instead of the detected one; `null` forgets it. A
  pipeline step's `languages` condition of `en` now takes `en-GB` and `en-US` too. Languages already stored stay as
  they are ([An asset's language](docs/api.md#an-assets-language)).
