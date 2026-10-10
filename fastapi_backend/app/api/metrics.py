"""GET /metrics: the Prometheus scrape endpoint (docs/prometheus.md), off by default and answered only with its token."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse, Response

from app.api.deps import Cfg, Db
from app.domain import prometheus

router = APIRouter(tags=["metrics"])
CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


@router.get("/metrics", include_in_schema=False)
def metrics(request: Request, db: Db, cfg: Cfg) -> Response:
    """Prometheus' text format, for a scraper that sends `Authorization: Bearer <telemetry.prometheus_token>`."""
    if not prometheus.enabled(cfg):
        return PlainTextResponse("Not Found\n", status_code=404)
    if not prometheus.allowed(cfg, request.headers.get("authorization")):
        return PlainTextResponse(
            "a scrape token is needed: Authorization: Bearer <telemetry.prometheus_token>\n",
            status_code=401,
            headers={"WWW-Authenticate": 'Bearer realm="lens-metrics"'},
        )
    return Response(prometheus.render(db), media_type=CONTENT_TYPE, headers={"Cache-Control": "no-store"})
