# Technology

- [FastAPI](https://fastapi.tiangolo.com/) and [Pydantic](https://docs.pydantic.dev/): the HTTP API, request and
  response validation, and the OpenAPI schema the frontend client is generated from
- [SurrealDB](https://surrealdb.com/): documents, graph edges and vectors in one database, embedded or as a server
  ([why](architecture.md#design-decisions))
- Word search: [SQLite FTS5](https://www.sqlite.org/fts5.html) (standard library) by default, or an optional
  [OpenSearch](https://opensearch.org/) cluster ([Database](database.md#schema))
- [py_webauthn](https://github.com/duo-labs/py_webauthn): passkeys, the default way to sign in (passwords are off on
  fresh installs); [PyJWT](https://pyjwt.readthedocs.io/): short-lived access tokens; scrypt (standard library) for
  passwords where they're on; [cryptography](https://cryptography.io/) (AES-GCM) for credentials stored in the database
- [rdflib](https://rdflib.readthedocs.io/): the archive as RDF (Dublin Core) for export, import and SPARQL
  ([RDF](rdf.md))
- [OpenTelemetry](https://opentelemetry.io/): opt-in traces and metrics over OTLP/HTTP ([Telemetry](telemetry.md))
- [llama.cpp](https://github.com/ggml-org/llama.cpp): `llama-server` runs a GGUF chat model on the server itself
  ([Local models](local-models.md))
- [Next.js](https://nextjs.org/) (App Router) with [Auth.js / NextAuth v5](https://authjs.dev/) for the browser session
- [@hey-api/openapi-ts](https://heyapi.dev/): the typed API client; [Zod](https://zod.dev/) for form validation
- [shadcn/ui](https://ui.shadcn.com/) and [Tailwind CSS](https://tailwindcss.com/)
- [BlockSuite](https://blocksuite.io/): the notes editor; [@xyflow/react](https://reactflow.dev/): the canvas
  pipelines and workflows are drawn on
- Processing: ffmpeg, [SenseVoice](https://github.com/FunAudioLLM/SenseVoice) / [faster-whisper](https://github.com/SYSTRAN/faster-whisper) /
  [mlx-whisper](https://github.com/ml-explore/mlx-examples), [SpeechBrain](https://speechbrain.github.io/) ECAPA voiceprints,
  optional [pyannote](https://github.com/pyannote/pyannote-audio), Tesseract / Apple Vision / RapidOCR, OpenCV YuNet + SFace,
  ONNX models on [ONNX Runtime](https://onnxruntime.ai/) (YOLOX objects, RapidOCR),
  [rclone](https://rclone.org/) for remote storage, Jinja2 (sandboxed) for templates
- [uv](https://docs.astral.sh/uv/), [pnpm](https://pnpm.io/), [Ruff](https://docs.astral.sh/ruff/), mypy, ESLint,
  pytest, Jest, Docker Compose, pre-commit, [mkdocs-material](https://squidfunk.github.io/mkdocs-material/)
