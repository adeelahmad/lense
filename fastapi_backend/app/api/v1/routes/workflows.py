"""Workflows: graphs of nodes, drawn on a canvas, that a pipeline runs after its steps to make metadata."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.deps import Acl, AdminWriter, CurrentUser, Db, Writer, domain_errors
from app.domain import auth, jobs, workflows
from app.schemas.common import Created, Ok
from app.schemas.pipelines import JobQueued
from app.schemas.templates import VersionSaved
from app.schemas.workflows import (
    NodeType,
    Workflow,
    WorkflowCatalog,
    WorkflowCreate,
    WorkflowRunRequest,
    WorkflowUpdate,
    WorkflowVersionCreate,
)

router = APIRouter(prefix="/workflows", tags=["workflows"])


def _ports(t):
    if t in workflows.TERMINAL:
        return []
    return ["yes", "no"] if t == "condition" else ["out"]


@router.get("")
def list_workflows(user: CurrentUser, db: Db) -> WorkflowCatalog:
    """Saved workflows, plus the nodes a workflow can be built from."""
    return WorkflowCatalog(
        node_types=[
            NodeType(
                type=t,
                settings=sorted(workflows.CONFIG[t]),
                scopes=[sc for sc, types in workflows.SCOPES.items() if t in types],
                inputs=0 if t == "input" else -1 if t == "merge" else 1,
                outputs=_ports(t),
            )
            for t in workflows.NODE_TYPES
        ],
        operators=list(workflows.OPS),
        entity_types=list(workflows.ENTITY_TYPES),
        workflows=workflows.list_workflows(db),
    )


@router.post("")
def create_workflow(body: WorkflowCreate, user: AdminWriter, db: Db) -> Created:
    with domain_errors():
        wid = workflows.create(db, body.name, body.graph.model_dump(), body.description, user.email, body.scope)
    auth.audit(db, user.as_audit(), "workflow.create", f"workflow:{wid}")
    return Created(id=wid)


@router.get("/{wid}")
def get_workflow(wid: int, user: CurrentUser, db: Db, version: int | None = None) -> Workflow:
    """One version (default: the current one) and the list of versions."""
    try:
        w = workflows.get(db, wid, version)
    except KeyError:
        raise HTTPException(404, "not found") from None
    w["history"] = workflows.history(db, wid)
    return w


@router.patch("/{wid}")
def update_workflow(wid: int, body: WorkflowUpdate, user: AdminWriter, db: Db) -> Ok:
    """Its name and description (the graph changes by saving a version)."""
    with domain_errors():
        workflows.rename(db, wid, body.name, body.description)
    auth.audit(db, user.as_audit(), "workflow.update", f"workflow:{wid}")
    return Ok()


@router.post("/{wid}/versions")
def create_workflow_version(wid: int, body: WorkflowVersionCreate, user: AdminWriter, db: Db) -> VersionSaved:
    with domain_errors():
        n = workflows.save_version(db, wid, body.graph.model_dump(), body.notes, user.email, body.publish)
    auth.audit(db, user.as_audit(), "workflow.save", f"workflow:{wid}", {"version": n})
    return VersionSaved(version=n)


@router.post("/{wid}/run")
def run_workflow(wid: int, body: WorkflowRunRequest, user: Writer, acl: Acl, db: Db) -> JobQueued:
    """Run this workflow on one recording now, as a job (after anything its current job still has to do)."""
    acl.recording(body.recording, "editor")
    try:
        w = workflows.get(db, wid, body.version)
    except KeyError:
        raise HTTPException(400, "no such workflow") from None
    if w["scope"] != "recording":
        raise HTTPException(400, "this workflow organises the graph; a routine runs it")
    return JobQueued(
        job=jobs.add_steps(db, body.recording, [{"type": "workflow", "workflow": wid, "version": w["version"]}], by=user.email)
    )
