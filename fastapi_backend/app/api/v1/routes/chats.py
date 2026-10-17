"""Conversations with the archive, the assistant's approvals, and checking an answer against its sources.

A question streams back server-sent events: ``branch`` (a new version: regenerated, edited, or beside others),
``scoped`` (the namespace a conversation over everything was narrowed
to) or ``suggested`` (namespaces to offer when it isn't clear, new ones included), ``step`` (a tool the assistant used), ``approval`` (work it proposed that waits for you), ``notice``, ``passages`` (the numbered excerpts), ``token`` (answer text), ``error`` and ``done``.
The assistant only ever sees, and cites, what the asker can read.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from app.api.deps import Access, Acl, Cfg, CurrentUser, Db, Principal, Writer
from app.api.media import sign_urls
from app.api.v1.routes.collections import own_collection
from app.domain import ai_tools, approvals, auth, auto_scope, browser, chat, llm, sandbox, store, uploads
from app.domain.store import DB
from app.schemas.chats import (
    AnswerCheck,
    Approval,
    ApprovalCount,
    ApprovalDecision,
    ApprovalOutcome,
    Chat,
    ChatCapabilities,
    ChatCreate,
    ChatScope,
    ChatSummary,
    ChatUpdate,
    HeadUpdate,
    MessageCreate,
    StopResult,
)
from app.schemas.common import Created, Ok

router = APIRouter(tags=["chats"])
R = store.R
log = logging.getLogger("lens")
SSE_HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}


def _scope(acl: Access, db: DB, scope: ChatScope | None) -> dict[str, Any]:
    """The scope with only the keys that narrow it; namespaces and recordings must be ones the caller can read, and
    collections theirs or shared."""
    s = scope.model_dump(by_alias=True, exclude_none=True) if scope else {}
    names = {n for sid, n in store.space_names(db).items() if sid in acl.roles}
    for n in s.get("namespaces") or []:
        if n not in names:
            raise HTTPException(404, f"no namespace {n!r}")
    for rid in s.get("recordings") or []:
        acl.recording(int(rid))
    for cid in s.get("collections") or []:
        if acl.user is None:
            raise HTTPException(401, "sign in first")
        own_collection(db, int(cid), acl.user)
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
        "SELECT record::id(id) AS id, title, scope, model, kind ?? 'chat' AS kind, created_at, updated_at FROM chat WHERE account = $a "
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
    if body.kind == "setup" and not user.admin:
        raise HTTPException(403, "only admins set up the server")
    return Created(id=chat.create(db, user.id, body.title, _scope(acl, db, body.scope), _model(cfg, body.model), body.kind))


@router.get("/chats/{cid}")
def get_chat(cid: int, user: CurrentUser, acl: Acl, db: Db) -> Chat:
    """A conversation with the version showing (`messages`, each with its `versions`) and every version's messages
    (`tree`). Citations follow your current access: ones you can no longer read are left out."""
    return _chat_view(db, acl, _own_chat(db, cid, user))


@router.put("/chats/{cid}/head")
def switch_version(cid: int, body: HeadUpdate, user: Writer, acl: Acl, db: Db) -> Chat:
    """Show another version: the one `message` is in, from the first message to the newest one below `message`. The
    next question goes on from there. Returns the conversation as GET /chats/{cid} does."""
    c = _own_chat(db, cid, user)
    try:
        c["head"] = chat.switch(db, cid, body.message)
    except KeyError:
        raise HTTPException(404, "no such message in this conversation") from None
    return _chat_view(db, acl, c)


def _chat_view(db: DB, acl: Access, c: dict[str, Any]) -> Chat:
    nodes = chat.tree(db, c["id"])
    msgs, ok, versions = chat.path(db, c["id"], c.get("head"), nodes), set(acl.roles), chat.versions(nodes)
    for m in msgs:
        m["versions"] = versions.get(m["id"])
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
    return Chat.model_validate(sign_urls({**c, "messages": msgs, "tree": nodes}, full=True))


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
def delete_chat(cid: int, user: Writer, db: Db, cfg: Cfg) -> Ok:
    """Delete a conversation, with its code sandbox and the files in it."""
    _own_chat(db, cid, user)
    db.run(["DELETE chat_message WHERE chat = $c", "DELETE $r"], c=cid, r=R("chat", cid))
    sandbox.forget(db, cfg, cid)
    return Ok()


def _attachments(db: DB, ids: list[str], user: Principal) -> list[dict[str, Any]]:
    """The files attached to a message: the sender's own uploads, all here and held for the assistant to place."""
    out = []
    for uid in dict.fromkeys(ids):
        try:
            up = uploads.get(db, uid)
        except KeyError:
            raise HTTPException(404, f"no upload {uid}") from None
        if up.get("account") != user.id:
            raise HTTPException(404, f"no upload {uid}")
        if up.get("state") not in ("held", "done"):
            raise HTTPException(409, f"{up['filename']} hasn't finished uploading")
        out.append(store.clean({"id": uid, "filename": up["filename"], "size": up["size"], "recording": up.get("recording")}))
    return out


def _ev(name: str, data: Any) -> str:
    return f"event: {name}\ndata: {json.dumps(sign_urls(data, full=True), default=str)}\n\n"


@router.post(
    "/chats/{cid}/messages",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}, "description": "server-sent events"}},
)
async def send_message(cid: int, body: MessageCreate, user: Writer, acl: Acl, db: Db, cfg: Cfg, request: Request) -> StreamingResponse:
    """Ask a question, optionally from a page (`context`: the page, its text and any highlighted part, which the model
    reads with the question). Streams events: step, approval, notice, passages, token (answer text), error, stopped (POST
    /chats/{cid}/stop: what came before is saved, marked stopped), done (the saved message id).

    A conversation is a tree of versions (GET /chats/{cid}: `tree`, and each message's `versions`); the question goes
    on from `parent` when given (any message of the conversation), else from the version showing (`head`). With
    `regenerate` (an answer's id) its question is answered again, as a new version beside that answer (`content` isn't
    needed; `model` may pick another). With `edit`, one of your earlier questions is edited: this question becomes a new
    version beside it and is answered, and the old one stays with what followed it (404 if it isn't a question in this
    conversation). Either keeps the page the question was asked from (where, and the highlighted text; not the page's
    text, which isn't kept) unless `context` is given, and streams `branch` first ({message, parent, versions}: the
    new version, the message it follows and its versions); so does a question beside others under `parent`.

    `/clear` makes the model start afresh (the messages stay to read) and `/compact [what to keep]` folds the
    conversation so far into its summary now; each streams cleared or compacted, then its reply as an answer.

    With `browser` (the session of a browser's Lens side panel, POST /browser/sessions), the assistant can use that
    browser while the side panel is connected: open pages, read them, click, type and download files into the
    conversation (docs/browser-assistant.md)."""
    c = await run_in_threadpool(_own_chat, db, cid, user)
    if body.regenerate is not None and body.edit is not None:
        raise HTTPException(400, "regenerate an answer or edit a question, not both")
    if body.parent is not None and (body.regenerate is not None or body.edit is not None):
        raise HTTPException(400, "parent is for a new question: regenerate and edit go beside their message")
    again: dict[str, Any] | None = None  # regenerating: the question answered again
    under: Any = chat.HEAD  # the message the question follows
    if body.regenerate is not None:
        try:
            again = found = await run_in_threadpool(chat.question_of, db, cid, body.regenerate)
        except KeyError:
            raise HTTPException(404, "no such answer in this conversation") from None
        q, files = found["content"], found.get("attachments") or []
        if chat.command(q):
            raise HTTPException(400, f"{q.split()[0]} isn't answered by the model, so there's nothing to regenerate")
    else:
        q = body.content.strip()[:4000]
        files = await run_in_threadpool(_attachments, db, body.attachments, user)
        if not q and files:
            q = "I attached " + ("this file." if len(files) == 1 else "these files.")
        if not q:
            raise HTTPException(400, "ask something")
        if body.parent is not None:
            try:
                under = (await run_in_threadpool(chat.said, db, cid, body.parent))["id"]
            except KeyError:
                raise HTTPException(404, "no such message in this conversation") from None
    cmd = chat.command(q) if not files and body.edit is None and again is None else None
    if cmd:  # /clear or /compact: done here, answered like a question, without asking the model anything else

        def run() -> Iterator[str]:
            reply, mid = chat.run_command(db, cfg, cid, cmd[0], cmd[1], parent=under)
            yield _ev({"clear": "cleared", "compact": "compacted"}[cmd[0]], {"message": mid})
            yield _ev("passages", [])
            yield _ev("token", {"text": reply})
            yield _ev("done", {"message": mid})

        return StreamingResponse(run(), media_type="text/event-stream", headers=SSE_HEADERS)
    # this answer's model: the one asked for (400 if it isn't offered), else the conversation's while it's still offered
    model = await run_in_threadpool(_model, cfg, body.model) if body.model else c.get("model")
    if model and not body.model and model not in await run_in_threadpool(chat.model_choices, cfg):
        model = None
    title = c["title"]
    page = body.context.model_dump() if body.context else None
    kept = chat.shared_context(page)  # what the question keeps of the page
    if again is not None:
        under = again.get("parent")
        if page is None and again.get("context"):
            page = again["context"]
    if body.edit is not None:
        try:
            old = await run_in_threadpool(chat.said, db, cid, body.edit, "user")
        except KeyError:
            raise HTTPException(404, "no such question in this conversation") from None
        under = old.get("parent")
        if title == old["content"][:80]:  # titled after the question being edited: retitle it after the edit
            title = "New conversation"
        if page is None and old.get("context"):
            page = kept = old["context"]
    # what the model reads: the question with the page it was asked from, and the files it can import
    asked = chat.with_context(q, page, cfg) + chat.attached_note(files)
    readable = set(acl.roles)
    summary: dict[str, Any] | None = None  # what compaction keeps of the earlier messages (chat.compact)
    question: int | None = again["id"] if again else None  # the question being answered
    answer_id = 0  # the answer's message id, taken once the question is saved (a reply is newer than what it follows)
    branched: dict[str, Any] | None = None  # the `branch` event, when this makes a new version

    def prepare() -> tuple[list[dict[str, Any]], list[dict[str, Any]], Any]:
        nonlocal summary, under, question, answer_id, branched
        under = chat.head(db, cid) if under == chat.HEAD else under
        past = chat.path(db, cid, under)  # the version it goes on from: all the model reads of the conversation
        try:  # a conversation that fills the model's context is folded into its summary before it's answered
            chat.compact(db, cfg, cid, full_only=True, line=past)
        except Exception:  # noqa: BLE001 - answered with what fits; tried again after the answer
            log.warning("chat %s: couldn't update the summary", cid, exc_info=True)
        summary = chat.memory(db, cfg, cid, past)
        if again is None:
            question = chat.add(db, cid, "user", q, attachments=files, context=kept, parent=under)
        answer_id = db.next_id("chat_message")
        if again is not None:
            branched = chat.branch(db, cid, answer_id, question, "assistant")
        elif body.edit is not None or body.parent is not None:  # beside other questions, unless it went on from a leaf
            made = chat.branch(db, cid, question, under, "user")
            branched = made if made["versions"]["count"] > 1 else None
        if not past and again is None and title == "New conversation":
            db.q("UPDATE $r SET title = $t", r=R("chat", cid), t=q[:80])
        # A conversation over everything gets the namespace its first question is about, when that's clear.
        if past or c.get("kind") == "setup" or c.get("scope"):
            return past, chat.retrieve(db, q, readable, c.get("scope"), cfg=cfg), None
        everywhere = chat.retrieve(db, q, readable, None, cfg=cfg)
        placed = auto_scope.place(db, cfg, q, readable, everywhere, admin=user.admin)
        if not placed or placed[0] != "scoped":
            return past, everywhere, placed
        c["scope"] = {"namespaces": [placed[1]["choice"]]}
        db.q("UPDATE $r SET scope = $s", r=R("chat", cid), s=c["scope"])
        return past, chat.retrieve(db, q, readable, c["scope"], cfg=cfg), placed

    past, passages, placed = await run_in_threadpool(prepare)
    picked = placed[1] if placed and placed[0] == "scoped" else None
    suggested = placed[1] if placed and placed[0] == "suggest" else None

    steps: list[dict[str, Any]] = []
    if picked:
        steps.append(
            {
                "tool": "choose_namespace",
                "args": {"namespace": picked["choice"]},
                "summary": f"Looked in {picked['choice']}, the namespace this is about",
            }
        )
    notice: str | None = None

    def save(text: str, cited: list[dict[str, Any]], **kw: Any) -> int:
        wrote = (model or cfg["llm"].get("model")) if llm.configured(cfg) else None
        return int(
            chat.add(db, cid, "assistant", text, cited, steps=steps, notice=notice, model=wrote, parent=question, mid=answer_id, **kw)
        )

    def stopped(text: str, cited: list[dict[str, Any]]) -> Iterator[str]:
        yield _ev("stopped", {"message": "Stopped"})
        yield _ev("done", {"message": save(text or "(stopped)", cited, stopped=True)})

    def answer(on: chat.Answering) -> Iterator[str]:
        nonlocal notice
        if llm.configured(cfg) and cfg["ai"].get("tools"):
            setup = c.get("kind") == "setup"
            box = ai_tools.Toolbox(
                db,
                cfg,
                user.as_audit(),
                readable,
                set(acl.editable()),
                c.get("scope"),
                cid,
                request.app.state.archive.base,
                user.admin,
                act=setup,
                said=q,
                # what it read before any tool ran: the page it was asked from, and earlier answers' excerpts and tools
                outside=(["page"] if page else []) + ai_tools.read_before(past, summary),
                browser=body.browser,
            )
            try:
                answer = ""
                # setting the server up, or working in the person's browser, takes more steps than looking things up
                steps_max = max(cfg["ai"].get("max_steps") or 6, 12 if setup else 0, browser.steps(cfg) if box.browser else 0)
                for kind, data in chat.tool_answer(cfg, box, asked, past, steps_max, model, setup=setup, summary=summary):
                    if kind == "step":
                        steps.append(data)
                        yield _ev("step", data)
                        if on.stop_requested(force=True):  # between steps: the model is called again after each
                            for appr in box.approvals:
                                yield _ev("approval", appr)
                            yield from stopped("", [])
                            return
                    # a model that skips the tools hasn't seen the archive (but may answer from the page it was asked on,
                    # or from what the namespace's assistant remembers, citing it)
                    elif kind == "direct" and passages and not setup and not (page and page.get("text")) and not box.cited(data):
                        break
                    else:
                        answer = data
                else:
                    box.after_answer(answer)
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
                for piece in llm.stream_chat(cfg, chat.messages_for(asked, passages, past, summary, cfg), model=model):
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
        saved = False
        try:
            if branched:
                yield _ev("branch", branched)
            if picked:
                yield _ev("scoped", {"namespaces": c["scope"]["namespaces"], "confidence": picked["confidence"], "by": picked["by"]})
                yield _ev("step", steps[0])
            if suggested:
                yield _ev("suggested", {"namespaces": suggested})
            for ev in answer(on):
                saved = saved or ev.startswith("event: done")
                yield ev
        except Exception:  # an answer that breaks off still ends with an error and is saved, so the question isn't left unanswered
            log.exception("chat %s: the answer failed", cid)
            message = "Something went wrong while answering. Try again."
            yield _ev("error", {"message": message})
            if not saved:
                yield _ev("done", {"message": save("(no answer)", [], error=message)})
        finally:
            on.end()
            try:  # once enough has built up, older messages are folded into the summary (after the answer, so it waits on nothing)
                chat.compact(db, cfg, cid)
            except Exception:  # noqa: BLE001 - tried again after the next answer
                log.warning("chat %s: couldn't update the summary", cid, exc_info=True)

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


def _via(user: Principal) -> str:
    """Where a pick is made: web for a signed-in session, else token (an API key) or oauth (an app)."""
    return "web" if user.via == "access" else user.via


def approval_view(a: dict, via: str = "web") -> Any:
    """An approval as the API shows it: an automation's intent with credentials hidden, and what `via` can do."""
    return {**a, "intent": approvals.shown(a.get("intent")), "told": a.get("told") or [], "can": approvals.can(a, via)}


@router.get("/approvals")
def list_approvals(
    user: CurrentUser,
    db: Db,
    chat_id: int | None = None,
    status: Literal["pending", "running", "done", "failed", "declined", "expired"] | None = None,
    origin: Literal["automation", "chat"] | None = None,
    automation: int | None = None,
    before: int | None = Query(default=None, description="ids below this one: the last id of the page before"),
    limit: int = Query(default=100, ge=1, le=200),
) -> list[Approval]:
    """What waits for your pick (and what became of it), newest first: the assistant's proposals and automations'
    actions. Filter by `status` (pending is waiting), `origin` (automation or chat), one `automation` or one chat
    (`chat_id`); page with `before`."""
    rows = approvals.listing(db, user.id, status, origin, automation, chat_id, before, limit)
    return [approval_view(a, _via(user)) for a in rows]


@router.get("/approvals/count")
def count_approvals(user: CurrentUser, db: Db) -> ApprovalCount:
    """How many approvals wait for you, for the badge in the top bar."""
    return approvals.count(db, user.id)


@router.get("/approvals/{aid}")
def get_approval(aid: int, user: CurrentUser, db: Db) -> Approval:
    """One of your approvals."""
    a = approvals.get(db, aid)
    if not a or a["account"] != user.id:
        raise HTTPException(404, "not found")
    return approval_view(a, _via(user))


@router.post("/approvals/{aid}")
def decide_approval(aid: int, body: ApprovalDecision, user: Writer, acl: Acl, db: Db, cfg: Cfg, request: Request) -> ApprovalOutcome:
    """Approve (or run a sample of, or decline) something the assistant proposed or an automation's action. Each
    approval is decided once. An automation's action can also be edited (`params`) and then carried out, or allowed
    from now on for that automation (`always`); approving one needs you signed in, since it's your pick: an API key
    or an app can only decline it."""
    a = db.one("SELECT account FROM $r", r=R("approval", aid))
    if not a or a["account"] != user.id:
        raise HTTPException(404, "not found")
    try:
        out = ai_tools.approve(
            db,
            cfg,
            aid,
            user.as_audit(),
            set(acl.editable()),
            body.decision,
            request.app.state.archive.base,
            user.admin,
            set(acl.roles),
            params=body.params,
            via=_via(user),
        )
    except PermissionError as e:
        raise HTTPException(403, str(e)) from None
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e)) from None
    auth.audit(db, user.as_audit(), "assistant.approval", f"approval:{aid}", {"decision": body.decision, **out})
    request.app.state.graph_cache.clear()  # an approved entity or topic change shows in the graph at once
    return out
