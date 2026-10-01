"""Descriptive metadata: per recording (editors), per namespace with a profile of required fields and defaults (owners),
bulk edits, and a history of every change that can be reverted.

The access fields (access, open, featured) decide who sees a recording and what IIIF publishes (docs/access.md), so
changing them takes an owner of the namespace.
"""

from __future__ import annotations

from collections.abc import Iterable

from fastapi import APIRouter, HTTPException

from app.api.deps import Acl, Cfg, CurrentUser, Db, Writer, domain_errors
from app.domain import auth
from app.domain import metadata as md
from app.domain.store import R
from app.schemas.common import Ok
from app.schemas.metadata import (
    MetadataBulk,
    MetadataBulkResult,
    MetadataEdit,
    NamespaceMetadata,
    NamespaceMetadataUpdate,
    RecordingMetadata,
    RecordingMetadataUpdate,
)

router = APIRouter(tags=["metadata"])
ACCESS_FIELDS = set(md.COLUMNS)  # access, open, featured: publishing, which is for owners


def _role_for(fields: Iterable[str]) -> str:
    return "owner" if ACCESS_FIELDS & set(fields) else "editor"


@router.get("/recordings/{rid}/metadata")
def get_recording_metadata(rid: int, user: CurrentUser, acl: Acl, db: Db, cfg: Cfg) -> RecordingMetadata:
    acl.recording(rid)
    return RecordingMetadata.model_validate(md.get(db, cfg, rid))


@router.put("/recordings/{rid}/metadata")
def update_recording_metadata(rid: int, body: RecordingMetadataUpdate, user: Writer, acl: Acl, db: Db, cfg: Cfg) -> RecordingMetadata:
    """Save fields (null clears one) or put them back to their derived values. The access fields need an owner."""
    acl.recording(rid, _role_for([*body.set, *body.reset]))
    with domain_errors():
        md.save(db, cfg, rid, body.set, body.reset, user.email)
    auth.audit(db, user.as_audit(), "metadata.save", f"recording:{rid}", sorted(body.set))
    return RecordingMetadata.model_validate(md.get(db, cfg, rid))


@router.get("/recordings/{rid}/metadata/history")
def list_recording_metadata_history(rid: int, user: CurrentUser, acl: Acl, db: Db) -> list[MetadataEdit]:
    acl.recording(rid)
    return [MetadataEdit.model_validate(e) for e in md.history(db, f"recording:{rid}")]


@router.post("/metadata/edits/{eid}/revert")
def revert_metadata_edit(eid: int, user: Writer, acl: Acl, db: Db, cfg: Cfg) -> Ok:
    """Put a recording's (editors) or namespace's (owners) metadata back to how it was before this edit."""
    e = db.one("SELECT target, before FROM $r", r=R("meta_edit", eid))
    if not e:
        raise HTTPException(404, "not found")
    kind, key = e["target"].split(":")
    if kind == "recording":
        before = md.stored(db, int(key))
        after = md._split({"meta_json": e["before"]})  # what the revert puts back, old access levels converted
        acl.recording(int(key), _role_for(f for f in ACCESS_FIELDS if before.get(f) != after.get(f)))
    else:
        acl.need(int(key), "owner")
    with domain_errors():
        md.revert(db, cfg, eid, user.email)
    auth.audit(db, user.as_audit(), "metadata.revert", e["target"])
    return Ok()


@router.get("/namespaces/{name}/metadata")
def get_namespace_metadata(name: str, user: CurrentUser, acl: Acl, db: Db) -> NamespaceMetadata:
    """The namespace's description and metadata profile, for anyone who sees some of it (the profile says how its
    recordings are catalogued)."""
    acl.scope(name)
    return NamespaceMetadata.model_validate(md.namespace(db, acl.nsid(name)))


@router.put("/namespaces/{name}/metadata")
def update_namespace_metadata(name: str, body: NamespaceMetadataUpdate, user: Writer, acl: Acl, db: Db) -> NamespaceMetadata:
    """Collection metadata and the profile (required fields, defaults, vocabularies, default access). Owners only."""
    sid = acl.namespace(name, "owner")
    with domain_errors():
        out = md.save_namespace(db, sid, body.meta, body.profile, user.email)
    auth.audit(db, user.as_audit(), "metadata.namespace", name)
    return NamespaceMetadata.model_validate({"name": name, **out})


@router.post("/metadata/bulk")
def bulk_update_metadata(body: MetadataBulk, user: Writer, acl: Acl, db: Db, cfg: Cfg) -> MetadataBulkResult:
    """Set or clear fields on many recordings. `dry_run` (the default) reports what would change."""
    rids = list(body.recordings)
    if body.namespace:
        sid = acl.namespace(body.namespace, "editor")
        rids += db.values("SELECT VALUE record::id(id) FROM recording WHERE space = $s", s=sid)
    for rid in set(rids):
        acl.recording(rid, _role_for([*body.set, *body.clear]))
    with domain_errors():
        out = md.bulk(db, cfg, sorted(set(rids)), body.set, body.clear, user.email, body.dry_run)
    if not body.dry_run:
        auth.audit(db, user.as_audit(), "metadata.bulk", None, out)
    return MetadataBulkResult.model_validate(out)
