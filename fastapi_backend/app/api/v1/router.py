"""Every /api/v1 route, one module per area."""

from fastapi import APIRouter

from app.api.v1.routes import (
    admin,
    auth,
    batches,
    chats,
    collections,
    entities,
    iiif,
    imports,
    jobs,
    metadata,
    namespaces,
    pipelines,
    recordings,
    search,
    sources,
    speakers,
    templates,
    users,
    video,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(auth.tokens)
for module in (
    users,
    admin,
    namespaces,
    recordings,
    imports,
    search,
    speakers,
    entities,
    metadata,
    video,
    iiif,
    jobs,
    sources,
    templates,
    pipelines,
    chats,
    collections,
    batches,
):
    api_router.include_router(module.router)
