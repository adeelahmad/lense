"""Workflows: graphs of nodes, drawn on a canvas, that a pipeline runs after its steps to make metadata."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.deps import Acl, AdminWriter, Cfg, CurrentUser, Db, Writer, domain_errors
from app.api.v1.routes.custom_nodes import me
from app.domain import auth, custom_nodes, flow, jobs, store, workflows
from app.schemas.common import Created, Ok
from app.schemas.pipelines import JobQueued
from app.schemas.templates import VersionSaved
from app.schemas.workflows import (
    NodeType,
    Workflow,
    WorkflowCatalog,
    WorkflowCreate,
    WorkflowRunRequest,
    WorkflowTry,
    WorkflowTryRequest,
    WorkflowUpdate,
    WorkflowVersionCreate,
)

router = APIRouter(prefix="/workflows", tags=["workflows"])


DYNAMIC = {"switch": "outputs", "set": "inputs", "group": "both", "custom": "both"}


def _node_type(t):
    kit = next(k for k in workflows.KITS.values() if t in k.all_types)
    ins, outs, many = flow.ports({"type": t, "config": {}}, kit)
    return NodeType(
        type=t,
        settings=sorted(workflows.CONFIG[t]),
        scopes=[sc for sc, types in workflows.SCOPES.items() if t in types],
        inputs=0 if not ins else -1 if many else len(ins),
        outputs=outs,
        input_ports=ins,
        dynamic=DYNAMIC.get(t),
        primitive=t in flow.PRIMITIVES,
        keeps=t in workflows.TERMINAL,
    )


@router.get("")
def list_workflows(user: CurrentUser, db: Db) -> WorkflowCatalog:
    """Saved workflows, plus the nodes a workflow can be built from: the primitives, each scope's own nodes, and the
    custom nodes you can use."""
    return WorkflowCatalog(
        node_types=[_node_type(t) for t in workflows.NODE_TYPES],
        operators=list(workflows.OPS),
        entity_types=list(workflows.ENTITY_TYPES),
        workflows=workflows.list_workflows(db),
        custom_nodes=custom_nodes.visible(db, me(user)),
    )


@router.post("/test")
def try_workflow(body: WorkflowTryRequest, user: AdminWriter, acl: Acl, db: Db, cfg: Cfg) -> WorkflowTry:
    """Run a graph (saved or not) once without keeping anything: what each node passed on, for the canvas. Nodes
    that would save something say what they would save; models are still asked."""
    spaces = None
    if body.scope == "recording":
        if body.recording is None:
            raise HTTPException(400, "choose a recording to try it on")
        acl.recording(body.recording, "editor")
    else:
        spaces = [acl.namespace(n) for n in body.namespaces] if body.namespaces else sorted(store.space_names(db))
    with domain_errors():
        return WorkflowTry(**workflows.try_graph(db, cfg, body.graph.model_dump(), body.scope, body.recording, spaces, user.email))


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
