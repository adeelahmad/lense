"""The graph for people and agents (docs/graph.md): the archive as a property graph of namespaces, collections,
recordings, speakers and entities, to walk (parents, children, ancestors, descendants, neighbours, paths) and to query
with read-only Cypher. Every call sees only the namespaces its caller can read: one (`ns:<name>`) or every shared one
(`global`). Changes are proposed (or, for editors who ask, made) as graph changes that can be undone.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.api.deps import Acl, Cfg, CurrentUser, Db, Writer
from app.domain import auth, cypher, graph_ask, graph_history, graph_model, llm, organize
from app.domain.store import DB

router = APIRouter(tags=["graph"])
SCOPE = Query("global", description='"global" (every shared namespace you can read) or "ns:<namespace>"')
AS_OF = Annotated[str | None, Query(description="the graph as of a version: a number or a version's name (default: today's)")]
_KEEP = 8  # property graphs kept in memory per process


def _stamp(db: DB):
    latest = db.rows("SELECT analyzed_at FROM recording ORDER BY analyzed_at DESC LIMIT 1")
    return tuple(db.values("SELECT VALUE n FROM seq")), tuple(r.get("analyzed_at") for r in latest)


def projection(request: Request, db: DB, acl, scope: str, as_of: str | None = None) -> graph_model.Graph:
    """The caller's graph for a scope, cached per graph version (graph_history.head) until anything else in the
    archive changes; `as_of` is the graph at an earlier version (a number or a version's name)."""
    scope = "global" if scope in ("", "all", "global") else scope
    if not (scope == "global" or scope.startswith("ns:")):
        raise HTTPException(400, 'scope is "global" or "ns:<namespace>"')
    version = None
    if as_of not in (None, "", "head"):
        try:
            version = graph_history.resolve(db, as_of)
        except KeyError:
            raise HTTPException(404, f"no version is called {as_of}") from None
    readable = acl.readable()
    cache = request.app.state.graph_cache
    key = ("property", scope, None if readable is None else tuple(sorted(readable)), version, graph_history.head(db), _stamp(db))
    if key not in cache:
        try:
            g = graph_model.build(db, scope, readable, as_of=version)
        except KeyError:
            raise HTTPException(404, "not found") from None
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
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
def graph_schema(request: Request, user: CurrentUser, acl: Acl, db: Db, scope: str = SCOPE, as_of: AS_OF = None) -> dict[str, Any]:
    """What the graph holds in this scope: labels, relationship types (and what they join), properties and counts, with
    example queries. Agents read this before writing Cypher."""
    g = projection(request, db, acl, scope, as_of)
    return {**graph_model.schema(g), "examples": graph_ask.EXAMPLES, "query_language": "cypher (read-only subset; see docs/graph.md)"}


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
    as_of: AS_OF = None,
) -> dict[str, Any]:
    """Nodes related to one node, nearest first, with the relationships between them."""
    g = projection(request, db, acl, scope, as_of)
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
    as_of: AS_OF = None,
) -> dict[str, Any]:
    """Paths from a to b, shortest first: every simple path up to max_depth hops, or just the shortest ones."""
    g = projection(request, db, acl, scope, as_of)
    try:
        return graph_model.paths(g, a, b, max_depth, limit, _types(types), directed, shortest)
    except KeyError:
        raise HTTPException(404, "not in this graph") from None


class GraphQuery(BaseModel):
    query: str = Field(description="read-only Cypher, e.g. MATCH (e:Person)<-[:MENTIONS]-(r:Recording) RETURN e.name, count(r)")
    params: dict[str, Any] = Field(default_factory=dict, description="values for $parameters in the query")
    scope: str = "global"
    limit: int = Field(500, ge=1, le=5000, description="rows at most")
    as_of: str | None = Field(None, description="query the graph as of a version: a number or a version's name")


def run_query(request: Request, db: DB, acl, body: GraphQuery) -> dict[str, Any]:
    g = projection(request, db, acl, body.scope, body.as_of)
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


class GraphQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=1000, description="a question in plain language")
    scope: str = "global"
    limit: int = Field(200, ge=1, le=2000)
    as_of: str | None = Field(None, description="ask the graph as of a version: a number or a version's name")


@router.post("/graph/ask")
def ask_graph(request: Request, body: GraphQuestion, user: CurrentUser, acl: Acl, db: Db, cfg: Cfg) -> dict[str, Any]:
    """A question in plain language: the language model writes read-only Cypher, Lens runs it over the graph you can
    read, and you get the answer with the query that found it ({question, cypher, explanation, result})."""
    if not llm.configured(cfg):
        raise HTTPException(409, "no language model is set up; ask in Cypher instead, or set one up in Settings")
    g = projection(request, db, acl, body.scope, body.as_of)
    try:
        out = graph_ask.ask(cfg, g, body.question, graph_ask.EXAMPLES, max_rows=body.limit)
    except cypher.CypherError as e:
        raise HTTPException(422, f"couldn't answer that: {e}") from None
    except llm.LLMError as e:
        raise HTTPException(502, f"the language model failed: {e}") from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return {"scope": g.scope, "namespaces": g.namespaces, **out}


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
