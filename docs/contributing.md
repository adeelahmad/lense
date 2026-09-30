# Contributing

## Setup

Follow [Get started](get-started.md), then install the pre-commit hooks:

```bash
cd fastapi_backend && uv run pre-commit install -c ../.pre-commit-config.yaml
```

## Backend

```bash
cd fastapi_backend
uv run pytest -n auto                    # all tests, in-memory SurrealDB
LENS_TEST_SURREAL_URL=ws://127.0.0.1:8000 uv run pytest -n 4   # the same tests against a SurrealDB server (surrealkv engine)
uv run ruff check . && uv run ruff format .
uv run mypy
```

Tests live in `tests/api` (HTTP, through `TestClient`) and `tests/domain` (the engine). Fixtures in
`tests/conftest.py` give every test a fresh database, data folder and app; `tests/helpers.py` has sample transcripts,
`seed()`, `make_user()` and `login()`.

Conventions for new endpoints:

* One router module per area in `app/api/v1/routes/`, registered in `app/api/v1/router.py`. The function name becomes
  the generated client's method name, so make it read well (`list_recordings`, `merge_speaker`).
* Request bodies are `RequestModel`s and responses `ResponseModel`s (`app/schemas/`).
* Check access with the `deps` dependencies (`Writer`, `AdminWriter`, `Acl.need()`, `Acl.recording()`): namespaces a
  caller can't read must answer 404, not 403.
* Keep HTTP out of `app/domain`; raise `ValueError`/`KeyError` there and wrap calls in `domain_errors()`.
* Pass responses that contain media links through `sign_urls()`.

After changing routes or schemas, regenerate the schema and the client (the dev watchers do this for you):

```bash
uv run python -m commands.generate_openapi_schema && (cd ../nextjs-frontend && pnpm generate-client)
```

## Frontend

```bash
cd nextjs-frontend
pnpm test && pnpm lint && pnpm tsc && pnpm build
```

## Documentation

The docs are these Markdown files, built with mkdocs-material: `cd fastapi_backend && uv run mkdocs serve -f ../mkdocs.yml`.

## Release

The backend and frontend share a version number.

1. Update the version in `fastapi_backend/pyproject.toml` and `nextjs-frontend/package.json`.
2. Add a `CHANGELOG.md` entry.
3. Open a PR; once merged, run the Release workflow to draft the GitHub release.
