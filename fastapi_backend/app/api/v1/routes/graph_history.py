"""The entity graph's history (docs/graph-history.md): every change as a numbered version, with who made it, through
what and why; the graph as of any version; what changed between two versions; and names for versions. Every call
sees only the namespaces its caller can read (one with `namespace`), and an event only when all its namespaces are
readable. A version is a number, a version's name, or `head`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.api.deps import Acl, AdminReader, AdminWriter, CurrentUser, Db, Writer, domain_errors
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


class RollbackAsk(BaseModel):
    to: str = Field(description="the version to go back to: a number, a version's name")
    namespace: str | None = Field(None, description="only this namespace (default: every namespace you can edit)")
    dry_run: bool = Field(True, description="only say what would change (the default); false to roll back")


@router.post("/graph/rollback")
def graph_rollback(request: Request, body: RollbackAsk, user: Writer, acl: Acl, db: Db) -> dict[str, Any]:
    """Take the graph back to a version: every change since then in these namespaces is undone, newest first, as one
    new version (so it can be rolled back too). Merges come undone with their mentions and moved mentions go back;
    what analysis found since stays. Preview first (`dry_run`, the default). Needs editor access to every namespace the
    changes touched."""
    editable = None if user.admin else set(acl.editable())
    if editable is not None and not editable:
        raise HTTPException(403, "rolling back needs editor access to a namespace")
    if body.namespace:
        spaces: set[int] | None = {acl.namespace(body.namespace, "editor")}
    else:
        spaces = editable
    try:
        with domain_errors():
            out = graph_history.rollback(db, _version(db, body.to), spaces, editable, body.dry_run, user.email)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from None
    if out["done"]:
        auth.audit(db, user.as_audit(), "graph.rollback", f"graph_event:{out['version']}", {"to": out["to"], "namespace": body.namespace})
        request.app.state.graph_cache.clear()
    return out


@router.get("/graph/verify")
def graph_verify(user: AdminReader, db: Db) -> dict[str, Any]:
    """Replay the history from its newest checkpoint and compare it with today's graph: what differs was written
    without being recorded. Admins."""
    return graph_history.verify(db)


@router.post("/graph/verify")
def graph_verify_fix(request: Request, user: AdminWriter, db: Db) -> dict[str, Any]:
    """Record what differs from the replayed history as one change (`graph.drift`), so they match again. Admins."""
    out = graph_history.verify(db, fix=True)
    if out.get("version"):
        auth.audit(db, user.as_audit(), "graph.drift", f"graph_event:{out['version']}", {"differences": out["differences"]})
        request.app.state.graph_cache.clear()
    return out


@router.get("/graph/checkpoints")
def graph_checkpoints(user: AdminReader, db: Db) -> list[dict[str, Any]]:
    """The versions the graph is kept whole at, to replay from (taken with the first change, then every 1000)."""
    return graph_history.checkpoints(db)


@router.post("/graph/checkpoints")
def graph_checkpoint(user: AdminWriter, db: Db) -> dict[str, Any]:
    """Keep the whole graph as it is now. Admins."""
    v = graph_history.checkpoint(db)
    auth.audit(db, user.as_audit(), "graph.checkpoint", f"graph_checkpoint:{v}")
    return {"version": v}
