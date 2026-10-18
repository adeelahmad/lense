"""Email replies (app/domain/responder.py, docs/email-replies.md): draft a cited reply to an email as a job, read it,
edit it or set it aside, and list the mail that waits for an answer. Nothing here sends mail."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import Acl, Cfg, CurrentUser, Db, Writer
from app.domain import auth, responder
from app.schemas.recordings import JobQueued
from app.schemas.replies import Reply, ReplyDraft, ReplyEdit, ReplyEmail, ReplyList

router = APIRouter(tags=["replies"])


@router.get("/replies")
def list_replies(
    acl: Acl,
    user: CurrentUser,
    db: Db,
    show: Literal["waiting", "draft", "drafting", "skipped", "dismissed", "sent", "failed", "none", "all"] = "waiting",
    namespace: str | None = None,
    limit: int = Query(50, ge=1, le=200),
) -> ReplyList:
    """Email in the namespaces you can read (or `namespace`), newest first, each with its reply. `waiting` (the default)
    is mail that may need an answer (not a mailing list's, a no-reply sender's or your own) with no reply yet, and every
    draft; or one status, `none` (no reply yet, automatic mail too) or `all`."""
    spaces = [acl.namespace(namespace)] if namespace else acl.spaces()
    rows = responder.listing(db, spaces, show, limit)
    return ReplyList(emails=[ReplyEmail.model_validate(r) for r in rows], drafts=responder.count(db, spaces))


@router.post("/recordings/{rid}/reply", status_code=202)
def draft_reply(rid: int, acl: Acl, user: Writer, db: Db, cfg: Cfg, body: ReplyDraft | None = None) -> JobQueued:
    """Draft a reply to this email in the background, from what its namespace holds, each fact cited to its excerpt
    and what the archive doesn't say marked [to confirm: …]; `instructions` says what it should say. It's drafted with
    the Reply to an email prompt template (edited on the Templates page), or `template`, and drafting again keeps the
    draft before as `previous`. Nothing is sent. Needs the right to process the email and a language model (400 without
    one, or when it isn't an email). Audited as `reply.draft`."""
    acl.recording(rid, "process")
    body = body or ReplyDraft()
    try:
        jid = responder.queue(db, cfg, rid, by=user.email, instructions=body.instructions, template_id=body.template, version=body.version)
    except responder.Problem as e:
        raise HTTPException(400, str(e)) from None
    auth.audit(db, user.as_audit(), "reply.draft", f"recording:{rid}", {"template": body.template})
    return JobQueued(job=jid)


@router.get("/recordings/{rid}/reply")
def get_reply(rid: int, acl: Acl, user: CurrentUser, db: Db) -> Reply:
    """This email's reply: its status, the draft with its sources, and what's left to confirm."""
    acl.recording(rid)
    return Reply.model_validate(responder.status(db, rid))


@router.patch("/recordings/{rid}/reply")
def edit_reply(rid: int, body: ReplyEdit, acl: Acl, user: Writer, db: Db) -> Reply:
    """Change a draft: its text, who it goes to, its subject; or set it aside (`status` dismissed) and bring it back
    (`status` draft), which undoes that. Needs the right to process the email. Audited as `reply.edit`."""
    acl.recording(rid, "process")
    try:
        out = responder.edit(db, rid, body.text, body.to, body.cc, body.subject, body.status)
    except responder.Problem as e:
        raise HTTPException(400, str(e)) from None
    auth.audit(db, user.as_audit(), "reply.edit", f"recording:{rid}", {k: True for k, v in body.model_dump().items() if v is not None})
    return Reply.model_validate(out)
