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

Lens is a private project for now, and its license isn't decided. `LICENSE.txt` is still the MIT notice of the
template Lens started from (Vinta's Next.js FastAPI template); it isn't a decision about Lens. Until a license is
chosen, contribute only if you're fine with the maintainer deciding later how Lens is licensed, including your
contributions.

<!-- TODO(license): when Lens goes public, pick a license, replace LICENSE.txt, and say here what contributors agree to
(for example "contributions are licensed under the project's license"). Update SECURITY.md's reporting section too. -->
