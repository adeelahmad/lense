"""Classify an asset or a text with the decision model (domain/classify.py)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.deps import Acl, Cfg, CurrentUser, Db, domain_errors
from app.domain import auth, decide
from app.domain import classify as classifymod
from app.schemas.classify import ClassifyRequest, ClassifyResult

router = APIRouter(tags=["search"])


@router.post("/classify")
def classify(body: ClassifyRequest, acl: Acl, user: CurrentUser, db: Db, cfg: Cfg) -> ClassifyResult:
    """Which label fits an asset (or a text) best, how sure that is, and every label ranked: the labels given, Lens's
    content types (of the asset's kind) or the namespace's topics. The decision model answers when it's set up
    (Settings → AI assistant), else the language model; the call is in the activity ledger. With `apply` (editors), a
    sure answer is acted on: the content type is set, the topic is suggested for someone to accept, or the label is
    added as a tag; one that isn't sure changes nothing. 409 when no model is set up to answer."""
    space = None
    if body.recording is not None:
        acl.recording(body.recording, "edit" if body.apply else "viewer")
    elif body.namespace:
        space = acl.namespace(body.namespace)
    with domain_errors():
        try:
            res = classifymod.classify(
                db,
                cfg,
                rid=body.recording,
                text=body.text,
                labels=body.labels,
                vocab=body.vocabulary,
                question=body.question,
                apply=body.apply,
                user=user.email,
                space=space,
            )
        except decide.Undecided as e:
            raise HTTPException(409, f"nothing could answer: {e}") from None
    if res["applied"]:
        auth.audit(
            db, user.as_audit(), "recording.classify", f"recording:{body.recording}", {"applied": res["applied"], "choice": res["choice"]}
        )
    return ClassifyResult.model_validate(res)
