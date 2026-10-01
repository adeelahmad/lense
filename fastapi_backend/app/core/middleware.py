"""HTTP guards: allowed Host headers, security headers and CORS for IIIF resources."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import PlainTextResponse

IIIF_PRIVATE = ("/iiif/auth/access", "/iiif/auth/token", "/iiif/auth/logout")


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
