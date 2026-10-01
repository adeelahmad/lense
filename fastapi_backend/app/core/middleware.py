"""HTTP guards: allowed Host headers, security headers and CORS for IIIF resources; and /api/v1/resources, the API's
name for what /api/v1/recordings serves."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

IIIF_PRIVATE = ("/iiif/auth/access", "/iiif/auth/token", "/iiif/auth/logout")
RESOURCES, RECORDINGS = "/api/v1/resources", "/api/v1/recordings"


class ResourcePaths:
    """`/api/v1/resources/…` is the API's name for recordings and the files and things that hang off them; the routes
    are written as `/api/v1/recordings/…`, which keeps working for existing clients (docs/api.md#resources). Requests
    to either reach the same route: this rewrites the first to the second before routing, so a signed link or a
    check on the path sees one spelling."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            path: str = scope.get("path") or ""
            if path == RESOURCES or path.startswith(RESOURCES + "/"):
                raw: bytes = scope.get("raw_path") or path.encode()
                scope = {
                    **scope,
                    "path": RECORDINGS + path[len(RESOURCES) :],
                    "raw_path": RECORDINGS.encode() + raw[len(RESOURCES) :] if raw.startswith(RESOURCES.encode()) else raw,
                }
        await self.app(scope, receive, send)


def publish_resources(schema: dict[str, Any]) -> dict[str, Any]:
    """The OpenAPI schema with `/api/v1/recordings/…` published as `/api/v1/resources/…` and the recordings tag as
    resources (operation ids follow), so generated clients call the canonical paths."""
    paths: dict[str, Any] = {}
    for path, ops in schema.get("paths", {}).items():
        if path == RECORDINGS or path.startswith(RECORDINGS + "/"):
            path = RESOURCES + path[len(RECORDINGS) :]
        for op in ops.values():
            if isinstance(op, dict) and "recordings" in (op.get("tags") or []):
                op["tags"] = ["resources" if t == "recordings" else t for t in op["tags"]]
                if str(op.get("operationId", "")).startswith("recordings-"):
                    op["operationId"] = "resources-" + op["operationId"][len("recordings-") :]
        paths[path] = ops
    schema["paths"] = paths
    return schema


def host_name(host: str) -> str:
    return (host.split("]")[0] + "]" if host.startswith("[") else host.rsplit(":", 1)[0]).lower()


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def guard(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        # The Host header must be one we serve: this stops DNS-rebinding attacks against a server on localhost.
        conf = getattr(request.app.state, "settings", None)
        if conf is not None:
            server = conf.current()["server"]
            allowed = [h.lower() for h in server["allowed_hosts"]]
            if "*" not in allowed and host_name(request.headers.get("host", "")) not in allowed:
                return PlainTextResponse("Invalid host header", status_code=400)
            frames = " ".join(server["embed_frame_ancestors"]) or "'none'"
        else:
            frames = "'none'"
        resp = await call_next(request)
        path = request.url.path
        # Built-in reports carry their player inline; reports rendered from people's templates may not run scripts at all.
        script = ("'none'" if "--" in path else "'self' 'unsafe-inline'") if path.startswith("/reports/") else "'self'"
        frame = frames if path.startswith(("/embed/", "/s/")) else "'none'"
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; media-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
            f"script-src {script}; connect-src 'self'; frame-ancestors {frame}",
        )
        if path.startswith("/iiif/") and not path.startswith(IIIF_PRIVATE):
            resp.headers["Access-Control-Allow-Origin"] = "*"  # IIIF viewers on any site read these
            resp.headers["Access-Control-Expose-Headers"] = "Content-Range, Accept-Ranges, Content-Length"
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        return resp
