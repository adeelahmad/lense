#!/bin/sh
# Wraps dist/safari (the clipper built for Safari) into a Safari Web Extension: a small macOS app that carries the
# extension, generated with Apple's safari-web-extension-converter and built with xcodebuild. Needs macOS with Xcode.
# Unsigned unless SAFARI_TEAM_ID is set; Safari loads an unsigned one only with Develop > Allow Unsigned Extensions.
#
#   safari/build.sh [out-dir]    (default: dist/safari-app; writes lens-clipper-safari.zip there)
set -eu

here=$(cd "$(dirname "$0")/.." && pwd)
out=${1:-$here/dist/safari-app}
app_name=${SAFARI_APP_NAME:-Lens Clipper}
bundle_id=${SAFARI_BUNDLE_ID:-dev.lens.clipper}

cd "$here"
[ -d node_modules/esbuild ] || npm ci --include=dev
node scripts/build.mjs --target safari --check

rm -rf "$out" && mkdir -p "$out"
out=$(cd "$out" && pwd)
xcrun safari-web-extension-converter "$here/dist/safari" \
  --project-location "$out/xcode" --app-name "$app_name" --bundle-identifier "$bundle_id" \
  --macos-only --swift --copy-resources --no-open --no-prompt --force

project=$(find "$out/xcode" -maxdepth 2 -name '*.xcodeproj' | head -n 1)
# The converter gives the extension $bundle_id.Extension but names the app from the app name (dev.lens.Lens-Clipper),
# and Xcode refuses an extension whose id doesn't start with its app's; make the app $bundle_id.
bundle_id=$bundle_id perl -pi -e 's/(PRODUCT_BUNDLE_IDENTIFIER = )(?!"?[^;]*\.Extension"?;)[^;]*;/$1"$ENV{bundle_id}";/' \
  "$project/project.pbxproj"
if [ -n "${SAFARI_TEAM_ID:-}" ]; then
  set -- DEVELOPMENT_TEAM="$SAFARI_TEAM_ID" CODE_SIGN_STYLE=Automatic
else
  set -- CODE_SIGNING_ALLOWED=NO CODE_SIGN_IDENTITY=
fi
version=$(node -p 'require("./dist/safari/manifest.json").version')
xcodebuild -project "$project" -scheme "$app_name" -configuration Release -destination 'generic/platform=macOS' \
  -derivedDataPath "$out/derived" MARKETING_VERSION="$version" "$@" build

app=$(find "$out/derived/Build/Products/Release" -maxdepth 1 -name '*.app' | head -n 1)
ditto -c -k --keepParent "$app" "$out/lens-clipper-safari.zip"
echo "built $out/lens-clipper-safari.zip ($app_name $version)"
