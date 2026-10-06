# Contributing to Lens

Thanks for helping. The full guide, with the backend and frontend conventions, is
[docs/contributing.md](docs/contributing.md); this page is the short version.

## Set up

1. Follow [Get started](docs/get-started.md): `make dev` runs the whole stack with hot reload, or `make start-backend` and
   `make start-frontend` run it without Docker.
2. Install the git hooks: `make hooks`. On each commit they run ruff, ESLint, Prettier and tsc on what you changed,
   check YAML, TOML and the GitHub workflows, regenerate the OpenAPI client when the API changes, and check the
   commit message.

## Make the change

- Backend: `make test-backend` and `make lint-backend` (ruff, mypy). Frontend: `make test-frontend` and
  `make lint-frontend`.
- Add tests with the change, and update `docs/` when behaviour or configuration changes.
- Changing the shape of stored data? Existing installs have real data: add an upgrade step
  (docs/database.md, Upgrades) rather than expecting a fresh database.
- One concern per pull request. Keep generated files (`openapi.json`, the frontend client) in the same PR as the API
  change that produced them.

## Commit messages and PR titles

Use [Conventional Commits](https://www.conventionalcommits.org/): `type(optional scope): what changed`, with `!` before
the colon for a breaking change.

```
feat(chat): answer from the selected recordings
fix(worker): retry a stalled transcription
docs: explain the Synology package
feat(api)!: drop the v1 recordings endpoint
```

Types: `feat`, `fix`, `perf`, `revert`, `docs`, `refactor`, `test`, `build`, `ci`, `chore`, `style`.

The PR title matters most. It lands in the merge commit, the PR title check enforces it, it becomes the PR's line in
the changelog, and it decides the next version. A breaking change bumps major (minor before 1.0), a `feat` bumps
minor, and anything else bumps patch.

## Changelog and releases

You don't have to edit `CHANGELOG.md`: each release lists the PR titles merged since the last one. When a change needs
more than its title (what users notice, what to do when upgrading), write it under `## Unreleased`, and it goes at the
top of the next version's section.

Maintainers release by running the Release workflow on `main`. It sets the version, writes the changelog, tags
`vX.Y.Z`, publishes the GitHub release and starts the package builds; see [Release](docs/contributing.md#release).

## Reporting bugs and security issues

Open an issue for bugs and ideas. Report security issues privately, as [SECURITY.md](SECURITY.md) describes, not in
an issue.

Everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md).

## License

Lens is MIT-licensed ([LICENSE.txt](LICENSE.txt)). By contributing, you agree that your contributions are licensed
under the same terms.

Keep it that way: a new dependency that is a required part of Lens must have a license that lets Lens stay MIT (MIT,
BSD, ISC, Apache-2.0, MPL-2.0 and LGPL libraries used unmodified are fine). A GPL or AGPL package may only be an
opt-in extra that people install themselves, the way `extract-msg` (GPL-3.0, for Outlook `.msg` files) is today, and
the extra's comment in `pyproject.toml` should say so.
