"""HTML pages the API serves itself: the embeddable player and the static reports. The web app is the Next.js frontend.

Neither page can rely on an Authorization header (they are opened in a browser tab or an iframe), and there are no
session cookies. So:

* ``/embed/{rid}`` needs a share link (``?s=``) or a signed link (``GET /api/v1/recordings/{rid}/embed-link``).
* ``/reports/{ns}/{name}`` needs a signed link (the API hands out a signed ``report_url``) or a bearer token with a role
  in the namespace. Links inside the page (other reports of the namespace, audio, frames) are signed as it is served,
  so the page keeps working on its own until its links expire.

The Content-Security-Policy for both comes from ``app.core.middleware``: embeds may be framed by the configured origins;
reports rendered from people's templates (a ``--`` in the name) may not run scripts.
"""

from __future__ import annotations

import html
import pathlib
import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.api.deps import Acl, Cfg, Db
from app.api.media import sign_page_links
from app.api.v1.routes.recordings import has_audio
from app.config import settings
from app.core.security import sign_path
from app.domain import render
from app.domain.store import API

router = APIRouter(include_in_schema=False, tags=["pages"])

REPORT_NAME = re.compile(r"[\w.-]+\.html")
REPORT_HREF = re.compile(r'href="([\w.-]+\.html)"')


@router.get("/")
def index(request: Request) -> RedirectResponse:
    """The web app lives in the frontend; old links (``/?iiif-content=...#/rec/12``) keep their query and fragment."""
    query = request.url.query
    return RedirectResponse(settings.FRONTEND_URL.rstrip("/") + "/" + (f"?{query}" if query else ""), status_code=307)


@router.get("/embed/{rid}", response_class=HTMLResponse)
def embed(rid: int, acl: Acl, db: Db, cfg: Cfg, t: float = 0, s: str = "") -> HTMLResponse:
    """The embeddable player for one recording. Needs a share link (``?s=``) or a signed link."""
    acl.recording(rid, share=s)
    audio = f"{API}/recordings/{rid}/audio" if has_audio(db, cfg, rid) else None
    return HTMLResponse(sign_page_links(render.embed_page(db, cfg, rid, t, audio_url=audio), {rid}))


@router.get("/reports/{ns}/{name}", response_class=HTMLResponse)
def report_file(ns: str, name: str, acl: Acl, db: Db, cfg: Cfg) -> HTMLResponse:
    # a signed link (to a namespace that still exists) or a role in the namespace
    sid = acl.nsid(ns) if acl.signed() else acl.namespace(ns)
    p = pathlib.Path(cfg["data_dir"]) / "reports" / ns / (name or "index.html")
    if not REPORT_NAME.fullmatch(name) or not p.is_file():
        raise HTTPException(404, "not found")
    own = set(db.values("SELECT VALUE record::id(id) FROM recording WHERE space = $s", s=sid))  # the namespace's recordings
    page = sign_page_links(p.read_text(encoding="utf-8"), own)
    page = REPORT_HREF.sub(lambda m: f'href="{html.escape(sign_path(f"/reports/{ns}/{m.group(1)}"))}"', page)
    return HTMLResponse(page, headers={"Cache-Control": "private, no-store"})


@router.get("/reports/{ns}/", response_class=HTMLResponse)
def report_index(ns: str, acl: Acl, db: Db, cfg: Cfg) -> HTMLResponse:
    return report_file(ns, "index.html", acl, db, cfg)
