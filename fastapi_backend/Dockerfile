# Lens API and worker image. The same image runs `fastapi` (API) or `lens worker` (background jobs).
# Two targets: the default one (lean), and `full` (docker build --target full -t lens:full), which adds what makes
# Word, PowerPoint and spreadsheet files, text, web pages and emails into documents: LibreOffice, Chromium and fonts
# for most scripts; and what finds objects in frames and pages: ONNX Runtime and the YOLOX-s model (both Apache-2.0).
# Outlook .msg emails also need the msg extra (extract-msg, GPL-3.0): --build-arg EXTRAS="msg".
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

# Optional processing extras baked into the image, e.g. --build-arg EXTRAS="sensevoice voices" or "doctr" (PyTorch)
ARG EXTRAS=""
# Development images also get the dev tools (tests, linters, the OpenAPI watcher)
ARG DEV=false
# docTR's OpenCV (opencv-python, not the headless build) needs libGL and GLib
RUN case " $EXTRAS " in *" doctr "*) apt-get update \
      && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*;; esac

# faster-whisper (the whisper extra) is always in: transcription works out of the box, on the CPU (SenseVoice, which
# needs PyTorch, is an extra)
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --extra whisper \
      $( [ "$DEV" = "true" ] || echo --no-dev ) \
      $(for e in $EXTRAS; do printf -- '--extra %s ' "$e"; done)

COPY . .
RUN uv sync --frozen --extra whisper $( [ "$DEV" = "true" ] || echo --no-dev ) $(for e in $EXTRAS; do printf -- '--extra %s ' "$e"; done) \
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
# objects: ONNX Runtime (the objects extra) and YOLOX-s, checked against its hash, where objects.py looks for it
ARG EXTRAS=""
ARG DEV=false
ARG YOLOX_URL=https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_s.onnx
ARG YOLOX_SHA256=c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063
RUN uv sync --frozen --extra whisper --extra objects $( [ "$DEV" = "true" ] || echo --no-dev ) $(for e in $EXTRAS; do printf -- '--extra %s ' "$e"; done) \
    && mkdir -p /opt/lens/models \
    && python -c "import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])" "$YOLOX_URL" /opt/lens/models/yolox_s.onnx \
    && echo "$YOLOX_SHA256  /opt/lens/models/yolox_s.onnx" | sha256sum -c -
USER lens

# the default: everything above, without the converters
FROM base AS lean
