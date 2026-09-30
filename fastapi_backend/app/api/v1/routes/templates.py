"""Templates: Jinja text rendered against a recording as an LLM prompt, a report page or an export file. Versioned."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from app.api.deps import Acl, AdminWriter, Cfg, CurrentUser, Db, Writer
from app.domain import auth, llm, templates
from app.schemas.common import Created
from app.schemas.templates import (
    Template,
    TemplateCreate,
    TemplatePreview,
    TemplatePreviewRequest,
    TemplateSummary,
    TemplateVersionCreate,
    VersionSaved,
)

router = APIRouter(prefix="/templates", tags=["templates"])


@router.get("")
def list_templates(user: CurrentUser, db: Db) -> list[TemplateSummary]:
    return templates.list_templates(db)


@router.post("")
def create_template(body: TemplateCreate, user: AdminWriter, db: Db) -> Created:
    try:
        tid = templates.create(db, body.name, body.kind, body.body, body.json_schema, body.system, body.description, user.email)
    except templates.TemplateProblem as e:
        raise HTTPException(400, str(e)) from None
    auth.audit(db, user.as_audit(), "template.create", f"template:{tid}")
    return Created(id=tid)


@router.post("/preview")
def preview_template(body: TemplatePreviewRequest, user: Writer, acl: Acl, db: Db, cfg: Cfg) -> TemplatePreview:
    """Render a template (saved, or the unsaved text in the editor) against a recording; run=true also calls the model.

    A template that fails to render answers ok=false with the error, so the editor can show it inline.
    """
    rid = body.recording
    acl.recording(rid)
    kind, text, schema, system = body.kind or "export", body.body, body.json_schema, body.system
    if body.template and text is None:
        try:
            t = templates.get(db, body.template, body.version)
        except KeyError:
            raise HTTPException(404, "not found") from None
        kind, text, schema, system = t["kind"], t["body"], t.get("schema"), t.get("system")
    try:
        out = templates.render_body(text or "", templates.context(db, cfg, rid), kind, body.filename)
    except templates.TemplateProblem as e:
        return TemplatePreview(ok=False, error=str(e))
    res = TemplatePreview(ok=True, kind=kind, rendered=out)
    if body.run and kind == "prompt":
        try:
            res.result = llm.json_out(cfg, system or templates.DEFAULT_SYSTEM, out, schema or {"type": "object"})
        except llm.LLMError as e:
            res.ok, res.error = False, str(e)
    return res


@router.get("/{tid}")
def get_template(tid: int, user: CurrentUser, db: Db, version: int | None = None) -> Template:
    """One version (default: the current one) and the list of versions."""
    try:
        return Template.model_validate({**templates.get(db, tid, version), "history": templates.versions(db, tid)})
    except KeyError:
        raise HTTPException(404, "not found") from None


@router.post("/{tid}/versions")
def create_template_version(tid: int, body: TemplateVersionCreate, user: AdminWriter, db: Db) -> VersionSaved:
    try:
        n = templates.save_version(db, tid, body.body, body.json_schema, body.system, body.notes, user.email, body.publish)
    except KeyError:
        raise HTTPException(404, "not found") from None
    except templates.TemplateProblem as e:
        raise HTTPException(400, str(e)) from None
    auth.audit(db, user.as_audit(), "template.save", f"template:{tid}", {"version": n})
    return VersionSaved(version=n)


@router.get("/{tid}/diff", response_class=PlainTextResponse)
def diff_template_versions(tid: int, a: int, b: int, user: CurrentUser, db: Db) -> PlainTextResponse:
    """A unified diff between two versions."""
    try:
        return PlainTextResponse(templates.diff(db, tid, a, b))
    except KeyError:
        raise HTTPException(404, "not found") from None
