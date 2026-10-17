"""Specs from transcripts (app/domain/specs.py, docs/notes.md#specs-from-transcripts): write an asset's spec as a job,
and see what there is of it."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.deps import Acl, Cfg, CurrentUser, Db, Writer
from app.domain import auth, specs
from app.schemas.recordings import JobQueued
from app.schemas.specs import Spec, SpecWrite

router = APIRouter(prefix="/recordings", tags=["specs"])


@router.post("/{rid}/spec", status_code=202)
def write_spec(rid: int, acl: Acl, user: Writer, db: Db, cfg: Cfg, body: SpecWrite | None = None) -> JobQueued:
    """Write a technical spec from this asset's transcript as a note linked to it, in the background: every named
    decision, every no and every "not that, this", every constraint, in the speaker's own words with when they said
    them, and the gaps they left to decide later, flagged. It's written with the Spec from transcript prompt template
    (edited on the Templates page), or `template`; writing it again updates the same note, whose history keeps what it
    said before. It runs after what the asset's job still has to do. Needs the right to process the asset and a
    language model (400 without one, or with no text and none on the way). Audited as `spec.write`."""
    acl.recording(rid, "process")
    body = body or SpecWrite()
    try:
        jid = specs.queue(db, cfg, rid, by=user.email, template_id=body.template, version=body.version)
    except specs.Problem as e:
        raise HTTPException(400, str(e)) from None
    auth.audit(db, user.as_audit(), "spec.write", f"recording:{rid}", {"template": body.template})
    return JobQueued(job=jid)


@router.get("/{rid}/spec")
def get_spec(rid: int, acl: Acl, user: CurrentUser, db: Db) -> Spec:
    """What there is of this asset's spec: its note and what it holds when it's written, the job writing it, or why
    the last one failed; and the template it was (or would be) written with."""
    acl.recording(rid)
    return Spec.model_validate(specs.status(db, rid))
