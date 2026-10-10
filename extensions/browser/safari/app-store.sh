#!/bin/sh
# Archives the Xcode project build.sh generated and uploads it to App Store Connect, the only way Safari extensions
# update: testers get each upload through TestFlight, everyone else once it's released on the Mac App Store. Signs with
# Xcode's cloud signing through an App Store Connect API key (Admin role), so no certificates need importing:
#   APPLE_TEAM_ID, APPLE_API_KEY_ID, APPLE_API_ISSUER, APPLE_API_KEY_P8 (the contents of the AuthKey_….p8 file)
# The app's record (bundle id dev.lens.clipper, or SAFARI_BUNDLE_ID) has to exist in App Store Connect first.
#
#   safari/app-store.sh [out-dir]    (the one build.sh wrote; default: dist/safari-app)
set -eu

here=$(cd "$(dirname "$0")/.." && pwd)
out=$(cd "${1:-$here/dist/safari-app}" && pwd)
app_name=${SAFARI_APP_NAME:-Lens Clipper}
: "${APPLE_TEAM_ID:?}" "${APPLE_API_KEY_ID:?}" "${APPLE_API_ISSUER:?}" "${APPLE_API_KEY_P8:?}"

project=$(find "$out/xcode" -maxdepth 2 -name '*.xcodeproj' | head -n 1)
version=$(cd "$here" && node -p 'require("./dist/safari/manifest.json").version')
build=${BUILD_NUMBER:-$(date -u +%Y%m%d%H%M)} # App Store Connect wants every upload's build number to be higher

key="$out/AuthKey_$APPLE_API_KEY_ID.p8"
trap 'rm -f "$key"' EXIT
(umask 077 && printf '%s\n' "$APPLE_API_KEY_P8" >"$key")
set -- -allowProvisioningUpdates -authenticationKeyPath "$key" -authenticationKeyID "$APPLE_API_KEY_ID" \
  -authenticationKeyIssuerID "$APPLE_API_ISSUER"

xcodebuild archive -project "$project" -scheme "$app_name" -configuration Release -destination 'generic/platform=macOS' \
  -archivePath "$out/LensClipper.xcarchive" DEVELOPMENT_TEAM="$APPLE_TEAM_ID" CODE_SIGN_STYLE=Automatic \
  MARKETING_VERSION="$version" CURRENT_PROJECT_VERSION="$build" \
  INFOPLIST_KEY_LSApplicationCategoryType=public.app-category.productivity "$@"

cat >"$out/ExportOptions.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>method</key><string>app-store-connect</string>
  <key>destination</key><string>upload</string>
  <key>teamID</key><string>$APPLE_TEAM_ID</string>
  <key>signingStyle</key><string>automatic</string>
</dict>
</plist>
PLIST
xcodebuild -exportArchive -archivePath "$out/LensClipper.xcarchive" -exportOptionsPlist "$out/ExportOptions.plist" \
  -exportPath "$out/export" "$@"
echo "uploaded $app_name $version ($build) to App Store Connect"
