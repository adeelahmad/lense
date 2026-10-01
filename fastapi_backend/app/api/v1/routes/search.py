"""Full-text search over transcripts, the knowledge graph, and where entities are mentioned."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.deps import Acl, Cfg, CurrentUser, Db
from app.api.media import sign_urls
from app.domain import graph as graphmod
from app.domain import render, store
from app.domain import search as searchmod
from app.schemas.search import Graph, Mention, SearchResults

router = APIRouter(tags=["search"])

R = store.R


@router.get("/search")
def search_transcripts(
    acl: Acl,
    user: CurrentUser,
    db: Db,
    q: str = Query(description='words, "phrases", OR between alternatives'),
    ns: str | None = None,
    speaker: int | None = None,
    emotion: str | None = None,
    recording: int | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    facets: bool = Query(False, description="also count all the matching moments by namespace, speaker, emotion and recording (`facets`)"),
) -> SearchResults:
    """Moments where the words are said (or shown on screen in a video), best first, in the namespaces you can read."""
    if ns:
        acl.need(acl.nsid(ns))
    res = searchmod.search(db, q, ns, speaker, emotion, recording, limit, offset, spaces=set(acl.roles), facets=facets)
    return sign_urls(res)


@router.get("/graph")
def get_graph(
    request: Request,
    acl: Acl,
    user: CurrentUser,
    db: Db,
    cfg: Cfg,
    scope: str = Query("global", description='"global" or "ns:<namespace>"'),
) -> Graph:
    """Speakers and entities as a graph, over the namespaces you can read (isolated ones only in their own scope)."""
    allowed = set(acl.roles)
    if scope.startswith("ns:"):
        acl.need(acl.nsid(scope[3:]))
    cache = request.app.state.graph_cache
    latest = db.rows("SELECT analyzed_at FROM recording ORDER BY analyzed_at DESC LIMIT 1")
    stamp = (tuple(db.values("SELECT VALUE n FROM seq")), tuple(r.get("analyzed_at") for r in latest))
    key = (scope, tuple(sorted(allowed)), stamp)
    if key not in cache:
        try:
            cache[key] = graphmod.build(db, cfg, scope, allowed)
        except KeyError:
            raise HTTPException(404, "not found") from None
    return cache[key]


@router.get("/mentions")
def list_mentions(
    acl: Acl, user: CurrentUser, db: Db, entities: str = Query(description="entity ids, comma separated (up to 50)")
) -> list[Mention]:
    ids = [int(x) for x in entities.split(",") if x.strip().isdigit()][:50]
    ms = (
        db.rows(
            "SELECT recording, text, record::id(in) AS seg, speaker FROM mentions WHERE entity IN $ids AND space IN $sp LIMIT 80",
            ids=ids,
            sp=acl.spaces(),
        )
        if ids
        else []
    )
    if not ms:
        return []
    segs = {
        x["id"]: x
        for x in db.rows("SELECT record::id(id) AS id, t0, text FROM segment WHERE id IN $s", s=[R("segment", m["seg"]) for m in ms])
    }
    recs = {
        x["id"]: x
        for x in db.rows(
            "SELECT record::id(id) AS id, title, recorded_at, space FROM recording WHERE id IN $r",
            r=[R("recording", i) for i in {m["recording"] for m in ms}],
        )
    }
    names, spaces = render.speaker_names(db, [m.get("speaker") for m in ms]), store.space_names(db)
    out = []
    for m in ms:
        rec, seg = recs.get(m["recording"], {}), segs.get(m["seg"], {})
        out.append(
            {
                "recording_id": m["recording"],
                "title": rec.get("title"),
                "namespace": spaces.get(rec.get("space")),
                "t0": seg.get("t0", 0),
                "text": seg.get("text", m["text"]),
                "speaker": names.get(m.get("speaker")),
                "recorded_at": rec.get("recorded_at") or "",
            }
        )
    return [Mention.model_validate(x) for x in sorted(out, key=lambda x: (x["recorded_at"], -x["t0"]), reverse=True)]
