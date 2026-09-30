# Lens API and worker image. The same image runs `fastapi` (API) or `lens worker` (background jobs).
FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
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

# Optional processing extras baked into the image, e.g. --build-arg EXTRAS="sensevoice voices" (CPU torch)
ARG EXTRAS=""
# Development images also get the dev tools (tests, linters, the OpenAPI watcher)
ARG DEV=false

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project \
      $( [ "$DEV" = "true" ] || echo --no-dev ) \
      $(for e in $EXTRAS; do printf -- '--extra %s ' "$e"; done)

COPY . .
RUN uv sync --frozen $( [ "$DEV" = "true" ] || echo --no-dev ) $(for e in $EXTRAS; do printf -- '--extra %s ' "$e"; done) \
    && useradd --create-home --uid 1000 lens && mkdir -p /data && chown lens:lens /data

ENV ARCHIVE_CONFIG=/app/docker/archive.yaml
USER lens
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/auth/status', timeout=4)" || exit 1
CMD ["fastapi", "run", "app/main.py", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
