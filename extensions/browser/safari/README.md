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

- **Updates:** Safari extensions update only through the App Store. With the App Store Connect secrets set
  (`APPLE_TEAM_ID`, `APPLE_API_KEY_ID`, `APPLE_API_ISSUER`, `APPLE_API_KEY_P8`), each release is signed and uploaded by
  `app-store.sh`; testers get it through TestFlight, everyone else once it's released on the Mac App Store. See
  [the Safari section of the docs](../../../docs/web-clipper.md#safari).
- **Signing:** the zip is unsigned and has to be re-allowed each time Safari restarts.
- **Permissions:** Safari grants hosts per site whatever the manifest says, so the Safari build lists them as
  `host_permissions` rather than optional ones; the options page still asks for your Lens server.
- **Settings:** `SAFARI_APP_NAME` and `SAFARI_BUNDLE_ID` (default `dev.lens.clipper`) change the app's name and id.

Refine later: ship it inside the Lens macOS app, an iOS/iPadOS build, App Store listing.
