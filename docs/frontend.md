# The web app

`nextjs-frontend/` is a Next.js App Router app that implements the Lens Archive design (built on the Aladdin design
system). NextAuth holds the session; every screen talks to the API through the generated, typed client.

## Layout

```
nextjs-frontend/
  app/
    (auth)/                 sign in, first-run setup, password reset
    (app)/                  every signed-in screen, inside the app shell
      page.tsx              Home
      library/ import/ reports/ recordings/[id]/
      search/ chat/ speakers/ graph/ batches/ collections/
      activity/ sources/ pipelines/ templates/
      settings/ admin/ account/ iiif/
    api/v1/, embed/, iiif/, reports/, static/
                            route handlers that proxy those paths to the API (lib/api/backend-proxy.ts)
    styles/tokens.css       design tokens: Aladdin + Lens additions, light and dark
  components/
    ui/                     primitives from the design's Foundations (see below)
    app-shell/              nav rail, top bar, namespace switcher, ⌘K palette, activity drawer, account menu
    <area>/                 one folder per screen area; pure logic in *-model.ts / model.ts, tested in __tests__/
  lib/
    api/                    typed clients (server and browser), the proxy, SSE streams
    hooks/                  session and archive context (roles, current namespace), jobs
    format.ts               times, dates, sizes
  app/openapi-client/       generated from the API's OpenAPI schema; never edit by hand
```

## Design system

Colour is semantic, never decorative: blue means working, selected or a link; green means ready or succeeded; red
means failed or destructive; gold ◆ means a person needs to decide; grey means waiting. Pipeline steps use the
four-colour loop (Transcribe blue, Diarize red, Analyze green, Summarize and Report gold).

* **Tokens** live in `app/styles/tokens.css` and are mapped into Tailwind (`bg-surface`, `text-fg-secondary`,
  `border-blue-border`, `spk-1…8`, `emo-*`, `rounded-pill`, `shadow-2`...). Don't hard-code colours: the dark theme
  switches by variables (`[data-theme=dark]`, or the system preference).
* **Type:** DM Sans for the UI (13–14 px), Source Serif 4 for transcripts (`.transcript-text`, 17.5/1.6), JetBrains
  Mono for paths and code. All three are self-hosted in `public/fonts`.
* **Primitives** (`components/ui`): Button (pill; `disabledReason` explains a disabled action), IconButton, Badge,
  StatusChip, RoleChip, SpeakerChip, EmotionChip, JobStepChip, Field/Input/Select/Switch/Checkbox, SecretField,
  Tabs, Dialog, Drawer, Menu, Popover, Toast, Banner, Table with sortable headers and Pagination, Skeleton,
  EmptyState, CodeBlock, DateTime, Avatar, Progress, PageHeader, Panel, StepLoop, VerdictCard, AgentChip.
* **The role rule:** actions a person can't take stay visible, disabled, with a tooltip saying who can
  (`needRole()`); namespaces they have no role in never appear. `useArchive()` gives `can(role, ns)` and the
  current namespace.

## Data

* Client components use `useApiClient()` with React Query and the generated SDK; `data()` unwraps a call and throws
  `ApiError` with the API's message, and `page()` unwraps a list call into `{items, total}` (the total comes from the
  `X-Total-Count` header). Server components use `getApiClient()`.
* Streams (job events, chat answers) use `streamSSE()` with the session's access token.
* Media links in API responses are signed relative URLs; the proxy serves them from the app's origin, so they work in
  `<audio>`, `<video>` and `<img>`.

## The proxy

`/api/v1`, `/embed`, `/iiif`, `/reports` and `/static` are forwarded to `API_BASE_URL` by route handlers at request
time (bodies, byte ranges and event streams pass straight through). Because it runs per request, one build works
against any API; `next.config` rewrites would have fixed the API address at build time.

## Developing

```bash
pnpm dev              # :3000, with API_BASE_URL pointing at the API
pnpm test             # jest
pnpm lint && pnpm tsc
pnpm build
```

Several dev servers can run side by side with separate build folders:
`NEXT_DIST_DIR=.next-a next dev --webpack -p 3021` (the webpack flag is needed: the config customises webpack).
`next dev` adds its build folder's type paths to `tsconfig.json`; don't commit those lines.

When the API changes, regenerate the client: `make openapi` from the repository root.

Features the design shows but the API can't provide yet are listed in [What the design needs from the API
next](backend-gaps.md).
