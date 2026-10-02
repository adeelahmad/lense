"""The Lens API.

``create_app()`` builds the app; the database opens when it starts (so importing this module, e.g. to generate the
OpenAPI schema, needs no database). Tests pass an open database and configuration instead.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.iiif import router as iiif_router
from app.api.mcp import router as mcp_router
from app.api.pages import router as pages_router
from app.api.v1.router import api_router
from app.api.v1.routes.oauth import bearer_challenge
from app.api.v1.routes.oauth import well_known as oauth_well_known
from app.config import settings
from app.core import middleware
from app.core.runtime import Archive
from app.domain import __version__, store, telemetry
from app.domain.render import WEB_DIR
from app.utils import simple_generate_unique_route_id

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _attach(app: FastAPI, archive: Archive) -> None:
    app.state.archive = archive
    app.state.db = archive.db
    app.state.settings = archive.settings
    app.state.graph_cache = {}


def create_app(cfg: dict[str, Any] | None = None, db: store.DB | None = None, background: bool | None = None) -> FastAPI:
    ready = Archive(cfg, db) if (cfg is not None or db is not None) else None

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        archive = ready or Archive()
        if ready is None:
            archive.prepare()
            _attach(app, archive)
        run = background if background is not None else settings.RUN_BACKGROUND
        if run is None:
            run = ready is None and int(archive.current()["workers"]["inline"]) > 0
        if run:
            archive.start_background()
        try:
            yield
        finally:
            archive.close()
            telemetry.shutdown()

    app = FastAPI(
        title=settings.PROJECT_NAME,
        version=__version__,
        generate_unique_id_function=simple_generate_unique_route_id,
        openapi_url=settings.OPENAPI_URL or None,
        lifespan=lifespan,
    )
    if ready is not None:
        ready.prepare()
        _attach(app, ready)

    # innermost, so the router has picked the route it names spans by; a no-op while telemetry is off
    app.add_middleware(telemetry.Middleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(settings.CORS_ORIGINS),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Total-Count"],
    )
    middleware.install(app)
    app.add_middleware(middleware.ResourcePaths)

    app.include_router(api_router, prefix="/api/v1")
    app.include_router(oauth_well_known)
    app.include_router(mcp_router)
    app.add_exception_handler(StarletteHTTPException, bearer_challenge)  # type: ignore[arg-type]
    app.include_router(iiif_router)
    app.include_router(pages_router)
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    built = app.openapi

    def openapi() -> dict[str, Any]:
        """The schema, with /api/v1/resources as the recordings' canonical paths (middleware.ResourcePaths)."""
        if not app.openapi_schema:
            app.openapi_schema = middleware.publish_resources(built())
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]
    return app


app = create_app()
