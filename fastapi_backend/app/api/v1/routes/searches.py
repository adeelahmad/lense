"""Saved searches: a search's words and filters under a name. Yours, and the ones shared with namespaces you can read.

They're saved views of kind ``search`` and follow the same rules (routes/views.py; docs/api.md#searches).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.api.deps import Acl, CurrentUser, Db, Principal, Writer, domain_errors
from app.api.v1.routes.views import change, check_share, common, remove
from app.domain import auth, store, views
from app.domain.store import DB
from app.schemas.common import Ok
from app.schemas.views import SavedSearch, SearchCreate, SearchUpdate

router = APIRouter(prefix="/searches", tags=["searches"])

R = store.R


def _out(db: DB, rows: list[dict[str, Any]], user: Principal, roles: dict[int, str]) -> list[SavedSearch]:
    """With the names of their speakers and recordings, where you can read them."""
    out = common(db, rows, user, roles)
    spk = sorted({c["state"]["speaker"] for c in out if c["state"].get("speaker") is not None})
    rec = sorted({c["state"]["recording"] for c in out if c["state"].get("recording") is not None})
    speakers = (
        {
            s["id"]: s.get("name") or s.get("label")
            for s in db.rows(
                "SELECT record::id(id) AS id, name, label, space FROM speaker WHERE id IN $ids", ids=[R("speaker", i) for i in spk]
            )
            if s["space"] in roles
        }
        if spk
        else {}
    )
    titles = (
        {
            r["id"]: r.get("title")
            for r in db.rows(
                "SELECT record::id(id) AS id, title, space FROM recording WHERE id IN $ids", ids=[R("recording", i) for i in rec]
            )
            if r["space"] in roles
        }
        if rec
        else {}
    )
    return [
        SavedSearch(
            **{k: v for k, v in c.items() if k != "state"},
            **c["state"],
            speaker_name=speakers.get(c["state"].get("speaker")),
            recording_title=titles.get(c["state"].get("recording")),
        )
        for c in out
    ]


@router.get("")
def list_searches(user: CurrentUser, acl: Acl, db: Db) -> list[SavedSearch]:
    """Your saved searches, then the ones shared with namespaces you can read; the latest changed first in each."""
    return _out(db, views.visible(db, user.id, set(acl.roles), "search"), user, acl.roles)


@router.post("")
def create_search(body: SearchCreate, user: Writer, acl: Acl, db: Db) -> SavedSearch:
    """Save a search (its words, namespace, speaker, emotion, recording and object) under a name unique among yours. Sharing it
    with its namespace needs editor access there."""
    sid = acl.namespace(body.namespace) if body.namespace else None
    if body.recording is not None:
        acl.recording(body.recording)
    if body.shared:
        check_share(acl, sid, "saved search")
    state = body.model_dump(include={"q", "speaker", "emotion", "recording", "object"})
    with domain_errors():
        vid = views.create(db, user.id, body.name, sid, state, body.shared, kind="search")
    if body.shared:
        auth.audit(db, user.as_audit(), "search.share", f"search:{vid}", {"name": body.name, "namespace": body.namespace})
    return _out(db, [views.get(db, vid)], user, acl.roles)[0]


@router.patch("/{sid}")
def update_search(sid: int, body: SearchUpdate, user: Writer, acl: Acl, db: Db) -> SavedSearch:
    """Rename it, or share or unshare it. Its maker only; sharing needs editor access to its namespace, and a search of
    every namespace can't be shared."""
    change(db, acl, user, sid, "search", body.name, body.shared, None)
    return _out(db, [views.get(db, sid)], user, acl.roles)[0]


@router.delete("/{sid}")
def delete_search(sid: int, user: Writer, acl: Acl, db: Db) -> Ok:
    """Delete it: its maker, or for a shared one an owner of its namespace. Deleting a shared one is audited."""
    remove(db, acl, user, sid, "search")
    return Ok()
