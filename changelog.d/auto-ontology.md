### Added

- Optional NVIDIA Auto-Ontology integration through its authenticated public REST gateway: semantic term search,
  readiness, answerability and SQL-backed answers with bounded rows and source provenance. Reuses private MCP
  extensions, sealed tokens, tool controls and the activity ledger; adds no default services or dependencies.
  Predictive uploads and persistent upstream conversations are disabled.

### Fixed

- Keep the Clicks workflow's default runner on Ubuntu 24.04, which its pinned Playwright browser bundle supports,
  instead of letting the moving Ubuntu label break browser installation on 26.04.
