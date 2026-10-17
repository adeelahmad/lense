# Contributing

## Setup

Follow [Get started](get-started.md), then install the git hooks with `make hooks`
(`cd fastapi_backend && uv run pre-commit install -c ../.pre-commit-config.yaml`). On each commit they run ruff,
ESLint, Prettier and tsc on the files you changed, check YAML, TOML and the GitHub workflows (actionlint), and
regenerate the OpenAPI client when the API changes. The commit message is checked too.

## Monorepo and CI (Nx)

Lens is one repository with several projects, and [Nx](https://nx.dev/) runs their checks. Each project has a
`project.json` naming its targets; `nx.json` holds what they share. The apps keep their own package files
(`pyproject.toml` and `uv.lock`, `package.json` and `pnpm-lock.yaml`); the root `package.json` only brings Nx, and
`make` installs it on first use (`pnpm install` at the root).

| Project | Folder | Targets |
| --- | --- | --- |
| `backend` | `fastapi_backend/` | `lint`, `format`, `typecheck`, `test` (`:ci` with coverage, `:server` against `LENS_TEST_SURREAL_URL`), `test-durations`, `openapi-check`, `openapi` |
| `frontend` | `nextjs-frontend/` | `lint`, `typecheck`, `test` (`:ci` with coverage), `build`, `generate-client` |
| `installer` | `installer/` | `test` |
| `docs` | `docs/` (with `mkdocs.yml`, `overrides/`, `README.md`, `CHANGELOG.md`) | `build` (strict, as CI), `serve` |
| `clipper` | `extensions/browser/` | `install` (`npm ci` when the lockfile is newer than `node_modules`), `test`, `build` (every browser target, checked) |
| `desktop` | `desktop/` | `lint` (`sh -n` and shellcheck on `lens-app.sh`), `test` (`lens-app.sh` checks on Linux; the Mac build stays in the Desktop workflow) |
| `microsandbox` | `microsandbox/` | `lint` (`sh -n` and shellcheck on its scripts), `test` (`test-dns.sh`, the name-lookup fallback, and `test-programs.sh`, LibreOffice and Chromium on first use, in the base image with Docker; the install itself runs in the Install workflow) |
| `scorecard` | `ci/scorecard/` | `test` |

```bash
pnpm exec nx run backend:test                          # one target (make test-backend)
pnpm exec nx run backend:test -- tests/api/test_auth.py  # arguments after -- go to the command
pnpm exec nx affected -t lint typecheck test build     # only what your changes affect, against main (make affected)
pnpm exec nx show projects --affected                  # which projects that is
pnpm exec nx graph                                     # the projects and how they depend on each other
```

The `make` targets (`test-backend`, `lint-backend`, `test-frontend`, `lint-frontend`, `openapi`, `docs`, `docs-build`)
run these same targets. A target whose inputs (its project's files, plus the few root files `nx.json` names) haven't
changed replays its last result from the local cache in `.nx/`; `--skip-nx-cache` runs it anyway. The cache stays on
your machine: Nx Cloud is never connected (`neverConnectToCloud`) and Nx's usage analytics are off (`analytics: false`).

CI (`.github/workflows/ci.yml`) runs the same targets, and only for the projects a change affects: a pull request is
compared with the `main` it merges into, a push to `main` with `main`'s last green CI run. Every job starts at once and
asks Nx for itself (`.github/actions/affected`), so no job waits for another one, and for a free runner a second time,
before it starts; a job whose project isn't affected stops there and passes. **FastAPI (embedded SurrealDB)** and
**FastAPI (SurrealDB server)** test `backend`, each split across two runners (`1/2`, `2/2`); the first embedded one
also lints, type checks and checks the OpenAPI schema. **Next.js** checks `frontend`, and **Other projects** runs the
`lint`, `typecheck`, `test` and `build` targets of every other affected project (the docs site, the installer tests, the
web clipper) and lists the affected projects in its summary. A change to `nx.json`, the root `package.json`, `ci.yml` or
`.github/actions/` affects every project. Running the CI workflow by hand (Actions > CI > Run workflow) checks every
project.

The backend's tests are split by how long each took ([pytest-split](https://github.com/jerry-git/pytest-split), from
`fastapi_backend/tests/durations.json`), so the runners finish close together. A test missing from the file counts as
the average; to record the durations again, run `pnpm exec nx run backend:test-durations` and commit the file. To
split across more runners, add a shard to the job's `matrix`. Each runner keeps its share of the coverage with the
run; on `main`, **Backend coverage** adds them up into the `backend-coverage` artifact and the run's summary.

Merging or closing a pull request cancels its CI, `pre-commit` and `Install` runs that are still waiting or going, and
a newer push or title cancels the older ones, so a runner never works on something that no longer counts.
`pre-commit`, `PR title`, `Install`, `threat model`, the clipper's browser workflows (which also keep the zips with the
run) and the packaging workflows otherwise run as before.

To add a project (the desktop app, say), give its folder a `project.json` with a `name` and any of the `lint`,
`typecheck`, `test` and `build` targets (`nx:run-commands`, with `cwd: "{projectRoot}"`); a target that needs its
dependencies installed first depends on an `install` target, as the clipper's do. **Other projects** picks it up
without a change to CI. Name files outside the folder that it reads in its `inputs`, so changing them counts as
affecting it.

## Commit messages and PR titles

Write them as [Conventional Commits](https://www.conventionalcommits.org/): `type(optional scope): what changed`,
with `!` before the colon (or a `BREAKING CHANGE:` footer in any of the PR's commits) for a breaking change, e.g.
`feat(chat): answer from the selected assets` or `fix(worker): retry a stalled transcription`. The types are
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

The docs are these Markdown files, built with mkdocs-material: `make docs` serves them, and `make docs-build` builds
the site the way CI does, failing on broken links.

## Requirements

The README lists what Lens does (built), what is in a pull request (In progress) and what comes next (Planned), and
[Requirements and checks](requirements.md) backs each built item with the tests, clicks journeys and measured numbers
that fail when it stops being true. A pull request that builds something adds it to the README and a row there, and
takes its own line out of In progress. `make requirements` (also a pre-commit hook, so CI runs it on every pull
request) fails when a check a row names is gone or a measured number is no longer met.

## Release

The backend and frontend share a [semantic version](https://semver.org/). The changelog is written from the PR titles,
so a PR needs no `CHANGELOG.md` edit. When a change deserves more than its title (what users notice, what to do when
upgrading), write it under `## Unreleased`; that text goes at the top of the next version's section.

To release, run the Release workflow on `main` (Actions > Release > Run workflow, or `gh workflow run release.yml`).
With `auto` it picks the version from the PR titles merged since the last release: a breaking change bumps major
(minor before 1.0), a `feat` minor, anything else patch; give `patch`, `minor`, `major` or an exact version to
override. It writes the new section (the Unreleased text, then the changes grouped into features, fixes,
performance, reverts, dependencies and other changes; `docs`, `refactor`, `test`, `build`, `ci`, `chore` and
`style` are left out, and so is a PR that wrote its own Unreleased entry, unless it's breaking), sets the version in `fastapi_backend/pyproject.toml`, `uv.lock`,
`nextjs-frontend/package.json`, the API schema `nextjs-frontend/openapi.json`, `app/domain/__init__.py` and
`CloudronManifest.json`, commits that to `main` as `chore(release): vX.Y.Z`, tags
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

## Scorecard

`make scorecard` prints one JSON line of the numbers that say whether Lens is meeting its goals, so a turn of work can
be compared with the one before (AGENTS.md: back gains with before-and-after numbers). Each number is measured, with
the command or GitHub API call behind it in `cmd`, or says why it isn't in `not_measured`; nothing is estimated. The
line carries the commit, `git describe` tag and release it was taken at.

| Numbers | Where they come from |
| --- | --- |
| Journeys passing, clicks, keys, swipes and passkey touches, on a computer and a phone | The last accepted survey, `ci/clicks/baseline.json` and `baseline-phone.json` (see `ci/clicks/README.md`) |
| Test functions defined, per project | `def test_*` in `test_*.py`, `it(` and `test(` in `*.test.*`, `*.spec.*` and `__tests__/` |
| Backend tests collected | `uv run --frozen pytest --collect-only -q`, when `fastapi_backend/.venv` exists |
| Backend and frontend line coverage | The newest `backend-coverage` and `frontend-coverage` artifacts from CI on `main` (needs a GitHub token) |
| Red-check rate, ask to merge, CI minutes per merged pull request | Pull requests opened in the last `--days` (7), bots left out: the share whose first push had a failing workflow run (a re-run counts its first attempt), the median hours from the earliest of the pull request and the issues it closes to its merge, and the median wall time of its workflow runs |
| Failed runs on `main` | Workflow runs on pushes to `main` in the same window that failed or timed out |
| Stars, forks, and issues and pull requests from outside | The repository, and issues and pull requests whose authors aren't its owner, members or collaborators |
| Install to first answer, and idle RAM and disk on arm64 (as a Raspberry Pi is) | The newest `install-scorecard` artifact from main: the Install workflow's arm64 job times the one-line install until the setup page answers, waits two minutes, then adds up its containers' memory (`docker stats`) and Docker's images, containers and volumes plus the install folder (`ci/scorecard/measure_install.py`). It runs weekly and when the install changes. GitHub's arm64 runner is faster than a Pi, so the time is a floor |

```bash
make scorecard                                       # print the line
make scorecard SCORECARD=loop/scorecard.jsonl        # compare with the file's last line, then append to it
python3 ci/scorecard/scorecard.py --offline          # only what the checkout can tell, no GitHub calls
```

With `SCORECARD` (`--log`), it prints what changed since the file's last line and exits 1 when a guard trips: journeys
passing, the number of journeys, tests or coverage went down, a number that was measured isn't any more, or a clicks
baseline changed without a new survey (its contents changed but not its `surveyed` time). A turn whose guard trips
doesn't count as better, whatever else improved. GitHub calls use `GITHUB_TOKEN` (or `GH_TOKEN`) when it's set.
