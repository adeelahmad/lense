"""The entity index, entity pages, curation (merge, rename, retype, hide, link, move mentions) and the graph explorer.

Reads cover the namespaces you have a role in; curation needs the editor role in every namespace it touches. Curation
survives re-analysis (aliases and overrides are kept), and each change drops the cached knowledge graph.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.api.deps import Access, Acl, CurrentUser, Db, Writer, domain_errors
from app.domain import auth, jobs
from app.domain import entities as ents
from app.domain.store import DB, R
from app.schemas.common import Ok
from app.schemas.entities import (
    EntityDetail,
    EntityHide,
    EntityLinkRequest,
    EntityList,
    EntityMerge,
    EntityMerged,
    EntityNotSame,
    EntityRename,
    EntityRetype,
    EntityType,
    MentionList,
    MentionMove,
    MentionMoved,
)

router = APIRouter(tags=["entities"])


def _csv_ints(v: str) -> list[int]:
    return [int(x) for x in (v or "").split(",") if x.strip().isdigit()]


def _csv_words(v: str) -> list[str] | None:
    return [x.strip() for x in (v or "").split(",") if x.strip()] or None


def _entity_space(db: DB, acl: Access, eid: int, role: str = "viewer") -> int:
    row = db.one("SELECT space FROM $r", r=R("entity", int(eid)))
    if not row:
        raise HTTPException(404, "not found")
    acl.need(row["space"], role)
    return row["space"]


def _changed(request: Request) -> None:
    request.app.state.graph_cache.clear()


# ---------- browsing ----------
@router.get("/entities")
def list_entities(
    user: CurrentUser,
    acl: Acl,
    db: Db,
    q: str = "",
    types: str = "",
    namespaces: str = "",
    speaker: int | None = None,
    recording: int | None = None,
    date_from: str = "",
    date_to: str = "",
    min_mentions: int = 1,
    hidden: bool = False,
    sort: str = "mentions",
    limit: int = 50,
    offset: int = 0,
    group: bool = False,
) -> EntityList:
    """Entities in the namespaces you can read. `types` and `namespaces` are comma-separated; `group` joins same-named ones."""
    return EntityList.model_validate(
        ents.list_entities(
            db,
            set(acl.roles),
            q,
            _csv_words(types),
            _csv_words(namespaces),
            speaker,
            recording,
            date_from or None,
            date_to or None,
            min_mentions,
            hidden,
            sort,
            min(limit, 500),
            offset,
            group,
        )
    )


@router.get("/entities/types")
def list_entity_types(user: CurrentUser) -> list[EntityType]:
    return [EntityType(type=k, label=v, quiet=k in ents.QUIET) for k, v in ents.TYPES.items()]


@router.get("/entities/suggestions")
def list_entity_suggestions(user: CurrentUser, acl: Acl, db: Db, entity: int | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """Entities that are probably the same thing (same letters, acronyms, sound-alikes), to merge or mark as different."""
    if entity:
        _entity_space(db, acl, entity)
    return ents.suggestions(db, set(acl.roles), entity, min(limit, 200))


@router.get("/entities/merges")
def list_entity_merges(user: CurrentUser, acl: Acl, db: Db) -> list[dict[str, Any]]:
    return ents.merges(db, set(acl.roles))


@router.get("/entities/timeline")
def get_entity_timeline(user: CurrentUser, acl: Acl, db: Db, ids: str, by: str = "month") -> dict[str, Any]:
    """Mentions per month (or week) per namespace for comma-separated entity ids."""
    return ents.timeline(db, _csv_ints(ids), set(acl.roles), "week" if by == "week" else "month")


# ---------- curation across entities ----------
@router.post("/entities/retype")
def retype_entities(body: EntityRetype, request: Request, user: Writer, acl: Acl, db: Db) -> Ok:
    for i in body.ids:
        _entity_space(db, acl, i, "editor")
    with domain_errors():
        ents.retype(db, body.ids, body.type)
    auth.audit(db, user.as_audit(), "entity.retype", None, {"ids": body.ids, "type": body.type})
    _changed(request)
    return Ok()


@router.post("/entities/merge")
def merge_entities(body: EntityMerge, request: Request, user: Writer, acl: Acl, db: Db) -> EntityMerged:
    for i in [body.keep, *body.others]:
        _entity_space(db, acl, i, "editor")
    with domain_errors():
        mid = ents.merge(db, body.keep, body.others, user.email)
    auth.audit(db, user.as_audit(), "entity.merge", f"entity:{body.keep}", {"others": body.others})
    _changed(request)
    return EntityMerged(merge=mid)


@router.post("/entities/merges/{mid}/undo")
def undo_entity_merge(mid: int, request: Request, user: Writer, acl: Acl, db: Db) -> Ok:
    m = db.one("SELECT space FROM $r", r=R("entity_merge", mid))
    if not m:
        raise HTTPException(404, "not found")
    acl.need(m["space"], "editor")
    with domain_errors():
        ents.undo_merge(db, mid)
    auth.audit(db, user.as_audit(), "entity.merge.undo", f"entity_merge:{mid}")
    _changed(request)
    return Ok()


@router.post("/entities/not-same")
def mark_entities_not_same(body: EntityNotSame, user: Writer, acl: Acl, db: Db) -> Ok:
    """Stop suggesting that these two are the same."""
    _entity_space(db, acl, body.a, "editor")
    _entity_space(db, acl, body.b, "editor")
    ents.not_same(db, body.a, body.b)
    auth.audit(db, user.as_audit(), "entity.not_same", None, {"a": body.a, "b": body.b})
    return Ok()


# ---------- one entity ----------
@router.get("/entities/{eid}")
def get_entity(eid: int, user: CurrentUser, acl: Acl, db: Db) -> EntityDetail:
    _entity_space(db, acl, eid)
    with domain_errors():
        return EntityDetail.model_validate(ents.detail(db, eid, set(acl.roles)))


@router.get("/entities/{eid}/mentions")
def list_entity_mentions(
    eid: int,
    user: CurrentUser,
    acl: Acl,
    db: Db,
    speaker: int | None = None,
    recording: int | None = None,
    date_from: str = "",
    date_to: str = "",
    sort: str = "newest",
    limit: int = 50,
    offset: int = 0,
) -> MentionList:
    """Every line that mentions the entity; `highlight` is the [start, end) of the mention in `text`. sort: newest, oldest, most."""
    _entity_space(db, acl, eid)
    with domain_errors():
        out = ents.mentions(db, eid, set(acl.roles), speaker, recording, date_from or None, date_to or None, sort, min(limit, 200), offset)
    return MentionList.model_validate(out)


@router.get("/entities/{eid}/connections")
def get_entity_connections(eid: int, user: CurrentUser, acl: Acl, db: Db) -> dict[str, Any]:
    """Entities mentioned together with this one, and who mentions it most."""
    _entity_space(db, acl, eid)
    with domain_errors():
        return ents.connections(db, eid, set(acl.roles))


@router.post("/entities/{eid}/rename")
def rename_entity(eid: int, body: EntityRename, request: Request, user: Writer, acl: Acl, db: Db) -> dict[str, Any]:
    """Rename; with `correct`, also fix the transcript lines (re-analysing the recordings). `dry_run` previews the lines."""
    _entity_space(db, acl, eid, "editor")
    with domain_errors():
        out = ents.rename(db, eid, body.name, body.keep_alias, body.correct, body.dry_run)
    if not body.dry_run:
        out["jobs"] = [jobs.enqueue(db, rid, ["analyze", "report"], by=user.email) for rid in out.get("recordings") or []]
        auth.audit(db, user.as_audit(), "entity.rename", f"entity:{eid}", {"name": out["name"], "lines": out["lines"]})
        _changed(request)
    return out


@router.post("/entities/{eid}/hide")
def hide_entity(eid: int, request: Request, user: Writer, acl: Acl, db: Db, body: EntityHide | None = None) -> Ok:
    body = body or EntityHide()
    _entity_space(db, acl, eid, "editor")
    ents.hide(db, eid, body.hidden, body.reason)
    action = "entity.hide" if body.hidden else "entity.restore"
    auth.audit(db, user.as_audit(), action, f"entity:{eid}", {"reason": body.reason})
    _changed(request)
    return Ok()


@router.post("/entities/{eid}/link")
def link_entity(eid: int, body: EntityLinkRequest, request: Request, user: Writer, acl: Acl, db: Db) -> Ok:
    """Say an entity in another namespace is the same thing (they stay separate; the graph joins them)."""
    other = body.with_
    _entity_space(db, acl, eid, "editor")
    _entity_space(db, acl, other, "editor")
    with domain_errors():
        ents.link(db, eid, other)
    auth.audit(db, user.as_audit(), "entity.link", f"entity:{eid}", {"with": other})
    _changed(request)
    return Ok()


@router.delete("/entities/{eid}/link/{other}")
def unlink_entity(eid: int, other: int, request: Request, user: Writer, acl: Acl, db: Db) -> Ok:
    _entity_space(db, acl, eid, "editor")
    ents.unlink(db, eid, other)
    auth.audit(db, user.as_audit(), "entity.unlink", f"entity:{eid}", {"with": other})
    _changed(request)
    return Ok()


@router.post("/mentions/{mention}/move")
def move_mention(mention: str, body: MentionMove, request: Request, user: Writer, acl: Acl, db: Db) -> MentionMoved:
    """Point one mention at another entity (or a new one), or say it isn't an entity. Survives re-analysis."""
    m = db.one("SELECT space FROM $r", r=R("mentions", mention))
    if not m:
        raise HTTPException(404, "not found")
    acl.need(m["space"], "editor")
    try:
        tid = ents.move_mention(db, mention, body.target, body.new_name, body.new_type or "TERM", body.remove)
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e)) from None
    auth.audit(db, user.as_audit(), "mention.move", f"mentions:{mention}", {"target": tid})
    _changed(request)
    return MentionMoved(entity=tid or None)


# ---------- the graph explorer ----------
def _explore_spaces(db: DB, acl: Access, scope: str) -> set[int]:
    if scope.startswith("ns:"):
        return {acl.namespace(scope[3:])}
    shared = {r["id"] for r in db.rows("SELECT record::id(id) AS id FROM space WHERE graph = 'shared'")}
    return set(acl.roles) & shared


@router.get("/graph/explore")
def explore_graph(
    user: CurrentUser,
    acl: Acl,
    db: Db,
    focus: str,
    scope: str = "all",
    depth: int = 1,
    types: str = "",
    kinds: str = "",
    min_weight: int = 1,
    limit: int = 100,
) -> dict[str, Any]:
    """The neighbourhood of a node (`e<id>` for an entity, a speaker node id...). scope: `all` (shared graphs) or `ns:<name>`."""
    spaces = _explore_spaces(db, acl, scope)
    try:
        return ents.neighbourhood(
            db, focus, spaces, max(1, min(depth, 2)), _csv_words(types), _csv_words(kinds), max(1, min_weight), min(limit, 300)
        )
    except (KeyError, ValueError):
        raise HTTPException(404, "not found in this scope") from None


@router.get("/graph/path")
def find_graph_path(user: CurrentUser, acl: Acl, db: Db, a: str, b: str, scope: str = "all") -> dict[str, Any]:
    """The shortest chain of links between two nodes, with the lines that show each link."""
    return ents.path(db, a, b, _explore_spaces(db, acl, scope))
