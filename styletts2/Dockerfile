# StyleTTS2 (https://github.com/yl4579/StyleTTS2, MIT; packaged as `styletts2` on PyPI, MIT) for Lens's spoken answers
# and podcasts: the optional `styletts2` service in Docker Compose, picked in Settings → AI assistant → Voice
# (fastapi_backend/app/domain/speech.py). Heavy (PyTorch, about 2 GB of image and 1 GB of model), so it's opt-in:
# `docker compose --profile styletts2 up -d`. It runs on the CPU; the model is fetched into /data on first use.
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 HOME=/data
RUN apt-get update && apt-get install -y --no-install-recommends libsndfile1 ffmpeg \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch==2.5.1 torchaudio==2.5.1 \
    && pip install "styletts2==0.1.6" "nltk==3.8.1" "numpy<2" \
    && python -m nltk.downloader -d /usr/local/share/nltk_data punkt
RUN useradd --uid 1000 --create-home --home-dir /data lens && mkdir -p /voices && chown lens /data /voices
COPY server.py /app/server.py
USER lens
VOLUME ["/data", "/voices"]
EXPOSE 8880
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8880/health', timeout=4)"
CMD ["python", "/app/server.py"]
