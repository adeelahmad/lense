# Technology

- [FastAPI](https://fastapi.tiangolo.com/) and [Pydantic](https://docs.pydantic.dev/): the HTTP API, request and
  response validation, and the OpenAPI schema the frontend client is generated from
- [SurrealDB](https://surrealdb.com/): documents, graph edges and full-text search in one database, embedded or as a
  server ([why](architecture.md#design-decisions))
- [PyJWT](https://pyjwt.readthedocs.io/): short-lived access tokens; scrypt (standard library) for passwords;
  [cryptography](https://cryptography.io/) (AES-GCM) for credentials stored in the database
- [Next.js](https://nextjs.org/) (App Router) with [Auth.js / NextAuth v5](https://authjs.dev/) for the browser session
- [@hey-api/openapi-ts](https://heyapi.dev/): the typed API client; [Zod](https://zod.dev/) for form validation
- [shadcn/ui](https://ui.shadcn.com/) and [Tailwind CSS](https://tailwindcss.com/)
- Processing: ffmpeg, [SenseVoice](https://github.com/FunAudioLLM/SenseVoice) / [faster-whisper](https://github.com/SYSTRAN/faster-whisper) /
  [mlx-whisper](https://github.com/ml-explore/mlx-examples), [SpeechBrain](https://speechbrain.github.io/) ECAPA voiceprints,
  optional [pyannote](https://github.com/pyannote/pyannote-audio), Tesseract / Apple Vision / RapidOCR, OpenCV YuNet + SFace,
  [rclone](https://rclone.org/) for remote storage, Jinja2 (sandboxed) for templates
- [uv](https://docs.astral.sh/uv/), [pnpm](https://pnpm.io/), [Ruff](https://docs.astral.sh/ruff/), mypy, ESLint,
  pytest, Jest, Docker Compose, pre-commit, [mkdocs-material](https://squidfunk.github.io/mkdocs-material/)
