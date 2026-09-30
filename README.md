# Lens

An archive for recorded speech and video. Lens transcribes recordings in batches, recognises speakers by voice within
a namespace, extracts people, organisations and topics into a knowledge graph, makes everything searchable, and
publishes recordings as IIIF. It also offers an assistant that answers with citations to the exact moment in a
recording.

- **Sources:** audio and video from watched folders on S3, Dropbox, Google Drive, OneDrive, SFTP, SMB, WebDAV or local
  disks, plus uploaded or pasted transcripts (txt, md, docx, pdf, srt, vtt, json and more).
- **Processing:** transcription (SenseVoice, faster-whisper, mlx-whisper), diarisation and voice IDs, entities,
  chapters and keywords, LLM summaries and templated outputs, and for video: shots, text on screen and (opt-in) faces.
- **Exploring:** full-text search with stemming, a speaker registry with merge and undo, an entity index and graph
  explorer, collections, batch runs with cost estimates, and chat scoped to what each person may read.
- **Publishing:** an embeddable transcript player, static reports, and IIIF Presentation 3 with Content Search, Change
  Discovery and the Authorization Flow.
- **Multi-user:** roles per namespace (viewer, editor, owner), API tokens, share links, and an audit log.

## Stack

| | |
|---|---|
| `fastapi_backend/` | FastAPI API (`/api/v1`), the processing engine, and the `lens` CLI and workers |
| `nextjs-frontend/` | Next.js web app with NextAuth (Auth.js v5) and a typed client generated from the API's OpenAPI schema |
| SurrealDB | all data: documents, graph edges, full-text indexes, the job queue |

## Quick start

```bash
cp fastapi_backend/.env.example fastapi_backend/.env          # set ACCESS_SECRET_KEY
cp nextjs-frontend/.env.example nextjs-frontend/.env.local    # set AUTH_SECRET
docker compose up --build
```

Open <http://localhost:3000> and create the first admin with the setup code from the backend log. The API docs are at
<http://localhost:8000/docs>.

## Documentation

Start with [Get started](docs/get-started.md), then:

- [Architecture](docs/architecture.md): how the pieces fit, and why
- [Authentication](docs/authentication.md): NextAuth, API tokens, share links, IIIF sign-in
- [Database](docs/database.md): SurrealDB modes, schema, and the traps the code avoids
- [Configuration](docs/configuration.md)
- [Processing](docs/processing.md), [Video](docs/video.md), [Chat and batch runs](docs/assistant.md), [IIIF](docs/iiif.md)
- [API](docs/api.md), [Deployment](docs/deployment.md), [Contributing](docs/contributing.md)

Built on the [Next.js FastAPI Template](https://github.com/vintasoftware/nextjs-fastapi-template) (MIT).
