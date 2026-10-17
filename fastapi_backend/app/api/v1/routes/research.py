"""Deep research (app/domain/research.py, docs/assistant.md#deep-research): a goal gets a plan to approve; approved,
it runs in the background and its cited report comes back in the conversation. Its plan's approval is decided like
any other (POST /approvals/{id}); these routes start one, follow it, stop it and use its report."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.api.deps import Access, Acl, Cfg, CurrentUser, Db, Principal, Writer
from app.api.v1.routes import podcasts as podcast_routes
from app.api.v1.routes.chats import _own_chat, _scope, _tools, _via
from app.domain import ai_tools, auth, chat, llm, research, store
from app.domain.store import DB
from app.schemas.podcasts import PodcastCreate, PodcastSelection, PodcastStarted
from app.schemas.research import (
    Research,
    ResearchNote,
    ResearchNoteMade,
    ResearchPodcast,
    ResearchRevise,
    ResearchStart,
    ResearchStarted,
    ResearchStop,
)

router = APIRouter(tags=["research"])


def toolbox(
    db: DB,
    cfg: dict[str, Any],
    user: Principal,
    acl: Access,
    cid: int,
    scope: dict[str, Any] | None,
    tools: list[str] | None,
    request: Request,
) -> ai_tools.Toolbox:
    """The person's tools in the conversation `cid`, as research plans with them."""
    return ai_tools.Toolbox(
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


def _mine(db: DB, rid: int, user: Principal) -> dict[str, Any]:
    try:
        return research.mine(db, rid, user.id)
    except KeyError:
        raise HTTPException(404, "not found") from None


def view(db: DB, r: dict[str, Any]) -> Research:
    return Research.model_validate(research.view(db, r))


@router.post("/chats/{cid}/research")
def start_research(cid: int, body: ResearchStart, user: Writer, acl: Acl, db: Db, cfg: Cfg, request: Request) -> ResearchStarted:
    """Start research on a goal in a conversation: the goal is saved as a question and one model call plans it (up to
    three follow-up questions with answers to pick, the sub-questions, where it looks, the tool calls it may make, the
    report's outline, and an estimate of the cost). The plan waits as an approval (POST /approvals/{id}: approve it as
    it is, or with `params` its edits; or decline), or revise it (POST /research/{id}/revise). With `review` false it
    starts at once: your own pick when signed in; an API key or an app needs research.agents_skip_review. The same as a
    question with `research: true`, or /research <goal>."""
    c = _own_chat(db, cid, user)
    if not research.available(cfg):
        raise HTTPException(400, "research needs a language model that uses tools (Settings › AI assistant)")
    via = _via(user)
    if not body.review and not research.agents_may_approve(cfg, via):
        raise HTTPException(403, "starting research without review is the person's own pick (research.agents_skip_review)")
    scope = _scope(acl, db, body.scope) if body.scope is not None else c.get("scope")
    pre = toolbox(db, cfg, user, acl, cid, scope, None, request)
    tools = _tools(pre, body.tools) if body.tools is not None else c.get("tools")
    if body.max_tool_calls is not None and body.max_tool_calls > research.settings(cfg)["max_tool_calls"]:
        raise HTTPException(
            400, f"max_tool_calls is a whole number from 1 to {research.settings(cfg)['max_tool_calls']} (research.max_tool_calls)"
        )
    box = toolbox(db, cfg, user, acl, cid, scope, tools, request)
    if not research.sources_of(box):
        raise HTTPException(400, "none of the tools you picked can look things up for research")
    kept = c.get("model")  # the conversation's model, while it's still offered
    model = research.model_for(cfg, kept if kept and kept in chat.model_choices(cfg) else None)
    if c["title"] == "New conversation":
        db.q("UPDATE $r SET title = $t", r=store.R("chat", cid), t=body.goal[:80])
    out = research.start(
        db,
        cfg,
        box,
        " ".join(body.goal.split()),
        "web" if via == "web" else "api",
        model=model,
        max_tool_calls=body.max_tool_calls,
        options={"tools": body.tools, "max_tool_calls": body.max_tool_calls, "scope": scope if body.scope is not None else None},
        review=body.review,
    )
    if out.get("error"):
        raise HTTPException(400, out["error"])
    return ResearchStarted(
        research=view(db, out["research"]), question=out["question"], message=out["message"], text=out["text"], meta=out["meta"]
    )


@router.get("/research")
def list_research(user: CurrentUser, db: Db, chat_id: int | None = None, limit: int = 50) -> list[Research]:
    """Your research, newest first; with `chat_id`, that conversation's (to show each plan's or run's card)."""
    where = "account = $a" + (" AND chat = $c" if chat_id is not None else "")
    rows = db.rows(
        f"SELECT *, record::id(id) AS id FROM research WHERE {where} ORDER BY id DESC LIMIT $n",
        a=user.id,
        c=chat_id,
        n=max(1, min(limit, 200)),
    )
    return [view(db, r) for r in rows]


@router.get("/research/{rid}")
def get_research(rid: int, user: CurrentUser, db: Db) -> Research:
    """A research run: its plan, status and stage (gather, write, check, done), progress (sub-questions done, sources
    found, tool calls and cost so far, from the activity ledger) and, once it's done, the report's message
    (`answer`, in the conversation). What it waits for when a budget holds it (`held`), and why it ended early."""
    return view(db, _mine(db, rid, user))


@router.post("/research/{rid}/revise")
def revise_research(rid: int, body: ResearchRevise, user: Writer, acl: Acl, db: Db, cfg: Cfg, request: Request) -> Research:
    """Send your answers to the follow-up questions and your edits to the plan back for one more planning call; the
    plan and its approval change in place. Only while the plan waits for approval."""
    r = _mine(db, rid, user)
    box = toolbox(db, cfg, user, acl, r["chat"], r.get("scope"), r.get("tools"), request)
    try:
        return view(db, research.revise(db, cfg, box, r, body.answers, body.plan))
    except (ValueError, llm.LLMError) as e:
        raise HTTPException(400, str(e)) from None


@router.post("/research/{rid}/stop")
def stop_research(rid: int, user: Writer, db: Db, cfg: Cfg) -> ResearchStop:
    """Stop a run: it stops after the step it's on and keeps what it found, saved in the conversation as its answer,
    marked stopped (one that hasn't started, or waits on a budget, stops now). Works whichever process runs it."""
    _mine(db, rid, user)
    return ResearchStop(stopping=research.request_stop(db, cfg, rid))


@router.post("/research/{rid}/resume")
def resume_research(rid: int, user: Writer, db: Db, cfg: Cfg) -> Research:
    """Let a run that a budget holds go on anyway (your pick, as "run it once" is for a held routine run)."""
    _mine(db, rid, user)
    try:
        return view(db, research.go_on(db, cfg, rid, user.as_audit()))
    except ValueError as e:
        raise HTTPException(409, str(e)) from None


@router.post("/research/{rid}/note")
def research_note(rid: int, user: Writer, acl: Acl, db: Db, body: ResearchNote | None = None) -> ResearchNoteMade:
    """Save the report as a note: in the namespace it looked in (`namespace` when there are several), its archive
    citations as links to the assets they cite, web citations as links, and a link back to the conversation (shown to
    you alone). Needs editor access there."""
    r = _mine(db, rid, user)
    try:
        out = research.to_note(db, r, user.id, set(acl.editable()), (body or ResearchNote()).namespace)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    auth.audit(db, user.as_audit(), "research.note", f"note_page:{out['page']}", {"research": rid})
    return ResearchNoteMade(**out)


@router.post("/research/{rid}/podcast", status_code=202)
def research_podcast(rid: int, user: Writer, acl: Acl, db: Db, cfg: Cfg, body: ResearchPodcast | None = None) -> PodcastStarted:
    """Make a podcast episode from the assets the report cites (POST /podcasts with them picked): about the research
    goal unless `prompt` says otherwise, titled after the report."""
    r = _mine(db, rid, user)
    body = body or ResearchPodcast()
    picked = research.podcast_sources(db, r, acl.readable())
    if not picked:
        raise HTTPException(400, "the report cites no assets from the archive to make an episode from")
    ask = PodcastCreate(
        selection=PodcastSelection(recordings=picked[:100]),
        prompt=body.prompt or (r.get("goal") or "")[:2000] or None,
        length=body.length,
        style=body.style,
        title=body.title or (r.get("plan") or {}).get("title"),
    )
    return podcast_routes.start(acl, user, db, cfg, ask)
