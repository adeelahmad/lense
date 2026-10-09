"""Full-text search over transcripts, the knowledge graph, and where entities are mentioned."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.deps import Acl, Cfg, CurrentUser, Db
from app.api.media import sign_urls
from app.domain import graph as graphmod
from app.domain import graph_history, hierarchy, photos, render, store
from app.domain import search as searchmod
from app.schemas.search import Graph, Mention, PhotoResults, SearchResults, TermSuggestion

router = APIRouter(tags=["search"])

R = store.R


@router.get("/search")
def search_transcripts(
    acl: Acl,
    user: CurrentUser,
    db: Db,
    cfg: Cfg,
    q: str = Query(description='words, "phrases", OR between alternatives'),
    ns: str | None = None,
    speaker: int | None = None,
    emotion: str | None = None,
    recording: int | None = None,
    object: str | None = Query(None, max_length=60, description="only recordings this kind of object is seen in (person, car …)"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    facets: bool = Query(
        False,
        description="also count all the matching moments by namespace, speaker, emotion and recording, and list their kinds of object",
    ),
    mode: Literal["auto", "keyword", "semantic", "hybrid"] = Query(
        "auto",
        description='"keyword": the words (BM25); "semantic": by meaning (needs an embedding model, Settings → Search); '
        '"hybrid": both, fused by rank; "auto": hybrid when search by meaning is set up and the query has no "phrases" '
        "or OR, else keyword",
    ),
) -> SearchResults:
    """Moments where the words are said (or shown on screen in a video, or written in a resource's supplementary
    transcripts, captions, translations and indexes, or the kinds of object seen in videos, documents and images),
    best first, in the namespaces you can read and the collections you were given a role on. A speaker or emotion
    filter keeps to what was said. By meaning, a moment is a passage about what was asked, though its words may differ."""
    if ns:
        acl.scope(ns)  # 404 unless they see some of it
    also = acl.partial_recordings()
    res = searchmod.search(
        db,
        q,
        ns,
        speaker,
        emotion,
        recording,
        limit,
        offset,
        spaces=set(acl.roles),
        facets=facets,
        also=also,
        files=True,
        objects=True,
        obj=object,
        described=True,
        cfg=cfg,
        mode=mode,
    )
    return sign_urls(res, full=True)


@router.get("/search/photos")
def search_photos(
    acl: Acl,
    user: CurrentUser,
    db: Db,
    cfg: Cfg,
    q: str = Query(min_length=1, max_length=300, description='what the photo shows, such as "a bus on a city street"'),
    ns: str | None = None,
    limit: int = Query(24, ge=1, le=100),
) -> PhotoResults:
    """Photos that show what was asked for, best first, in the namespaces you can read and the collections you were
    given a role on, whatever words are on them: anytopdf's CLIP plugin compares the query with each photo
    (Settings → Search → Search photos by what they show; off by default)."""
    if not photos.enabled(cfg):
        raise HTTPException(409, "searching photos by what they show is off (Settings → Search)")
    whole, part = acl.scope(ns)
    spaces, also = set(whole), hierarchy.recordings_in(db, [c for cols in part.values() for c in cols])
    try:
        hits = photos.search(db, cfg, q, spaces, also, limit)
    except photos.Unavailable as e:
        raise HTTPException(503, str(e)) from None
    except ValueError as e:
        raise HTTPException(502, str(e)) from None
    rids = [h["recording"] for h in hits]
    recs = {
        r["id"]: r
        for r in db.rows("SELECT record::id(id) AS id, title, space FROM recording WHERE id IN $r", r=[R("recording", x) for x in rids])
    }
    thumbs = {
        x["recording"]: x.get("thumb") for x in db.rows("SELECT recording, thumb FROM page WHERE recording IN $r AND idx = 0", r=rids)
    }
    names = store.space_names(db)
    out = [
        {
            **h,
            "title": recs.get(h["recording"], {}).get("title"),
            "namespace": names.get(recs.get(h["recording"], {}).get("space")),
            "thumb": f"{store.API}/recordings/{h['recording']}/frames/{thumbs[h['recording']]}" if thumbs.get(h["recording"]) else None,
        }
        for h in hits
        if h["recording"] in recs
    ]
    return PhotoResults.model_validate(sign_urls({"hits": out}, full=True))


@router.get("/search/terms")
def suggest_terms(
    acl: Acl,
    user: CurrentUser,
    db: Db,
    prefix: str = Query(min_length=2, max_length=60, description="the start of a word (a trailing * is ignored)"),
    ns: str | None = None,
    limit: int = Query(8, ge=1, le=20),
) -> list[TermSuggestion]:
    """Whole words said in the namespaces you can read (or `ns`) that start with `prefix`, the most said first. Search
    has no prefix search, so the web app offers these when someone types interp*. Words are counted per namespace, so
    collections you were given a role on don't add any."""
    if ns:
        acl.scope(ns)
    return [TermSuggestion.model_validate(t) for t in searchmod.terms(db, prefix, set(acl.roles), ns, limit)]


@router.get("/graph")
def get_graph(
    request: Request,
    acl: Acl,
    user: CurrentUser,
    db: Db,
    cfg: Cfg,
    scope: str = Query("global", description='"global" or "ns:<namespace>"'),
    as_of: str | None = Query(None, description="the entities as of a graph version: a number, a version's name, or head"),
) -> Graph:
    """Speakers and entities as a graph, over the namespaces you can read (isolated ones only in their own scope).
    `as_of` shows the entities as they were at an earlier version (docs/graph-history.md); mentions are today's."""
    allowed = set(acl.roles)
    if scope.startswith("ns:"):
        acl.need(acl.nsid(scope[3:]))
    version = None
    if as_of not in (None, "", "head"):
        try:
            version = graph_history.resolve(db, as_of)
        except KeyError:
            raise HTTPException(404, f"no version is called {as_of}") from None
    cache = request.app.state.graph_cache
    latest = db.rows("SELECT analyzed_at FROM recording ORDER BY analyzed_at DESC LIMIT 1")
    stamp = (tuple(db.values("SELECT VALUE n FROM seq")), tuple(r.get("analyzed_at") for r in latest))
    key = ("canvas", scope, tuple(sorted(allowed)), stamp, graph_history.head(db), version)
    if key not in cache:
        try:
            cache[key] = graphmod.build(db, cfg, scope, allowed, as_of=version)
        except KeyError:
            raise HTTPException(404, "not found") from None
        except ValueError as e:  # a version the graph hasn't reached
            raise HTTPException(400, str(e)) from None
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
