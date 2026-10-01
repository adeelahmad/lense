"""Conversations with the archive, the assistant's approvals, and checking an answer against its sources.

A question streams back server-sent events: ``step`` (a tool the assistant used), ``approval`` (work it proposed that
waits for you), ``notice``, ``passages`` (the numbered excerpts), ``token`` (answer text), ``error`` and ``done``.
The assistant only ever sees, and cites, what the asker can read.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from app.api.deps import Access, Acl, Cfg, CurrentUser, Db, Principal, Writer
from app.api.media import sign_urls
from app.domain import ai_tools, auth, chat, llm, store
from app.domain.store import DB
from app.schemas.chats import (
    AnswerCheck,
    Approval,
    ApprovalDecision,
    ApprovalOutcome,
    Chat,
    ChatCapabilities,
    ChatCreate,
    ChatScope,
    ChatSummary,
    ChatUpdate,
    MessageCreate,
    StopResult,
)
from app.schemas.common import Created, Ok

router = APIRouter(tags=["chats"])
R = store.R
SSE_HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}


def _scope(acl: Access, db: DB, scope: ChatScope | None) -> dict[str, Any]:
    """The scope with only the keys that narrow it; namespaces and recordings must be ones the caller can read."""
    s = scope.model_dump(by_alias=True, exclude_none=True) if scope else {}
    names = {n for sid, n in store.space_names(db).items() if sid in acl.roles}
    for n in s.get("namespaces") or []:
        if n not in names:
            raise HTTPException(404, f"no namespace {n!r}")
    for rid in s.get("recordings") or []:
        acl.recording(int(rid))
    return {k: v for k, v in s.items() if v}


def _own_chat(db: DB, cid: int, user: Principal) -> dict[str, Any]:
    try:
        return chat.get(db, cid, user.id)
    except KeyError:
        raise HTTPException(404, "not found") from None


@router.get("/chats")
def list_chats(user: CurrentUser, db: Db) -> list[ChatSummary]:
    """Your conversations, most recent first."""
    return db.rows(
        "SELECT record::id(id) AS id, title, scope, model, created_at, updated_at FROM chat WHERE account = $a "
        "ORDER BY updated_at DESC LIMIT 200",
        a=user.id,
    )


@router.get("/chats/capabilities")
def chat_capabilities(user: CurrentUser, cfg: Cfg) -> ChatCapabilities:
    """What the assistant can do, for anyone signed in: whether a language model is set up and which, whether it uses
    tools (and at most how many steps), and whether answers can be checked against their sources. Not the model
    server's address or key."""
    on = llm.configured(cfg)
    return ChatCapabilities(
        configured=on,
        model=cfg["llm"].get("model") if on else None,
        tools=on and bool(cfg["ai"].get("tools")),
        max_steps=int(cfg["ai"].get("max_steps") or 6),
        check=on,
        models=chat.model_choices(cfg),
    )


def _model(cfg: dict[str, Any], model: str | None) -> str | None:
    try:
        return chat.check_model(cfg, model)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.post("/chats")
def create_chat(user: Writer, acl: Acl, db: Db, cfg: Cfg, body: ChatCreate | None = None) -> Created:
    body = body or ChatCreate()
    return Created(id=chat.create(db, user.id, body.title, _scope(acl, db, body.scope), _model(cfg, body.model)))


@router.get("/chats/{cid}")
def get_chat(cid: int, user: CurrentUser, acl: Acl, db: Db) -> Chat:
    """A conversation with its messages. Citations follow your current access: ones you can no longer read are left out."""
    c = _own_chat(db, cid, user)
    msgs, ok = chat.history(db, cid), set(acl.roles)
    ids = {p["recording_id"] for m in msgs for p in m.get("passages") or []}
    visible = (
        {
            r["id"]
            for r in db.rows("SELECT record::id(id) AS id, space FROM recording WHERE id IN $ids", ids=[R("recording", i) for i in ids])
            if r["space"] in ok
        }
        if ids
        else set()
    )
    for m in msgs:
        m["passages"] = [p for p in m.get("passages") or [] if p["recording_id"] in visible]
    return Chat.model_validate(sign_urls({**c, "messages": msgs}))


@router.patch("/chats/{cid}")
def update_chat(cid: int, body: ChatUpdate, user: Writer, acl: Acl, db: Db, cfg: Cfg) -> Ok:
    """Rename a conversation, change what it draws on, or the model that answers in it (null: the configured one)."""
    _own_chat(db, cid, user)
    if "model" in body.model_fields_set:
        db.q("UPDATE $r SET model = $m", r=R("chat", cid), m=_model(cfg, body.model))
    if body.title:
        db.q("UPDATE $r SET title = $t", r=R("chat", cid), t=body.title[:120])
    if "scope" in body.model_fields_set:  # replaces the scope as a whole, so narrowing can also be removed
        db.q("UPDATE $r SET scope = $s", r=R("chat", cid), s=_scope(acl, db, body.scope))
    return Ok()


@router.delete("/chats/{cid}")
def delete_chat(cid: int, user: Writer, db: Db) -> Ok:
    _own_chat(db, cid, user)
    db.run(["DELETE chat_message WHERE chat = $c", "DELETE $r"], c=cid, r=R("chat", cid))
    return Ok()


def _ev(name: str, data: Any) -> str:
    return f"event: {name}\ndata: {json.dumps(sign_urls(data), default=str)}\n\n"


@router.post(
    "/chats/{cid}/messages",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}, "description": "server-sent events"}},
)
async def send_message(cid: int, body: MessageCreate, user: Writer, acl: Acl, db: Db, cfg: Cfg) -> StreamingResponse:
    """Ask a question. Streams events: step, approval, notice, passages, token (answer text), error, stopped (POST
    /chats/{cid}/stop: what came before is saved, marked stopped), done (the saved message id)."""
    c = await run_in_threadpool(_own_chat, db, cid, user)
    q = body.content.strip()[:4000]
    if not q:
        raise HTTPException(400, "ask something")
    # this answer's model: the one asked for (400 if it isn't offered), else the conversation's while it's still offered
    model = await run_in_threadpool(_model, cfg, body.model) if body.model else c.get("model")
    if model and not body.model and model not in await run_in_threadpool(chat.model_choices, cfg):
        model = None
    readable = set(acl.roles)

    def prepare() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        past = chat.history(db, cid)
        chat.add(db, cid, "user", q)
        if not past and c["title"] == "New conversation":
            db.q("UPDATE $r SET title = $t", r=R("chat", cid), t=q[:80])
        return past, chat.retrieve(db, q, readable, c.get("scope"))

    past, passages = await run_in_threadpool(prepare)

    steps: list[dict[str, Any]] = []
    notice: str | None = None

    def save(text: str, cited: list[dict[str, Any]], **kw: Any) -> int:
        wrote = (model or cfg["llm"].get("model")) if llm.configured(cfg) else None
        return int(chat.add(db, cid, "assistant", text, cited, steps=steps, notice=notice, model=wrote, **kw))

    def stopped(text: str, cited: list[dict[str, Any]]) -> Iterator[str]:
        yield _ev("stopped", {"message": "Stopped"})
        yield _ev("done", {"message": save(text or "(stopped)", cited, stopped=True)})

    def answer(on: chat.Answering) -> Iterator[str]:
        nonlocal notice
        if llm.configured(cfg) and cfg["ai"].get("tools"):
            box = ai_tools.Toolbox(db, cfg, user.as_audit(), readable, set(acl.editable()), c.get("scope"), cid)
            try:
                answer = ""
                for kind, data in chat.tool_answer(cfg, box, q, past, cfg["ai"].get("max_steps") or 6, model):
                    if kind == "step":
                        steps.append(data)
                        yield _ev("step", data)
                        if on.stop_requested(force=True):  # between steps: the model is called again after each
                            for appr in box.approvals:
                                yield _ev("approval", appr)
                            yield from stopped("", [])
                            return
                    else:
                        answer = data
                for appr in box.approvals:
                    yield _ev("approval", appr)
                cited = box.cited(answer)
                yield _ev("passages", cited)
                yield _ev("token", {"text": answer})
                yield _ev("done", {"message": save(answer or "(no answer)", cited)})
                return
            except llm.ToolsUnsupported:
                notice = "This model can't use tools, so the answer comes from a search instead."
                yield _ev("notice", {"message": notice})
            except llm.LLMError as e:
                yield _ev("error", {"message": str(e)})
                yield _ev("done", {"message": save("(no answer)", [], error=str(e))})
                return
        yield _ev("passages", passages)
        text, error = "", None
        try:
            if llm.configured(cfg):
                for piece in llm.stream_chat(cfg, chat.messages_for(q, passages, past), model=model):
                    text += piece
                    yield _ev("token", {"text": piece})
                    if on.stop_requested():
                        yield from stopped(text, chat.cited(text, passages))
                        return
            else:
                text = chat.fallback(passages)
                yield _ev("token", {"text": text})
        except llm.LLMError as e:
            error = str(e)
            yield _ev("error", {"message": error})
        yield _ev("done", {"message": save(text or "(no answer)", chat.cited(text, passages), error=error)})

    def gen() -> Iterator[str]:
        on = chat.Answering(db, cid)
        try:
            yield from answer(on)
        finally:
            on.end()

    # A sync generator: Starlette iterates it in the threadpool, so the domain calls inside don't block the loop.
    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/chats/{cid}/stop")
def stop_answer(cid: int, user: Writer, db: Db) -> StopResult:
    """Stop the answer being written in a conversation: it ends after the piece or tool step it's on, keeps what came
    before (saved, marked stopped) and streams `stopped`, then `done`. Works whichever server process is answering."""
    _own_chat(db, cid, user)
    return StopResult(stopping=chat.request_stop(db, cid))


@router.post("/chats/{cid}/messages/{mid}/check")
def check_message(cid: int, mid: int, user: Writer, db: Db, cfg: Cfg) -> AnswerCheck:
    """Re-check each cited claim of an answer against the excerpts it cites; the verdict is saved on the message."""
    _own_chat(db, cid, user)
    m = db.one("SELECT chat, role, content, passages FROM $r", r=R("chat_message", mid))
    if not m or m["chat"] != cid or m["role"] != "assistant":
        raise HTTPException(404, "not found")
    try:
        out = chat.check_sources(cfg, m["content"], m.get("passages") or [])
    except llm.LLMError as e:
        raise HTTPException(400, str(e)) from None
    db.q("UPDATE $r SET check = $c", r=R("chat_message", mid), c=out)
    return out


@router.get("/approvals")
def list_approvals(user: CurrentUser, db: Db, chat_id: int | None = None) -> list[Approval]:
    """Work the assistant proposed for you to approve (and what became of it), newest first."""
    return db.rows(
        "SELECT record::id(id) AS id, chat, tool, summary, estimate, status, created_at, result FROM approval WHERE account = $a"
        + (" AND chat = $c" if chat_id else "")
        + " ORDER BY created_at DESC LIMIT 100",
        a=user.id,
        c=chat_id,
    )


@router.post("/approvals/{aid}")
def decide_approval(aid: int, body: ApprovalDecision, user: Writer, acl: Acl, db: Db, cfg: Cfg) -> ApprovalOutcome:
    """Approve (or run a sample of, or decline) something the assistant proposed. Each approval is decided once."""
    a = db.one("SELECT account FROM $r", r=R("approval", aid))
    if not a or a["account"] != user.id:
        raise HTTPException(404, "not found")
    try:
        out = ai_tools.approve(db, cfg, aid, user.as_audit(), set(acl.editable()), body.decision)
    except (ValueError, PermissionError) as e:
        raise HTTPException(400, str(e)) from None
    auth.audit(db, user.as_audit(), "assistant.approval", f"approval:{aid}", {"decision": body.decision, **out})
    return out
