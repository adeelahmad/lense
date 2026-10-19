- **Line up a transcript with its audio.** A transcript imported without times (plain text, Word, PDF, Markdown) now
  says its times are estimated, and once its audio is attached its page offers **Line up with the audio**: one click
  transcribes the audio in the background with the engine transcription already uses (activity ledger, budgets and
  quiet processing hours apply), matches the transcript's words to the words heard, and gives each line their times;
  lines none of whose words were heard sit between their neighbours. The text and speakers stay exactly as they were.
  Nothing is lined up unless someone asks. Agents use `POST /api/v1/resources/{id}/align` and the MCP tool
  `align_transcript`; `GET /api/v1/resources/{id}` says `times` (estimated or aligned). Upgrading marks transcripts
  imported before with estimated times (the `estimated-times` step)
  ([Lining up a transcript with its audio](docs/processing.md#aligning)).
