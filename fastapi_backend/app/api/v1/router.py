"""Every /api/v1 route, one module per area."""

from fastapi import APIRouter

from app.api.v1.routes import (
    admin,
    auth,
    batches,
    chats,
    collections,
    entities,
    fields,
    files,
    hierarchy,
    iiif,
    imports,
    jobs,
    metadata,
    namespaces,
    notes,
    pipelines,
    public,
    recordings,
    requests,
    search,
    searches,
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
    hierarchy,
    recordings,
    notes,
    files,
    fields,
    imports,
    uploads,
    search,
    searches,
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
