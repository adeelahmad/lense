"""The graph for people and agents (docs/graph.md): the archive as a property graph of namespaces, collections,
recordings, speakers and entities, to walk (parents, children, ancestors, descendants, neighbours, paths) and to query
with read-only Cypher. Every call sees only the namespaces its caller can read: one (`ns:<name>`) or every shared one
(`global`). Changes are proposed (or, for editors who ask, made) as graph changes that can be undone.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.api.deps import Acl, CurrentUser, Db, Writer
from app.domain import auth, cypher, graph_model, organize
from app.domain.store import DB

router = APIRouter(tags=["graph"])
SCOPE = Query("global", description='"global" (every shared namespace you can read) or "ns:<namespace>"')
_KEEP = 8  # property graphs kept in memory per process


def _stamp(db: DB):
    latest = db.rows("SELECT analyzed_at FROM recording ORDER BY analyzed_at DESC LIMIT 1")
    return tuple(db.values("SELECT VALUE n FROM seq")), tuple(r.get("analyzed_at") for r in latest)


def projection(request: Request, db: DB, acl, scope: str) -> graph_model.Graph:
    """The caller's graph for a scope, cached until anything in the archive changes."""
    scope = "global" if scope in ("", "all", "global") else scope
    if not (scope == "global" or scope.startswith("ns:")):
        raise HTTPException(400, 'scope is "global" or "ns:<namespace>"')
    readable = acl.readable()
    cache = request.app.state.graph_cache
    key = ("property", scope, None if readable is None else tuple(sorted(readable)), _stamp(db))
    if key not in cache:
        try:
            g = graph_model.build(db, scope, readable)
        except KeyError:
            raise HTTPException(404, "not found") from None
        mine = [k for k in cache if isinstance(k, tuple) and k[:1] == ("property",)]
        for old in mine[: max(0, len(mine) - _KEEP + 1)]:
            cache.pop(old, None)
        cache[key] = g
    return cache[key]


def _types(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    out = [t.strip().upper() for t in raw.split(",") if t.strip()]
    bad = [t for t in out if t not in graph_model.REL_TYPES]
    if bad:
        raise HTTPException(400, f"unknown relationship type {', '.join(bad)}; one of {', '.join(graph_model.REL_TYPES)}")
    return out


@router.get("/graph/schema")
def graph_schema(request: Request, user: CurrentUser, acl: Acl, db: Db, scope: str = SCOPE) -> dict[str, Any]:
    """What the graph holds in this scope: labels, relationship types (and what they join), properties and counts, with
    example queries. Agents read this before writing Cypher."""
    g = projection(request, db, acl, scope)
    return {**graph_model.schema(g), "examples": EXAMPLES, "query_language": "cypher (read-only subset; see docs/graph.md)"}


@router.get("/graph/related")
def graph_related(
    request: Request,
    user: CurrentUser,
    acl: Acl,
    db: Db,
    node: str = Query(description="a node id: n<id>, c<id>, r<id>, s<id>, e<id> or e:<name key>"),
    relation: Literal["children", "parents", "ancestors", "descendants", "neighbours"] = "neighbours",
    depth: int = Query(1, ge=1, le=8),
    types: str | None = Query(
        None, description="relationship types to follow, comma separated (default: the hierarchy, or any for neighbours)"
    ),
    limit: int = Query(200, ge=1, le=graph_model.MAX_RESULTS),
    scope: str = SCOPE,
) -> dict[str, Any]:
    """Nodes related to one node, nearest first, with the relationships between them."""
    g = projection(request, db, acl, scope)
    try:
        return graph_model.related(g, node, relation, depth, _types(types), limit)
    except KeyError:
        raise HTTPException(404, "not in this graph") from None


@router.get("/graph/paths")
def graph_paths(
    request: Request,
    user: CurrentUser,
    acl: Acl,
    db: Db,
    a: str,
    b: str,
    max_depth: int = Query(4, ge=1, le=8),
    limit: int = Query(10, ge=1, le=100),
    types: str | None = None,
    directed: bool = False,
    shortest: bool = False,
    scope: str = SCOPE,
) -> dict[str, Any]:
    """Paths from a to b, shortest first: every simple path up to max_depth hops, or just the shortest ones."""
    g = projection(request, db, acl, scope)
    try:
        return graph_model.paths(g, a, b, max_depth, limit, _types(types), directed, shortest)
    except KeyError:
        raise HTTPException(404, "not in this graph") from None


class GraphQuery(BaseModel):
    query: str = Field(description="read-only Cypher, e.g. MATCH (e:Person)<-[:MENTIONS]-(r:Recording) RETURN e.name, count(r)")
    params: dict[str, Any] = Field(default_factory=dict, description="values for $parameters in the query")
    scope: str = "global"
    limit: int = Field(500, ge=1, le=5000, description="rows at most")


def run_query(request: Request, db: DB, acl, body: GraphQuery) -> dict[str, Any]:
    g = projection(request, db, acl, body.scope)
    try:
        out = cypher.run(g, body.query, body.params, max_rows=body.limit)
    except cypher.CypherError as e:
        raise HTTPException(400, f"query error: {e}") from None
    return {"scope": g.scope, "namespaces": g.namespaces, **out}


@router.post("/graph/query")
def graph_query(request: Request, body: GraphQuery, user: CurrentUser, acl: Acl, db: Db) -> dict[str, Any]:
    """Run a read-only Cypher query over the graph you can read: {columns, rows, nodes, edges, truncated}. Nodes and
    relationships it returns are also listed in nodes and edges, ready to draw. Read-only tokens may query."""
    return run_query(request, db, acl, body)


class ChangeAsk(BaseModel):
    kind: Literal["merge", "link"] = Field(
        description="merge: two entities of one namespace are one; link: entities of two namespaces are the same thing"
    )
    a: str = Field(description="an entity node id, e<id> (for a merge, the one kept)")
    b: str
    reason: str | None = Field(None, max_length=300)
    apply: bool = Field(False, description="make it now (editors) instead of proposing it for someone to accept")


def _entity_id(raw: str) -> int:
    raw = str(raw).strip()
    if raw.startswith("e") and raw[1:].isdigit():
        return int(raw[1:])
    if raw.isdigit():
        return int(raw)
    raise HTTPException(400, "name entities by id: e<id> (a merged global node lists its ids)")


@router.post("/graph/changes")
def propose_graph_change(request: Request, body: ChangeAsk, user: Writer, acl: Acl, db: Db) -> dict[str, Any]:
    """Ask for a change to the graph. Proposed by default, for someone with editor access to accept in Proposed
    changes; `apply` makes it at once, and it can still be undone. Both need editor access to the two namespaces; read-only
    tokens can't ask."""
    from app.domain import entities

    ids = [_entity_id(body.a), _entity_id(body.b)]
    rows = []
    for eid in ids:
        try:
            rows.append(entities._entity(db, eid))
        except KeyError:
            raise HTTPException(404, "not found") from None
    for r in rows:
        acl.need(r["space"], "editor")
    origin = {"by": user.email, "via": user.via}
    try:
        cid, status = organize.propose(db, body.kind, ids[0], ids[1], body.reason, origin, body.apply, user.email)
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e)) from None
    auth.audit(
        db, user.as_audit(), f"graph_change.{'apply' if status == 'applied' else 'propose'}", f"graph_change:{cid}", body.model_dump()
    )
    request.app.state.graph_cache.clear()
    return {"id": cid, "status": status}


EXAMPLES = [
    {"ask": "Who is mentioned most?", "cypher": "MATCH (e:Person) RETURN e.name, e.mentions ORDER BY e.mentions DESC LIMIT 10"},
    {
        "ask": "Which recordings mention Acme?",
        "cypher": "MATCH (r:Recording)-[m:MENTIONS]->(e:Entity) WHERE toLower(e.name) = 'acme' RETURN r.name, r.date, m.count ORDER BY r.date",
    },
    {
        "ask": "Who talks about Acme, and how often?",
        "cypher": "MATCH (s:Speaker)-[x:SAID]->(e:Entity {name: 'Acme'}) RETURN s.name, x.count ORDER BY x.count DESC",
    },
    {
        "ask": "What is discussed together with Acme?",
        "cypher": "MATCH (e:Entity {name: 'Acme'})-[w:MENTIONED_WITH]-(o:Entity) RETURN o.name, o.type, w.count ORDER BY w.count DESC LIMIT 20",
    },
    {
        "ask": "How is Alice connected to Acme?",
        "cypher": "MATCH p = shortestPath((s:Speaker {name: 'Alice'})-[*..6]-(e:Entity {name: 'Acme'})) RETURN [n IN nodes(p) | n.name] AS chain",
    },
    {
        "ask": "Everything in the Interviews collection",
        "cypher": "MATCH (c:Collection {name: 'Interviews'})-[:CONTAINS*1..8]->(r:Recording) RETURN r.name, r.date ORDER BY r.date DESC",
    },
]
