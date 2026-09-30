"""Recordings: the list, one recording, its player data and audio, shares, exports and transcript edits.

Media (audio, word clouds) is served to anyone the recording is visible to: a role in its namespace (bearer token), a
share link (``?s=``) or a signed link from one of the JSON responses here.
"""

from __future__ import annotations

import pathlib
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from app.api.deps import Acl, Cfg, CurrentUser, Db, Writer, domain_errors
from app.api.media import sign_url, sign_urls
from app.api.streaming import file_response, range_response
from app.domain import analyze, auth, jobs, render, sources, store, video
from app.domain.store import API, DB
from app.schemas.common import Ok
from app.schemas.recordings import (
    EmbedLink,
    JobQueued,
    Output,
    Player,
    Recording,
    RecordingSummary,
    ReprocessRequest,
    SegmentEdit,
    SegmentUpdate,
    Share,
    ShareCreate,
    ShareLink,
)

router = APIRouter(prefix="/recordings", tags=["recordings"])

R = store.R
EXPORT_TYPES = {"txt": "text/plain", "md": "text/markdown", "srt": "application/x-subrip", "vtt": "text/vtt", "json": "application/json"}


def has_audio(db: DB, cfg: dict[str, Any], rid: int) -> bool:
    """Whether the recording has audio we can play (a local file, or a file on a storage source)."""
    rec = db.one("SELECT source, remote FROM $r", r=R("recording", rid)) or {}
    return rec.get("source") == "audio" and bool(rec.get("remote") or render.has_audio(db, cfg, rid))


def audio_link(db: DB, cfg: dict[str, Any], rid: int, share: str = "") -> str | None:
    return (f"{API}/recordings/{rid}/audio" + (f"?s={share}" if share else "")) if has_audio(db, cfg, rid) else None


@router.get("")
def list_recordings(
    acl: Acl,
    user: CurrentUser,
    db: Db,
    ns: str | None = None,
    limit: int = Query(500, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> list[RecordingSummary]:
    if ns:
        acl.need(acl.nsid(ns))
    spaces = [acl.nsid(ns)] if ns else acl.spaces()
    rows = db.rows(
        "SELECT record::id(id) AS id, title, recorded_at, duration_ms, status, error, source, stats, summary, space, media "
        f"FROM recording WHERE space IN $s ORDER BY recorded_at DESC LIMIT {int(limit)} START {int(offset)}",
        s=spaces,
    )
    ids = [r["id"] for r in rows]
    apps: dict[int, list[int]] = defaultdict(list)
    for a in db.rows("SELECT recording, speaker FROM appearance WHERE recording IN $r", r=ids) if rows else []:
        apps[a["recording"]].append(a["speaker"])
    names, spaces_n = render.speaker_names(db, [x for v in apps.values() for x in v]), store.space_names(db)
    posters = (
        {x["recording"]: x.get("frame") for x in db.rows("SELECT recording, frame FROM shot WHERE recording IN $r AND idx = 0", r=ids)}
        if rows
        else {}
    )
    out = []
    for r in rows:
        st, sm = r.pop("stats", None) or {}, r.pop("summary", None) or {}
        r["media_kind"] = (r.pop("media", None) or {}).get("kind") or ("audio" if r.get("source") == "audio" else "transcript")
        r["poster"] = f"{API}/recordings/{r['id']}/frames/{posters[r['id']]}" if posters.get(r["id"]) else None
        out.append(
            {
                **r,
                "namespace": spaces_n.get(r["space"]),
                "emotions": st.get("emotions", {}),
                "words": st.get("words"),
                "importance": sm.get("importance"),
                "sentiment": sm.get("sentiment"),
                "speakers": ",".join(dict.fromkeys(names.get(x, "?") for x in apps.get(r["id"], []))),
            }
        )
    return [RecordingSummary.model_validate(x) for x in sign_urls(out)]


@router.get("/{rid}")
def get_recording(rid: int, acl: Acl, db: Db, cfg: Cfg) -> Recording:
    r = acl.recording(rid)
    space = db.one("SELECT name FROM $s", s=R("space", r["space"])) or {}
    d = {k: v for k, v in r.items() if k not in ("envelope", "stats", "summary")}
    d.update(
        id=rid, namespace=space.get("name"), summary=r.get("summary"), stats=render.recording_stats(db, rid), role=acl.roles.get(r["space"])
    )
    rep = pathlib.Path(cfg["data_dir"]) / "reports" / (space.get("name") or "_") / f"{render.slug(r.get('title'))}-{rid}.html"
    d["report_url"] = f"/reports/{space.get('name')}/{rep.name}" if rep.exists() else None
    apps = db.rows("SELECT speaker, method, score FROM appearance WHERE recording = $r", r=rid)
    names = render.speaker_names(db, [a["speaker"] for a in apps])
    d["speakers"] = [
        {"id": a["speaker"], "name": names.get(a["speaker"], "?"), "method": a.get("method"), "score": a.get("score")} for a in apps
    ]
    d["jobs"] = jobs.list_jobs(db, recording=rid, limit=5)
    return Recording.model_validate(sign_urls(d))


@router.get("/{rid}/player")
def get_player(rid: int, acl: Acl, db: Db, cfg: Cfg, s: str = "") -> Player:
    """Player data. Works with a share link (``?s=``) as well as signed in; media links in it are signed."""
    acl.recording(rid, share=s)
    return sign_urls(render.player_data(db, rid, audio_link(db, cfg, rid, s)))


@router.get("/{rid}/embed-link")
def get_embed_link(rid: int, acl: Acl, t: float = Query(0, ge=0, description="start at this second")) -> EmbedLink:
    """A signed link to the embeddable player, for people who can see the recording (it expires; share links don't)."""
    acl.recording(rid)
    return EmbedLink(url=sign_url(f"/embed/{rid}" + (f"?t={t:g}" if t else "")) or "")


def _media_type(rec: dict[str, Any], path: Any) -> str | None:
    if (rec.get("media") or {}).get("kind") == "video":
        return video.VIDEO_TYPES.get(pathlib.PurePosixPath(str(path)).suffix.lower())
    return None


def serve_audio(db: DB, cfg: dict[str, Any], rec: dict[str, Any], rid: int, request: Request) -> Response:
    """The recording's audio (or video) file with byte ranges: local, from a local source, from the cache, or streamed."""
    if rec.get("source") != "audio":
        raise HTTPException(404, "no audio for this recording")
    path = render.has_audio(db, cfg, rid)
    if path:
        return file_response(path, request, _media_type(rec, path))
    rm = rec.get("remote")
    if not rm:
        raise HTTPException(404, "no audio for this recording")
    with domain_errors():  # the source was removed (404) or the path is no longer allowed (400)
        src = sources.get(db, rm["source"])
        p = sources.check_path(cfg, src, rm["path"])
    if src["type"] == "local":
        return file_response(p, request, _media_type(rec, p))
    cached = sources.cache_file(cfg, rm["source"], p)
    if cached.exists():
        return file_response(str(cached), request, _media_type(rec, p))
    if not rec.get("size"):
        raise HTTPException(404, "audio size unknown; it will play once the recording has been processed")
    ctype = _media_type(rec, p) or render.AUDIO_TYPES.get(pathlib.PurePosixPath(p).suffix.lower(), "application/octet-stream")
    return range_response(rec["size"], request, ctype, lambda a, b: sources.stream(db, cfg, rm["source"], rm["path"], a, b - a + 1))


@router.get("/{rid}/audio", response_class=Response, responses={200: {"content": {"audio/*": {}}}, 206: {"description": "a byte range"}})
def get_audio(rid: int, request: Request, acl: Acl, db: Db, cfg: Cfg, s: str = "") -> Response:
    """The audio, with byte ranges. Accepts a bearer token, a share link (``?s=``) or a signed link."""
    return serve_audio(db, cfg, acl.recording(rid, share=s), rid, request)


@router.get("/{rid}/wordcloud.svg", response_class=Response, responses={200: {"content": {"image/svg+xml": {}}}})
def get_recording_wordcloud(rid: int, acl: Acl, db: Db, s: str = "") -> Response:
    acl.recording(rid, share=s)
    return Response(render.wordcloud_svg(analyze.keywords(db, rid, 60)), media_type="image/svg+xml")


@router.post("/{rid}/reprocess")
def reprocess_recording(rid: int, acl: Acl, user: Writer, db: Db, body: ReprocessRequest | None = None) -> JobQueued:
    acl.recording(rid, "editor")
    body = body or ReprocessRequest()
    try:
        return JobQueued(job=jobs.enqueue(db, rid, body.steps or None, by=user.email, pipeline=body.pipeline))
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e)) from None


@router.post("/{rid}/share")
def create_share(rid: int, acl: Acl, user: Writer, db: Db, body: ShareCreate | None = None) -> ShareLink:
    """A read-only link to this one recording, for people without an account. Revoke with DELETE."""
    acl.recording(rid, "editor")
    raw = auth.create_share(db, rid, user.id, (body or ShareCreate()).days)
    auth.audit(db, user.as_audit(), "share.create", f"recording:{rid}")
    return ShareLink(token=raw, embed=f"/embed/{rid}?s={raw}")


@router.delete("/{rid}/share")
def revoke_shares(rid: int, acl: Acl, user: Writer, db: Db) -> Ok:
    acl.recording(rid, "editor")
    auth.revoke_shares(db, rid)
    auth.audit(db, user.as_audit(), "share.revoke", f"recording:{rid}")
    return Ok()


@router.get("/{rid}/shares")
def list_shares(rid: int, acl: Acl, user: CurrentUser, db: Db) -> list[Share]:
    acl.recording(rid, "editor")
    people = {a["id"]: a["email"] for a in db.rows("SELECT record::id(id) AS id, email FROM account")}
    now = store.now()
    rows = db.rows(
        "SELECT record::id(id) AS id, created_by, created_at, expires_at, revoked FROM share_link WHERE recording = $r ORDER BY created_at DESC",
        r=rid,
    )
    return [
        Share(
            id=str(r["id"])[:10],
            created_by=people.get(r.get("created_by")),
            created_at=r.get("created_at"),
            expires_at=r.get("expires_at"),
            active=not r.get("revoked") and (r.get("expires_at") or "") >= now,
        )
        for r in rows
    ]


@router.get(
    "/{rid}/export.{fmt}",
    response_class=Response,
    responses={200: {"content": {t: {} for t in EXPORT_TYPES.values()}}},
)
def export_recording(rid: int, fmt: str, acl: Acl, db: Db) -> Response:
    """The transcript as txt, md, srt, vtt or json (a download)."""
    acl.recording(rid)
    if fmt not in EXPORT_TYPES:
        raise HTTPException(404, f"exports: {', '.join(EXPORT_TYPES)}")
    d = render.player_data(db, rid)
    return Response(
        render.export_text(d, fmt),
        media_type=EXPORT_TYPES[fmt] + "; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{render.slug(d["title"])}.{fmt}"'},
    )


@router.patch("/{rid}/segments/{idx}")
def edit_segment(rid: int, idx: int, body: SegmentUpdate, acl: Acl, user: Writer, db: Db) -> JobQueued:
    """Correct a transcript line (text and/or speaker); the recording is re-analysed afterwards."""
    rec = acl.recording(rid, "editor")
    sr = R("segment", rid * store.SEG + idx)
    seg = db.one("SELECT text, speaker FROM $s", s=sr)
    if not seg:
        raise HTTPException(404, "not found")
    sent = body.model_fields_set
    patch: dict[str, Any] = {}
    if "text" in sent:
        text = (body.text or "").strip()
        if not text:
            raise HTTPException(400, "the text can't be empty")
        patch["text"] = text[:5000]
    if "speaker" in sent:
        if body.speaker is not None:
            row = db.one("SELECT space FROM $s", s=R("speaker", body.speaker))
            if not row or row["space"] != rec["space"]:
                raise HTTPException(400, "that speaker isn't in this namespace")
        patch["speaker"] = body.speaker
    if not patch:
        raise HTTPException(400, "change the text or the speaker")
    db.q("UPDATE $s MERGE $p", s=sr, p=patch)
    edit = {"recording": rid, "idx": idx, "before": seg, "after": patch, "by": user.email, "at": store.now()}
    db.q("CREATE segment_edit CONTENT $d", d=store.clean(edit))
    auth.audit(db, user.as_audit(), "transcript.edit", f"recording:{rid}", {"segment": idx})
    return JobQueued(job=jobs.enqueue(db, rid, ["analyze", "report"], by=user.email))


@router.get("/{rid}/edits")
def list_segment_edits(rid: int, acl: Acl, user: CurrentUser, db: Db) -> list[SegmentEdit]:
    acl.recording(rid)
    return db.rows("SELECT idx, before, after, by, at FROM segment_edit WHERE recording = $r ORDER BY at DESC", r=rid)


@router.get("/{rid}/outputs")
def list_outputs(rid: int, acl: Acl, user: CurrentUser, db: Db) -> list[Output]:
    """Results of LLM pipeline steps for this recording."""
    acl.recording(rid)
    return db.rows("SELECT key, value, origin, created_at FROM output WHERE recording = $r ORDER BY key", r=rid)
