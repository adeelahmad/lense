"""HTML pages the API serves itself: the embeddable player and the static reports. The web app is the Next.js frontend.

Neither page can rely on an Authorization header (they are opened in a browser tab or an iframe), and there are no
session cookies. So:

* ``/embed/{rid}`` needs a share link (``?s=``) or a signed link (``GET /api/v1/recordings/{rid}/embed-link``);
  ``/s/{code}`` is a share link's short address and serves the same player. A link that doesn't work (expired,
  revoked, mistyped, or its recording is gone) gets one neutral page with status 410, which says nothing about the
  recording, not even whether it exists.
* ``/reports/{ns}/{name}`` needs a signed link (the API hands out a signed ``report_url``) or a bearer token with a role
  in the namespace. Links inside the page (other reports of the namespace, audio, frames) are signed as it is served,
  so the page keeps working on its own until its links expire.

The Content-Security-Policy for both comes from ``app.core.middleware``: embeds (``/embed/``, ``/s/``) may be framed by
the configured origins; reports rendered from people's templates (a ``--`` in the name) may not run scripts.

Opening a share link's player in a frame remembers the framing site (the Referer's origin, when the browser says it's a
frame), and the player reports its first play on each page load (``POST /embed/{rid}/played``). Neither counts when the
page that opened it is Lens itself, such as the embed builder's preview. Editors see both in
``GET /api/v1/recordings/{rid}/shares``.
"""

from __future__ import annotations

import html
import pathlib
import re
import urllib.parse
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.api.deps import Acl, Cfg, Db
from app.api.media import sign_page_links
from app.api.v1.routes.recordings import has_audio
from app.config import settings
from app.core.security import sign_path
from app.domain import auth, keyring, render
from app.domain.store import API, R

router = APIRouter(include_in_schema=False, tags=["pages"])

REPORT_NAME = re.compile(r"[\w.-]+\.html")
REPORT_HREF = re.compile(r'href="([\w.-]+\.html)"')
FRAMED = {"iframe", "frame", "embed", "object"}  # Sec-Fetch-Dest of a page opened inside another page


@router.get("/")
def index(request: Request) -> RedirectResponse:
    """The web app lives in the frontend; old links (``/?iiif-content=...#/rec/12``) keep their query and fragment."""
    query = request.url.query
    return RedirectResponse(settings.FRONTEND_URL.rstrip("/") + "/" + (f"?{query}" if query else ""), status_code=307)


def _gone() -> HTMLResponse:
    """The page for any link that doesn't work: the same whatever was wrong with it."""
    return HTMLResponse(render.link_gone_page(), status_code=410, headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"})


def _origin(url: str | None) -> str | None:
    """``https://blog.example.org/post`` → ``https://blog.example.org``; None when it isn't a web address."""
    try:
        u = urllib.parse.urlsplit(url or "")
        port = u.port
    except ValueError:
        return None
    if u.scheme not in ("http", "https") or not u.hostname or len(u.hostname) > 253:
        return None
    host = f"[{u.hostname}]" if ":" in u.hostname else u.hostname
    return f"{u.scheme}://{host}" + (f":{port}" if port and port != {"http": 80, "https": 443}[u.scheme] else "")


def _first(v: str | None) -> str:
    """The first of a proxy header's values (a chain of proxies lists one per hop)."""
    return (v or "").split(",")[0].strip()


def _player(db: Db, cfg: Cfg, rid: int, t: float, played: str | None = None) -> HTMLResponse:
    audio = f"{API}/recordings/{rid}/audio" if has_audio(db, cfg, rid) else None
    return HTMLResponse(sign_page_links(render.embed_page(db, cfg, rid, t, audio_url=audio, played=played), {rid}))


def _live(db: Db, s: str, rid: int | None = None) -> dict[str, Any] | None:
    """The share link `s` (a token or a short code), if it works (for recording `rid`, when given)."""
    link: dict[str, Any] | None = auth.find_share(db, s) if s else None
    return link if link and auth.share_live(link) and (rid is None or link["recording"] == rid) else None


def _shared(request: Request, db: Db, cfg: Cfg, link: dict[str, Any], t: float, s: str) -> HTMLResponse:
    """The player opened with a share link: note the site that framed it, and have the player report its first play,
    unless the page that opened it is Lens itself."""
    rid = link["recording"]
    h = request.headers
    site = _origin(h.get("referer"))
    own = _origin(f"{_first(h.get('x-forwarded-proto')) or request.url.scheme}://{_first(h.get('x-forwarded-host')) or h.get('host')}")
    if site and site == own:
        return _player(db, cfg, rid, t)
    if site and h.get("sec-fetch-dest") in FRAMED:
        auth.share_embedded(db, link, site)
    return _player(db, cfg, rid, t, played=f"/embed/{rid}/played?s={urllib.parse.quote(s)}")


@router.get("/embed/{rid}", response_class=HTMLResponse)
def embed(rid: int, request: Request, acl: Acl, db: Db, cfg: Cfg, t: float = 0, s: str = "") -> HTMLResponse:
    """The embeddable player for one recording. Needs a share link (``?s=``) or a signed link; else the 410 page."""
    try:
        acl.recording(rid, share=s)
    except HTTPException:
        return _gone()
    link = _live(db, s, rid)
    return _shared(request, db, cfg, link, t, s) if link else _player(db, cfg, rid, t)


@router.post("/embed/{rid}/played", status_code=204)
def played(rid: int, db: Db, s: str = "") -> Response:
    """The shared player started playing (the page says so once per load): one more play on its link."""
    link = _live(db, s, rid)
    if link:
        auth.share_played(db, link["key"])
    return Response(status_code=204)


@router.get("/s/{code}", response_class=HTMLResponse)
def short_link(code: str, request: Request, db: Db, cfg: Cfg, t: float = 0) -> HTMLResponse:
    """A share link's short address: the same player as ``/embed/{rid}?s=…``, or the 410 page."""
    link = _live(db, code) if len(code) == auth.SHORT_LEN else None
    if not link or not db.one("SELECT id FROM $r", r=R("recording", link["recording"])):
        return _gone()
    return _shared(request, db, cfg, link, t, code)


@router.get("/reports/{ns}/{name}", response_class=HTMLResponse)
def report_file(ns: str, name: str, acl: Acl, db: Db, cfg: Cfg) -> HTMLResponse:
    # a signed link (to a namespace that still exists) or a role in the namespace
    sid = acl.nsid(ns) if acl.signed() else acl.namespace(ns)
    p = pathlib.Path(cfg["data_dir"]) / "reports" / ns / (name or "index.html")
    if not REPORT_NAME.fullmatch(name) or not p.is_file():
        raise HTTPException(404, "not found")
    own = set(db.values("SELECT VALUE record::id(id) FROM recording WHERE space = $s", s=sid))  # the namespace's recordings
    try:
        text = keyring.read_plain(db, cfg, p).decode("utf-8")
    except keyring.Locked:
        raise HTTPException(423, "this namespace is a locked vault: its owners unlock it with a passkey") from None
    page = sign_page_links(text, own, full=True)
    page = REPORT_HREF.sub(lambda m: f'href="{html.escape(sign_path(f"/reports/{ns}/{m.group(1)}"))}"', page)
    return HTMLResponse(page, headers={"Cache-Control": "private, no-store"})


@router.get("/reports/{ns}/", response_class=HTMLResponse)
def report_index(ns: str, acl: Acl, db: Db, cfg: Cfg) -> HTMLResponse:
    return report_file(ns, "index.html", acl, db, cfg)
