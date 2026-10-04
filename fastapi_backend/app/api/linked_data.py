"""Linked data: the URIs the archive's RDF uses (domain/rdf.py), each answering with RDF or a page.

`/id/<kind>/<id>` is what a recording, collection, namespace, entity or speaker is called in RDF. An RDF client
(Accept: text/turtle, application/ld+json, application/n-triples or application/rdf+xml, or `?format=`) gets its
description; a browser is sent to its page (303 See Other, as linked data does). A recording is open to everyone when it
is public (its IIIF manifest is), and to whoever may see it otherwise; the rest are for the namespace's members, and
look absent to anyone else. `/ns` is the Lens vocabulary.
"""

from __future__ import annotations

import urllib.parse

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response

from app.api.deps import Acl, Cfg, Db, OptionalUser
from app.api.iiif import _rec, base_url
from app.domain import rdf
from app.domain.store import R

router = APIRouter(include_in_schema=False, tags=["linked-data"])
FORMAT = Query(None, description="turtle, json-ld, nt or xml (or ttl, jsonld, rdf); else the Accept header decides")


def rdf_response(g, fmt: str, filename: str | None = None) -> Response:
    headers = {"Vary": "Accept", "Access-Control-Allow-Origin": "*"}
    if filename:
        ext = {v: k for k, v in rdf.SUFFIX.items()}[fmt]
        headers["Content-Disposition"] = f'attachment; filename="{filename}.{ext}"'
    return Response(rdf.serialize(g, fmt), media_type=f"{rdf.FORMATS[fmt]}; charset=utf-8", headers=headers)


def wanted(request: Request, fmt: str | None) -> str | None:
    try:
        return rdf.negotiate(request.headers.get("accept"), fmt)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


def _see(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303, headers={"Vary": "Accept"})


@router.get("/ns")
def vocabulary(request: Request, cfg: Cfg, format: str | None = FORMAT) -> Response:
    return rdf_response(rdf.vocabulary(base_url(request, cfg)), wanted(request, format) or "turtle")


@router.get("/id/recording/{rid}")
def recording(rid: int, request: Request, user: OptionalUser, acl: Acl, db: Db, cfg: Cfg, format: str | None = FORMAT) -> Response:
    rec, a = _rec(request, db, cfg, user, rid)
    row = db.one("SELECT space, collection FROM $r", r=R("recording", rid)) or {}
    member = bool(user and acl.rank_in(row["space"], row.get("collection")))
    base = base_url(request, cfg)
    fmt = wanted(request, format)
    if not fmt:
        return _see(f"{base}/recordings/{rid}" if member else f"{base}/explore/recordings/{rid}")
    return rdf_response(rdf.recording_graph(db, cfg, base, rid, member), fmt)


def _member_of(acl: Acl, space: int | None) -> None:
    if space is None or not acl.user or not acl.rank_in(space, None):
        raise HTTPException(404, "not found")


@router.get("/id/collection/{cid}")
def collection(cid: int, request: Request, acl: Acl, db: Db, cfg: Cfg, format: str | None = FORMAT) -> Response:
    c = db.one("SELECT space FROM $r", r=R("collection", cid))
    if not c or not acl.user or not acl.rank_in(c["space"], cid):
        raise HTTPException(404, "not found")
    base, fmt = base_url(request, cfg), wanted(request, format)
    return rdf_response(rdf.collection_graph(db, base, cid), fmt) if fmt else _see(f"{base}/collections/{cid}")


@router.get("/id/entity/{eid}")
def entity(eid: int, request: Request, acl: Acl, db: Db, cfg: Cfg, format: str | None = FORMAT) -> Response:
    _member_of(acl, (db.one("SELECT space FROM $r", r=R("entity", eid)) or {}).get("space"))
    base, fmt = base_url(request, cfg), wanted(request, format)
    return rdf_response(rdf.entity_graph(db, base, eid), fmt) if fmt else _see(f"{base}/entities/{eid}")


@router.get("/id/speaker/{sid}")
def speaker(sid: int, request: Request, acl: Acl, db: Db, cfg: Cfg, format: str | None = FORMAT) -> Response:
    space = (db.one("SELECT space FROM $r", r=R("speaker", sid)) or {}).get("space")
    _member_of(acl, space)
    base, fmt = base_url(request, cfg), wanted(request, format)
    if not fmt:
        return _see(f"{base}/speakers/{sid}")
    return rdf_response(rdf.speaker_graph(db, base, sid), fmt)


@router.get("/id/namespace/{name}")
def namespace(name: str, request: Request, acl: Acl, db: Db, cfg: Cfg, format: str | None = FORMAT) -> Response:
    sid = acl.nsid(name)
    _member_of(acl, sid)
    base, fmt = base_url(request, cfg), wanted(request, format)
    if not fmt:
        return _see(f"{base}/library?ns={urllib.parse.quote(name)}")
    return rdf_response(rdf.namespace_graph(db, cfg, base, sid), fmt)
