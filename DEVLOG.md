# Lens Dev Log

## Working State
**Session:** 1 (this log starts here; earlier work is in `CHANGELOG.md`) | **Date:** 2026-10-02
**Branch:** `claude/oauth-search-mcp-telemetry` (from `origin/main` @ 86c86ae)

### Active Task
Backlog, continued: Docker fixes, then OAuth (#45), semantic search (#47), MCP server (#44), telemetry (#48),
then the remaining rows of `docs/backend-gaps.md`. One commit per slice; each slice is domain → route + schema →
backend tests (both engines) → `make openapi` → web app → frontend tests → docs → CHANGELOG.

- [x] **0. Docker: errors in the logs and the local workarounds** (own commit)
  - [x] Logs read, stack rebuilt and exercised: no application errors; Next 16.3.6; stops with exit 0 in 2.6 s
  - [x] `make` finds `docker compose` or `docker-compose`; Mailpit instead of MailHog; anonymous dependency volumes renewed by `make dev`; TERM handled by `start.sh` and `lens worker`; `agentRules: false`; browser-support data updated
- [x] **1. OAuth sign-in for API and MCP clients (#45)** (own commit)
  - [x] Domain, settings, routes, discovery, bearer path, audit; 11 backend tests (embedded and SurrealDB 3.2.4 server)
  - [x] Security review applied (see Mistakes & Lessons)
  - [x] Web app: `/oauth/authorize`, Apps with access, settings fields, `/.well-known` proxy; 13 frontend tests
  - [x] Docs, CHANGELOG, browser check (viewer and admin, dark, 390 px)
  - [ ] Push, CI green <-- CURRENT
- [ ] **2. Semantic search with ONNX text embeddings (#47)**
  - [ ] `search.semantic` settings (off by default), model fetch like YOLOX, `embed` pipeline step, `embedding` table
  - [ ] Search blends cosine similarity with full-text ranking; facets keep working
  - [ ] Web app: Settings → Search; "Meaning" toggle in `/search` and `/explore/search`
  - [ ] Tests, docs (`configuration.md`, `processing.md`, `api.md`), CHANGELOG, browser check, commit, push, CI
- [ ] **3. MCP server for agents (#44)**
  - [ ] `/mcp` (streamable HTTP), OAuth and API tokens, `WWW-Authenticate` with resource metadata
  - [ ] Tools: search, get resource/transcript/pages, list namespaces/collections, navigate, start import; resources for documents; roles honoured
  - [ ] Tests, `docs/mcp.md` (Claude, Cursor), CHANGELOG, commit, push, CI
- [ ] **4. Telemetry and analytics per collection (#48)**
  - [ ] `event` table (account, resource/collection, time; no IP/UA; "visitor"), 90-day retention setting, daily rollups, purge job
  - [ ] Analytics: owners per namespace, collection admins per collection, admins everywhere; own activity under the account
  - [ ] Web app, tests, docs, CHANGELOG, browser check, commit, push, CI
- [ ] **5. Afterwards:** rows of `docs/backend-gaps.md`, smallest-useful first; remove a row when its slice lands

### Decisions (active)
- OAuth access and refresh tokens are opaque and stored as hashes (like `la_…` keys), so revoking a grant works at once; no JWTs.
- The local edits to `Makefile` (`docker-compose`) and `docker-compose.yml` (arm64 MailHog image) were replaced by fixes that work everywhere (compose detection, Mailpit).
- An admin's OAuth app gets the admin's namespace roles but not the administration (`deps._admin`): consent plus open registration is a phishing target. To confirm with the owner.
- Discovery names the web app's address only from a trusted proxy's `X-Forwarded-Host`, else `FRONTEND_URL`.
- Custom redirect schemes are allowed without a dot (`cursor://`), with a denylist of browser and OS handler schemes: requiring reverse-DNS schemes would lock out Cursor and VS Code.

### Blockers
- None.

### Watch Out
- This Mac fails 11 backend tests that pass on CI: Homebrew's ffmpeg has no `drawtext` filter (video tests), `127.0.0.x` aliases can't be bound (web capture proxy), and a converter being installed changes one preview count. CI (Linux) is the arbiter for the full suite and for coverage (89 % here without the video tests).
- The dev container mounts the source tree: half-written backend code crashes the running API there, and the Next dev server writes `.next/` (and rewrites `tsconfig.json`) into `nextjs-frontend/`. Stop the stack, `rm -rf nextjs-frontend/.next`, `git checkout -- nextjs-frontend/tsconfig.json` before `tsc` or a build.
- Ports 8021 and 1025 are taken on this machine; browser checks use 18021 (API) and 13021 (web). A throwaway SurrealDB 3.2.4 for the server engine: `scratchpad/surreal/docker-compose.yml` on 127.0.0.1:8011.

---
---

## Session Archive

## Milestones
- [x] 59 slices merged as PR #4 and #5 (see `CHANGELOG.md`, Unreleased)
- [ ] OAuth, semantic search, MCP, telemetry

## Mistakes & Lessons
### 2026-10-02 - Loopback redirect matching trusted `urlsplit().hostname`
**What happened:** The first OAuth draft matched loopback redirect addresses by host, path and query. `http://evil.example\@localhost/cb` has hostname `localhost` for Python and goes to `evil.example` in a browser, so a crafted consent link could have sent a victim's code to an attacker.
**Root cause:** Comparing parsed parts while later redirecting to the raw string.
**How we fixed it:** `check_redirect` refuses backslashes and user names, and `redirect_ok` runs it on the requested address too; tests cover both forms. Found by the security review, before any commit.
**Lesson:** Validate the exact string the browser will be sent to, and get auth code reviewed before it lands.

### 2026-10-02 - A router without tags took the dev API down
**What happened:** `APIRouter(include_in_schema=False)` for `/.well-known` crashed `create_app()`: `simple_generate_unique_route_id` reads `route.tags[0]`. The Docker dev API, which mounts the source, went unhealthy on it.
**How we fixed it:** `tags=["oauth-discovery"]`.
**Lesson:** Every router here needs a tag, in the schema or not.

## Technical Debt & Future Ideas
