Lens is usable context for you and your agents: one private AI hub where every source becomes cited memory.
Recordings, documents, email, calendars and other sources flow in; Lens transcribes, tells speakers apart, makes
everything searchable (by words and by meaning) and builds a knowledge graph of the people, places and topics
mentioned. Its assistant, and any agent signed in over MCP, answers with citations to the exact moment in a recording
or the line of a document.

### Features

* **Transcription and speakers:** transcripts with word timings, speaker diarisation and voice IDs.
* **Search:** full-text and semantic search across transcripts, documents and images.
* **Knowledge graph:** entities and their relations, per namespace or shared.
* **Assistant:** chat with your archive through any OpenAI-compatible model server (Ollama, llama.cpp, vLLM, LM Studio).
* **Publishing:** an embeddable transcript player, static reports, and IIIF Presentation 3 with Content Search.

### This package

SurrealDB, the API, a background worker and the web app run together in one app. The database and the archive's
files live in the app's data directory and are included in Cloudron backups, with a fresh database export every
six hours.
