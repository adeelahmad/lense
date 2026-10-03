"""The archive as RDF (domain/rdf.py) for the app and API clients: a recording's description, or a whole namespace's
graph (its collections, recordings, entities and speakers), in Turtle, JSON-LD, N-Triples or RDF/XML; and Dublin Core
read back into a namespace's recordings (domain/rdf_import.py)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from app.api.deps import Acl, Cfg, CurrentUser, Db, Writer
from app.api.iiif import base_url
from app.api.linked_data import rdf_response, wanted
from app.domain import auth, rdf, rdf_import
from app.schemas.rdf import RdfImport, RdfImportResult

router = APIRouter(tags=["rdf"])
RDF_CONTENT: dict[int | str, dict[str, Any]] = {
    200: {"content": {m: {"schema": {"type": "string"}} for m in rdf.FORMATS.values()}, "description": "RDF"}
}
FORMAT = Query(None, description="turtle, json-ld, nt or xml; else the Accept header decides (Turtle when it doesn't)")


@router.get("/recordings/{rid}/rdf", response_class=Response, responses=RDF_CONTENT)
def get_recording_rdf(rid: int, request: Request, user: CurrentUser, acl: Acl, db: Db, cfg: Cfg, format: str | None = FORMAT) -> Response:
    """The recording described with Dublin Core, with the entities it mentions."""
    rec = acl.recording(rid)
    g = rdf.recording_graph(db, cfg, base_url(request, cfg), rid, member=acl.member(rec))
    return rdf_response(g, wanted(request, format) or "turtle")


@router.get("/namespaces/{name}/rdf", response_class=Response, responses=RDF_CONTENT)
def get_namespace_rdf(
    name: str,
    request: Request,
    user: CurrentUser,
    acl: Acl,
    db: Db,
    cfg: Cfg,
    format: str | None = FORMAT,
    download: bool = Query(False, description="as a file to save"),
) -> Response:
    """Everything in the namespace as one graph: itself, its collections, recordings, entities and speakers."""
    sid = acl.namespace(name)
    g = rdf.namespace_graph(db, cfg, base_url(request, cfg), sid)
    return rdf_response(g, wanted(request, format) or "turtle", filename=name if download else None)


@router.post("/namespaces/{name}/rdf/import")
def import_namespace_rdf(name: str, body: RdfImport, request: Request, user: Writer, acl: Acl, db: Db, cfg: Cfg) -> RdfImportResult:
    """Read Dublin Core descriptions into the namespace's recordings (matched by their URI or an identifier). Each change
    is a metadata edit, kept in the recording's history. With dry_run (the default) nothing changes."""
    sid = acl.namespace(name, "editor")
    try:
        out = rdf_import.run(db, cfg, base_url(request, cfg), sid, body.data, body.format, body.dry_run, user.email)
    except rdf_import.ImportProblem as e:
        raise HTTPException(400, str(e)) from None
    if not body.dry_run:
        auth.audit(db, user.as_audit(), "metadata.rdf_import", f"space:{sid}", [i["recording"] for i in out["items"] if i["fields"]])
    return RdfImportResult.model_validate(out)
