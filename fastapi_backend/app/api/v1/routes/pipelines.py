"""Pipelines: the steps run on a recording, versioned. A namespace can make one its default."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.deps import Acl, AdminWriter, CurrentUser, Db, Writer, domain_errors
from app.domain import auth, jobs, pipelines
from app.schemas.common import Created
from app.schemas.pipelines import (
    JobQueued,
    Pipeline,
    PipelineCatalog,
    PipelineCreate,
    PipelineRunRequest,
    PipelineVersionCreate,
)
from app.schemas.templates import VersionSaved

router = APIRouter(prefix="/pipelines", tags=["pipelines"])


@router.get("")
def list_pipelines(user: CurrentUser, db: Db) -> PipelineCatalog:
    """Saved pipelines, plus what a pipeline can be built from."""
    return PipelineCatalog(
        standard=pipelines.STANDARD,
        step_types=sorted(pipelines.TYPES),
        conditions=list(pipelines.WHEN),
        pipelines=pipelines.list_pipelines(db),
    )


@router.post("")
def create_pipeline(body: PipelineCreate, user: AdminWriter, db: Db) -> Created:
    with domain_errors():
        pid = pipelines.create(db, body.name, body.steps, body.description, user.email)
    auth.audit(db, user.as_audit(), "pipeline.create", f"pipeline:{pid}")
    return Created(id=pid)


@router.get("/{pid}")
def get_pipeline(pid: int, user: CurrentUser, db: Db, version: int | None = None) -> Pipeline:
    """One version (default: the current one) and the list of versions."""
    try:
        p = pipelines.get(db, pid, version)
    except KeyError:
        raise HTTPException(404, "not found") from None
    p["history"] = db.rows(
        "SELECT version, notes, created_at, created_by FROM pipeline_version WHERE pipeline = $p ORDER BY version DESC", p=pid
    )
    return p


@router.post("/{pid}/versions")
def create_pipeline_version(pid: int, body: PipelineVersionCreate, user: AdminWriter, db: Db) -> VersionSaved:
    with domain_errors():
        n = pipelines.save_version(db, pid, body.steps, body.notes, user.email, body.publish)
    auth.audit(db, user.as_audit(), "pipeline.save", f"pipeline:{pid}", {"version": n})
    return VersionSaved(version=n)


@router.post("/{pid}/run")
def run_pipeline(pid: int, body: PipelineRunRequest, user: Writer, acl: Acl, db: Db) -> JobQueued:
    """Run this pipeline on one recording now (its outputs are kept)."""
    acl.recording(body.recording, "editor")
    try:
        return JobQueued(job=jobs.enqueue(db, body.recording, None, by=user.email, pipeline=pid))
    except KeyError:
        raise HTTPException(400, "no such pipeline") from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
