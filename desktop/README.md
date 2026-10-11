# Lens for macOS

A menu-bar app around Lens, built with [Tauri](https://tauri.app): `Lens-macOS-arm64.dmg` in each release, for Macs
with Apple silicon. It carries [microsandbox](../docs/microsandbox.md), so Lens runs without Docker.

**Status: built, early.** Unsigned unless the repository has signing secrets (see below).

## What it does

- **First launch:** puts the microsandbox it carries in `~/.microsandbox` (where `install.microsandbox.dev` would),
  then runs `install.sh` with the web installer, shown in the app's window with microsandbox chosen. When the install
  is done, Lens opens in the browser.
- **After that:** starts Lens if it isn't running (`~/lens/.lens-sandbox`, or Docker Compose for a Docker install)
  and opens it in the browser.
- **Menu bar:** Open Lens, Start Lens, Stop Lens, Update Lens (runs the install line again: the secrets, the database
  and the archive are kept), Show Window, Show Logs, Quit. Closing the window leaves it in the menu bar.

Lens opens in the browser, not in the app's window, because Lens signs in with passkeys and macOS lets an app's web
view use them only for its own associated web domains.

## How it's built

- `src-tauri/src/main.rs`: the window, the menu-bar item, and what the helper's output asks for.
- `resources/lens-app.sh`: the helper that does the work (`launch`, `start`, `stop`, `update`, `url`, `setup-msb`). It
  talks to the app on its output (`@status`, `@show`, `@open`, `@error`) and logs to `~/Library/Logs/Lens/app.log`.
- `ui/index.html`: the status page the window shows while Lens starts.

The [Desktop workflow](../.github/workflows/desktop.yml) builds it on a Mac runner: it downloads the latest
microsandbox release for `darwin-aarch64`, checks it against the release's checksums, puts it in the app's resources,
and builds the `.app` and the `.dmg`. Each release gets the `.dmg` attached.

Build it yourself on a Mac (Rust, Node 22):

```bash
cd desktop
npm ci
gh release download --repo superradcompany/microsandbox --pattern microsandbox-darwin-aarch64.tar.gz -D resources
npx tauri build --config '{"bundle":{"resources":{"../resources/microsandbox-darwin-aarch64.tar.gz":"microsandbox-darwin-aarch64.tar.gz"}}}'
```

Without the microsandbox bundle it still builds (`npx tauri build`); `install.sh` then downloads microsandbox itself.

## Signing

With these repository secrets the workflow signs and notarizes the app: `APPLE_CERTIFICATE` (a base64 Developer ID
Application `.p12`), `APPLE_CERTIFICATE_PASSWORD`, `APPLE_SIGNING_IDENTITY`, and for notarizing `APPLE_ID`,
`APPLE_PASSWORD` (an app-specific password) and `APPLE_TEAM_ID`. Without them the app is unsigned: right-click it and
choose **Open** the first time.

## Not yet

- Starting with the Mac (install.sh's LaunchAgent already starts the sandbox at sign-in).
- Intel Macs (microsandbox needs Apple silicon; a Docker-only build could follow).
- Windows and Linux builds of the same app.
