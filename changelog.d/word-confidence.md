- **See the words transcription wasn't sure of.** Whisper, Deepgram, AssemblyAI and ElevenLabs say how sure they
  were of each word, and Lens now keeps it with the word's timing instead of dropping it. In the transcript, a word
  below the threshold gets a dotted gold underline and a tooltip ("Transcription wasn't sure of this word (42%)"), so
  you can spot it and correct it in place; correcting it takes the mark away. Settings → Transcription → Mark words
  it was less sure of than sets the threshold (`transcribe.confidence_highlight`, 0.6; 0 marks none). `GET
  …/player` gives each word's confidence as a fifth in `w`, and the threshold as `unsure_below`; the MCP tool
  `get_transcript` lists each line's `unsure` words. Transcripts made before, and those from SenseVoice and OpenAI's
  own API, which don't say, have no marks and work as before
  ([Words transcription wasn't sure of](docs/processing.md#unsure-words)).
