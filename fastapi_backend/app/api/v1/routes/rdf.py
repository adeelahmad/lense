"""The archive as RDF (domain/rdf.py) for the app and API clients: a recording's description, or a whole namespace's
graph (its collections, recordings, entities and speakers), in Turtle, JSON-LD, N-Triples or RDF/XML; and Dublin Core
read back into a namespace's recordings (domain/rdf_import.py)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response

from app.api.deps import Access, Acl, Cfg, CurrentUser, Db, Writer
from app.api.iiif import base_url
from app.api.linked_data import rdf_response, wanted
from app.domain import auth, rdf, rdf_import
from app.domain.store import DB
from app.schemas.rdf import RdfImport, RdfImportResult, SparqlQuery

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


SPARQL_RESULTS = "application/sparql-results+json"
SPARQL_CONTENT: dict[int | str, dict[str, Any]] = {
    200: {
        "content": {SPARQL_RESULTS: {"schema": {"type": "object"}}, **{m: {"schema": {"type": "string"}} for m in rdf.FORMATS.values()}},
        "description": "SPARQL results (SELECT, ASK) or RDF (CONSTRUCT, DESCRIBE)",
    }
}


def _sparql(name: str, query: str, request: Request, acl: Access, db: DB, cfg: dict[str, Any], format: str | None) -> Response:
    sid = acl.namespace(name)
    base = base_url(request, cfg)
    g = rdf.namespace_graph(db, cfg, base, sid)
    try:
        kind, out = rdf.sparql(g, query, base)
    except rdf.QueryProblem as e:
        raise HTTPException(400, str(e)) from None
    if kind == "results":
        return JSONResponse(out, media_type=SPARQL_RESULTS)
    return rdf_response(out, wanted(request, format) or "turtle")


@router.get("/namespaces/{name}/sparql", response_class=Response, responses=SPARQL_CONTENT)
def query_namespace_sparql(
    name: str,
    request: Request,
    user: CurrentUser,
    acl: Acl,
    db: Db,
    cfg: Cfg,
    query: str = Query(description="a SPARQL SELECT, ASK, CONSTRUCT or DESCRIBE query"),
    format: str | None = FORMAT,
) -> Response:
    """A read-only SPARQL query over the namespace's graph (what GET /namespaces/{name}/rdf returns). dcterms, dcmitype,
    foaf, skos, owl, rdf, rdfs, xsd and lens are known prefixes. SERVICE and FROM aren't allowed."""
    return _sparql(name, query, request, acl, db, cfg, format)


@router.post("/namespaces/{name}/sparql", response_class=Response, responses=SPARQL_CONTENT)
def post_namespace_sparql(
    name: str, body: SparqlQuery, request: Request, user: CurrentUser, acl: Acl, db: Db, cfg: Cfg, format: str | None = FORMAT
) -> Response:
    """The same, with the query in the body (for long ones)."""
    return _sparql(name, body.query, request, acl, db, cfg, format)
