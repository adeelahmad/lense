"""Speakers (voices) per namespace: naming, merging (and undoing it), linking the same person across namespaces (and
unlinking), and saying two voices aren't the same person."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.api.deps import Acl, Cfg, CurrentUser, Db, Writer
from app.domain import auth, graph, render, store
from app.domain import speakers as spk
from app.domain.store import DB
from app.schemas.common import Ok
from app.schemas.speakers import (
    SpeakerDirectory,
    SpeakerLinkRequest,
    SpeakerMerged,
    SpeakerMergeRequest,
    SpeakerPair,
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


def _cross(db: DB, cfg: dict[str, Any], nid: int, readable: set[int]) -> list[dict[str, Any]]:
    """Likely the same voice: this namespace's speakers and those of other shared namespaces the person can read."""
    shared = {i: n for i, n in graph.scope_namespaces(db, "global", readable)}
    if cfg["speakers"].get("cross_namespace") != "suggest" or nid not in shared:
        return []
    matches = spk.cross_namespace_matches(db, cfg, list(shared), only=nid)
    ids = {x for a, b, _ in matches for x in (a, b)}
    rows = {
        r["id"]: r
        for r in (
            db.rows("SELECT record::id(id) AS id, space, name, label FROM speaker WHERE id IN $r", r=[R("speaker", i) for i in ids])
            if ids
            else []
        )
    }
    out = []
    for a, b, score in sorted(matches, key=lambda m: -m[2]):
        mine, other = (a, b) if rows[a]["space"] == nid else (b, a)
        out.append(
            {
                "a": mine,
                "b": other,
                "a_name": rows[mine].get("name") or rows[mine]["label"],
                "b_name": rows[other].get("name") or rows[other]["label"],
                "a_ns": shared[nid],
                "b_ns": shared[rows[other]["space"]],
                "score": round(score, 3),
            }
        )
    return out


@router.get("/speakers")
def list_speakers(ns: str, acl: Acl, user: CurrentUser, db: Db, cfg: Cfg) -> SpeakerDirectory:
    """A namespace's speakers, its recent merges (undoable; who merged and how many recordings moved), links to speakers
    in other namespaces, and voices in other shared namespaces that are likely the same person (`cross`)."""
    nid = acl.namespace(ns)
    merges = db.rows(
        "SELECT record::id(id) AS id, from_id, into_id, at, undone, snapshot.speaker.label AS from_label, "
        "snapshot.speaker.name AS from_name, by, recordings, segments, undone_by, undone_at FROM merge WHERE space = $s "
        "ORDER BY at DESC LIMIT 30",
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
    return SpeakerDirectory.model_validate(
        {"speakers": spk.list_speakers(db, nid), "merges": merges, "links": links, "cross": _cross(db, cfg, nid, set(acl.roles))}
    )


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
        mid = spk.merge(db, sid, body.into, by=user.email)
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
        spk.undo(db, mid, by=user.email)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    auth.audit(db, user.as_audit(), "speaker.merge.undo", f"merge:{mid}", None)
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
    auth.audit(db, user.as_audit(), "speaker.link", f"speaker:{sid}", {"with": body.with_})
    request.app.state.graph_cache.clear()
    return Ok()


@router.post("/speakers/{sid}/unlink")
def unlink_speaker(sid: int, body: SpeakerPair, request: Request, acl: Acl, user: Writer, db: Db) -> Ok:
    """They aren't the same person after all: remove the link (editor access to both). 404 when they aren't linked.
    Audited as speaker.unlink."""
    acl.need(speaker_space(db, sid), "editor")
    acl.need(speaker_space(db, body.with_), "editor")
    if not spk.unlink(db, sid, body.with_):
        raise HTTPException(404, "they aren't linked")
    auth.audit(db, user.as_audit(), "speaker.unlink", f"speaker:{sid}", {"with": body.with_})
    request.app.state.graph_cache.clear()
    return Ok()


@router.post("/speakers/{sid}/not-same")
def not_same_speaker(sid: int, body: SpeakerPair, request: Request, acl: Acl, user: Writer, db: Db) -> Ok:
    """These two aren't the same person: drop the suggestion to merge them (one namespace) or link them (two), and
    never suggest it again. Editor access to both namespaces. Audited as speaker.not_same."""
    acl.need(speaker_space(db, sid), "editor")
    acl.need(speaker_space(db, body.with_), "editor")
    try:
        spk.not_same(db, sid, body.with_, user.email)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    auth.audit(db, user.as_audit(), "speaker.not_same", f"speaker:{sid}", {"with": body.with_})
    request.app.state.graph_cache.clear()
    return Ok()
