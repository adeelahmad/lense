"""Namespaces: the ones you can read (with counts), creating them (admins) and their settings (owners)."""

from __future__ import annotations

from collections import Counter, defaultdict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from app.api.deps import Acl, AdminWriter, CurrentUser, Db, Writer
from app.api.media import sign_urls
from app.domain import analyze, auth, pipelines, render, store
from app.domain.store import API
from app.schemas.common import Created, Ok
from app.schemas.namespaces import Namespace, NamespaceCreate, NamespaceUpdate

router = APIRouter(prefix="/namespaces", tags=["namespaces"])

R = store.R


@router.get("")
def list_namespaces(user: CurrentUser, db: Db) -> list[Namespace]:
    rm = user.roles
    agg: dict[int, dict[str, int]] = defaultdict(lambda: {"recordings": 0, "ms": 0, "analyzed": 0, "errors": 0})
    for r in db.rows("SELECT space, status, duration_ms FROM recording WHERE space IN $s", s=sorted(rm)):
        a = agg[r["space"]]
        a["recordings"] += 1
        a["ms"] += r.get("duration_ms") or 0
        a["analyzed"] += r.get("status") == "analyzed"
        a["errors"] += r.get("status") == "error"
    speakers_n = Counter(r["space"] for r in db.rows("SELECT space FROM speaker WHERE space IN $s", s=sorted(rm)))
    out = [
        {
            **s,
            **agg[s["id"]],
            "speakers": speakers_n.get(s["id"], 0),
            "role": rm[s["id"]],
            "wordcloud": f"{API}/namespaces/{s['name']}/wordcloud.svg",
        }
        for s in db.rows("SELECT record::id(id) AS id, name, graph FROM space ORDER BY name")
        if s["id"] in rm
    ]
    return [Namespace.model_validate(x) for x in sign_urls(out)]


@router.post("")
def create_namespace(body: NamespaceCreate, user: AdminWriter, db: Db, request: Request) -> Created:
    name = body.name.strip()
    if not store.NS_RX.match(name):
        raise HTTPException(400, "namespace names use lowercase letters, digits, - and _")
    sid = store.ns_id(db, name)
    db.q("UPDATE $r SET graph = $g", r=R("space", sid), g=body.graph)
    auth.audit(db, user.as_audit(), "namespace.create", name)
    request.app.state.graph_cache.clear()
    return Created(id=sid)


@router.patch("/{name}")
def update_namespace(name: str, body: NamespaceUpdate, acl: Acl, user: Writer, db: Db, request: Request) -> Ok:
    """Owners: the graph mode (shared or isolated) and the default pipeline (null for the built-in one)."""
    sid = acl.namespace(name, "owner")
    sent = body.model_fields_set
    if "graph" in sent:
        if body.graph is None:
            raise HTTPException(400, "graph is shared or isolated")
        db.q("UPDATE $r SET graph = $g", r=R("space", sid), g=body.graph)
    if "pipeline" in sent:
        if body.pipeline is not None:
            try:
                pipelines.get(db, body.pipeline)
            except (KeyError, TypeError, ValueError):
                raise HTTPException(400, "no such pipeline") from None
        db.q("UPDATE $r SET pipeline = $p", r=R("space", sid), p=body.pipeline)
    request.app.state.graph_cache.clear()
    return Ok()


@router.get("/{name}/wordcloud.svg", response_class=Response, responses={200: {"content": {"image/svg+xml": {}}}})
def get_namespace_wordcloud(name: str, acl: Acl, db: Db) -> Response:
    """The namespace's word cloud. Accepts a bearer token or the signed link from the namespace list."""
    if acl.signed():
        sid = acl.nsid(name)
    else:
        sid = acl.namespace(name)
    return Response(render.wordcloud_svg(analyze.ns_keywords(db, sid, 80), label=f"Word cloud for {name}"), media_type="image/svg+xml")
