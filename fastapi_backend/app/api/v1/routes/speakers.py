"""Speakers (voices) per namespace: naming, merging (and undoing it) and linking the same person across namespaces."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.api.deps import Acl, CurrentUser, Db, Writer
from app.domain import auth, render, store
from app.domain import speakers as spk
from app.domain.store import DB
from app.schemas.common import Ok
from app.schemas.speakers import (
    SpeakerDirectory,
    SpeakerLinkRequest,
    SpeakerMerged,
    SpeakerMergeRequest,
    SpeakerRecording,
    SpeakerRename,
)

router = APIRouter(tags=["speakers"])

R = store.R


def speaker_space(db: DB, sid: int) -> int:
    row = db.one("SELECT space FROM $r", r=R("speaker", sid))
    if not row:
        raise HTTPException(404, "not found")
    return row["space"]


@router.get("/speakers")
def list_speakers(ns: str, acl: Acl, user: CurrentUser, db: Db) -> SpeakerDirectory:
    """A namespace's speakers, its recent merges (undoable) and links to speakers in other namespaces."""
    nid = acl.namespace(ns)
    merges = db.rows(
        "SELECT record::id(id) AS id, from_id, into_id, at, undone, snapshot.speaker.label AS from_label, "
        "snapshot.speaker.name AS from_name FROM merge WHERE space = $s ORDER BY at DESC LIMIT 30",
        s=nid,
    )
    names = render.speaker_names(db, [m["into_id"] for m in merges])
    for m in merges:
        m["into_name"] = names.get(m["into_id"])
    spaces, rm = store.space_names(db), acl.roles
    rows = db.rows(
        "SELECT record::id(in) AS a, record::id(out) AS b, in.space AS asp, out.space AS bsp, in.name AS an, "
        "in.label AS al, out.name AS bn, out.label AS bl FROM same_as"
    )
    links: list[dict[str, Any]] = [
        {
            "a": x["a"],
            "b": x["b"],
            "a_name": x.get("an") or x.get("al"),
            "b_name": x.get("bn") or x.get("bl"),
            "a_ns": spaces.get(x["asp"]) if x["asp"] in rm else "another namespace",
            "b_ns": spaces.get(x["bsp"]) if x["bsp"] in rm else "another namespace",
        }
        for x in rows
        if nid in (x["asp"], x["bsp"])
    ]
    return SpeakerDirectory.model_validate({"speakers": spk.list_speakers(db, nid), "merges": merges, "links": links})


@router.get("/speakers/{sid}/recordings")
def list_speaker_recordings(sid: int, acl: Acl, user: CurrentUser, db: Db) -> list[SpeakerRecording]:
    acl.need(speaker_space(db, sid))
    agg: dict[int, dict[str, int]] = {}
    for s in db.rows("SELECT recording, t0, dur FROM segment WHERE speaker = $s", s=sid):
        a = agg.setdefault(s["recording"], {"talk_ms": 0, "first_t0": s["t0"]})
        a["talk_ms"] += s.get("dur") or 0
        a["first_t0"] = min(a["first_t0"], s["t0"])
    recs = (
        db.rows("SELECT record::id(id) AS id, title, recorded_at FROM recording WHERE id IN $ids", ids=[R("recording", i) for i in agg])
        if agg
        else []
    )
    rows = sorted(({**r, **agg[r["id"]]} for r in recs), key=lambda r: r.get("recorded_at") or "", reverse=True)
    return [SpeakerRecording.model_validate(r) for r in rows]


@router.post("/speakers/{sid}")
def rename_speaker(sid: int, body: SpeakerRename, request: Request, acl: Acl, user: Writer, db: Db) -> Ok:
    acl.need(speaker_space(db, sid), "editor")
    spk.rename(db, sid, body.name[:80])
    request.app.state.graph_cache.clear()
    return Ok()


@router.post("/speakers/{sid}/merge")
def merge_speaker(sid: int, body: SpeakerMergeRequest, request: Request, acl: Acl, user: Writer, db: Db) -> SpeakerMerged:
    """Fold this speaker into ``into`` (same namespace). Undo with POST /merges/{merge_id}/undo."""
    acl.need(speaker_space(db, sid), "editor")
    try:
        mid = spk.merge(db, sid, body.into)
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(400, str(e)) from None
    auth.audit(db, user.as_audit(), "speaker.merge", f"speaker:{sid}", {"into": body.into})
    request.app.state.graph_cache.clear()
    return SpeakerMerged(merge_id=mid)


@router.post("/merges/{mid}/undo")
def undo_speaker_merge(mid: int, request: Request, acl: Acl, user: Writer, db: Db) -> Ok:
    m = db.one("SELECT space FROM $r", r=R("merge", mid))
    if not m:
        raise HTTPException(404, "not found")
    acl.need(m["space"], "editor")
    try:
        spk.undo(db, mid)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    request.app.state.graph_cache.clear()
    return Ok()


@router.post("/speakers/{sid}/link")
def link_speaker(sid: int, body: SpeakerLinkRequest, request: Request, acl: Acl, user: Writer, db: Db) -> Ok:
    """Mark this speaker and one in another namespace as the same person (you need editor access to both)."""
    acl.need(speaker_space(db, sid), "editor")
    acl.need(speaker_space(db, body.with_), "editor")
    try:
        spk.link(db, sid, body.with_)
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(400, str(e)) from None
    request.app.state.graph_cache.clear()
    return Ok()
