#!/usr/bin/env bash
# Builds the Lens QPKG for QNAP NAS: one self-contained .qpkg per architecture, with the Lens images (API and worker,
# web app) and SurrealDB inside, so the NAS needs nothing but Container Station.
#
#   packaging/qnap/build.sh                        # x86_64 (Intel and AMD NAS), the full image
#   packaging/qnap/build.sh --arch arm_64          # ARM NAS (on an x86 machine this needs QEMU, see README.md)
#   packaging/qnap/build.sh --arch x86_64 --arch arm_64 --target lean --extras "msg"
#
# Needs only Docker with buildx (Linux, macOS, or Windows with WSL); QDK runs in a container built the first time.
# Output: packaging/qnap/build/Lens_<version>_<arch>.qpkg
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
QDK_REF=v2.5.3
WORK="$HERE/.work"
OUT="$HERE/build"
SURREAL_IMAGE=surrealdb/surrealdb:v3.2.4

ARCHES=()
TARGET=full     # full: LibreOffice and Chromium, to read Office files, text, web pages and emails (as `make run`)
EXTRAS=""
PREBUILT=""    # --prebuilt: package the lens-backend:<version> and lens-frontend:<version> images already in Docker
VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' "$REPO/fastapi_backend/pyproject.toml" | head -n 1)

while [ $# -gt 0 ]; do
    case "$1" in
        --arch) ARCHES+=("$2"); shift 2 ;;
        --target) TARGET="$2"; shift 2 ;;
        --extras) EXTRAS="$2"; shift 2 ;;
        --version) VERSION="$2"; shift 2 ;;
        --prebuilt) PREBUILT=yes; shift ;;
        -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done
[ ${#ARCHES[@]} -gt 0 ] || ARCHES=(x86_64)
[ ${#VERSION} -le 10 ] || { echo "QPKG versions are at most 10 characters: $VERSION" >&2; exit 2; }

platform_of() {
    case "$1" in
        x86_64) echo linux/amd64 ;;
        arm_64) echo linux/arm64 ;;
        *) echo "unsupported architecture: $1 (x86_64 or arm_64)" >&2; exit 2 ;;
    esac
}
for a in "${ARCHES[@]}"; do platform_of "$a" >/dev/null; done

# --- QDK, in a small Linux image of its own (qbuild expects GNU tools under /bin) --------------------------------
docker image inspect "lens-qdk:${QDK_REF#v}" >/dev/null 2>&1 ||
docker build -q -t "lens-qdk:${QDK_REF#v}" --build-arg QDK_REF="$QDK_REF" - >/dev/null <<'DOCKERFILE'
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates git gcc libc6-dev rsync bsdextrautils \
    && rm -rf /var/lib/apt/lists/*
ARG QDK_REF
RUN git clone -q --depth 1 --branch "$QDK_REF" https://github.com/qnap-dev/QDK.git /tmp/QDK \
    && cp -R /tmp/QDK/shared /usr/share/QDK && mkdir -p /etc/config \
    && printf 'QDK_VERSION=%s\nQDK_PATH=/usr/share/QDK\n' "${QDK_REF#v}" > /etc/config/qdk.conf \
    && gcc -o /usr/local/bin/qpkg_encrypt /tmp/QDK/src/qpkg_encrypt.c && rm -rf /tmp/QDK
ENV PATH="/usr/share/QDK/bin:$PATH"
DOCKERFILE

qbuild() {
    docker run --rm -u "$(id -u):$(id -g)" -v "$HERE:/qnap" -w /qnap/.work "lens-qdk:${QDK_REF#v}" qbuild "$@"
}

save_args=()
mkdir -p "$OUT"
for arch in "${ARCHES[@]}"; do
    platform=$(platform_of "$arch")
    echo "==> Lens $VERSION for $arch ($platform), backend target $TARGET"

    rm -rf "$WORK"
    mkdir -p "$WORK"
    cp -R "$HERE/qpkg/." "$WORK/"
    images="$WORK/$arch/images"
    mkdir -p "$images"

    if [ -z "$PREBUILT" ]; then
        docker buildx build --platform "$platform" --target "$TARGET" --build-arg EXTRAS="$EXTRAS" \
            --load -t "lens-backend:$VERSION" "$REPO/fastapi_backend"
        docker buildx build --platform "$platform" -f "$REPO/nextjs-frontend/Dockerfile.prod" \
            --load -t "lens-frontend:$VERSION" "$REPO/nextjs-frontend"
    fi
    [ "$(docker image inspect -f '{{.Os}}/{{.Architecture}}' "$SURREAL_IMAGE" 2>/dev/null)" = "$platform" ] ||
        docker pull -q --platform "$platform" "$SURREAL_IMAGE"

    # With Docker's containerd image store, save only the platform being packaged
    docker save --help 2>/dev/null | grep -q -- '--platform' && save_args=(--platform "$platform")
    : > "$images/manifest"
    for ref in "lens-backend:$VERSION" "lens-frontend:$VERSION" "$SURREAL_IMAGE"; do
        got=$(docker image inspect -f '{{.Os}}/{{.Architecture}}' "$ref")
        [ "$got" = "$platform" ] || { echo "$ref is $got, expected $platform" >&2; exit 1; }
        file=$(echo "$ref" | tr '/:' '__').tar  # left uncompressed: qbuild compresses the whole package
        echo "    saving $ref"
        docker save ${save_args[@]+"${save_args[@]}"} -o "$images/$file" "$ref"
        echo "$ref $file" >> "$images/manifest"
    done

    qbuild --build-arch "$arch" --build-version "$VERSION" --build-dir /qnap/build
done
rm -rf "$WORK"
ls -lh "$OUT"/*.qpkg
