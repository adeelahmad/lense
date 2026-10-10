Lens is intelligence for your agent: safe, secure and cost-effective. Connect your live data streams (recordings,
documents, email, calendars, web pages and more); Lens transcribes, tells speakers apart, makes everything searchable
and builds a knowledge graph of the people, places and topics mentioned. Its assistant, and any agent signed in over
MCP, answers with citations to the exact moment in a recording or the line of a document.

### Features

* **For agents:** an MCP server with OAuth sign-in and a REST API; agents see only what their person may read.
* **Assistant:** chat and voice with citations, tools that wait for your approval, and skills and plugins from
  marketplaces, through any OpenAI-compatible model server or a cloud model.
* **Sources:** watched folders, IMAP mail, iCal calendars, MQTT, syslog and webhooks, uploads, web pages and the
  Lens web clipper.
* **Transcription and speakers:** transcripts with word timings, speaker diarisation and voice IDs.
* **Search:** full-text search, and search by meaning when it's turned on, across transcripts, documents and images.
* **Knowledge graph:** entities, topics and their relations, per namespace or shared, queried with Cypher or SPARQL,
  with its history.
* **Podcasts and notes:** cited, fact-checked two-host episodes about what you pick, and notes beside everything.
* **Costs:** an activity ledger of every model call, with reports and budgets.
* **Publishing:** an embeddable transcript player, static reports, and IIIF Presentation 3 with Content Search.

### This package

SurrealDB, the API, a background worker and the web app run together in one app. The database and the archive's
files live in the app's data directory and are included in Cloudron backups, with a fresh database export every
six hours.
