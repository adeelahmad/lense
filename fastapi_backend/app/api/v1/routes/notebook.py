"""Notes as pages (docs/notes.md): free notes in a tree, and the page of every resource, entity and topic.

Pages belong to a namespace: anyone with a role there reads them, editors write them. A thing's own page is made the
first time someone writes on it; until then it reads as a draft.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import Access, Acl, CurrentUser, Db, Principal, Writer, domain_errors
from app.domain import auth, notebook, store
from app.domain.store import DB
from app.schemas.common import Ok
from app.schemas.notebook import (
    NoteLinkTarget,
    NotePage,
    NotePageCreate,
    NotePageDraft,
    NotePageItem,
    NotePageMove,
    NotePageUpdate,
    NoteTree,
)

router = APIRouter(prefix="/notes", tags=["notes"])


def _page(acl: Access, db: DB, pid: int, role: str = "viewer") -> dict[str, Any]:
    try:
        p = notebook.get(db, pid)
    except KeyError:
        raise HTTPException(404, "not found") from None
    acl.need(p["space"], role)
    return p


def _backlinks(acl: Access, db: DB, sid: int, targets: list[str]) -> list[dict[str, Any]]:
    return notebook.backlinks(db, sid, targets) if targets else []


def _out(acl: Access, db: DB, p: dict[str, Any], user: Principal) -> NotePage:
    names = store.space_names(db)
    links = notebook.links(db, p["id"])
    found = notebook.labels(db, [x["target"] for x in links])
    readable = set(acl.spaces()) if not user.admin else set(names)
    out_links = []
    for x in links:
        t = found.get(x["target"])
        seen = t is not None and t.get("space") in readable
        out_links.append({**x, "name": t["name"] if seen else None, "namespace": names.get(t["space"]) if seen else None})
    writer = db.one("SELECT email FROM $r", r=store.R("account", p["created_by"])) if p.get("created_by") else None
    targets = [f"page:{p['id']}"] + ([p["about"]] if p.get("about") else [])
    return NotePage(
        **{k: v for k, v in p.items() if k not in ("space", "created_by", "updated_by")},
        namespace=names.get(p["space"], ""),
        created_by=(writer or {}).get("email"),
        links=out_links,
        backlinks=[b for b in _backlinks(acl, db, p["space"], targets) if b["page"] != p["id"]],
        can_edit=user.can_write and auth.allows(acl.roles, p["space"], "editor"),
    )


@router.get("")
def list_pages(ns: str, user: CurrentUser, acl: Acl, db: Db, everything: bool = Query(False, alias="all")) -> NoteTree:
    """The namespace's free notes for the tree (no bodies), in order; `all=true` adds the pages of things."""
    sid = acl.namespace(ns)
    return NoteTree(namespace=ns, pages=[NotePageItem(**p) for p in notebook.tree(db, sid, everything)])


@router.post("")
def create_page(body: NotePageCreate, user: Writer, acl: Acl, db: Db) -> NotePage:
    """Write a free note (optionally inside `parent`), or the page of a thing (`about`, like "recording:12"), which
    each thing has one of. Needs editor access to the namespace."""
    sid = acl.namespace(body.ns, "editor")
    with domain_errors():
        pid = notebook.create(
            db,
            sid,
            user.id,
            body.title,
            body.body,
            body.summary,
            body.date,
            body.place,
            body.parent,
            body.about,
            doc=body.doc,
            view=body.view,
        )
    return _out(acl, db, notebook.get(db, pid), user)


@router.get("/targets")
def link_targets(
    ns: str, user: CurrentUser, acl: Acl, db: Db, q: str = "", sign: str = "@", limit: int = Query(20, ge=1, le=50)
) -> list[NoteLinkTarget]:
    """What a mention can link to, best matches first: `#` for topics; `@` for pages, recordings, people and other
    entities, collections and speakers."""
    sid = acl.namespace(ns)
    if sign not in ("@", "#"):
        raise HTTPException(400, "sign is @ or #")
    return [NoteLinkTarget(**t) for t in notebook.search_targets(db, sid, q, sign, limit)]


@router.get("/about/{kind}/{key}")
def page_about(kind: str, key: int, user: CurrentUser, acl: Acl, db: Db) -> NotePage | NotePageDraft:
    """The page of a recording, entity, topic, collection or speaker: its page, or a draft while nobody has written one."""
    thing = f"{kind}:{key}"
    with domain_errors():
        notebook._about(thing)
        sid = notebook.owner(db, kind, key)
    acl.need(sid, "viewer")
    if kind == "recording":
        rec = db.one("SELECT space, collection FROM $r", r=store.R("recording", key))
        acl.need_in(rec["space"], rec.get("collection"))
    p = notebook.about(db, sid, thing)
    if p:
        return _out(acl, db, p, user)
    name = notebook.labels(db, [thing]).get(thing, {}).get("name") or thing
    return NotePageDraft(
        namespace=store.space_names(db).get(sid, ""),
        about=thing,
        title=str(name).rsplit("/", 1)[-1],
        backlinks=_backlinks(acl, db, sid, [thing]),
        can_edit=user.can_write and auth.allows(acl.roles, sid, "editor"),
    )


@router.get("/{pid}")
def get_page(pid: int, user: CurrentUser, acl: Acl, db: Db) -> NotePage:
    """A page with its body, its links (with their targets' current names) and the pages linking to it."""
    return _out(acl, db, _page(acl, db, pid), user)


@router.patch("/{pid}")
def update_page(pid: int, body: NotePageUpdate, user: Writer, acl: Acl, db: Db) -> NotePage:
    """Change what's given. A new body without `doc` keeps the editor's state (drawings on the canvas live there) but
    marks it stale (`doc_stale`), so the editor brings its text in line with the body."""
    _page(acl, db, pid, "editor")
    given = body.model_fields_set
    kw: dict[str, Any] = {k: getattr(body, k) for k in ("summary", "date", "doc") if k in given}
    if body.view is not None:
        kw["view"] = body.view
    if "place" in given:
        kw["place"] = body.place or None
    with domain_errors():
        notebook.update(db, pid, user.id, title=body.title, body=body.body, **kw)
    return _out(acl, db, notebook.get(db, pid), user)


@router.post("/{pid}/move")
def move_page(pid: int, body: NotePageMove, user: Writer, acl: Acl, db: Db) -> NotePage:
    """Move a free note in the tree: inside `parent` (null: the top), before `before` (null: at the end)."""
    _page(acl, db, pid, "editor")
    with domain_errors():
        notebook.move(db, pid, body.parent, body.before)
    return _out(acl, db, notebook.get(db, pid), user)


@router.delete("/{pid}")
def delete_page(pid: int, user: Writer, acl: Acl, db: Db) -> Ok:
    """Delete a page; the pages inside it move up a level. Needs editor access."""
    _page(acl, db, pid, "editor")
    notebook.delete(db, pid)
    return Ok()
