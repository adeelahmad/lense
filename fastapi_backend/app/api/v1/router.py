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
    public,
    recordings,
    requests,
    search,
    sources,
    speakers,
    templates,
    uploads,
    users,
    video,
    views,
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
    uploads,
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
    public,
    requests,
    views,
):
    api_router.include_router(module.router)
