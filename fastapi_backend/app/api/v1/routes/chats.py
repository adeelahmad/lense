"""Conversations with the archive, the assistant's approvals, and checking an answer against its sources.

A question streams back server-sent events: ``branch`` (a new version: regenerated, edited, or beside others),
``scoped`` (the namespace a conversation over everything was narrowed
to) or ``suggested`` (namespaces to offer when it isn't clear, new ones included), ``step`` (a tool the assistant used), ``research`` (a research goal's plan, research.py), ``approval`` (work it proposed that waits for you), ``notice``, ``passages`` (the numbered excerpts), ``token`` (answer text), ``error``, ``meta`` (the answer's record), ``done`` and, after a first answer, ``title`` (the title Lens gave the conversation).
The assistant only ever sees, and cites, what the asker can read.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
from collections.abc import Iterator
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from app.api.deps import Access, Acl, Cfg, CurrentUser, Db, Principal, Writer
from app.api.media import sign_urls
from app.api.v1.routes.collections import own_collection
from app.api.v1.routes.oauth import web_app_base
from app.domain import (
    activity,
    ai_tools,
    anytopdf,
    approvals,
    auth,
    auto_scope,
    browser,
    chat,
    chat_export,
    convert,
    llm,
    research,
    sandbox,
    store,
    uploads,
)
from app.domain import files as resource_files
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
    ChatTitle,
    ChatTools,
    ChatUpdate,
    HeadUpdate,
    MessageCreate,
    MessageDetail,
    StopResult,
)
from app.schemas.common import Created, Ok

router = APIRouter(tags=["chats"])
OUTSIDE = {"web", "docs", "server"}  # citations that aren't the archive's: web pages, Lens's docs, this server's facts
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
        "SELECT record::id(id) AS id, title, title_by, scope, model, kind ?? 'chat' AS kind, tools, max_tool_calls, created_at, "
        "updated_at FROM chat WHERE account = $a ORDER BY updated_at DESC LIMIT 200",
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
        max_tool_calls=int(cfg["ai"].get("max_tool_calls") or 50),
        check=on,
        models=chat.model_choices(cfg),
    )


def _box(db: DB, cfg: dict[str, Any], user: Principal, acl: Access, scope: dict[str, Any] | None, request: Request) -> ai_tools.Toolbox:
    """The assistant's tools as this person has them, in `scope`, before anything is asked."""
    return ai_tools.Toolbox(
        db, cfg, user.as_audit(), set(acl.roles), set(acl.editable()), scope, None, request.app.state.archive.base, user.admin
    )


@router.get("/chats/tools")
def chat_tools(user: CurrentUser, acl: Acl, db: Db, cfg: Cfg, request: Request, chat_id: int | None = None) -> ChatTools:
    """The tool groups and tools an answer can be given (`tools` on a question or a conversation), as you have them: each
    tool with what it does (read, asks: it waits for your yes, out: it sends what it's given to another host) and
    whether you have it now, each group with why you don't when you have none of it, and the setting that changes it.
    Built-in groups first, then your extensions' (mcp:<server>, ext:<extension>). With `chat_id`, as in that
    conversation's scope (a namespace's own memory is there only in a conversation about that namespace). Also: the most
    tool calls an answer may be given, and your latest choice, which new conversations start with."""
    scope = _own_chat(db, chat_id, user).get("scope") if chat_id is not None else None
    box = _box(db, cfg, user, acl, scope, request)
    return ChatTools(
        groups=ai_tools.catalog(box),
        max_tool_calls=int(cfg["ai"].get("max_tool_calls") or 50),
        remembered=chat.remembered_tools(db, user.id),
    )


def _model(cfg: dict[str, Any], model: str | None) -> str | None:
    try:
        return chat.check_model(cfg, model)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


def _tools(box: ai_tools.Toolbox, tools: list[str] | None) -> list[str] | None:
    """A choice of tools, each a group or tool this person has (400 otherwise)."""
    if tools is None:
        return None
    bad = box.unknown(tools)
    if bad:
        raise HTTPException(400, f"no tool or tool group {bad[0]!r} (GET /chats/tools lists them)")
    return list(dict.fromkeys(tools))


def _limit(cfg: dict[str, Any], n: int | None) -> int | None:
    try:
        return chat.tool_calls_allowed(cfg, n)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@router.post("/chats")
def create_chat(user: Writer, acl: Acl, db: Db, cfg: Cfg, request: Request, body: ChatCreate | None = None) -> Created:
    """A conversation. Its answers may use what `tools` and `max_tool_calls` say, which become your latest choice;
    left out, your latest choice (here or PATCH /chats/{cid}), else Auto: everything you may use, limited by the rounds
    alone. A `title` is yours, and kept; without one, Lens titles it after its first answer (ai.auto_title)."""
    body = body or ChatCreate()
    if body.kind == "setup" and not user.admin:
        raise HTTPException(403, "only admins set up the server")
    # setting the server up keeps every tool: your latest choice is for conversations about the archive
    last = chat.remembered_tools(db, user.id) if body.kind == "chat" else {"tools": None, "max_tool_calls": None}
    tools = _tools(_box(db, cfg, user, acl, None, request), body.tools) if "tools" in body.model_fields_set else last["tools"]
    limit = _limit(cfg, body.max_tool_calls) if "max_tool_calls" in body.model_fields_set else _most(cfg, last["max_tool_calls"])
    cid = chat.create(db, user.id, body.title, _scope(acl, db, body.scope), _model(cfg, body.model), body.kind, tools, limit, "person")
    if body.kind == "chat" and {"tools", "max_tool_calls"} & body.model_fields_set:
        chat.remember_tools(db, user.id, tools, limit)
    return Created(id=cid)


def _most(cfg: dict[str, Any], n: int | None) -> int | None:
    """A limit kept from before, within ai.max_tool_calls as it is now."""
    return None if n is None else max(1, min(int(n), int(cfg["ai"].get("max_tool_calls") or 50)))


@router.get("/chats/{cid}")
def get_chat(cid: int, user: CurrentUser, acl: Acl, db: Db) -> Chat:
    """A conversation with the version showing (`messages`, each with its `versions`) and every version's messages
    (`tree`). Citations follow your current access: ones you can no longer read are left out (web pages, Lens's docs
    and this server's facts it cited stay: they aren't the archive's)."""
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
    msgs, versions = chat.path(db, c["id"], c.get("head"), nodes), chat.versions(nodes)
    for m in msgs:
        m["versions"] = versions.get(m["id"])
    _readable_citations(db, acl, msgs)
    return Chat.model_validate(sign_urls({**c, "messages": msgs, "tree": nodes}, full=True))


def _readable_citations(db: DB, acl: Access, msgs: list[dict[str, Any]]) -> None:
    """Leave out the citations the asker can no longer read (web pages, Lens's docs and this server's facts stay)."""
    ok = set(acl.roles)
    ids = {p["recording_id"] for m in msgs for p in m.get("passages") or [] if p.get("recording_id") is not None}
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
        m["passages"] = [p for p in m.get("passages") or [] if p.get("source") in OUTSIDE or p.get("recording_id") in visible]


@router.get("/chats/{cid}/messages/{mid}")
def get_message(cid: int, mid: int, user: CurrentUser, acl: Acl, db: Db) -> MessageDetail:
    """One message of any version, with its record (`meta`: for an answer, the model and the host it ran on, the tools
    offered and used, the scope, limits and use, tokens, cost, time and how it ended) and its rows in the activity ledger
    (`ledger`: the calls made for it, newest first). Citations follow your current access, as in GET /chats/{cid}."""
    _own_chat(db, cid, user)
    msgs = chat.history(db, cid, [mid])
    if not msgs:
        raise HTTPException(404, "not found")
    msgs[0]["versions"] = chat.versions(chat.tree(db, cid)).get(mid)
    _readable_citations(db, acl, msgs)
    return MessageDetail.model_validate({**sign_urls(msgs[0], full=True), "ledger": activity.history(db, f"chat_message:{mid}", 200)})


EXPORTS = {"md": "text/markdown", "json": "application/json", "pdf": "application/pdf"}


def export_view(db: DB, acl: Access, c: dict[str, Any], message: int | None = None, every: bool = False) -> list[dict[str, Any]]:
    """The messages an export holds, oldest first, each with its `versions`, citations the reader can no longer read
    left out: the version showing, every version (`every`), or one message with the question it answers (`message`)."""
    nodes = chat.tree(db, c["id"])
    if message is not None:
        node = next((n for n in nodes if n["id"] == message), None)
        if node is None:
            raise HTTPException(404, "no such message in this conversation")
        msgs = chat.history(db, c["id"], [node["parent"], message] if node["role"] == "assistant" and node.get("parent") else [message])
    elif every:
        msgs = chat.history(db, c["id"])
    else:
        msgs = chat.path(db, c["id"], c.get("head"), nodes)
    versions = chat.versions(nodes)
    for m in msgs:
        m["versions"] = versions.get(m["id"])
    _readable_citations(db, acl, msgs)
    return msgs


def exported(
    db: DB,
    cfg: dict[str, Any],
    acl: Access,
    user: Principal,
    cid: int,
    fmt: str,
    web: str,
    message: int | None = None,
    every: bool = False,
    shape: str = "tree",
) -> tuple[bytes, str, str]:
    """One of your conversations as a file: (its bytes, its media type, its file name). Also the MCP server's
    export_chat."""
    if fmt not in EXPORTS:
        raise HTTPException(404, f"exports: {', '.join(EXPORTS)}")
    if every and (fmt != "json" or message is not None or shape == "messages"):
        raise HTTPException(400, "only the JSON tree holds every version (all), and only of the whole conversation")
    if shape == "messages" and fmt != "json":
        raise HTTPException(400, "format=messages is a JSON export")
    c = _own_chat(db, cid, user)
    msgs = export_view(db, acl, c, message, every)
    name = chat_export.file_name(c.get("title"), fmt, message)
    if fmt == "json":
        if shape == "messages":
            return json.dumps(chat_export.messages(msgs), ensure_ascii=False).encode() + b"\n", EXPORTS[fmt], name
        doc = chat_export.document(c, msgs, web, user.name or user.email)
        return json.dumps(doc, ensure_ascii=False, indent=2).encode(), EXPORTS[fmt], name
    text = chat_export.markdown(c, msgs, web, one=message is not None)
    if fmt == "md":
        return text.encode(), EXPORTS[fmt], name
    try:
        return chat_export.pdf(cfg, c.get("title") or "Conversation", text), EXPORTS[fmt], name
    except (chat_export.Unavailable, anytopdf.Unavailable, convert.Unavailable) as e:
        raise HTTPException(409, str(e)) from None
    except (urllib.error.URLError, OSError, RuntimeError, ValueError) as e:  # fetching anytopdf, or printing, failed
        raise HTTPException(409, f"the PDF couldn't be made here ({getattr(e, 'reason', e)}); print it from the web app instead") from None


@router.get("/chats/{cid}/export.{fmt}", response_class=Response, responses={200: {"content": {t: {} for t in EXPORTS.values()}}})
def export_chat(
    cid: int,
    fmt: Literal["md", "json", "pdf"],
    request: Request,
    user: CurrentUser,
    acl: Acl,
    db: Db,
    cfg: Cfg,
    message: int | None = Query(None, description="one message (an answer comes with the question it answers); else the version showing"),
    every: bool = Query(False, alias="all", description="JSON: every version of the conversation, not only the one showing"),
    shape: Literal["tree", "messages"] = Query(
        "tree", alias="format", description='JSON: messages gives the version showing as one {"messages": [...]} line'
    ),
) -> Response:
    """A conversation as a download: Markdown (citations as footnotes linking back to Lens), PDF (that Markdown
    printed: by Chromium, else anytopdf, else LibreOffice; 409 with the reason when none can run here) or JSON
    (OpenAI chat messages in a tree, with Dublin Core terms; docs/schemas/chat-export.schema.json). Citations follow
    your current access, as in GET /chats/{cid}."""
    data, media, name = exported(db, cfg, acl, user, cid, fmt, web_app_base(request), message, every, shape)
    return Response(
        data,
        media_type=media + ("; charset=utf-8" if fmt != "pdf" else ""),
        headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "private, no-store"},
    )


@router.patch("/chats/{cid}")
def update_chat(cid: int, body: ChatUpdate, user: Writer, acl: Acl, db: Db, cfg: Cfg, request: Request) -> Ok:
    """Rename a conversation, change what it draws on, the model that answers in it (null: the configured one), or
    what its answers may use (`tools`, null: Auto) and at most how many tool calls each makes (`max_tool_calls`, null:
    no limit but the rounds). The choice of tools and limit is also what your new conversations start with."""
    c = _own_chat(db, cid, user)
    if "model" in body.model_fields_set:
        db.q("UPDATE $r SET model = $m", r=R("chat", cid), m=_model(cfg, body.model))
    if body.title:  # yours from now on: Lens doesn't retitle it (POST /chats/{cid}/title has it title it again)
        chat.rename(db, cid, body.title)
    if "scope" in body.model_fields_set:  # replaces the scope as a whole, so narrowing can also be removed
        db.q("UPDATE $r SET scope = $s", r=R("chat", cid), s=_scope(acl, db, body.scope))
    chose = {"tools", "max_tool_calls"} & body.model_fields_set
    if chose:
        tools = _tools(_box(db, cfg, user, acl, c.get("scope"), request), body.tools) if "tools" in chose else c.get("tools")
        limit = _limit(cfg, body.max_tool_calls) if "max_tool_calls" in chose else c.get("max_tool_calls")
        db.q("UPDATE $r SET tools = $t, max_tool_calls = $n", r=R("chat", cid), t=tools, n=limit)
        if c.get("kind") == "chat":
            chat.remember_tools(db, user.id, tools, limit)
    return Ok()


@router.post("/chats/{cid}/title")
def title_chat(cid: int, user: Writer, db: Db, cfg: Cfg) -> ChatTitle:
    """Have Lens title a conversation now, from how it starts (the version showing's first messages), even one you
    named: the title is Lens's again, as after a first answer. Written by the model for summaries (ai.compact_model)
    when set, else the chat's, in one call counted for the conversation in the activity ledger. 409 before the first
    question; 400 with no model, or when the model writes no title."""
    _own_chat(db, cid, user)
    try:
        title = chat.retitle(db, cfg, cid)
    except ValueError as e:
        raise HTTPException(409, str(e)) from None
    except (llm.LLMError, activity.OverBudget) as e:
        raise HTTPException(400, str(e)) from None
    return ChatTitle(title=title, title_by="assistant")


@router.delete("/chats/{cid}")
def delete_chat(cid: int, user: Writer, db: Db, cfg: Cfg) -> Ok:
    """Delete a conversation, with its code sandbox and the files in it."""
    _own_chat(db, cid, user)
    db.run(["DELETE chat_message WHERE chat = $c", "DELETE $r"], c=cid, r=R("chat", cid))
    sandbox.forget(db, cfg, cid)
    return Ok()


@router.get(
    "/chats/{cid}/files/{fid}",
    response_class=Response,
    responses={200: {"content": {"application/octet-stream": {}}, "description": "the file"}},
)
def download_chat_file(cid: int, fid: str, user: CurrentUser, db: Db, cfg: Cfg) -> Response:
    """A file the code sandbox handed you in this conversation (share_sandbox_file, the download on its answer)."""
    _own_chat(db, cid, user)
    try:
        name, body = sandbox.shared(db, cfg, cid, fid)
    except KeyError:
        raise HTTPException(404, "no such file in this conversation") from None
    ascii_name = name.encode("ascii", "replace").decode().replace('"', "").replace("?", "_")
    headers = {
        **resource_files.HEADERS,
        "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{urllib.parse.quote(name)}",
        "Cache-Control": "private, no-store",
    }
    return Response(body, media_type="application/octet-stream", headers=headers)


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


def _research_reply(
    cid: int,
    c: dict[str, Any],
    goal: str,
    said: str,
    under: Any,
    model: str | None,
    tools: list[str] | None,
    body: MessageCreate,
    own_scope: dict[str, Any] | None,
    user: Principal,
    acl: Access,
    db: DB,
    cfg: dict[str, Any],
    request: Request,
) -> StreamingResponse:
    """A question that is a research goal (`research: true`, or /research <goal>): its answer is a plan to approve
    (research.start). Streams research (the plan, as GET /research/{id}), approval, passages, token (the plan as
    text), meta and done; error when the plan couldn't be made."""
    if not research.available(cfg):
        raise HTTPException(400, "research needs a language model that uses tools (Settings › AI assistant)")
    scope = own_scope if own_scope is not None else c.get("scope")

    def run() -> Iterator[str]:
        box = ai_tools.Toolbox(
            db,
            cfg,
            user.as_audit(),
            set(acl.roles),
            set(acl.editable()),
            scope,
            cid,
            request.app.state.archive.base,
            user.admin,
            only=tools,
        )
        if c["title"] == "New conversation":
            db.q("UPDATE $r SET title = $t", r=R("chat", cid), t=goal[:80])
        via = "voice" if body.via == "voice" else "web" if user.via == "access" else "api"
        options = {"model": body.model, "tools": body.tools, "max_tool_calls": body.max_tool_calls, "scope": own_scope}
        out = research.start(
            db,
            cfg,
            box,
            goal,
            via,
            said=said,
            parent=under,
            model=research.model_for(cfg, model),
            max_tool_calls=body.max_tool_calls,
            options=options,
        )
        if out.get("error"):
            yield _ev("error", {"message": out["error"]})
        else:
            yield _ev("research", research.view(db, out["research"]))
            yield _ev("approval", out["approval"])
            yield _ev("passages", [])
            yield _ev("token", {"text": out["text"]})
        yield _ev("meta", out["meta"])
        yield _ev("done", {"message": out["message"]})

    return StreamingResponse(run(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post(
    "/chats/{cid}/messages",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}, "description": "server-sent events"}},
)
async def send_message(cid: int, body: MessageCreate, user: Writer, acl: Acl, db: Db, cfg: Cfg, request: Request) -> StreamingResponse:
    """Ask a question, optionally from a page (`context`: the page, its text and any highlighted part, which the model
    reads with the question). Streams events: step, approval, notice, passages, token (answer text), error, stopped (POST
    /chats/{cid}/stop: what came before is saved, marked stopped), meta (the answer's record, kept with it: what produced
    it), done (the saved message id).

    A conversation is a tree of versions (GET /chats/{cid}: `tree`, and each message's `versions`); the question goes
    on from `parent` when given (any message of the conversation), else from the version showing (`head`). With
    `regenerate` (an answer's id) its question is answered again, as a new version beside that answer (`content` isn't
    needed; `model` may pick another). With `edit`, one of your earlier questions is edited: this question becomes a new
    version beside it and is answered, and the old one stays with what followed it (404 if it isn't a question in this
    conversation). Either keeps the page the question was asked from (where, and the highlighted text; not the page's
    text, which isn't kept) unless `context` is given, and streams `branch` first ({message, parent, versions}: the
    new version, the message it follows and its versions); so does a question beside others under `parent`.

    What the answer may use: `tools` (tool groups and tools, GET /chats/tools; [] for none, when it answers from a
    search) and `max_tool_calls` (at most that many tool calls, in as many rounds as it takes), else the conversation's
    choice (PATCH /chats/{cid}); and `scope`, what this answer alone draws on (the conversation keeps its own). Its
    record keeps what it was given and used.

    `/clear` makes the model start afresh (the messages stay to read) and `/compact [what to keep]` folds the
    conversation so far into its summary now; each streams cleared or compacted, then its reply as an answer.

    With `research` (or /research <goal>) the content is a research goal: the answer is a plan to approve (streamed as
    `research`, then `approval`), which runs in the background once approved (POST /chats/{cid}/research, GET
    /research/{id}).

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
    # what this answer may use: its tools and at most how many calls (the conversation's, unless the question says), and
    # what it draws on, when the question gives it a scope of its own (the conversation keeps its scope)
    if body.tools is not None:
        tools = await run_in_threadpool(lambda: _tools(_box(db, cfg, user, acl, c.get("scope"), request), body.tools))
    else:
        tools = c.get("tools")
    limit = _limit(cfg, body.max_tool_calls) if body.max_tool_calls is not None else _most(cfg, c.get("max_tool_calls"))
    own_scope = await run_in_threadpool(_scope, acl, db, body.scope) if body.scope is not None else None
    goal = (q if body.research else research.command(q)) if not files and body.edit is None and again is None else None
    if goal:  # a research goal: the answer is a plan to approve (research.py)
        return _research_reply(cid, c, goal, q, under, model, tools, body, own_scope, user, acl, db, cfg, request)
    record = chat.Record(cfg, model, picked=tools, tool_calls=limit)  # what produced the answer (from here: it times it)
    retitle = chat.lens_titles(c)  # whether a first question titles the conversation (nobody has named it)
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
        if c.get("title_by") is None and c["title"] == old["content"][:80]:  # from before Lens kept who titled it
            retitle = True
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
            via = "voice" if body.via == "voice" else "web" if user.via == "access" else "api"
            meta = chat.question_record(via, model=body.model, tools=tools, max_tool_calls=limit, scope=own_scope)
            question = chat.add(db, cid, "user", q, attachments=files, context=kept, parent=under, meta=meta)
        answer_id = db.next_id("chat_message")
        if again is not None:
            branched = chat.branch(db, cid, answer_id, question, "assistant")
        elif body.edit is not None or body.parent is not None:  # beside other questions, unless it went on from a leaf
            made = chat.branch(db, cid, question, under, "user")
            branched = made if made["versions"]["count"] > 1 else None
        # what's worked out for the answer from here (the excerpts, the namespace it's about) counts for it in the ledger
        with activity.scope(db, f"chat:{cid}", f"chat_message:{answer_id}", cfg=cfg):
            return draw_on(past)

    def draw_on(past: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], Any]:
        if not past and again is None and retitle:  # its first words, until the model's title comes after the answer
            chat.title_from_question(db, cid, q)
        if own_scope is not None:  # this answer's own scope, as asked: nothing is narrowed for it
            return past, chat.retrieve(db, q, readable, own_scope, cfg=cfg), None
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
    scope = own_scope if own_scope is not None else c.get("scope")  # after one over everything was narrowed to what it's about
    record.scope = scope or {}
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
    answered: dict[str, Any] = {}  # the answer as saved: its text and outcome

    def save(text: str, cited: list[dict[str, Any]], outcome: str = "answered", **kw: Any) -> Iterator[str]:
        """The answer saved with its record: `meta`, then `done` (its id)."""
        wrote = (model or cfg["llm"].get("model")) if llm.configured(cfg) else None
        answered.update(text=text, outcome=outcome)
        meta = record.meta(db, outcome, f"chat_message:{answer_id}")
        chat.add(
            db, cid, "assistant", text, cited, steps=steps, notice=notice, model=wrote, parent=question, mid=answer_id, meta=meta, **kw
        )
        yield _ev("meta", meta)
        yield _ev("done", {"message": answer_id})

    def stopped(text: str, cited: list[dict[str, Any]]) -> Iterator[str]:
        yield _ev("stopped", {"message": "Stopped"})
        yield from save(text or "(stopped)", cited, "stopped", stopped=True)

    def answer(on: chat.Answering) -> Iterator[str]:
        nonlocal notice
        if llm.configured(cfg) and cfg["ai"].get("tools") and tools != []:  # given no tools, it answers from a search
            setup = c.get("kind") == "setup"
            box = ai_tools.Toolbox(
                db,
                cfg,
                user.as_audit(),
                readable,
                set(acl.editable()),
                scope,
                cid,
                request.app.state.archive.base,
                user.admin,
                act=setup,
                said=q,
                # what it read before any tool ran: the page it was asked from, and earlier answers' excerpts and tools
                outside=(["page"] if page else []) + ai_tools.read_before(past, summary),
                browser=body.browser,
                only=tools,
                web_app=web_app_base(request),  # the address the person uses, for lens_help's MCP and API addresses
            )
            try:
                answer, how = "", "answered"
                # setting the server up, or working in the person's browser, takes more steps than looking things up
                steps_max = max(cfg["ai"].get("max_steps") or 6, 12 if setup else 0, browser.steps(cfg) if box.browser else 0)
                answering = chat.tool_answer(
                    cfg, box, asked, past, steps_max, model, setup=setup, summary=summary, record=record, max_tool_calls=limit
                )
                for kind, data in answering:
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
                        answer, how = data, "direct" if kind == "direct" else "answered"
                else:
                    box.after_answer(answer)
                    for appr in box.approvals:
                        yield _ev("approval", appr)
                    cited = box.cited(answer)
                    yield _ev("passages", cited)
                    yield _ev("token", {"text": answer})
                    yield from save(answer or "(no answer)", cited, how)
                    return
            except llm.ToolsUnsupported:
                notice = "This model can't use tools, so the answer comes from a search instead."
                yield _ev("notice", {"message": notice})
            except llm.LLMError as e:
                yield _ev("error", {"message": str(e)})
                yield from save("(no answer)", [], "error", error=str(e))
                return
        yield _ev("passages", passages)
        text, error = "", None
        try:
            if llm.configured(cfg):
                pieces = llm.stream_chat(cfg, chat.messages_for(asked, passages, past, summary, cfg), model=model)
                for piece in pieces:
                    text += piece
                    yield _ev("token", {"text": piece})
                    if on.stop_requested():
                        pieces.close()  # the call ends here, so its ledger row is in the answer's record
                        yield from stopped(text, chat.cited(text, passages))
                        return
            else:
                text = chat.fallback(passages)
                yield _ev("token", {"text": text})
        except llm.LLMError as e:
            error = str(e)
            yield _ev("error", {"message": error})
        how = "error" if error else "fallback" if notice or not llm.configured(cfg) else "answered"
        yield from save(text or "(no answer)", chat.cited(text, passages), how, error=error)

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
            # every call made for the answer counts for it in the ledger (the scope holds across the stream's steps)
            for ev in activity.scoped(answer(on), db, f"chat:{cid}", f"chat_message:{answer_id}", cfg=cfg):
                saved = saved or ev.startswith("event: done")
                yield ev
        except Exception:  # an answer that breaks off still ends with an error and is saved, so the question isn't left unanswered
            log.exception("chat %s: the answer failed", cid)
            message = "Something went wrong while answering. Try again."
            yield _ev("error", {"message": message})
            if not saved:
                yield from save("(no answer)", [], "error", error=message)
        finally:
            on.end()
            try:  # once enough has built up, older messages are folded into the summary (after the answer, so it waits on nothing)
                chat.compact(db, cfg, cid)
            except Exception:  # noqa: BLE001 - tried again after the next answer
                log.warning("chat %s: couldn't update the summary", cid, exc_info=True)
        # a first answer titles the conversation: a cheap model call, counted for the conversation, not the answer
        if not past and again is None and retitle and answered.get("outcome") not in (None, "error"):
            if titled := chat.title_soon(db, cfg, cid, q, answered["text"]):
                yield _ev("title", {"title": titled})

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


def approval_view(a: dict, via: str = "web", cfg: dict[str, Any] | None = None) -> Any:
    """An approval as the API shows it: an automation's intent with credentials hidden, and what `via` can do."""
    return {**a, "intent": approvals.shown(a.get("intent")), "told": a.get("told") or [], "can": approvals.can(a, via, cfg)}


@router.get("/approvals")
def list_approvals(
    user: CurrentUser,
    db: Db,
    cfg: Cfg,
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
    return [approval_view(a, _via(user), cfg) for a in rows]


@router.get("/approvals/count")
def count_approvals(user: CurrentUser, db: Db) -> ApprovalCount:
    """How many approvals wait for you, for the badge in the top bar."""
    return approvals.count(db, user.id)


@router.get("/approvals/{aid}")
def get_approval(aid: int, user: CurrentUser, db: Db, cfg: Cfg) -> Approval:
    """One of your approvals."""
    a = approvals.get(db, aid)
    if not a or a["account"] != user.id:
        raise HTTPException(404, "not found")
    return approval_view(a, _via(user), cfg)


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
