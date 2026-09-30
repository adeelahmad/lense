"""The IIIF panel of a recording, shared moments (IIIF Content State) and importing from other IIIF archives.

The IIIF resources themselves (manifests, collections, search, the authorization flow) live outside /api/v1, at /iiif/.
"""

from __future__ import annotations

import threading
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.api.deps import Acl, AdminWriter, Cfg, CurrentUser, Db
from app.api.iiif import base_url, viewer_links
from app.domain import auth, iiif, store
from app.domain import metadata as md
from app.schemas.iiif import ContentState, IiifImport, IiifImported, IiifPanel, IiifUrl, ViewerLink

router = APIRouter(tags=["iiif"])


@router.get("/recordings/{rid}/iiif")
def get_recording_iiif(rid: int, request: Request, user: CurrentUser, acl: Acl, db: Db, cfg: Cfg) -> IiifPanel:
    """The recording's manifest (as `json`), whether it is published, its layers, a schema check and viewer links."""
    acl.recording(rid)
    man = iiif.manifest(db, cfg, rid, base_url(request, cfg))
    problems = iiif.validate(man)
    level = md.access_of(db, cfg, rid)
    return IiifPanel.model_validate(
        {
            "manifest": man["id"],
            "collection": man["partOf"][0]["id"],
            "access": level,
            "published": level != "private",
            "layers": [a.get("label") and md.first(a["label"]) for a in man["items"][0].get("annotations", [])[1:]],
            "search": bool(man.get("service")),
            "validation": {"checked": problems is not None, "problems": problems or []},
            "viewers": viewer_links(cfg, man["id"]),
            "json": man,
        }
    )


@router.get("/recordings/{rid}/content-state")
def get_content_state(
    rid: int, request: Request, user: CurrentUser, acl: Acl, cfg: Cfg, t0: float | None = None, t1: float | None = None
) -> ContentState:
    """A IIIF Content State pointing at a moment (t0..t1 seconds) of the recording, and links that open it."""
    acl.recording(rid)
    base = base_url(request, cfg)
    state, enc = iiif.content_state(base, rid, t0, t1)
    return ContentState(
        content_state=state,
        encoded=enc,
        link=f"{base}/?iiif-content={enc}#/rec/{rid}",
        viewers=[ViewerLink(**v) for v in viewer_links(cfg, f"{base}/iiif/{rid}/manifest", enc)],
    )


@router.post("/import/iiif/preview")
def preview_iiif_import(body: IiifUrl, user: AdminWriter) -> dict[str, Any]:
    """What a IIIF manifest or collection would import (Presentation 3; version 2 is refused with a message)."""
    try:
        return iiif.preview(body.url)
    except (ValueError, OSError) as e:
        raise HTTPException(400, f"couldn't read that IIIF resource: {e}") from None


@router.post("/import/iiif", responses={202: {"model": IiifImported, "description": "importing in the background"}})
def import_iiif(body: IiifImport, user: AdminWriter, db: Db, cfg: Cfg) -> IiifImported:
    """Import recordings (audio, captions and metadata) from a IIIF manifest or collection into a namespace.

    With `wait`, answers with the new recording ids; otherwise imports in the background (202).
    """
    url, ns = body.url, body.namespace.strip()
    if not store.NS_RX.match(ns):
        raise HTTPException(400, "choose a namespace: lowercase letters, digits, - and _")

    def run() -> list[int]:
        return iiif.import_url(db, cfg, url, ns, body.keep_transcripts, user.email, body.limit)

    auth.audit(db, user.as_audit(), "import.iiif", url)
    if body.wait:
        try:
            return IiifImported(recordings=run())
        except (ValueError, OSError) as e:
            raise HTTPException(400, f"couldn't import: {e}") from None
    threading.Thread(target=run, daemon=True).start()
    return JSONResponse({"ok": True, "status": "importing"}, status_code=202)  # type: ignore[return-value]
