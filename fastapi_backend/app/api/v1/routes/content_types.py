"""Content types: the base types and the subtypes under them, and the subtype of one resource."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.deps import Acl, AdminWriter, CurrentUser, Db, Writer, domain_errors
from app.domain import auth, content_types
from app.schemas.common import Ok
from app.schemas.content_types import (
    ContentType,
    ContentTypeCatalog,
    ContentTypeCreate,
    ContentTypeUpdate,
    RecordingContentType,
    RecordingContentTypeSet,
)

router = APIRouter(tags=["content-types"])


@router.get("/content-types")
def list_content_types(user: CurrentUser, db: Db) -> ContentTypeCatalog:
    """Every subtype, by base type (general first)."""
    return ContentTypeCatalog(bases=list(content_types.BASES), types=content_types.all_types(db))


@router.post("/content-types")
def create_content_type(body: ContentTypeCreate, user: AdminWriter, db: Db) -> ContentType:
    rules = body.rules.model_dump(exclude_none=True) if body.rules else None
    with domain_errors():
        key = content_types.create(db, body.base, body.label, body.key, body.description, body.pipeline, rules)
    auth.audit(db, user.as_audit(), "content_type.create", f"content_type:{key}")
    return content_types.get(db, key)


@router.patch("/content-types/{key}")
def update_content_type(key: str, body: ContentTypeUpdate, user: AdminWriter, db: Db) -> ContentType:
    changes = {k: getattr(body, k) for k in body.model_fields_set}
    if "rules" in changes and body.rules is not None:
        changes["rules"] = body.rules.model_dump(exclude_none=True)
    with domain_errors():
        content_types.update(db, key, changes)
    auth.audit(db, user.as_audit(), "content_type.update", f"content_type:{key}", {"changed": sorted(changes)})
    return content_types.get(db, key)


@router.delete("/content-types/{key}")
def delete_content_type(key: str, user: AdminWriter, db: Db) -> Ok:
    """Remove a subtype (not a base type's general one). Resources that had it are recognised again."""
    with domain_errors():
        content_types.delete(db, key)
    auth.audit(db, user.as_audit(), "content_type.delete", f"content_type:{key}")
    return Ok()


@router.get("/recordings/{rid}/content-type")
def get_recording_content_type(rid: int, acl: Acl, db: Db) -> RecordingContentType:
    acl.recording(rid)
    t, chosen = content_types.of_recording(db, rid)
    return RecordingContentType(content_type=t, chosen=chosen)


@router.put("/recordings/{rid}/content-type")
def set_recording_content_type(rid: int, body: RecordingContentTypeSet, user: Writer, acl: Acl, db: Db) -> RecordingContentType:
    """Choose the resource's subtype (null: recognise it from the file again). Editors. New runs use its pipeline."""
    acl.recording(rid, "editor")
    try:
        content_types.choose(db, rid, body.content_type)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    auth.audit(db, user.as_audit(), "recording.content_type", f"recording:{rid}", {"content_type": body.content_type})
    t, chosen = content_types.of_recording(db, rid)
    return RecordingContentType(content_type=t, chosen=chosen)
