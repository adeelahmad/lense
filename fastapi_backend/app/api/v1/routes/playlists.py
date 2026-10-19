"""Playlists: clips of resources (a moment from t0 to t1, with a title and a note) in order, to play back to back
(docs/api.md#playlists).

A playlist is its maker's, private until shared. Whoever reads it gets only the clips of resources they have a role
on (in the resource's namespace, or on its collection): share links and signed links don't reach them.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.api.deps import Access, Acl, Cfg, CurrentUser, Db, Principal, Writer, domain_errors
from app.api.media import sign_urls
from app.api.v1.routes.recordings import audio_link
from app.domain import auth, highlights, playlists, store
from app.domain.store import DB
from app.schemas.common import Ok
from app.schemas.playlists import Clip, ClipCreate, ClipUpdate, Playlist, PlaylistCreate, PlaylistDetail, PlaylistOrder, PlaylistUpdate

R = store.R
router = APIRouter(prefix="/playlists", tags=["playlists"])


def _playlist(db: DB, pid: int, user: Principal, write: bool = False) -> dict[str, Any]:
    """A playlist this person may read (theirs, or shared) or, with write, change (theirs only); else 404."""
    try:
        p = playlists.get(db, pid)
    except KeyError:
        raise HTTPException(404, "not found") from None
    if p["account"] != user.id and (write or not p["shared"]):
        raise HTTPException(404, "not found")
    return p


def _clip(db: DB, pid: int, iid: int) -> dict[str, Any]:
    try:
        i = playlists.get_item(db, iid)
    except KeyError:
        raise HTTPException(404, "not found") from None
    if i["playlist"] != pid:
        raise HTTPException(404, "not found")
    return i


def _readable(acl: Access, db: DB, rids: set[int]) -> dict[int, dict[str, Any]]:
    """The resources among these this person has a role on, by id."""
    if not rids:
        return {}
    rows = db.rows(
        "SELECT record::id(id) AS id, space, collection, title, duration_ms, media, source FROM recording WHERE id IN $ids",
        ids=[R("recording", r) for r in sorted(rids)],
    )
    return {r["id"]: r for r in rows if acl.rank_in(r["space"], r.get("collection"))}


def _resource(acl: Access, db: DB, rid: int) -> dict[str, Any]:
    """A resource to clip: one this person has a role on, else 404."""
    rec = db.one("SELECT space, collection, duration_ms FROM $r", r=R("recording", rid))
    if not rec:
        raise HTTPException(404, "not found")
    acl.need_in(rec["space"], rec.get("collection"))
    return rec


def _people(db: DB, ids: set[int]) -> dict[int, dict[str, Any]]:
    if not ids:
        return {}
    rows = db.rows("SELECT record::id(id) AS id, email, name FROM account WHERE id IN $ids", ids=[R("account", a) for a in ids])
    return {a["id"]: a for a in rows}


def _kind(rec: dict[str, Any]) -> str:
    return (rec.get("media") or {}).get("kind") or ("audio" if rec.get("source") == "audio" else "transcript")


def _summaries(acl: Access, db: DB, rows: list[dict[str, Any]], user: Principal) -> list[Playlist]:
    held = playlists.items_of(db, [p["id"] for p in rows])
    recs = _readable(acl, db, {i["recording"] for items in held.values() for i in items})
    people = _people(db, {p["account"] for p in rows})
    out = []
    for p in rows:
        mine = [i for i in held[p["id"]] if i["recording"] in recs]
        by = people.get(p["account"], {})
        out.append(
            Playlist(
                id=p["id"],
                name=p["name"],
                description=p.get("description"),
                shared=p["shared"],
                mine=p["account"] == user.id,
                created_by=by.get("email"),
                created_by_name=by.get("name") or None,
                count=len(mine),
                duration_ms=sum(i["t1"] - i["t0"] for i in mine),
                created_at=p.get("created_at"),
                updated_at=p.get("updated_at"),
            )
        )
    return out


def _detail(acl: Access, db: DB, cfg: dict[str, Any], p: dict[str, Any], user: Principal) -> PlaylistDetail:
    """The playlist with its clips the reader may read, each with the resource's signed media link, as the player gets it."""
    held = playlists.items(db, p["id"])
    recs = _readable(acl, db, {i["recording"] for i in held})
    names = store.space_names(db)
    media: dict[int, str | None] = {rid: audio_link(db, cfg, rid) for rid in recs}
    items = []
    for i in (i for i in held if i["recording"] in recs):
        rec = recs[i["recording"]]
        items.append(
            {
                **{
                    k: i.get(k)
                    for k in ("id", "playlist", "position", "recording", "t0", "t1", "title", "note", "quote", "highlight", "created_at")
                },
                "recording_title": rec.get("title"),
                "namespace": names.get(rec["space"]),
                "media": _kind(rec),
                "audio": media[i["recording"]],
                "link": f"/resources/{i['recording']}" + (f"?t={i['t0'] // 1000}" if i["t0"] >= 1000 else ""),
            }
        )
    sign_urls(items, full=True)  # each one's resource is one the reader has a role on
    summary = _summaries(acl, db, [p], user)[0]
    mine = p["account"] == user.id
    return PlaylistDetail(
        **summary.model_dump(), items=[Clip.model_validate(x) for x in items], hidden=len(held) - len(items) if mine else 0
    )


@router.get("")
def list_playlists(user: CurrentUser, acl: Acl, db: Db) -> list[Playlist]:
    """Your playlists, the latest used first (the first is where a clip goes when none is named), then the ones others
    shared; each counts only the clips of resources you can read."""
    return _summaries(acl, db, playlists.visible(db, user.id), user)


@router.post("")
def create_playlist(body: PlaylistCreate, user: Writer, acl: Acl, db: Db) -> Playlist:
    """Make a playlist, with a name unique among yours. Up to 200 each."""
    with domain_errors():
        pid = playlists.create(db, user.id, body.name, body.description, body.shared)
    if body.shared:
        auth.audit(db, user.as_audit(), "playlist.share", f"playlist:{pid}", {"name": body.name})
    return _summaries(acl, db, [playlists.get(db, pid)], user)[0]


@router.get("/{pid}")
def get_playlist(pid: int, user: CurrentUser, acl: Acl, db: Db, cfg: Cfg) -> PlaylistDetail:
    """The playlist and its clips in order, each with what a player needs: the resource's kind, its signed audio or
    video link (null when it has none: play its text), the clip's times and its page's link. Only clips of
    resources you can read; `hidden` counts the others in your own playlist."""
    return _detail(acl, db, cfg, _playlist(db, pid, user), user)


@router.patch("/{pid}")
def update_playlist(pid: int, body: PlaylistUpdate, user: Writer, acl: Acl, db: Db) -> Playlist:
    """Rename your playlist, change its description, or share or unshare it. Sharing and unsharing are audited
    (`playlist.share`, `playlist.unshare`)."""
    p = _playlist(db, pid, user, write=True)
    with domain_errors():
        playlists.update(db, pid, body.name, body.description, body.shared)
    if body.shared is not None and body.shared != p["shared"]:
        auth.audit(db, user.as_audit(), "playlist.share" if body.shared else "playlist.unshare", f"playlist:{pid}", {"name": p["name"]})
    return _summaries(acl, db, [playlists.get(db, pid)], user)[0]


@router.delete("/{pid}")
def delete_playlist(pid: int, user: Writer, db: Db) -> Ok:
    """Delete your playlist and its clips."""
    _playlist(db, pid, user, write=True)
    playlists.delete(db, pid)
    return Ok()


@router.post("/{pid}/items")
def add_clip(pid: int, body: ClipCreate, user: Writer, acl: Acl, db: Db, cfg: Cfg) -> Clip:
    """Add a clip to your playlist: a resource you can read (`recording`) from `t0` to `t1`, or a `highlight` on one
    (its passage and label, unless given), with a `title` and a `note`, at `position` (default: the end). Up to 500
    in a playlist."""
    _playlist(db, pid, user, write=True)
    rid, t0, t1, title, quote = body.recording, body.t0, body.t1, body.title, body.quote
    if body.highlight is not None:
        try:
            h = highlights.get(db, body.highlight)
        except KeyError:
            raise HTTPException(404, "not found") from None
        _resource(acl, db, h["recording"])
        if rid is not None and rid != h["recording"]:
            raise HTTPException(400, "that highlight is on another resource")
        rid = h["recording"]
        if t0 is None:
            t0, t1 = h["t0"], h["t1"] if t1 is None else t1
        title, quote = title if title is not None else h.get("label") or h.get("quote"), quote or h.get("quote")
    if rid is None:
        raise HTTPException(400, "give the resource (recording) or a highlight")
    rec = _resource(acl, db, rid)
    with domain_errors():
        iid = playlists.add(
            db, pid, user.id, rid, t0, t1, (title or "")[:200], body.note, quote, body.highlight, body.position, rec.get("duration_ms")
        )
    return _one(acl, db, cfg, pid, iid, user)


def _one(acl: Access, db: DB, cfg: dict[str, Any], pid: int, iid: int, user: Principal) -> Clip:
    return next(c for c in _detail(acl, db, cfg, playlists.get(db, pid), user).items if c.id == iid)


@router.patch("/{pid}/items/{iid}")
def update_clip(pid: int, iid: int, body: ClipUpdate, user: Writer, acl: Acl, db: Db, cfg: Cfg) -> Clip:
    """Change a clip's title or note (empty clears it), or its times."""
    _playlist(db, pid, user, write=True)
    i = _clip(db, pid, iid)
    rec = _resource(acl, db, i["recording"])
    with domain_errors():
        playlists.update_item(db, iid, body.title, body.note, body.t0, body.t1, rec.get("duration_ms"))
    return _one(acl, db, cfg, pid, iid, user)


@router.delete("/{pid}/items/{iid}")
def remove_clip(pid: int, iid: int, user: Writer, db: Db) -> Ok:
    """Take a clip out of your playlist. To undo, add it again with its `position`."""
    _playlist(db, pid, user, write=True)
    _clip(db, pid, iid)
    playlists.remove_item(db, iid)
    return Ok()


@router.put("/{pid}/order")
def reorder_playlist(pid: int, body: PlaylistOrder, user: Writer, acl: Acl, db: Db, cfg: Cfg) -> PlaylistDetail:
    """Put your playlist's clips in this order (their ids); clips not named follow, as they were."""
    p = _playlist(db, pid, user, write=True)
    with domain_errors():
        playlists.reorder(db, pid, body.items)
    return _detail(acl, db, cfg, p, user)
