"""The entity graph's history (docs/graph-history.md): every change as a numbered version, with who made it, through
what and why; the graph as of any version; what changed between two versions; and names for versions. Every call
sees only the namespaces its caller can read (one with `namespace`), and an event only when all its namespaces are
readable. A version is a number, a version's name, or `head`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import Acl, CurrentUser, Db, Writer, domain_errors
from app.domain import auth, graph_history

router = APIRouter(tags=["graph"])
NAMESPACE = Query(None, description="one namespace (default: every namespace you can read)")


def _spaces(acl, namespace: str | None) -> set[int] | None:
    if namespace:
        return {acl.namespace(namespace)}
    return acl.readable()


def _version(db, ref: str | None) -> int:
    try:
        return graph_history.resolve(db, ref)
    except KeyError:
        raise HTTPException(404, f"no version is called {ref}") from None


@router.get("/graph/history")
def graph_history_list(
    user: CurrentUser,
    acl: Acl,
    db: Db,
    namespace: str | None = NAMESPACE,
    entity: int | None = Query(None, description="only the changes that touched this entity"),
    before: int | None = Query(None, ge=1, description="older than this version (the next page)"),
    limit: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    """The graph's versions, newest first: what changed, who changed it, through what (web, token, oauth, assistant,
    mcp, routine, workflow, analysis, cli, system) and why, with the entities each touched."""
    spaces = _spaces(acl, namespace)
    rows = graph_history.versions(db, spaces, entity, before, limit)
    return {"head": graph_history.head(db), "versions": rows, "next": rows[-1]["version"] if len(rows) == limit else None}


@router.get("/graph/history/{version}")
def graph_history_event(version: str, user: CurrentUser, acl: Acl, db: Db) -> dict[str, Any]:
    """One version in full: every record it changed, as it was before and after."""
    try:
        return graph_history.event(db, _version(db, version), acl.readable())
    except KeyError:
        raise HTTPException(404, "not found") from None


@router.get("/graph/as-of/{version}")
def graph_as_of(version: str, user: CurrentUser, acl: Acl, db: Db, namespace: str | None = NAMESPACE) -> dict[str, Any]:
    """The graph as it was at a version: entities (with their other names), cross-namespace links and the pairs someone
    said are different. Mentions aren't versioned: counts elsewhere are today's."""
    with domain_errors():
        return graph_history.as_of(db, _version(db, version), _spaces(acl, namespace))


@router.get("/graph/diff")
def graph_diff(
    user: CurrentUser,
    acl: Acl,
    db: Db,
    from_: str = Query(..., alias="from", description="a version, a version's name, or head"),
    to: str = Query("head", description="a version, a version's name, or head"),
    namespace: str | None = NAMESPACE,
) -> dict[str, Any]:
    """What changed between two versions: entities added, removed and changed (field by field), and other names,
    links and distinct pairs added and removed."""
    with domain_errors():
        return graph_history.diff(db, _version(db, from_), _version(db, to), _spaces(acl, namespace))


class TagAsk(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    version: str | None = Field(None, description="a version or a version's name (default: head)")
    note: str | None = Field(None, max_length=300)


@router.get("/graph/tags")
def graph_tags(user: CurrentUser, db: Db) -> list[dict[str, Any]]:
    """Named versions, newest first."""
    return graph_history.tags(db)


@router.post("/graph/tags")
def graph_tag(body: TagAsk, user: Writer, acl: Acl, db: Db) -> dict[str, Any]:
    """Name a version ("before the cleanup") to come back to it; naming again moves the name. Editors of a namespace
    (or admins) can."""
    if not (user.admin or acl.editable()):
        raise HTTPException(403, "naming versions needs editor access to a namespace")
    with domain_errors():
        out = graph_history.tag(db, body.name, _version(db, body.version), body.note, user.email)
    auth.audit(db, user.as_audit(), "graph.tag", f"graph_tag:{out['name']}", out)
    return out


@router.delete("/graph/tags/{name}")
def graph_untag(name: str, user: Writer, acl: Acl, db: Db) -> dict[str, Any]:
    if not (user.admin or acl.editable()):
        raise HTTPException(403, "naming versions needs editor access to a namespace")
    try:
        graph_history.untag(db, name)
    except KeyError:
        raise HTTPException(404, "not found") from None
    auth.audit(db, user.as_audit(), "graph.untag", f"graph_tag:{name}")
    return {"ok": True}
