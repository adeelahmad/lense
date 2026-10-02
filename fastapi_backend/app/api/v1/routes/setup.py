"""The first-run setup wizard (admins): the first namespace, the model provider and storage, then finishing it.

The first admin is created before this, with the setup code (POST /auth/setup) or from the environment. Fields the
environment sets are left out of every save and reported as locked (domain/setup.py).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.api.deps import AdminReader, AdminWriter, Db
from app.domain import auth, setup
from app.schemas.setup import SetupFinish, SetupLlm, SetupNamespace, SetupSaved, SetupStorage, SetupView

router = APIRouter(prefix="/setup", tags=["setup"])


@router.get("")
def get_setup(user: AdminReader, request: Request, db: Db) -> SetupView:
    """What is set already, and which fields .env or archive.yaml locks."""
    return SetupView.model_validate(setup.view(db, request.app.state.archive.base))


@router.post("/namespace")
def save_namespace(body: SetupNamespace, user: AdminWriter, request: Request, db: Db) -> SetupSaved:
    """Create the first namespace (or set the graph mode of one that exists)."""
    if setup.view(db, request.app.state.archive.base)["namespace"]["locked"]:
        raise HTTPException(409, "the namespaces are set in archive.yaml or LENS_NAMESPACE")
    try:
        setup.save_namespace(db, body.name, body.graph)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    auth.audit(db, user.as_audit(), "namespace.create", body.name.strip().lower(), ["setup"])
    request.app.state.graph_cache.clear()
    return SetupSaved(saved=["namespace"])


@router.put("/llm")
def save_llm(body: SetupLlm, user: AdminWriter, request: Request, db: Db) -> SetupSaved:
    """The model provider: an OpenAI-compatible server's address, the model, and its key (empty: unchanged)."""
    values: dict = {"base_url": (body.base_url or "").strip() or None, "model": (body.model or "").strip() or None}
    if body.api_key:
        values["api_key"] = body.api_key
    try:
        saved = setup.save_llm(db, request.app.state.archive.base, values, user.email)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    if saved:
        auth.audit(db, user.as_audit(), "settings.save", "llm", saved)
    return SetupSaved(saved=saved)


@router.put("/storage")
def save_storage(body: SetupStorage, user: AdminWriter, request: Request, db: Db) -> SetupSaved:
    """The upload limit, and optionally a folder on this machine (inside sources.local_roots) to watch."""
    try:
        wid = setup.save_storage(db, request.app.state.archive.base, body.max_upload_mb, body.folder, body.namespace, user.email)
    except KeyError:
        raise HTTPException(400, "no such namespace") from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    if body.max_upload_mb is not None:
        auth.audit(db, user.as_audit(), "settings.save", "uploads", ["max_mb"])
    if wid is not None:
        auth.audit(db, user.as_audit(), "watch.create", f"watch_path:{wid}")
    return SetupSaved(saved=[k for k in ("max_upload_mb", "folder") if getattr(body, k) is not None], watch=wid)


@router.post("/finish")
def finish(body: SetupFinish, user: AdminWriter, db: Db) -> SetupSaved:
    """Finish (or skip) the wizard; the web app stops showing it. Everything stays changeable in Settings."""
    setup.finish(db, user.email, body.skipped)
    auth.audit(db, user.as_audit(), "setup.finish", None, ["skipped"] if body.skipped else None)
    return SetupSaved()
