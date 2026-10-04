# Lens brand

The Search Lens identity, v1.0 (4 October 2026). These are the files the repository uses; the full kit (app icons for
every platform, social images, CLI banners, print PDFs) is kept outside the repository.

| File | Use |
| --- | --- |
| `lens-horizontal-light.svg`, `lens-horizontal-dark.svg` | Mark and wordmark on light or dark surfaces |
| `lens-horizontal-*-tagline.svg` | The same, with "Your life. Your AI." |
| `lens-mark-colour.svg` | The mark alone: app icons, favicons, avatars |
| `lens-mark-ink.svg`, `lens-mark-white.svg` | One-colour mark for tinted or monochrome surfaces |
| `lens-github-social-light-1280x640.png` | The repository's social preview (GitHub settings, General) |
| `lens-open-graph-light-1200x630.png` | Link previews |
| `lens.css`, `lens-colours.json` | Brand colours and type |

## Colours

| Token | sRGB |
| --- | --- |
| Blue | `#1A73E8` |
| Red | `#EA4335` |
| Green | `#34A853` |
| Gold | `#F9AB00` |
| Ink | `#202124` |
| White | `#FFFFFF` |

The web app's `nextjs-frontend/app/styles/tokens.css` uses the same values. Type is DM Sans.

## Rules

- Keep one ring-band width of clear space around the artwork.
- Use the mark without the wordmark for icons and favicons; keep the memory dot in every variant.
- The SVGs are outlined paths, so they need no font installed.

## Where it is used

- Web app: `nextjs-frontend/components/ui/lens-mark.tsx`, `app/icon.svg`, `app/favicon.ico`, `app/apple-icon.png`,
  `app/manifest.ts` and `public/pwa/`.
- Packages: `cloudron/logo.png`, `packaging/synology/icons/`, `packaging/qnap/qpkg/icons/`.
