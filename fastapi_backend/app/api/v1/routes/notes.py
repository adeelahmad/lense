"""Notes on recordings: yours, and the ones shared with everyone who can read the recording (docs/api.md#notes).

Notes are for people with a role in the recording's namespace: share links and signed links don't reach them.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.api.deps import Access, Acl, CurrentUser, Db, Principal, Writer, domain_errors
from app.domain import auth, notes, store
from app.domain.store import DB
from app.schemas.common import Ok
from app.schemas.notes import Note, NoteCreate, NoteUpdate

R = store.R
router = APIRouter(prefix="/recordings/{rid}/notes", tags=["notes"])


def _recording(acl: Access, db: DB, rid: int, role: str = "viewer") -> dict[str, Any]:
    """The recording, for someone with this role in its namespace."""
    rec = db.one("SELECT space, duration_ms FROM $r", r=R("recording", rid))
    if not rec:
        raise HTTPException(404, "not found")
    acl.need(rec["space"], role)
    return rec


def _note(db: DB, rid: int, nid: int, user: Principal) -> dict[str, Any]:
    """One of the recording's notes this person can see (theirs, or shared), else 404."""
    try:
        n = notes.get(db, nid)
    except KeyError:
        raise HTTPException(404, "not found") from None
    if n["recording"] != rid or (n["account"] != user.id and not n["shared"]):
        raise HTTPException(404, "not found")
    return n


def _can_delete(n: dict[str, Any], space: int, user: Principal, roles: dict[int, str]) -> bool:
    """Its writer, or for a shared note an owner of the recording's namespace."""
    return n["account"] == user.id or (bool(n["shared"]) and auth.allows(roles, space, "owner"))


def _out(db: DB, rows: list[dict[str, Any]], space: int, user: Principal, roles: dict[int, str]) -> list[Note]:
    people = (
        {
            a["id"]: a
            for a in db.rows(
                "SELECT record::id(id) AS id, email, name FROM account WHERE id IN $ids",
                ids=[R("account", a) for a in {n["account"] for n in rows}],
            )
        }
        if rows
        else {}
    )
    return [
        Note(
            id=n["id"],
            recording=n["recording"],
            text=n["text"],
            t0=n.get("t0"),
            t1=n.get("t1"),
            quote=n.get("quote"),
            shared=bool(n["shared"]),
            created_by=people.get(n["account"], {}).get("email"),
            created_by_name=people.get(n["account"], {}).get("name") or None,
            created_at=n.get("created_at"),
            updated_at=n.get("updated_at"),
            edited_at=n.get("edited_at"),
            mine=n["account"] == user.id,
            can_delete=_can_delete(n, space, user, roles),
        )
        for n in rows
    ]


@router.get("")
def list_notes(rid: int, user: CurrentUser, acl: Acl, db: Db) -> list[Note]:
    """Your notes on the recording and the ones shared on it: notes about the whole recording first, then by moment."""
    rec = _recording(acl, db, rid)
    return _out(db, notes.visible(db, rid, user.id), rec["space"], user, acl.roles)


@router.post("")
def create_note(rid: int, body: NoteCreate, user: Writer, acl: Acl, db: Db) -> Note:
    """Write a note about a moment (`t0`–`t1`, with the `quote` picked in the transcript) or about the whole recording.
    Anyone who can read the recording can; only you see it unless you share it with everyone who can read the
    recording, which needs editor access. Up to 500 each on a recording. Sharing is audited (`note.share`)."""
    rec = _recording(acl, db, rid, "editor" if body.shared else "viewer")
    with domain_errors():
        nid = notes.create(db, rid, rec["space"], user.id, body.text, body.t0, body.t1, body.quote, body.shared, rec.get("duration_ms"))
    if body.shared:
        auth.audit(db, user.as_audit(), "note.share", f"recording:{rid}", {"note": nid})
    return _out(db, [notes.get(db, nid)], rec["space"], user, acl.roles)[0]


@router.patch("/{nid}")
def update_note(rid: int, nid: int, body: NoteUpdate, user: Writer, acl: Acl, db: Db) -> Note:
    """Change its text, or share or unshare it: its writer only. Sharing needs editor access; sharing and unsharing
    are audited (`note.share`, `note.unshare`)."""
    rec = _recording(acl, db, rid)
    n = _note(db, rid, nid, user)
    if n["account"] != user.id:
        raise HTTPException(403, "only its writer can change a note")
    if body.text is None and body.shared is None:
        raise HTTPException(400, "change the text, or share or unshare it")
    if body.shared and not n["shared"]:
        acl.need(rec["space"], "editor")
    with domain_errors():
        notes.update(db, nid, body.text, body.shared)
    if body.shared is not None and body.shared != bool(n["shared"]):
        auth.audit(db, user.as_audit(), "note.share" if body.shared else "note.unshare", f"recording:{rid}", {"note": nid})
    return _out(db, [notes.get(db, nid)], rec["space"], user, acl.roles)[0]


@router.delete("/{nid}")
def delete_note(rid: int, nid: int, user: Writer, acl: Acl, db: Db) -> Ok:
    """Delete it: its writer, or for a shared note an owner of the recording's namespace. Deleting a shared note is
    audited (`note.delete`)."""
    rec = _recording(acl, db, rid)
    n = _note(db, rid, nid, user)
    if not _can_delete(n, rec["space"], user, acl.roles):
        raise HTTPException(403, "only its writer or an owner of the namespace can delete it")
    notes.delete(db, nid)
    if n["shared"]:
        auth.audit(db, user.as_audit(), "note.delete", f"recording:{rid}", {"note": nid, "writer": n["account"] == user.id})
    return Ok()
