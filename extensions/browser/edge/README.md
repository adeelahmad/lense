# The web clipper in Microsoft Edge

Edge runs the Chrome build's code; `../targets/edge.json` is its manifest (Edge reads `minimum_chrome_version` as
its own minimum, and Edge 116 is the first with everything the clipper uses).

```sh
cd extensions/browser && npm ci && npm run build -- --target edge
```

- **Try it**: open `edge://extensions`, turn on **Developer mode**, **Load unpacked**, and pick `dist/edge`.
- **Install a release**: every release has `lens-clipper-edge.zip` attached (and every CI run keeps it as an
  artifact). Unzip it and load the folder the same way.
- **Edge Add-ons**: `lens-clipper-edge.zip` is the package to upload in Partner Center
  (<https://partner.microsoft.com/dashboard/microsoftedge>). The listing's privacy policy answer: the clipper sends
  what you clip only to the Lens you add, and keeps its token on this device.

## Smoke test

`smoke.mjs` loads `dist/edge` into Edge, checks the options page and the popup render, then clips an article page and
checks a stand-in Lens import webhook gets the Markdown with the hook's token. CI runs it in
`.github/workflows/clipper-edge.yml`.

```sh
cd edge && npm ci && npx playwright-core install msedge   # once, if Edge isn't installed
xvfb-run -a npm run smoke    # or just `npm run smoke` on a desktop
```

`EDGE_CHANNEL=chromium CHROMIUM_PATH=/path/to/chrome npm run smoke` runs the same test in Chromium.
