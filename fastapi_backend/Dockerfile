# Lens API and worker image. The same image runs `fastapi` (API) or `lens worker` (background jobs).
# Two targets: the default one (lean), and `full` (docker build --target full -t lens:full), which adds what makes
# Word, PowerPoint and spreadsheet files, text, web pages and emails into documents: LibreOffice, Chromium and fonts
# for most scripts. Heavy Python extras and models (speech, voice IDs, faces, objects) are in neither: a worker fetches
# them in the background, at run time, when a job first needs them or they're turned on in Settings → Components.
# Outlook .msg emails also need the msg extra (extract-msg, GPL-3.0): --build-arg EXTRAS="msg".
FROM python:3.14-slim-bookworm AS base

# UV_NO_CACHE: no package cache left in the image (it doubled its size) or in the container (what workers fetch); the
# build's own `uv sync` steps use a BuildKit cache mount instead, outside the image, so a rerun after a failed or
# interrupted download picks up where it stopped. UV_HTTP_TIMEOUT and UV_HTTP_RETRIES: slow or flaky networks (a
# Raspberry Pi on Wi-Fi) finish the big wheels instead of failing the install after uv's default 30 seconds.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_HTTP_TIMEOUT=300 \
    UV_HTTP_RETRIES=5 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

# ffmpeg: audio and video; tesseract: text on screen; antiword + poppler: legacy .doc and PDF fallbacks
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg tesseract-ocr antiword poppler-utils ca-certificates \
    && rm -rf /var/lib/apt/lists/*
# rclone reaches S3, Dropbox, Google Drive, OneDrive, SFTP, SMB and WebDAV sources
COPY --from=rclone/rclone:1.75.1 /usr/local/bin/rclone /usr/local/bin/rclone
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv

WORKDIR /app

# Optional processing extras baked into the image, e.g. --build-arg EXTRAS="sensevoice voices" or "doctr" (PyTorch)
ARG EXTRAS=""
# Development images also get the dev tools (tests, linters, the OpenAPI watcher)
ARG DEV=false
# docTR's OpenCV (opencv-python, not the headless build) needs libGL and GLib
RUN case " $EXTRAS " in *" doctr "*) apt-get update \
      && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*;; esac

# No speech-to-text engine is baked in (light by default: cloud speech users never need one). The worker fetches the
# configured one on first use (components.py); EXTRAS="whisper" bakes faster-whisper in, EXTRAS="sensevoice" SenseVoice.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv env -u UV_NO_CACHE uv sync --frozen --no-install-project \
      $( [ "$DEV" = "true" ] || echo --no-dev ) \
      $(for e in $EXTRAS; do printf -- '--extra %s ' "$e"; done)

COPY . .
RUN --mount=type=cache,target=/root/.cache/uv env -u UV_NO_CACHE uv sync --frozen $( [ "$DEV" = "true" ] || echo --no-dev ) $(for e in $EXTRAS; do printf -- '--extra %s ' "$e"; done) \
    && useradd --create-home --uid 1000 lens && mkdir -p /data && chown lens:lens /data

ENV ARCHIVE_CONFIG=/app/docker/archive.yaml
USER lens
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/auth/status', timeout=4)" || exit 1
CMD ["fastapi", "run", "app/main.py", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]

FROM base AS full
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends libreoffice-writer-nogui libreoffice-calc-nogui libreoffice-impress-nogui \
       chromium fonts-dejavu-core fonts-liberation2 fonts-noto-core fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*
USER lens

# the default: everything above, without the converters
FROM base AS lean
