# Contributing

## Setup

Follow [Get started](get-started.md), then install the git hooks with `make hooks`
(`cd fastapi_backend && uv run pre-commit install -c ../.pre-commit-config.yaml`). On each commit they run ruff,
ESLint, Prettier and tsc on the files you changed, check YAML, TOML and the GitHub workflows (actionlint), and
regenerate the OpenAPI client when the API changes. The commit message is checked too.

## Commit messages and PR titles

Write them as [Conventional Commits](https://www.conventionalcommits.org/): `type(optional scope): what changed`,
with `!` before the colon (or a `BREAKING CHANGE:` line) for a breaking change, e.g.
`feat(chat): answer from the selected recordings` or `fix(worker): retry a stalled transcription`. The types are
`feat`, `fix`, `perf`, `revert`, `docs`, `refactor`, `test`, `build`, `ci`, `chore` and `style`.

A PR's title is what counts: it lands in the merge commit, the PR title check enforces it, and it becomes the PR's
line in the changelog and decides the next version.

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

The backend and frontend share a [semantic version](https://semver.org/). The changelog is written from the PR titles,
so a PR needs no `CHANGELOG.md` edit. When a change deserves more than its title (what users notice, what to do when
upgrading), write it under `## Unreleased`; that text goes at the top of the next version's section.

To release, run the Release workflow on `main` (Actions > Release > Run workflow, or `gh workflow run release.yml`).
With `auto` it picks the version from the PR titles merged since the last release: a breaking change bumps major
(minor before 1.0), a `feat` minor, anything else patch; give `patch`, `minor`, `major` or an exact version to
override. It writes the new section (the Unreleased text, then the changes grouped into features, fixes,
performance, reverts, dependencies and other changes; `docs`, `refactor`, `test`, `build`, `ci`, `chore` and
`style` are left out), sets the version in `fastapi_backend/pyproject.toml`, `uv.lock`,
`nextjs-frontend/package.json` and `CloudronManifest.json`, commits that to `main` as `chore(release): vX.Y.Z`, tags
it and publishes the GitHub release with that section as its notes. Pushing a `vX.Y.Z` tag yourself publishes it the
same way, without the commit. `python3 .github/scripts/release.py cut auto` shows locally what it would write.
An exact version must be newer than the current one, and only the newest version is marked Latest. If a run pushed its
commit and tag but failed before publishing, run it again (or re-run it): it publishes that version instead of
cutting another.

Publishing starts the package builds (Cloudron, QNAP, Synology), which attach their files to the release; the
Proxmox script installs the latest release. If `main` is protected, add a `RELEASE_TOKEN` secret: a fine-grained
token with Contents: write on the repository, allowed to push past the protection. The workflow then commits and
publishes with it, and the package workflows start from the `release: published` event instead of being started
by the Release workflow.
