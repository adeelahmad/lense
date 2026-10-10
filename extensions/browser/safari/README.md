# Lens web clipper for Safari

Safari runs the same clipper as Chrome and Firefox; `targets/safari.json` is all that changes in its manifest, and
`build.sh` wraps that build in the small macOS app Safari needs to carry an extension.

```sh
extensions/browser/safari/build.sh          # macOS with Xcode; writes dist/safari-app/lens-clipper-safari.zip
```

To use it: unzip, move **Lens Clipper.app** to Applications and open it once, then in Safari turn on
**Settings > Advanced > Show features for web developers**, **Develop > Allow Unsigned Extensions**, and enable
Lens Clipper in **Settings > Extensions**. Safari asks before the clipper reads a site; allow your Lens server and the
pages you clip.

The CI build (the Safari clipper workflow) runs on every pull request that touches the extension and attaches the
zip to each release.

- **Signing:** unsigned unless `SAFARI_TEAM_ID` is set (an Apple Developer team id, with its certificate in the
  keychain). An unsigned build has to be re-allowed each time Safari restarts.
- **Permissions:** Safari grants hosts per site whatever the manifest says, so the Safari build lists them as
  `host_permissions` rather than optional ones; the options page still asks for your Lens server.
- **Settings:** `SAFARI_APP_NAME` and `SAFARI_BUNDLE_ID` (default `dev.lens.clipper`) change the app's name and id.

Refine later: sign and notarize in CI, ship it inside the Lens macOS app, an iOS/iPadOS build, App Store listing.
