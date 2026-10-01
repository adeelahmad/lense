"""IIIF: Presentation 3.0 manifests and collections, Content Search 2.0, Change Discovery, open media, and the
Authorization Flow 2.0 services other IIIF viewers use to play restricted recordings.

What a recording publishes follows its access (docs/access.md): a public recording's manifest is open, and so are the
parts it opens (media, transcript, index); restricted and private recordings answer 404 unless the request has
permission: a role in their namespace, permission given on the recording, or an address in an IP group that opens it.
Closed content is open to the same, a link signed by the probe service, or the IIIF access cookie set by the sign-in
page below.

The sign-in page (the access service) is opened by a viewer on another site in a new tab. It authenticates with its own
email/password form (throttled like the API's sign-in) and, on success, sets the IIIF access cookie (HttpOnly,
SameSite=None; Secure over HTTPS, scoped to /iiif/). The form is protected by a double-submit CSRF token: a random value
both in a SameSite=Strict cookie scoped to /iiif/auth and in a hidden field. The token service then posts an access
token, in a hidden iframe, to the viewer's origin only; the probe service turns that token into a short-lived signed
link; logout revokes the cookie and every token issued from it.
"""

from __future__ import annotations

import pathlib
import re
import secrets
import urllib.parse
from html import escape as html_escape
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from app.api.deps import Cfg, Db, OptionalUser, Principal, client_ip, get_cfg, get_db, network
from app.api.v1.routes.recordings import serve_audio
from app.domain import access as acc
from app.domain import auth, iiif, iiif_auth, render, store, video
from app.domain import metadata as md
from app.domain.store import DB, R

router = APIRouter(include_in_schema=False, tags=["iiif-resources"])

CSRF_COOKIE = "la_iiif_csrf"
FRAME_RX = re.compile(r"[\w.-]+\.jpg")
Config = dict[str, Any]


def base_url(request: Request, cfg: Config) -> str:
    """Where IIIF ids point: `iiif.base_url` when set (behind a proxy), else the address this request came to."""
    return (cfg["iiif"].get("base_url") or str(request.base_url)).rstrip("/")


def viewer_links(cfg: Config, manifest_url: str, state: str | None = None) -> list[dict[str, str]]:
    """Links to open a manifest (or a content state) in the IIIF viewers listed in the configuration."""
    out = []
    for v in cfg["iiif"].get("viewers") or []:
        if isinstance(v, dict) and v.get("url"):
            url = v["url"].replace("{manifest}", urllib.parse.quote(manifest_url, safe="")).replace("{content_state}", state or "")
            out.append({"name": v.get("name") or "Viewer", "url": url})
    return out


def _roles(user: Principal | None) -> dict[int, str]:
    return user.roles if user else {}


def _permitted(db: DB, user: Principal | None, rid: int, space: int) -> bool:
    """A role in the recording's namespace, or permission given on the recording (docs/access.md)."""
    return acc.permitted(db, _roles(user), user.id if user else None, rid, space)


def _account_permitted(db: DB, acct: dict[str, Any], rid: int, space: int) -> bool:
    """The same, for the account behind an IIIF access token or cookie."""
    return acc.permitted(db, auth.roles(db, acct), acct["id"], rid, space)


def _readable(request: Request, db: DB, user: Principal | None) -> set[int]:
    """The namespaces whose recordings the requester sees all of: a role there, or an IP group that opens everything."""
    return set(_roles(user)) | set(network(request, db).spaces)


def _granted(request: Request, db: DB, user: Principal | None) -> frozenset[int]:
    """The recordings given to the requester, and those an IP group opens to their address."""
    return acc.granted(db, user.id if user else None) | frozenset(network(request, db).recordings)


PART = {"audio": "media", "transcript": "transcript"}  # what the probe and content routes call each part


def _allowed(request: Request, db: DB, cfg: Config, user: Principal | None, rec: dict[str, Any], rid: int, what: str | None = None) -> bool:
    """Permission, however it arrives: the requester's (a role in the namespace, permission given on the recording, or
    an IP group their address is in), the IIIF access cookie's account's, or, for content, a link the probe service
    signed for it."""
    if _permitted(db, user, rid, rec["space"]) or network(request, db).opens(rid, rec["space"]):
        return True
    q = request.query_params
    if what and q.get("sig") and iiif.signed_ok(cfg, rid, what, q.get("exp"), q.get("sig")):
        return True
    acct, _ = iiif_auth.cookie_account(db, request.cookies.get(iiif_auth.COOKIE))
    return bool(acct and _account_permitted(db, acct, rid, rec["space"]))


def _rec(
    request: Request, db: DB, cfg: Config, user: Principal | None, rid: int, what: str | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """A recording's IIIF resources are public when the recording is; otherwise they need permission (_allowed), and
    look absent without it. `what` is the content asked for (audio or transcript), which a signed link may open."""
    rec = db.one("SELECT space, source, path, remote, size FROM $r", r=R("recording", rid))
    if not rec:
        raise HTTPException(404, "not found")
    a = acc.of(db, rid)
    if not acc.published(a) and not _allowed(request, db, cfg, user, rec, rid, what):
        raise HTTPException(404, "not found")
    return rec, a


def _content_ok(
    request: Request, db: DB, cfg: Config, user: Principal | None, rec: dict[str, Any], rid: int, a: dict[str, Any], what: str
) -> bool:
    """A part open to everyone, or permission (_allowed)."""
    return acc.is_open(a, PART[what]) or _allowed(request, db, cfg, user, rec, rid, what)


def _ld(doc: dict[str, Any], status: int = 200) -> JSONResponse:
    ctype = iiif.JSONLD if doc.get("type") in ("Manifest", "Collection") else "application/ld+json"
    return JSONResponse(doc, status_code=status, media_type=ctype)


def _nsid(db: DB, name: str) -> int:
    try:
        return store.ns_id(db, name, create=False)
    except KeyError:
        raise HTTPException(404, "not found") from None


# ---------- collections and change discovery ----------
@router.get("/iiif/collection")
def iiif_root_collection(request: Request, user: OptionalUser, db: Db, cfg: Cfg) -> JSONResponse:
    return _ld(iiif.root_collection(db, cfg, base_url(request, cfg), _readable(request, db, user), _granted(request, db, user)))


@router.get("/iiif/collection/{name}")
def iiif_collection(name: str, request: Request, user: OptionalUser, db: Db, cfg: Cfg) -> JSONResponse:
    sid = _nsid(db, name)
    return _ld(iiif.collection(db, cfg, sid, base_url(request, cfg), _readable(request, db, user), _granted(request, db, user)))


@router.get("/iiif/collection/{name}/search")
def iiif_collection_search(name: str, request: Request, user: OptionalUser, db: Db, cfg: Cfg, q: str = "", page: int = 0) -> JSONResponse:
    sid = _nsid(db, name)
    rows = db.rows("SELECT record::id(id) AS id, space, access, access_parts, featured FROM recording WHERE space = $s", s=sid)
    readable, granted = sid in _readable(request, db, user), _granted(request, db, user)
    rids = [rid for rid, a in acc.many(db, rows).items() if readable or rid in granted or acc.is_open(a, "transcript")]
    base = base_url(request, cfg)
    url = f"{base}/iiif/collection/{name}/search?q={urllib.parse.quote(q)}" + (f"&page={page}" if page else "")
    return _ld(iiif.search(db, base, q, rids, url, page))


@router.get("/iiif/collection/{name}/{cid}")
def iiif_subcollection(name: str, cid: int, request: Request, user: OptionalUser, db: Db, cfg: Cfg) -> JSONResponse:
    """One of a namespace's collections: the collections inside it and its recordings the requester sees."""
    sid = _nsid(db, name)
    try:
        doc = iiif.subcollection(db, cfg, sid, cid, base_url(request, cfg), _readable(request, db, user), _granted(request, db, user))
    except KeyError:
        raise HTTPException(404, "not found") from None
    return _ld(doc)


@router.get("/iiif/discovery/activity")
def iiif_activity(request: Request, db: Db, cfg: Cfg) -> JSONResponse:
    return _ld(iiif.activity_stream(db, base_url(request, cfg)))


@router.get("/iiif/discovery/activity/page/{n}")
def iiif_activity_page(n: int, request: Request, db: Db, cfg: Cfg) -> JSONResponse:
    try:
        return _ld(iiif.activity_page(db, base_url(request, cfg), n))
    except KeyError:
        raise HTTPException(404, "not found") from None


# ---------- Authorization Flow 2.0 ----------
def _auth_page(content: str, nonce: str, frame: str = "'none'") -> HTMLResponse:
    csp = f"default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-{nonce}'; form-action 'self'; frame-ancestors {frame}"
    return HTMLResponse(content, headers={"Cache-Control": "no-store", "Content-Security-Policy": csp})


def _secure(request: Request, cfg: Config) -> bool:
    return request.url.scheme == "https" or bool(cfg["server"].get("secure_cookies"))


def _access_form(request: Request, cfg: Config, origin: str, error: str = "", account: dict[str, Any] | None = None) -> HTMLResponse:
    """The sign-in (or continue) form with a fresh double-submit CSRF token."""
    nonce, csrf = secrets.token_urlsafe(12), secrets.token_urlsafe(24)
    site = iiif.site_label(cfg, base_url(request, cfg))
    resp = _auth_page(iiif_auth.access_page(site, nonce, account, csrf, origin, error), nonce)
    resp.set_cookie(CSRF_COOKIE, csrf, max_age=3600, httponly=True, path="/iiif/auth", secure=_secure(request, cfg), samesite="strict")
    return resp


@router.options("/iiif/auth/probe/{rid}/{what}")
def iiif_probe_preflight(rid: int, what: str) -> Response:
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Authorization",
        "Access-Control-Allow-Methods": "GET",
        "Access-Control-Max-Age": "600",
    }
    return Response(status_code=204, headers=headers)


@router.get("/iiif/auth/probe/{rid}/{what}")
def iiif_probe(rid: int, what: str, request: Request, db: Db, cfg: Cfg) -> JSONResponse:
    """Can the viewer (with the access token it holds, if any) play this? 302 carries a short-lived signed link."""

    def result(*a: Any, **k: Any) -> JSONResponse:
        return JSONResponse(iiif_auth.probe_result(*a, **k), media_type="application/ld+json")

    rec = db.one("SELECT space, path, remote, source, media FROM $r", r=R("recording", rid))
    if not rec or what not in ("audio", "transcript"):
        return result(404, heading="Not found")
    if acc.is_open(acc.of(db, rid), PART[what]):
        return result(200)
    h = request.headers.get("authorization", "")
    acct = iiif_auth.token_account(db, h[7:].strip() if h.lower().startswith("bearer ") else "")
    if (acct and _account_permitted(db, acct, rid, rec["space"])) or network(request, db).opens(rid, rec["space"]):
        base = base_url(request, cfg)
        if what == "audio":
            ext = pathlib.PurePosixPath((rec.get("remote") or {}).get("path") or rec.get("path") or "").suffix.lower()
            sig = iiif.sign(cfg, rid, "audio")
            loc = {"id": f"{base}/iiif/{rid}/audio?{sig}", "type": "Sound", "format": render.AUDIO_TYPES.get(ext, "audio/mpeg")}
            if (rec.get("media") or {}).get("kind") == "video":
                loc = {"id": f"{base}/iiif/{rid}/media?{sig}", "type": "Video", "format": video.VIDEO_TYPES.get(ext, "video/mp4")}
        else:
            sig = iiif.sign(cfg, rid, "transcript")
            loc = {"id": f"{base}/iiif/{rid}/transcript.vtt?{sig}", "type": "Text", "format": "text/vtt"}
        return result(302, loc)
    if acct:
        return result(403, heading="No access", note="Your account doesn't have access to this recording.")
    return result(401, heading="Sign in to listen", note="This recording needs an account with access to its collection.")


@router.get("/iiif/auth/access")
def iiif_access_page(request: Request, db: Db, cfg: Cfg, origin: str = "") -> HTMLResponse:
    """The access service: a sign-in page. Someone who already holds a valid IIIF cookie just confirms."""
    acct, _ = iiif_auth.cookie_account(db, request.cookies.get(iiif_auth.COOKIE))
    return _access_form(request, cfg, origin, account=acct)


@router.post("/iiif/auth/access")
async def iiif_access_submit(request: Request) -> HTMLResponse:
    db, cfg = get_db(request), get_cfg(request)
    raw_form = (await request.body()).decode("utf-8", "replace")
    form = {k: v[0] for k, v in urllib.parse.parse_qs(raw_form).items()}
    origin = form.get("origin", "")
    expected = request.cookies.get(CSRF_COOKIE, "")
    if not expected or not secrets.compare_digest(form.get("csrf", ""), expected):
        return _access_form(request, cfg, origin, "Your sign-in form expired; please try again.")
    if form.get("continue"):
        acct, _ = await run_in_threadpool(iiif_auth.cookie_account, db, request.cookies.get(iiif_auth.COOKIE))
        if not acct:
            return _access_form(request, cfg, origin, "Your session expired; sign in again.")
    else:
        key = f"{form.get('email', '').strip().lower()}|{client_ip(request)}"
        if auth.throttled(key):
            return _access_form(request, cfg, origin, "Too many attempts; try again in a few minutes.")
        acct = await run_in_threadpool(auth.login, db, form.get("email"), form.get("password"), key)
        if not acct:
            return _access_form(request, cfg, origin, "Wrong email or password.")
    raw = await run_in_threadpool(iiif_auth.grant_cookie, db, cfg, acct["id"])
    nonce = secrets.token_urlsafe(12)
    resp = _auth_page(iiif_auth.access_page(iiif.site_label(cfg, base_url(request, cfg)), nonce, done=True), nonce)
    secure = _secure(request, cfg)
    resp.set_cookie(
        iiif_auth.COOKIE,
        raw,
        max_age=int(cfg["server"]["session_hours"] * 3600),
        httponly=True,
        path="/iiif/",
        secure=secure,
        samesite="none" if secure else "lax",
    )
    resp.delete_cookie(CSRF_COOKIE, path="/iiif/auth")
    return resp


@router.get("/iiif/auth/token")
def iiif_token_service(request: Request, db: Db, cfg: Cfg, messageId: str = "", origin: str = "") -> HTMLResponse:  # noqa: N803 - the spec's name
    """The token service: a page for a hidden iframe that posts an access token to exactly the viewer's origin."""
    nonce = secrets.token_urlsafe(12)
    allowed = cfg["iiif"].get("allowed_origins") or ["*"]
    if not messageId or len(messageId) > 200 or not iiif_auth.ORIGIN_RX.match(origin or ""):
        return _auth_page("<!doctype html><title>token</title>", nonce, "*")  # nowhere safe to post a reply
    base_msg = {"@context": iiif.AUTH2, "messageId": messageId}
    if "*" not in allowed and origin not in allowed:
        msg = {
            **base_msg,
            "type": "AuthAccessTokenError2",
            "profile": "invalidOrigin",
            "heading": iiif.lm("This viewer isn't allowed", "en"),
        }
    else:
        acct, problem = iiif_auth.cookie_account(db, request.cookies.get(iiif_auth.COOKIE))
        if acct:
            raw, secs = iiif_auth.issue_token(db, cfg, acct["id"], origin)
            msg = {**base_msg, "type": "AuthAccessToken2", "accessToken": raw, "expiresIn": secs}
        else:
            msg = {**base_msg, "type": "AuthAccessTokenError2", "profile": problem, "heading": iiif.lm("Please sign in", "en")}
    return _auth_page(iiif_auth.token_page(msg, origin, nonce), nonce, "*" if "*" in allowed else " ".join(allowed))


@router.get("/iiif/auth/logout")
def iiif_logout(request: Request, db: Db, cfg: Cfg) -> HTMLResponse:
    """Revoke the IIIF cookie and every access token issued from it."""
    iiif_auth.forget(db, request.cookies.get(iiif_auth.COOKIE))
    nonce = secrets.token_urlsafe(12)
    site = html_escape(iiif.site_label(cfg, base_url(request, cfg)))
    resp = _auth_page(f"<!doctype html><meta charset='utf-8'><title>Signed out</title><p>You're signed out of {site}.</p>", nonce)
    resp.delete_cookie(iiif_auth.COOKIE, path="/iiif/")
    return resp


# ---------- one recording ----------
@router.get("/iiif/{rid}/manifest")
def iiif_manifest(rid: int, request: Request, user: OptionalUser, db: Db, cfg: Cfg) -> JSONResponse:
    _rec(request, db, cfg, user, rid)
    return _ld(iiif.manifest(db, cfg, rid, base_url(request, cfg)))


@router.get("/iiif/{rid}/annotations/{layer}")
def iiif_annotations(rid: int, layer: str, request: Request, user: OptionalUser, db: Db, cfg: Cfg) -> JSONResponse:
    rec, a = _rec(request, db, cfg, user, rid, "transcript")
    if layer not in iiif.LAYERS or not _content_ok(request, db, cfg, user, rec, rid, a, "transcript"):
        raise HTTPException(404, "not found")
    return _ld(iiif.annotation_page(db, cfg, rid, base_url(request, cfg), layer))


def _ns_name(db: DB, space: int) -> str | None:
    return (db.one("SELECT name FROM $s", s=R("space", space)) or {}).get("name")


@router.get("/iiif/{rid}/record.json")
def iiif_record(rid: int, request: Request, user: OptionalUser, db: Db, cfg: Cfg) -> JSONResponse:
    """schema.org AudioObject (or VideoObject) for search engines and harvesters."""
    _rec(request, db, cfg, user, rid)
    base, row = base_url(request, cfg), db.one("SELECT duration_ms, space, collection FROM $r", r=R("recording", rid))
    ns = _ns_name(db, row["space"])
    urls = {
        "manifest": f"{base}/iiif/{rid}/manifest",
        "collection": iiif.collection_url(base, ns, row.get("collection")),
        "page": f"{base}/#/rec/{rid}",
    }
    return JSONResponse(md.schema_org(md.effective(db, cfg, rid), row, urls), media_type="application/ld+json")


@router.get("/iiif/{rid}/dc.xml")
def iiif_dublin_core(rid: int, request: Request, user: OptionalUser, db: Db, cfg: Cfg) -> Response:
    _rec(request, db, cfg, user, rid)
    base, row = base_url(request, cfg), db.one("SELECT space, path, collection FROM $r", r=R("recording", rid))
    ns = _ns_name(db, row["space"])
    fmt = render.AUDIO_TYPES.get(pathlib.Path(row.get("path") or "").suffix.lower())
    urls = {"manifest": f"{base}/iiif/{rid}/manifest", "collection": iiif.collection_url(base, ns, row.get("collection"))}
    return Response(md.dublin_core(md.effective(db, cfg, rid), {"format": fmt}, urls), media_type="application/xml")


@router.get("/iiif/{rid}/audio")
def iiif_audio(rid: int, request: Request, user: OptionalUser, db: Db, cfg: Cfg) -> Response:
    rec, a = _rec(request, db, cfg, user, rid, "audio")
    if not _content_ok(request, db, cfg, user, rec, rid, a, "audio"):
        raise HTTPException(401, "sign in through the viewer to play this recording")
    return serve_audio(db, cfg, db.one("SELECT * FROM $r", r=R("recording", rid)), rid, request)


@router.get("/iiif/{rid}/media")
def iiif_media(rid: int, request: Request, user: OptionalUser, db: Db, cfg: Cfg) -> Response:
    return iiif_audio(rid, request, user, db, cfg)


@router.get("/iiif/{rid}/frames/{name}")
def iiif_frame(rid: int, name: str, request: Request, user: OptionalUser, db: Db, cfg: Cfg) -> FileResponse:
    rec, a = _rec(request, db, cfg, user, rid, "audio")
    p = video.frames_dir(cfg, rid) / name
    if not FRAME_RX.fullmatch(name) or not p.is_file() or not _content_ok(request, db, cfg, user, rec, rid, a, "audio"):
        raise HTTPException(404, "not found")
    if name.startswith("face-") and not cfg["video"].get("publish_faces") and not auth.allows(_roles(user), rec["space"]):
        raise HTTPException(404, "not found")
    return FileResponse(p, media_type="image/jpeg")


@router.get("/iiif/{rid}/transcript.{fmt}")
def iiif_transcript(rid: int, fmt: str, request: Request, user: OptionalUser, db: Db, cfg: Cfg) -> Response:
    rec, a = _rec(request, db, cfg, user, rid, "transcript")
    if fmt not in iiif.DOWNLOADS:
        raise HTTPException(404, "not found")
    if not _content_ok(request, db, cfg, user, rec, rid, a, "transcript"):
        raise HTTPException(401, "sign in through the viewer to read this transcript")
    text = render.export_text(render.player_data(db, rid), fmt)
    return Response(text, media_type=iiif.DOWNLOADS[fmt][0] + "; charset=utf-8")


@router.get("/iiif/{rid}/search")
def iiif_search(rid: int, request: Request, user: OptionalUser, db: Db, cfg: Cfg, q: str = "", page: int = 0) -> JSONResponse:
    rec, a = _rec(request, db, cfg, user, rid, "transcript")
    base = base_url(request, cfg)
    rids = [rid] if _content_ok(request, db, cfg, user, rec, rid, a, "transcript") else []
    url = f"{base}/iiif/{rid}/search?q={urllib.parse.quote(q)}" + (f"&page={page}" if page else "")
    return _ld(iiif.search(db, base, q, rids, url, page))


@router.get("/iiif/{rid}/autocomplete")
def iiif_autocomplete(rid: int, request: Request, user: OptionalUser, db: Db, cfg: Cfg, q: str = "") -> JSONResponse:
    rec, a = _rec(request, db, cfg, user, rid, "transcript")
    rids = [rid] if _content_ok(request, db, cfg, user, rec, rid, a, "transcript") else []
    url = f"{base_url(request, cfg)}/iiif/{rid}/autocomplete?q={urllib.parse.quote(q)}"
    return _ld(iiif.autocomplete(db, q, rids, url))
