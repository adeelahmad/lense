#!/bin/sh
# Builds a self-contained Synology package (SPK) for Lens: the API/worker image, the web app image and SurrealDB are
# saved inside it, so the NAS needs no registry access and nobody sets up containers by hand.
#
#   packaging/synology/build.sh                    # x86_64 (most Plus models), lean image
#   ARCH=armv8 packaging/synology/build.sh         # 64-bit ARM models (cross-builds with QEMU/binfmt)
#   LENS_TARGET=full packaging/synology/build.sh   # with LibreOffice and Chromium (a much bigger package)
#   SKIP_BUILD=1 BUILD=0002 packaging/synology/build.sh   # package lens-backend:<version> and lens-frontend:<version>
#                                                         # images already built for that architecture (label them
#                                                         # net.lens.package=synology so upgrades remove them)
#
# Needs Docker with buildx. Writes dist/synology/lens-<version>-<arch>.spk.
set -eu
export COPYFILE_DISABLE=1 # macOS tar: no ._ resource-fork files in the package

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
ARCH=${ARCH:-x86_64}
LENS_TARGET=${LENS_TARGET:-lean}
EXTRAS=${EXTRAS:-}
BUILD=${BUILD:-0001}
SURREAL_IMAGE=surrealdb/surrealdb:v3.2.4
DOCKER_CLI_IMAGE=docker:27-cli
APP_VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' "$ROOT/fastapi_backend/pyproject.toml" | head -n 1)
VERSION="$APP_VERSION-$BUILD"

case "$ARCH" in
    x86_64) PLATFORM=linux/amd64 ;;
    armv8) PLATFORM=linux/arm64 ;;
    *) echo "ARCH must be x86_64 or armv8" >&2; exit 1 ;;
esac

OUT=$ROOT/dist/synology
WORK=$OUT/work-$ARCH
rm -rf "$WORK"
mkdir -p "$WORK/spk" "$WORK/package/ui/images"

if [ -z "${SKIP_BUILD:-}" ]; then
    echo "==> Building images for $PLATFORM ($LENS_TARGET)"
    docker buildx build --platform "$PLATFORM" --load --label net.lens.package=synology --target "$LENS_TARGET" --build-arg EXTRAS="$EXTRAS" \
        -t "lens-backend:$VERSION" "$ROOT/fastapi_backend"
    docker buildx build --platform "$PLATFORM" --load --label net.lens.package=synology -f "$ROOT/nextjs-frontend/Dockerfile.prod" \
        -t "lens-frontend:$VERSION" "$ROOT/nextjs-frontend"
fi
docker pull --platform "$PLATFORM" "$SURREAL_IMAGE"
docker pull --platform "$PLATFORM" "$DOCKER_CLI_IMAGE"

echo "==> Saving images into the package"
SAVE_PLATFORM=
docker save --help 2>/dev/null | grep -q -- '--platform' && SAVE_PLATFORM="--platform $PLATFORM"
# shellcheck disable=SC2086
docker save $SAVE_PLATFORM "lens-backend:$VERSION" "lens-frontend:$VERSION" "$SURREAL_IMAGE" "$DOCKER_CLI_IMAGE" \
    | gzip -1 > "$WORK/package/lens-images.tar.gz"

echo "==> Assembling the SPK"
cp -R "$HERE/package/compose" "$WORK/package/"
cp "$HERE/package/ui/config.in" "$WORK/package/ui/"
cp "$HERE"/icons/lens_*.png "$WORK/package/ui/images/"
# Package Center needs some ui/config before postinst writes the real one (with the chosen port).
sed 's/@PORT@/3000/' "$HERE/package/ui/config.in" > "$WORK/package/ui/config"
(cd "$WORK/package" && tar czf "$WORK/spk/package.tgz" .)

cp -R "$HERE/conf" "$HERE/scripts" "$HERE/WIZARD_UIFILES" "$WORK/spk/"
chmod 755 "$WORK/spk/scripts/"*
cp "$HERE/icons/lens_64.png" "$WORK/spk/PACKAGE_ICON.PNG"
cp "$HERE/icons/lens_256.png" "$WORK/spk/PACKAGE_ICON_256.PNG"
sed -e "s/@VERSION@/$VERSION/" -e "s/@ARCH@/$ARCH/" "$HERE/INFO.in" > "$WORK/spk/INFO"
# md5sum on Linux, md5 on macOS
SUM=$( (md5sum "$WORK/spk/package.tgz" 2>/dev/null || md5 -r "$WORK/spk/package.tgz") | cut -d' ' -f1)
echo "checksum=\"$SUM\"" >> "$WORK/spk/INFO"
echo "extractsize=\"$(du -sk "$WORK/package" | cut -f1)\"" >> "$WORK/spk/INFO"

SPK=$OUT/lens-$VERSION-$ARCH.spk
(cd "$WORK/spk" && tar cf "$SPK" INFO package.tgz scripts conf WIZARD_UIFILES PACKAGE_ICON.PNG PACKAGE_ICON_256.PNG)
rm -rf "$WORK"
echo "==> $SPK ($(du -h "$SPK" | cut -f1))"
