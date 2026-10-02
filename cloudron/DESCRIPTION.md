Lens is an archive for recorded speech and video. It transcribes recordings, tells speakers apart, makes everything
searchable (full text and by meaning), builds a knowledge graph of the people, places and topics mentioned, and
publishes recordings as IIIF. Its assistant answers questions with citations to the exact moment in a recording.

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
