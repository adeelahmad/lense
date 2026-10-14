# Lens web clipper

A browser extension that clips pages, selections or any part of a page into Lens as Markdown, with the page's
metadata and images, through a namespace's import webhook. See [docs/web-clipper.md](../../docs/web-clipper.md).

```sh
npm ci && npm test && npm run build   # then Load unpacked: dist/chrome
```

- `src/core/`: the shared code every browser uses: `clip.js` (page, selection, element), `metadata.js`,
  `markdown.js` (Turndown), `template.js`, `picker.js`, `lens.js` (pushing to Lens), `settings.js`; for the assistant's
  side panel, `account.js` (signing in with OAuth) and `assistant.js` (the chat API, answers as safe HTML); for the
  assistant using the browser, `browser.js` (the session and its actions, sites and asks), `snapshot.js` (a page read
  with numbered elements) and `act.js` (clicking, typing, keys), the last two injected as `src/agent.js`
- `src/background.js`, `src/content.js`, `src/ui/`: the extension around it
- `targets/<browser>.json`: what each browser's manifest changes from `src/manifest.json`
