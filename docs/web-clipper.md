# Web clipper

**Status: built (Chrome, Firefox).** Firefox, Edge and Safari builds come from the same code; each gets a target in
`extensions/browser/targets/`.

The Lens web clipper is a browser extension that turns what you're reading into Markdown and sends it to your Lens,
where it's imported like pasted text and runs through the namespace's pipeline. It works like MarkDownload: you
choose what to clip, look over the Markdown, and send it, copy it or download it as a `.md` file.

## What it clips

- **The page**: the article, through Mozilla's Readability (menus, ads and footers left out; turn that off in the
  options to keep the whole page), with its metadata: title, author, site, publish date, lead image, keywords and
  language, from the page's meta tags, Open Graph and JSON-LD.
- **The selection**: what you've selected, keeping the list, table, quote or code block it's in.
- **A part you pick**: hover to outline an element, <kbd>↑</kbd> to widen to its parent, <kbd>↓</kbd> to narrow again,
  click or <kbd>Enter</kbd> to take it, <kbd>Esc</kbd> to stop.
- **A link or an image** from the right-click menu: a link is sent for Lens to capture itself (a PDF as it is, a page
  printed by Chromium on the full image); an image is sent as a file.

Relative links and images become absolute, lazy-loaded images are loaded, code blocks keep their language, and
tables, strikethrough and task lists become GitHub-style Markdown.

## Setting it up

1. In Lens, open a namespace's admin page and make an **import webhook** (see [the API](api.md), Import webhooks).
   Copy its token: it's shown once.
2. In the clipper's options, enter your Lens address and the token and press **Add and check**. The browser asks to
   let the clipper talk to that address; the clipper then checks the token without importing anything.

Add one per namespace you clip into, and pick between them in the popup. The tokens stay on this device; every other
setting syncs through your browser account. Plain `http://` addresses are only accepted on this computer or the local
network.

## Ways to clip

| | |
| --- | --- |
| Toolbar button, or <kbd>Alt</kbd>+<kbd>Shift</kbd>+<kbd>L</kbd> | the popup: clips the selection if there is one, else the page, for you to look over and send |
| Right-click, or <kbd>Alt</kbd>+<kbd>Shift</kbd>+<kbd>P</kbd> / <kbd>S</kbd> | clip the page / the selection and send it straight away (a ✓ on the button when it's in) |
| Right-click **Pick part of the page**, or <kbd>Alt</kbd>+<kbd>Shift</kbd>+<kbd>K</kbd> | the picker, then send |

With **Right-click and shortcut clips open the popup** on, those open the popup to look over first instead.

## Templates

What's sent is the template with the clip's variables filled in. The default puts the metadata in YAML front matter
and the content under the title:

```markdown
---
title: {title|yaml}
source: {url}
author: {byline|yaml}
site: {siteName|yaml}
published: {published:YYYY-MM-DD}
clipped: {date:YYYY-MM-DDTHH:mm:ssZ}
image: {image}
tags: {tags|list}
---

# {title}

{content}
```

A front matter line whose value comes out empty is left out. Selections and picked parts can have a template of their
own; the title has one too (`{title}` by default).

Variables: `title`, `url`, `domain`, `origin`, `siteName`, `byline`, `excerpt`, `published`, `modified`, `date` (now),
`keywords`, `tags` (yours, from the options), `image`, `lang`, `clipType` (`page`, `selection` or `element`) and
`content`.

- Dates take a format after a colon: `{date:YYYY-MM-DD HH:mm}` (`YYYY YY MM M DD D HH H mm ss Z`).
- Lists take a separator after a colon: `{keywords:, }`.
- Filters follow a bar and chain: `{title|kebab}`, `{title|lower|trim}`. There are `lower`, `upper`, `trim`, `kebab`,
  `snake`, `camel`, `pascal`, `title`, `filename`, `yaml`, `list`, `hashtags`, `json` and `first`.
- `{{` and `}}` are literal braces.

## Markdown and clipping options

Heading style, bullet marker, fenced or indented code and its fence, emphasis and strong markers, inline or reference
links, links kept or reduced to their text, images kept, reduced to their alt text or dropped, and GitHub tables on or
off.

**Also send the clip's images as files** pushes up to 19 of the content's images along with the Markdown; each becomes
a resource in Lens. The browser asks once to let the clipper read images from any site. **Also ask Lens to capture the
page itself** sends the address too, for a PDF snapshot next to the Markdown (needs Chromium on your Lens).

## Building it

```sh
cd extensions/browser
npm ci
npm test            # templates, metadata, Markdown, sending (jsdom)
npm run build       # dist/<target>/ for every target; --target chrome for one
```

Load `dist/chrome` with **Load unpacked** on `chrome://extensions` (Developer mode on). The Browser extension workflow
builds every target on each pull request that changes the clipper and keeps `lens-clipper-<target>.zip` with the run;
a release gets them attached.

A target is `extensions/browser/targets/<name>.json`, merged over `src/manifest.json` (a `null` removes a key), so a
browser that needs something different (Firefox's `browser_specific_settings` and background `scripts`, say) adds only
that. The code reaches the extension API as `globalThis.browser ?? globalThis.chrome`.

### Firefox

Firefox 128 or later (desktop). `targets/firefox.json` runs the background as a script instead of a service worker
and gives the add-on its id, `lens-clipper@adeelahmad.github.io`. To try a build, open `about:debugging`, choose
**This Firefox**, then **Load Temporary Add-on…** and pick `dist/firefox/manifest.json`; it stays until Firefox
restarts.

The Browser extension (Firefox) workflow runs Mozilla's linter, `web-ext lint` (the checks addons.mozilla.org runs),
on every pull request that changes the clipper, and keeps `lens-clipper-firefox.zip` (ready to upload to
addons.mozilla.org) with the run. On a release, if the `AMO_JWT_ISSUER` and `AMO_JWT_SECRET` secrets hold an
[addons.mozilla.org API key](https://addons.mozilla.org/developers/addon/api/key/), Mozilla signs it as an unlisted
add-on and the release gets `lens-clipper-firefox.xpi`, which any Firefox installs by opening it. That copy is built
from `dist/firefox-selfhosted`, so it updates itself from the newest release (see below).

## Releases and updates

Every Lens release builds the clipper for each browser and attaches `lens-clipper-<browser>.zip` to it. How a copy
keeps itself up to date depends on where it came from. Each publishing step runs only when its repository secrets
are set, and is skipped otherwise:

| Browser | Updates through | Secrets |
| --- | --- | --- |
| Chrome | the Chrome Web Store: each release is uploaded and published | `CWS_EXTENSION_ID`, `CWS_CLIENT_ID`, `CWS_CLIENT_SECRET`, `CWS_REFRESH_TOKEN` |
| Edge | Microsoft Edge Add-ons: each release is uploaded and submitted | `EDGE_PRODUCT_ID`, `EDGE_CLIENT_ID`, `EDGE_API_KEY` |
| Chrome, Edge (self-hosted) | `lens-clipper-<browser>.crx`, which checks `lens-clipper-<browser>-updates.xml` on the newest release | `CRX_PRIVATE_KEY` |
| Firefox | the Mozilla-signed `lens-clipper-firefox.xpi`, which checks `lens-clipper-firefox-updates.json` on the newest release | `AMO_JWT_ISSUER`, `AMO_JWT_SECRET` |
| Safari | the App Store, from the Safari build | see the Safari section |

- Chrome and Edge only install a `.crx` from outside their stores through policy (`ExtensionInstallForcelist`
  with the update XML's address) or on Linux. For everyone else the stores are the way to get updates.
- `CRX_PRIVATE_KEY` is a PEM RSA key (`openssl genrsa 2048`), and it fixes the extension's ID: keep it, because a new
  key makes a different extension.
- The self-hosted builds are `dist/<browser>-selfhosted`. They are built when `EXT_UPDATE_BASE` is set, which a release
  sets to its `releases/latest/download` address, so every installed copy finds the newest version.
