"""Comments on resources, threaded and resolvable, and highlights, passages marked in colour (docs/api.md#comments).

Both are for people with a role on the resource (in its namespace, or on its collection): share links and signed
links don't reach them.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.api.deps import Access, Acl, CurrentUser, Db, Principal, Track, Writer, domain_errors
from app.domain import auth, comments, highlights, store
from app.domain.store import DB
from app.schemas.comments import Comment, CommentCreate, CommentUpdate, Highlight, HighlightCreate, HighlightUpdate
from app.schemas.common import Ok

R = store.R
router = APIRouter(prefix="/recordings/{rid}", tags=["comments"])


def _recording(acl: Access, db: DB, rid: int, role: str = "viewer") -> dict[str, Any]:
    """The resource, for someone with this role in its namespace or on its collection."""
    rec = db.one("SELECT space, collection, duration_ms FROM $r", r=R("recording", rid))
    if not rec:
        raise HTTPException(404, "not found")
    acl.need_in(rec["space"], rec.get("collection"), role)
    return rec


def _people(db: DB, ids: set[int]) -> dict[int, dict[str, Any]]:
    if not ids:
        return {}
    rows = db.rows("SELECT record::id(id) AS id, email, name FROM account WHERE id IN $ids", ids=[R("account", a) for a in ids])
    return {a["id"]: a for a in rows}


def _rank(acl: Access, rec: dict[str, Any]) -> int:
    return acl.rank_in(rec["space"], rec.get("collection"))


def _comment(db: DB, rid: int, cid: int) -> dict[str, Any]:
    try:
        c = comments.get(db, cid)
    except KeyError:
        raise HTTPException(404, "not found") from None
    if c["recording"] != rid:
        raise HTTPException(404, "not found")
    return c


def _comments_out(db: DB, rows: list[dict[str, Any]], rank: int, user: Principal) -> list[Comment]:
    people = _people(db, {c["account"] for c in rows} | {c["resolved_by"] for c in rows if c.get("resolved_by") is not None})
    editor, owner = rank >= auth.ROLES["editor"], rank >= auth.ROLES["owner"]
    out = []
    for c in rows:
        by, res = people.get(c["account"], {}), people.get(int(c["resolved_by"]), {}) if c.get("resolved_by") is not None else {}
        mine = c["account"] == user.id
        out.append(
            Comment(
                id=c["id"],
                recording=c["recording"],
                parent=c.get("parent"),
                text=c["text"],
                t0=c.get("t0"),
                t1=c.get("t1"),
                quote=c.get("quote"),
                resolved=bool(c.get("resolved")),
                resolved_by=res.get("email"),
                resolved_by_name=res.get("name") or None,
                resolved_at=c.get("resolved_at"),
                created_by=by.get("email"),
                created_by_name=by.get("name") or None,
                created_at=c.get("created_at"),
                updated_at=c.get("updated_at"),
                edited_at=c.get("edited_at"),
                mine=mine,
                can_resolve=c.get("parent") is None and (mine or editor),
                can_delete=mine or owner,
            )
        )
    return out


@router.get("/comments")
def list_comments(rid: int, user: CurrentUser, acl: Acl, db: Db) -> list[Comment]:
    """The resource's comments, threaded: each thread (the ones about the whole resource first, then by moment)
    followed by its replies, the earliest first."""
    rec = _recording(acl, db, rid)
    return _comments_out(db, comments.on(db, rid), _rank(acl, rec), user)


@router.post("/comments")
def create_comment(rid: int, body: CommentCreate, user: Writer, acl: Acl, db: Db, track: Track) -> Comment:
    """Comment on a moment or passage (`t0`–`t1`, with the `quote` picked in the text) or on the whole resource, or
    with `parent` reply on a thread (a reply to a reply goes on the thread too). Anyone who can read the resource
    can, up to 1,000 each on a resource."""
    rec = _recording(acl, db, rid)
    with domain_errors():
        cid = comments.create(db, rid, rec["space"], user.id, body.text, body.t0, body.t1, body.quote, body.parent, rec.get("duration_ms"))
    track("comment", rid, rec["space"], rec.get("collection"))
    return _comments_out(db, [comments.get(db, cid)], _rank(acl, rec), user)[0]


@router.patch("/comments/{cid}")
def update_comment(rid: int, cid: int, body: CommentUpdate, user: Writer, acl: Acl, db: Db) -> Comment:
    """Change its text (its writer only), or resolve or reopen its thread (`resolved`, on the thread's first
    comment: its writer, or an editor of the resource; audited as `comment.resolve` and `comment.reopen`)."""
    rec = _recording(acl, db, rid)
    c = _comment(db, rid, cid)
    rank = _rank(acl, rec)
    if body.text is None and body.resolved is None:
        raise HTTPException(400, "change the text, or resolve or reopen the thread")
    if body.text is not None and c["account"] != user.id:
        raise HTTPException(403, "only its writer can change a comment")
    if body.resolved is not None and c["account"] != user.id and rank < auth.ROLES["editor"]:
        raise HTTPException(403, "only its writer or an editor of the resource can resolve a thread")
    with domain_errors():
        if body.text is not None:
            comments.update(db, cid, body.text)
        if body.resolved is not None and body.resolved != bool(c.get("resolved")):
            comments.resolve(db, cid, user.id, body.resolved)
            auth.audit(db, user.as_audit(), "comment.resolve" if body.resolved else "comment.reopen", f"recording:{rid}", {"comment": cid})
    return _comments_out(db, [comments.get(db, cid)], rank, user)[0]


@router.delete("/comments/{cid}")
def delete_comment(rid: int, cid: int, user: Writer, acl: Acl, db: Db) -> Ok:
    """Delete it: its writer, or an owner of the resource (of its namespace, or an admin of its collection). A
    thread's first comment takes its replies with it. Audited (`comment.delete`)."""
    rec = _recording(acl, db, rid)
    c = _comment(db, rid, cid)
    if c["account"] != user.id and _rank(acl, rec) < auth.ROLES["owner"]:
        raise HTTPException(403, "only its writer or an owner of the resource can delete it")
    replies = comments.delete(db, cid)
    auth.audit(
        db, user.as_audit(), "comment.delete", f"recording:{rid}", {"comment": cid, "writer": c["account"] == user.id, "replies": replies}
    )
    return Ok()


def _highlight(db: DB, rid: int, hid: int) -> dict[str, Any]:
    try:
        h = highlights.get(db, hid)
    except KeyError:
        raise HTTPException(404, "not found") from None
    if h["recording"] != rid:
        raise HTTPException(404, "not found")
    return h


def _highlights_out(db: DB, rows: list[dict[str, Any]], rank: int, user: Principal) -> list[Highlight]:
    people = _people(db, {h["account"] for h in rows})
    editor = rank >= auth.ROLES["editor"]
    return [
        Highlight(
            id=h["id"],
            recording=h["recording"],
            t0=h["t0"],
            t1=h["t1"],
            quote=h.get("quote"),
            colour=h["colour"],
            label=h.get("label"),
            created_by=people.get(h["account"], {}).get("email"),
            created_by_name=people.get(h["account"], {}).get("name") or None,
            created_at=h.get("created_at"),
            updated_at=h.get("updated_at"),
            mine=h["account"] == user.id,
            can_edit=editor,
        )
        for h in rows
    ]


@router.get("/highlights")
def list_highlights(rid: int, user: CurrentUser, acl: Acl, db: Db) -> list[Highlight]:
    """The resource's highlights, by passage, for everyone who can read it."""
    rec = _recording(acl, db, rid)
    return _highlights_out(db, highlights.on(db, rid), _rank(acl, rec), user)


@router.post("/highlights")
def create_highlight(rid: int, body: HighlightCreate, user: Writer, acl: Acl, db: Db) -> Highlight:
    """Mark a passage (`t0`–`t1`, with the `quote` picked in the text) in a `colour` (yellow, green, blue or red),
    with a `label`: editors of the resource. Up to 1,000 on a resource."""
    rec = _recording(acl, db, rid, "editor")
    with domain_errors():
        hid = highlights.create(
            db, rid, rec["space"], user.id, body.t0, body.t1, body.quote, body.colour, body.label, rec.get("duration_ms")
        )
    return _highlights_out(db, [highlights.get(db, hid)], _rank(acl, rec), user)[0]


@router.patch("/highlights/{hid}")
def update_highlight(rid: int, hid: int, body: HighlightUpdate, user: Writer, acl: Acl, db: Db) -> Highlight:
    """Change its colour or its label (an empty label clears it): editors of the resource."""
    rec = _recording(acl, db, rid, "editor")
    _highlight(db, rid, hid)
    if body.colour is None and body.label is None:
        raise HTTPException(400, "change the colour or the label")
    with domain_errors():
        highlights.update(db, hid, body.colour, body.label)
    return _highlights_out(db, [highlights.get(db, hid)], _rank(acl, rec), user)[0]


@router.delete("/highlights/{hid}")
def delete_highlight(rid: int, hid: int, user: Writer, acl: Acl, db: Db) -> Ok:
    """Delete it: editors of the resource. Audited (`highlight.delete`)."""
    _recording(acl, db, rid, "editor")
    h = _highlight(db, rid, hid)
    highlights.delete(db, hid)
    auth.audit(db, user.as_audit(), "highlight.delete", f"recording:{rid}", {"highlight": hid, "writer": h["account"] == user.id})
    return Ok()
