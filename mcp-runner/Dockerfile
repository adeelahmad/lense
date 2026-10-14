# Lens's MCP runner (runner.py): MCP servers that run as a program (stdio), in a container of their own, reached by
# Lens's backend over HTTP. The optional `mcp` service in Docker Compose; it has node/npx and uv/uvx, which is what
# MCP servers are usually started with.
FROM node:22-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 ca-certificates git \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /uvx /usr/local/bin/
COPY runner.py /usr/local/lib/lens-mcp-runner.py
RUN mkdir -p /data /key && chown node:node /data /key
USER node
ENV LENS_MCP_HOME=/data LENS_MCP_TOKEN_FILE=/key/token
VOLUME /data /key
EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=5s CMD ["python3", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/health', timeout=4)"]
ENTRYPOINT ["python3", "/usr/local/lib/lens-mcp-runner.py"]
