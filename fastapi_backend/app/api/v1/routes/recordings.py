"""Recordings: the list, one recording, its player data and audio, shares, exports and transcript edits.

Media (audio, word clouds) is served to anyone the recording is visible to: a role in its namespace (bearer token), a
share link (``?s=``) or a signed link from one of the JSON responses here.
"""

from __future__ import annotations

import pathlib
from datetime import date
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import Response

from app.api.deps import Acl, Cfg, CurrentUser, Db, Writer, domain_errors
from app.api.media import sign_url, sign_urls
from app.api.streaming import file_response, range_response
from app.domain import access as acc
from app.domain import analyze, auth, deletion, ipgroups, jobs, library, render, sources, store, video
from app.domain import metadata as md
from app.domain.store import API, DB
from app.schemas.common import AccessLevel, Ok
from app.schemas.recordings import (
    AccessRequest,
    EmbedLink,
    JobQueued,
    MediaKind,
    NamespaceAccess,
    Output,
    Permission,
    PermissionAdd,
    Player,
    Recording,
    RecordingAccess,
    RecordingAccessUpdate,
    RecordingIpGroup,
    RecordingSort,
    RecordingState,
    RecordingSummary,
    RecordingUpdate,
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


TOTAL_HEADER = {"X-Total-Count": {"description": "how many recordings match the filters, on all pages", "schema": {"type": "integer"}}}


@router.get("", responses={200: {"headers": TOTAL_HEADER}})
def list_recordings(
    acl: Acl,
    user: CurrentUser,
    db: Db,
    response: Response,
    ns: str | None = Query(None, description="one namespace (default: every namespace you can read)"),
    q: str | None = Query(None, max_length=200, description="words that must all appear in the title, the namespace or a speaker's name"),
    status: list[RecordingState] | None = Query(
        None,
        description="recording statuses, or processing (a job is queued or running) and failed (the latest job failed); "
        "repeat for several (any of them matches)",
    ),
    attention: bool = Query(
        False, description="only recordings that need a person: errored, latest job failed, or a voice match to review"
    ),
    processing: bool = Query(False, description="only recordings with a job queued or running"),
    speaker: list[int] | None = Query(None, description="speaker ids; repeat for several (any of them matches)"),
    date_from: date | None = Query(None, alias="from", description="recorded on or after this day"),
    date_to: date | None = Query(None, alias="to", description="recorded on or before this day"),
    min_duration: int | None = Query(None, ge=0, description="at least this many seconds long"),
    max_duration: int | None = Query(None, ge=1, description="shorter than this many seconds"),
    media: MediaKind | None = Query(None, description="audio, video or transcript (no media)"),
    access: list[AccessLevel] | None = Query(None, description="public, restricted or private; repeat for several"),
    featured: bool | None = Query(None, description="only featured recordings (true) or only the others (false)"),
    sort: RecordingSort = Query("-date", description="date, title, duration, speakers, status or importance; prefix - for descending"),
    limit: int = Query(500, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> list[RecordingSummary]:
    """Recordings you can read, newest first by default. Filters combine with AND; the ``X-Total-Count`` header says how many
    match in all, so pages can be counted."""
    spaces = [acl.namespace(ns)] if ns else acl.spaces()
    with domain_errors():
        rows, total = library.list_recordings(
            db,
            spaces,
            sort=sort,
            limit=limit,
            offset=offset,
            q=q,
            status=status,
            attention=attention,
            processing=processing,
            speakers=speaker,
            date_from=date_from,
            date_to=date_to,
            min_duration=min_duration,
            max_duration=max_duration,
            media=media,
            access=access,
            featured=featured,
        )
    response.headers["X-Total-Count"] = str(total)
    return [RecordingSummary.model_validate(x) for x in sign_urls(rows)]


@router.get("/{rid}")
def get_recording(rid: int, acl: Acl, db: Db, cfg: Cfg) -> Recording:
    r = acl.recording(rid)
    space = db.one("SELECT name FROM $s", s=R("space", r["space"])) or {}
    d = {k: v for k, v in r.items() if k not in ("envelope", "stats", "summary", "access_parts")}
    a = acc.of(db, rid)
    d.update(access=a["access"], open=a["open"], featured=a["featured"], access_inherited=a["inherited"])
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


@router.patch("/{rid}")
def update_recording(rid: int, body: RecordingUpdate, acl: Acl, user: Writer, db: Db, cfg: Cfg) -> Recording:
    """Rename a recording (editors). Its report is rebuilt with the new title."""
    rec = acl.recording(rid, "editor")
    if body.title is None:
        raise HTTPException(400, "send a title")
    with domain_errors():
        before, after = library.rename(db, cfg, rid, body.title)
    if before != after:
        auth.audit(db, user.as_audit(), "recording.rename", f"recording:{rid}", {"from": before, "to": after})
        if rec.get("analyzed_at"):
            jobs.enqueue(db, rid, ["report"], by=user.email)
    return get_recording(rid, acl, db, cfg)


@router.delete("/{rid}")
def delete_recording(rid: int, acl: Acl, user: Writer, db: Db, cfg: Cfg, request: Request, tasks: BackgroundTasks) -> Ok:
    """Delete a recording (owners). Everything Lens made from it goes: its transcript and analysis, frames, reports and
    outputs, shares, permissions and requests for access. The media file stays where it is, and scans and watched
    folders don't import it again; importing it on purpose brings it back. Its waiting jobs are cancelled; while a job
    is running on it, this answers 409. Audited as `recording.delete`."""
    acl.recording(rid, "owner")
    try:
        with domain_errors():
            gone = deletion.delete(db, cfg, rid, user.email)
    except deletion.Running as e:
        raise HTTPException(409, str(e)) from None
    auth.audit(db, user.as_audit(), "recording.delete", f"recording:{rid}", gone)
    request.app.state.graph_cache.clear()
    if gone.get("namespace"):
        tasks.add_task(render.refresh_overview, db, cfg, gone["namespace"])
    return Ok()


def _access(db: DB, rid: int) -> RecordingAccess:
    a = acc.of(db, rid)
    space = (db.one("SELECT space FROM $r", r=R("recording", rid)) or {}).get("space")
    level, open_ = acc.namespace_defaults(db, [space]).get(space, ("private", list(acc.PARTS)))
    return RecordingAccess.model_validate({**a, "default": NamespaceAccess.model_validate({"access": level, "open": open_})})


@router.get("/{rid}/access")
def get_recording_access(rid: int, acl: Acl, user: CurrentUser, db: Db) -> RecordingAccess:
    """Who may see the recording: public, restricted or private, the parts a public one opens, featured."""
    acl.recording(rid)
    return _access(db, rid)


@router.put("/{rid}/access")
def update_recording_access(rid: int, body: RecordingAccessUpdate, acl: Acl, user: Writer, db: Db, cfg: Cfg) -> RecordingAccess:
    """Make a recording public, restricted or private, choose what a public one opens, feature it (owners).

    access or open set to null follow the namespace's default again. The change is kept in the metadata history, and
    IIIF harvesters hear when the recording is published, changed or withdrawn."""
    acl.recording(rid, "owner")
    sent = body.model_fields_set
    if not sent:
        raise HTTPException(400, "send access, open or featured")
    patch = {k: getattr(body, k) for k in sent if getattr(body, k) is not None}
    reset = [k for k in sent if getattr(body, k) is None]
    with domain_errors():
        md.save(db, cfg, rid, patch, reset, user.email)
    auth.audit(db, user.as_audit(), "recording.access", f"recording:{rid}", {k: getattr(body, k) for k in sorted(sent)})
    return _access(db, rid)


@router.get("/{rid}/permissions")
def list_recording_permissions(rid: int, acl: Acl, user: CurrentUser, db: Db) -> list[Permission]:
    """The people given permission on the recording (owners), newest first."""
    acl.recording(rid, "owner")
    return [Permission.model_validate(p) for p in acc.people(db, rid)]


@router.post("/{rid}/permissions")
def add_recording_permission(rid: int, body: PermissionAdd, acl: Acl, user: Writer, db: Db) -> list[Permission]:
    """Give someone with an account permission on the recording (owners): they see all of it on the pages visitors see
    and in IIIF, whatever its access; members of its namespace already do. Answers with everyone who has permission."""
    r = acl.recording(rid, "owner")
    acct = auth.find_account(db, body.email)
    if not acct or acct.get("disabled"):
        raise HTTPException(404, "No account uses that address. An admin can create one.")
    if auth.allows(auth.roles(db, acct), r["space"]):
        raise HTTPException(400, "They have a role in this namespace, so they already see all of it.")
    acc.give(db, rid, acct["id"], user.email)
    auth.audit(db, user.as_audit(), "recording.permission.give", f"recording:{rid}", {"email": acct["email"]})
    return [Permission.model_validate(p) for p in acc.people(db, rid)]


@router.delete("/{rid}/permissions/{account}")
def remove_recording_permission(rid: int, account: int, acl: Acl, user: Writer, db: Db) -> list[Permission]:
    """Take someone's permission on the recording away (owners). Answers with everyone who still has it."""
    acl.recording(rid, "owner")
    if not acc.take(db, rid, account):
        raise HTTPException(404, "not found")
    email = (auth.get_account(db, account) or {}).get("email")
    auth.audit(db, user.as_audit(), "recording.permission.take", f"recording:{rid}", {"email": email})
    return [Permission.model_validate(p) for p in acc.people(db, rid)]


def _ip_groups(db: DB, rid: int, space: int) -> list[RecordingIpGroup]:
    return [
        RecordingIpGroup(id=g["id"], name=g["name"], ranges=g["ranges"], everything=bool(g.get("everything")), opens=g["opens"])
        for g in ipgroups.for_recording(db, rid, space)
    ]


@router.get("/{rid}/ip-groups")
def list_recording_ip_groups(rid: int, acl: Acl, user: CurrentUser, db: Db) -> list[RecordingIpGroup]:
    """The namespace's IP groups (owners), each with whether visitors from its addresses see all of this recording."""
    r = acl.recording(rid, "owner")
    return _ip_groups(db, rid, r["space"])


def _choose(rid: int, gid: int, on: bool, acl: Acl, user: Any, db: DB) -> list[RecordingIpGroup]:
    r = acl.recording(rid, "owner")
    with domain_errors():
        changed = ipgroups.choose(db, gid, r["space"], rid, on)
        name = ipgroups.get(db, gid)["name"]
    if not on and not changed:
        raise HTTPException(404, "that IP group doesn't open this recording")
    if changed:
        action = "recording.ip_group.open" if on else "recording.ip_group.close"
        auth.audit(db, user.as_audit(), action, f"recording:{rid}", {"id": gid, "name": name})
    return _ip_groups(db, rid, r["space"])


@router.put("/{rid}/ip-groups/{gid}")
def open_recording_to_ip_group(rid: int, gid: int, acl: Acl, user: Writer, db: Db) -> list[RecordingIpGroup]:
    """Open the recording to an IP group that opens chosen recordings (owners): visitors from its addresses see all of
    it. Answers with the namespace's groups."""
    return _choose(rid, gid, True, acl, user, db)


@router.delete("/{rid}/ip-groups/{gid}")
def close_recording_to_ip_group(rid: int, gid: int, acl: Acl, user: Writer, db: Db) -> list[RecordingIpGroup]:
    """Close the recording to an IP group again (owners)."""
    return _choose(rid, gid, False, acl, user, db)


@router.get("/{rid}/requests")
def list_access_requests(rid: int, acl: Acl, user: CurrentUser, db: Db) -> list[AccessRequest]:
    """Requests for access to the recording (owners), newest first; pending ones wait for an answer."""
    acl.recording(rid, "owner")
    return [AccessRequest.model_validate(x) for x in acc.requests(db, rids=[rid])]


def _decide(rid: int, account: int, approve: bool, acl: Acl, user: Any, db: DB) -> list[AccessRequest]:
    acl.recording(rid, "owner")
    req = acc.request_of(db, rid, account)
    if not acc.decide(db, rid, account, approve, user.email):
        raise HTTPException(404, "no request waits for an answer")
    email = (auth.get_account(db, account) or {}).get("email")
    action = "recording.request.approve" if approve else "recording.request.decline"
    auth.audit(db, user.as_audit(), action, f"recording:{rid}", {"email": email, "message": (req or {}).get("message")})
    return [AccessRequest.model_validate(x) for x in acc.requests(db, rids=[rid])]


@router.post("/{rid}/requests/{account}/approve")
def approve_access_request(rid: int, account: int, acl: Acl, user: Writer, db: Db) -> list[AccessRequest]:
    """Approve a request (owners): the person gets permission on the recording."""
    return _decide(rid, account, True, acl, user, db)


@router.post("/{rid}/requests/{account}/decline")
def decline_access_request(rid: int, account: int, acl: Acl, user: Writer, db: Db) -> list[AccessRequest]:
    """Decline a request (owners). They can ask again."""
    return _decide(rid, account, False, acl, user, db)


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
