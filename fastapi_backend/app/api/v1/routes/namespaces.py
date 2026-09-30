"""Namespaces: the ones you can read (with counts), creating them (admins), their settings and IP groups (owners)."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from app.api.deps import Acl, AdminWriter, CurrentUser, Db, Writer, domain_errors, visitor_address
from app.api.media import sign_urls
from app.domain import analyze, auth, ipgroups, pipelines, render, store
from app.domain.store import API, DB
from app.schemas.common import Created, Ok
from app.schemas.namespaces import IpGroup, IpGroupCreate, IpGroups, IpGroupUpdate, Namespace, NamespaceCreate, NamespaceUpdate

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


# ---------- IP groups (docs/access.md) ----------
def _ip_groups(request: Request, db: DB, sid: int) -> IpGroups:
    addr = visitor_address(request)
    fields = ("id", "name", "ranges", "everything", "by", "at", "updated_by", "updated_at")
    return IpGroups(
        address=str(addr) if addr else None,
        groups=[
            IpGroup(
                **{k: g.get(k) for k in fields},
                chosen=len(g.get("recordings") or []),
                here=ipgroups.within(addr, g["ranges"]),
            )
            for g in ipgroups.groups(db, sid)
        ],
    )


def _ip_group_detail(g: dict[str, Any]) -> dict[str, Any]:
    return {"id": g["id"], "name": g["name"], "ranges": g["ranges"], "everything": g["everything"]}


@router.get("/{name}/ip-groups")
def list_ip_groups(name: str, request: Request, acl: Acl, user: CurrentUser, db: Db) -> IpGroups:
    """The namespace's IP groups (owners), and your address as the server sees it, to check the ranges against."""
    return _ip_groups(request, db, acl.namespace(name, "owner"))


@router.post("/{name}/ip-groups")
def create_ip_group(name: str, body: IpGroupCreate, request: Request, acl: Acl, user: Writer, db: Db) -> IpGroups:
    """Add an IP group (owners): visitors from its addresses see all of every recording in the namespace
    (everything), or of the recordings chosen on each. Answers with all of the namespace's groups."""
    sid = acl.namespace(name, "owner")
    with domain_errors():
        g = ipgroups.create(db, sid, body.name, body.ranges, body.everything, user.email)
    auth.audit(db, user.as_audit(), "namespace.ip_group.create", f"space:{sid}", _ip_group_detail(g))
    return _ip_groups(request, db, sid)


@router.patch("/{name}/ip-groups/{gid}")
def update_ip_group(name: str, gid: int, body: IpGroupUpdate, request: Request, acl: Acl, user: Writer, db: Db) -> IpGroups:
    """Rename an IP group, change its ranges or what it opens (owners). Choosing recordings again after opening
    everything brings back the ones chosen before."""
    sid = acl.namespace(name, "owner")
    changes = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if not changes:
        raise HTTPException(400, "send name, ranges or everything")
    with domain_errors():
        before = ipgroups.get(db, gid, sid)
        g = ipgroups.update(db, gid, sid, changes, user.email)
    detail = {k: {"from": before[k], "to": g[k]} for k in ("name", "ranges", "everything") if before[k] != g[k]}
    auth.audit(db, user.as_audit(), "namespace.ip_group.update", f"space:{sid}", {"id": gid, **detail})
    return _ip_groups(request, db, sid)


@router.delete("/{name}/ip-groups/{gid}")
def delete_ip_group(name: str, gid: int, request: Request, acl: Acl, user: Writer, db: Db) -> IpGroups:
    """Delete an IP group (owners): its visitors lose what it opened. Answers with the groups left."""
    sid = acl.namespace(name, "owner")
    with domain_errors():
        g = ipgroups.delete(db, gid, sid)
    auth.audit(db, user.as_audit(), "namespace.ip_group.delete", f"space:{sid}", _ip_group_detail(g))
    return _ip_groups(request, db, sid)
