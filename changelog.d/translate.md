- **Translate a transcript.** The Files tab's **Translate…** has the configured model translate an asset's transcript
  into another language (the one your browser reads is filled in), showing what it would take first, always labelled
  an estimate. It runs only when asked, in the background, sending the lines in batches marked as data so instructions
  in a transcript are never followed, and keeps the result as a translation file whose lines keep their times and
  speaker, read by search, the Files tab and IIIF like an uploaded one. Every call is in the activity ledger; a budget
  that would go over holds it, and one used up stops it. Agents use `GET` and
  `POST /api/v1/resources/{id}/files/translate`, the MCP tools `translate_transcript` and `get_translation`, and the
  assistant's `translate_transcript`, which asks first
  ([Translating a transcript](docs/api.md#translating-a-transcript)).
