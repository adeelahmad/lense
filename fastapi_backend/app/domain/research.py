"""Deep research (docs/assistant.md, "Deep research"): give a goal, approve a plan, get a cited report back.

A goal (`research: true` on a question, `/research <goal>`, POST /chats/{cid}/research, the MCP tool or the
assistant's own `start_research`) gets one planning call: up to three follow-up questions, each with answers to pick
from, and a plan (the sub-questions it will answer, the sources it will look in, the tool calls it may make, the
report's outline). The plan is saved as a `research` row and waits as an approval of the assistant's (tool
`research`), with an estimate of what it will cost. The person approves it as it is or edited (Enter in the chat,
the Approvals page, "yes" in a chat app), or revises it (their answers and edits go back for one more planning call).
An API key or an app only declines it, unless research.agents_skip_review lets agents start runs without review.

Approved, it runs in the background, claimed by one process (CREATE-as-claim on `research_run:<id>`, a heartbeat
every HEARTBEAT seconds, taken over after STALE seconds of silence and resumed from the last finished stage), in
stages:

    gather   each sub-question answered with the tool loop (chat.tool_answer), its share of the tool calls, and a
             toolbox of the plan's sources only, whose outbound tools (web search, a sandbox that reaches the web)
             don't ask again: the person approved them with the plan (Toolbox.allowed_out). Other change tools still
             ask.
    write    one call: the report in Markdown, following the outline, citing [n] across every finding, renumbered
    check    chat.check_sources on the report; unsupported claims are rewritten once, or marked
    done     the report saved in the conversation as an answer of kind `research`, with its record (chat.Record)

Every call counts in the ledger for the conversation and `research:<id>`. Before each stage the budgets of the
scope's namespaces and `all` are checked: one over holds the run (on_over ask) until the budget allows or the person
lets it go on, or stops it with what it has (skip). research.max_minutes stops a run with what it found; Stop
(POST /research/{id}/stop) stops it after the step it's on, like stopping an answer. A person has one run going at a
time; the next waits its turn.

After: save the report as a note (archive citations become mention links, web ones links, and it links back to the
conversation), read it aloud, make a podcast episode from its archive sources, or export the conversation.

Refine later: sub-questions in parallel, research on a schedule, sharing a report with namespace members, research as
an automation action, a report template people can edit, the report posted back into the chat room it was asked in.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import threading
import time
import uuid

from . import activity, auth, budgets, chat, llm, notebook, store, telemetry, untrusted

log = logging.getLogger("lens")
R = store.R

STATUSES = ("planned", "declined", "queued", "running", "held", "done", "stopped", "failed")
ACTIVE = ("queued", "running", "held")  # approved and not finished
STAGES = ("gather", "write", "check", "done")
SOURCES = ("archive", "graph", "notes", "web", "code")
# the tools a run may use for each source: reading only (code that runs in the sandbox reads and works things out)
SOURCE_TOOLS = {
    "archive": ["search_transcripts", "find_resources", "find_similar", "list_recordings", "read_transcript", "recording_outputs"],
    "graph": [
        "find_entities",
        "entity_mentions",
        "entity_timeline",
        "find_topics",
        "topic_recordings",
        "graph_neighbours",
        "speaker_stats",
        "graph_schema",
        "graph_query",
        "graph_related",
        "graph_paths",
    ],
    "notes": ["find_notes", "read_note"],
    "web": ["web_search", "web_read"],
    "code": ["run_python", "run_bash", "sandbox_files"],
}
SOURCE_LABELS = {"archive": "the archive", "graph": "the graph", "notes": "notes", "web": "the web", "code": "the code sandbox"}
QUESTIONS_MAX, ANSWERS_MAX, SUBS_MAX, OUTLINE_MAX = 3, 4, 8, 10
HEARTBEAT = 10  # seconds between a run's heartbeats
STALE = 120  # seconds of silence after which another process takes a run over
SWEEP_SECONDS = 30  # how often the scheduler looks for runs no process is on
LOOKBACK = 20  # past runs an estimate is drawn from

# the start_research tool (ai_tools.GROUPS "research"): it plans, and the plan waits for the person's approval
TOOLS = [
    (
        "start_research",
        "Plan deep research on a goal that needs many look-ups (several sub-questions, more than one source): you get "
        "a plan with follow-up questions, which waits for the person's approval; once approved it runs in the "
        "background and the cited report comes back in this conversation. Use it when the person asks for research or "
        "a report, not for a quick question.",
        {"goal": {"type": "string", "description": "what to research, in the person's words"}},
        ["goal"],
        True,
    )
]

PLAN_SCHEMA = {
    "type": "object",
    "required": ["questions", "plan"],
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["question", "answers"],
                "properties": {"question": {"type": "string"}, "answers": {"type": "array", "items": {"type": "string"}}},
            },
        },
        "plan": {
            "type": "object",
            "required": ["title", "goal", "sub_questions", "sources", "max_tool_calls", "report_outline"],
            "properties": {
                "title": {"type": "string"},
                "goal": {"type": "string"},
                "sub_questions": {"type": "array", "items": {"type": "string"}},
                "sources": {"type": "array", "items": {"type": "string", "enum": list(SOURCES)}},
                "max_tool_calls": {"type": "integer"},
                "report_outline": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
}
PLAN_SYSTEM = (
    "You plan deep research for a person. From their goal, write a plan: a short title, the goal in one sentence, "
    "the sub-questions that together answer it (3 to {subs}, each answerable by looking things up), the sources to "
    "look in (only from: {sources}), how many tool calls it may make in all (at most {calls}; about 3 to 5 per "
    "sub-question) and the report's outline (its section headings). Ask follow-up questions (at most 3, each with 2 "
    "to 4 short suggested answers) only where the answer would change the plan; none when the goal is clear. When "
    "you're given answers and edits to an earlier plan, follow them."
)
FIND = (
    "Research goal: {goal}\n\nAnswer this part of it: {sub}\n\nLook it up with the tools, then answer in a short "
    "paragraph or a few bullets, citing what you found as [n]. Say plainly what you couldn't find."
)
WRITE_SYSTEM = (
    "You write research reports. From the findings below, write the report in Markdown: a title (# heading), a short "
    "summary, then the outline's sections (## headings), then what is still open. Cite every claim with the [n] numbers "
    "of the excerpts it rests on, and only those; say so where the findings don't cover a section. Don't make up "
    "anything the findings don't say."
)
REWRITE_SCHEMA = {
    "type": "object",
    "required": ["rewrites"],
    "properties": {
        "rewrites": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["claim", "text"],
                "properties": {"claim": {"type": "string"}, "text": {"type": "string"}},
            },
        }
    },
}
REWRITE_SYSTEM = (
    "These sentences of a report aren't supported by the excerpts they cite. Rewrite each so that it says only what "
    "its excerpts support, keeping its [n] citations; give an empty text for one the excerpts don't support at all."
)
UNSUPPORTED = " *(not supported by its sources)*"


class Stopped(Exception):
    """The run stops here: asked to, out of time, or another process took it over."""

    def __init__(self, why):
        super().__init__(why)
        self.why = why


# ---------- settings ----------
def settings(cfg):
    r = cfg.get("research") or {}
    return {
        "max_tool_calls": int(r.get("max_tool_calls") or 40),
        "max_minutes": int(r.get("max_minutes") or 20),
        "model": r.get("model") or None,
        "agents_skip_review": bool(r.get("agents_skip_review")),
    }


def unavailable(cfg):
    """Why research can't run, and the setting that would change it, or None: it needs a language model that uses
    tools."""
    if not llm.configured(cfg):
        return "no language model is set up", "llm.model"
    if not cfg["ai"].get("tools"):
        return "the assistant's tools are switched off", "ai.tools"
    return None


def available(cfg):
    """Whether research can run: a language model that uses tools."""
    return unavailable(cfg) is None


def agents_may_approve(cfg, via):
    """Whether a pick made `via` (web, chat, token, oauth or agent) may approve a plan: the person's own, signed in or
    in their chat app; an API key, an app or an agent only with research.agents_skip_review."""
    return via in ("web", "chat") or settings(cfg)["agents_skip_review"]


# ---------- planning ----------
def sources_of(box):
    """The sources this person's toolbox (as the conversation and their choice of tools have it) can look in."""
    offered = {s["function"]["name"] for s in box.specs()}
    return [s for s in SOURCES if offered & set(SOURCE_TOOLS[s])]


def _text(v, n):
    return " ".join(str(v or "").split())[:n]


def clean_plan(cfg, plan, sources, goal=None):
    """A plan as a run takes it: its sub-questions, sources (of those this person has), tool calls (1 to
    research.max_tool_calls) and outline, trimmed. ValueError for one with no sub-question or no source."""
    plan = plan or {}
    subs = [_text(s, 300) for s in plan.get("sub_questions") or [] if _text(s, 300)][:SUBS_MAX]
    if not subs:
        raise ValueError("a plan has at least one sub-question")
    picked = [s for s in dict.fromkeys(plan.get("sources") or []) if s in sources]
    if not picked:
        if plan.get("sources"):
            raise ValueError(f"a plan looks in {', '.join(sources) or 'nothing you have'}")
        picked = sources[:1]
    if not picked:
        raise ValueError("you have no tools research can look things up with")
    most = settings(cfg)["max_tool_calls"]
    try:
        calls = int(plan.get("max_tool_calls") or 0)
    except (TypeError, ValueError):
        raise ValueError("max_tool_calls is a whole number") from None
    calls = max(len(subs), min(most, calls or 4 * len(subs)))
    calls = min(calls, most)
    goal = _text(plan.get("goal") or goal, 1000)
    return {
        "title": _text(plan.get("title") or goal, 120) or "Research",
        "goal": goal,
        "sub_questions": subs,
        "sources": picked,
        "max_tool_calls": calls,
        "report_outline": [_text(s, 200) for s in plan.get("report_outline") or [] if _text(s, 200)][:OUTLINE_MAX],
    }


def _questions(raw):
    out = []
    for q in raw or []:
        text = _text((q or {}).get("question"), 300)
        if text:
            out.append({"question": text, "answers": [_text(a, 120) for a in (q.get("answers") or []) if _text(a, 120)][:ANSWERS_MAX]})
    return out[:QUESTIONS_MAX]


def model_for(cfg, chat_model=None):
    """The model research uses: research.model, else the conversation's, else the configured one (None)."""
    return settings(cfg)["model"] or chat_model or None


def plan(cfg, goal, sources, scope=None, model=None, previous=None, answers=None):
    """One planning call: {questions, plan}. `previous` (a plan) with `answers` ({question: answer}) revises it."""
    if not llm.configured(cfg):
        raise llm.LLMError("research needs a language model: set one up in Settings › AI assistant")
    s = settings(cfg)
    system = PLAN_SYSTEM.format(subs=SUBS_MAX, sources=", ".join(sources), calls=s["max_tool_calls"]) + untrusted.rules(cfg)
    parts = [f"Goal: {goal}"]
    if (scope or {}).get("namespaces"):
        parts.append("It looks only in these namespaces: " + ", ".join(scope["namespaces"]))
    if previous:
        lines = "\n".join(f"- {s}" for s in previous.get("sub_questions") or [])
        parts.append(
            f"The earlier plan, as the person edited it:\nTitle: {previous.get('title')}\nSub-questions:\n{lines}\n"
            f"Sources: {', '.join(previous.get('sources') or [])}\nTool calls: {previous.get('max_tool_calls')}\n"
            f"Outline: {'; '.join(previous.get('report_outline') or [])}"
        )
    if answers:
        parts.append("Their answers:\n" + "\n".join(f"- {q}: {a}" for q, a in answers.items() if a))
    out = llm.json_out(cfg, system, "\n\n".join(parts), PLAN_SCHEMA, model=model)
    return {"questions": [] if answers else _questions(out.get("questions")), "plan": clean_plan(cfg, out.get("plan"), sources, goal)}


def estimate(db, cfg, p, model=None):
    """What a plan will likely cost, always labelled an estimate: from the ledger's past research runs (what each tool
    call cost them, times this plan's), else its tokens at the model's price in Settings. {usd, tokens, tool_calls,
    basis, estimate}."""
    calls = int(p["max_tool_calls"])
    past = db.rows(
        "SELECT record::id(id) AS id, used, finished_at FROM research WHERE status = 'done' ORDER BY finished_at DESC LIMIT $n", n=LOOKBACK
    )
    usd = tokens = used = 0.0
    runs = 0
    for r in past:
        n = (r.get("used") or {}).get("tool_calls") or 0
        if n:
            t = activity.totals(db, f"research:{r['id']}")
            usd, tokens, used, runs = usd + t["cost_usd"], tokens + t["tokens_in"] + t["tokens_out"], used + n, runs + 1
    if runs:
        return {
            "usd": round(usd / used * calls, 4),
            "tokens": int(tokens / used * calls),
            "tool_calls": calls,
            "basis": f"what each tool call of {runs} past research run{'s' if runs != 1 else ''} cost",
            "estimate": True,
        }
    subs = len(p["sub_questions"])
    tin, tout = 3000 + calls * 2500 + 8000 + 4000, subs * 500 + 2500 + 800  # planning, gathering, writing, checking
    name = model or cfg["llm"].get("model")
    cost = telemetry.cost(name, tin, tout, telemetry.prices(cfg))
    return {
        "usd": round(cost, 4) if cost is not None else None,
        "tokens": tin + tout,
        "tool_calls": calls,
        "basis": f"about {tin + tout:,} tokens at {name}'s price in Settings"
        if cost is not None
        else f"about {tin + tout:,} tokens ({name} has no price in Settings)",
        "estimate": True,
    }


def summary(p):
    n = len(p["sub_questions"])
    where = ", ".join(SOURCE_LABELS[s] for s in p["sources"])
    return f"Research: {p['title']} ({n} sub-question{'s' if n != 1 else ''}, in {where}, up to {p['max_tool_calls']} tool calls)"


def create(db, cfg, box, goal, made, message=None, model=None, rid=None):
    """Save a plan ({questions, plan}) as a `research` row of the conversation `box` is in (`rid`: its id, taken
    before planning so the planning call counts for it), and the approval it waits for (box._approval). Returns the
    row."""
    rid = rid or db.next_id("research")
    p = made["plan"]
    est = estimate(db, cfg, p, model)
    t = store.now()
    db.q(
        "CREATE $r CONTENT $d",
        r=R("research", rid),
        d=store.clean(
            {
                "chat": box.chat,
                "message": message,
                "account": box.account,
                "goal": _text(goal, 2000),
                "status": "planned",
                "plan": p,
                "questions": made["questions"],
                "scope": box.scope or None,
                "tools": sorted(box.only) if box.only is not None else None,
                "model": model,
                "estimate": est,
                "revisions": 0,
                "created_at": t,
                "updated_at": t,
            }
        ),
    )
    box._approval("research", {"research": rid, "plan": p}, summary(p), est)
    aid = box.approvals[-1]["id"]
    db.q("UPDATE $r SET approval = $a", r=R("research", rid), a=aid)
    return get(db, rid)


def propose(db, cfg, box, goal, model=None):
    """The assistant's start_research: a plan for `goal` in the conversation `box` is in, waiting for approval (the
    planning call counts for the research too). Returns the row."""
    rid = db.next_id("research")
    with activity.scope(db, f"research:{rid}", cfg=cfg):
        made = plan(cfg, goal, sources_of(box), box.scope, model)
    return create(db, cfg, box, goal, made, model=model, rid=rid)


COMMAND = re.compile(r"^/research\s+(.+)$", re.S | re.I)


def command(text):
    """The goal of `/research <goal>`, else None."""
    m = COMMAND.match((text or "").strip())
    return " ".join(m.group(1).split()) if m else None


def _money(usd):
    return f"${usd:,.4f}".rstrip("0").rstrip(".") if usd < 1 else f"${usd:,.2f}"


def plan_text(r):
    """The plan as the conversation shows it, for people reading it in a chat app or without the plan card."""
    p, est = r["plan"], r.get("estimate") or {}
    where = ", ".join(SOURCE_LABELS[s] for s in p["sources"])
    cost = f", about {_money(est['usd'])} (an estimate)" if est.get("usd") is not None else ""
    lines = [f"Here's a plan to research **{p['title']}**:", ""]
    lines += [f"{i}. {q}" for i, q in enumerate(p["sub_questions"], 1)]
    lines += ["", f"It looks in {where}, with up to {p['max_tool_calls']} tool calls{cost}."]
    if p.get("report_outline"):
        lines.append("The report: " + "; ".join(p["report_outline"]) + ".")
    if r.get("questions"):
        lines += ["", "A few questions, so it fits what you need:"]
        lines += [f"- {q['question']}" + (f" ({' / '.join(q['answers'])})" if q.get("answers") else "") for q in r["questions"]]
    lines += ["", "Approve it to start, or answer the questions or edit it and revise it."]
    return "\n".join(lines)


def start(db, cfg, box, goal, via="web", said=None, parent=chat.HEAD, model=None, max_tool_calls=None, options=None, review=True):
    """A research goal asked in the conversation `box` is in: the question saved (`said`: as typed, e.g. with
    /research), one planning call, the plan saved and its approval, and the plan as the answer (kind research_plan).
    With review False the person's own pick (via web or chat; an agent's only with research.agents_skip_review)
    approves it at once. Returns {research, approval, question, message, text, meta}, with `error` when the plan
    couldn't be made (saved as the answer's error)."""
    cid = box.chat
    question = chat.add(db, cid, "user", said or goal, parent=parent, meta=chat.question_record(via, research=True, **(options or {})))
    rid, mid = db.next_id("research"), db.next_id("chat_message")
    record = chat.Record(cfg, model, box.scope, picked=sorted(box.only) if box.only is not None else None, research=rid)
    wrote = (model or cfg["llm"].get("model")) if llm.configured(cfg) else None
    try:
        with activity.scope(db, f"chat:{cid}", f"chat_message:{mid}", f"research:{rid}", cfg=cfg):
            made = plan(cfg, goal, sources_of(box), box.scope, model)
        if max_tool_calls:
            made["plan"]["max_tool_calls"] = max(1, min(int(max_tool_calls), settings(cfg)["max_tool_calls"]))
    except (llm.LLMError, ValueError) as e:
        meta = record.meta(db, "error", f"chat_message:{mid}")
        chat.add(db, cid, "assistant", "(no plan)", [], error=str(e), model=wrote, parent=question, mid=mid, meta=meta)
        return {"error": str(e), "question": question, "message": mid, "meta": meta}
    r = create(db, cfg, box, goal, made, message=question, model=model, rid=rid)
    text = plan_text(r)
    meta = record.meta(db, "answered", f"chat_message:{mid}")
    chat.add(db, cid, "assistant", text, [], model=wrote, parent=question, mid=mid, meta=meta, kind="research_plan")
    db.q("UPDATE $r SET plan_message = $m", r=R("research", rid), m=mid)
    approval = box.approvals[-1]
    if not review:
        a = db.one("SELECT *, record::id(id) AS id FROM $r", r=R("approval", approval["id"]))
        decide(db, cfg, a, box.user, "approve", None, via if via in ("web", "chat") else "agent")
    return {"research": get(db, rid), "approval": approval, "question": question, "message": mid, "text": text, "meta": meta}


def get(db, rid):
    r = db.one("SELECT *, record::id(id) AS id FROM $r", r=R("research", int(rid)))
    if not r:
        raise KeyError(rid)
    return r


def mine(db, rid, account):
    """A research row of this person's, or KeyError."""
    r = get(db, rid)
    if r.get("account") != account:
        raise KeyError(rid)
    return r


def view(db, r):
    """A research row as the API shows it, with what it has spent so far (the ledger rows that name it)."""
    spent = activity.totals(db, f"research:{r['id']}")
    findings = r.get("findings") or []
    p = r.get("plan") or {}
    sources = {(x.get("url") or "", x.get("recording_id"), x.get("t0"), x.get("page")) for f in findings for x in f.get("passages") or []}
    return {
        "id": r["id"],
        "chat": r.get("chat"),
        "message": r.get("message"),
        "plan_message": r.get("plan_message"),
        "answer": r.get("answer"),
        "approval": r.get("approval"),
        "status": r.get("status"),
        "stage": r.get("stage"),
        "goal": r.get("goal"),
        "plan": p,
        "questions": r.get("questions") or [],
        "answers": r.get("answers") or {},
        "scope": r.get("scope") or {},
        "estimate": r.get("estimate"),
        "progress": {
            "sub_questions": len(p.get("sub_questions") or []),
            "done": len(findings),
            "sources": len(sources) + int(r.get("sources_now") or 0),
            "tool_calls": int((r.get("used") or {}).get("tool_calls") or 0),
            "cost_usd": spent["cost_usd"],
            "tokens": spent["tokens_in"] + spent["tokens_out"],
            "cost_estimated": spent["estimate"],
            "minutes": round(float(r.get("ran_s") or 0) / 60, 1),
        },
        "findings": [{"question": f["question"], "tool_calls": f.get("tool_calls") or 0} for f in findings],
        "held": r.get("held"),
        "error": r.get("error"),
        "ended": r.get("ended"),
        "revisions": r.get("revisions") or 0,
        "notes": r.get("notes") or [],
        "created_at": r.get("created_at"),
        "started_at": r.get("started_at"),
        "finished_at": r.get("finished_at"),
    }


def revise(db, cfg, box, r, answers=None, edited=None):
    """One more planning call with the person's answers and edits; the plan and its approval change in place."""
    if r["status"] != "planned":
        raise ValueError(f"this research is {r['status']}: only a plan waiting for approval can be revised")
    sources = sources_of(box)
    previous = clean_plan(cfg, {**(r.get("plan") or {}), **(edited or {})}, sources, r.get("goal"))
    answers = {_text(k, 300): _text(v, 300) for k, v in (answers or {}).items() if _text(k, 300)}
    with activity.scope(db, f"chat:{r['chat']}", f"research:{r['id']}", cfg=cfg):
        made = plan(cfg, r.get("goal") or previous["goal"], sources, r.get("scope"), r.get("model"), previous, answers)
    p = made["plan"]
    est = estimate(db, cfg, p, r.get("model"))
    db.q(
        "UPDATE $r SET plan = $p, questions = $q, answers = $a, estimate = $e, revisions += 1, updated_at = $t",
        r=R("research", r["id"]),
        p=p,
        q=made["questions"],
        a=answers or None,
        e=est,
        t=store.now(),
    )
    db.q(
        "UPDATE $r SET args = $args, summary = $s, estimate = $e WHERE status = 'pending'",
        r=R("approval", r["approval"]),
        args={"research": r["id"], "plan": p},
        s=summary(p),
        e=est,
    )
    return get(db, r["id"])


# ---------- deciding ----------
def decide(db, cfg, a, user, decision, params=None, via="web"):
    """The person's pick on a plan's approval: approve (as it is, or with `params`: the plan edited; `edit` is the
    same) or decline. An approved plan queues its run, which starts at once unless the person has one going."""
    decision = "approve" if decision == "edit" else decision
    if decision not in ("approve", "decline"):
        raise ValueError("a research plan is approved or declined")
    r = get(db, a["args"]["research"])
    if decision == "approve" and not agents_may_approve(cfg, via):
        raise PermissionError("approving a research plan is the person's own pick: an agent can only decline (research.agents_skip_review)")
    p = r["plan"]
    if decision == "approve" and params:  # the plan as the person edited it, within the sources they have
        box = toolbox(db, cfg, {**r, "plan": {**p, "sources": list(SOURCES)}}, account_of(db, r["account"]))
        allowed = sources_of(box) if box else p["sources"]
        p = clean_plan(cfg, {**p, **{k: v for k, v in params.items() if v is not None}}, allowed, r.get("goal"))
    if not approvals_take(db, a["id"], "done" if decision == "approve" else "declined", user):
        raise ValueError("nothing to approve")
    if decision == "decline":
        db.q("UPDATE $r SET status = 'declined', updated_at = $t", r=R("research", r["id"]), t=store.now())
        return {"status": "declined", "result": {"research": r["id"], "status": "declined"}}
    queue(db, cfg, r["id"], p, user)
    result = {"research": r["id"], "status": "queued"}
    db.q("UPDATE $r SET result = $res", r=R("approval", a["id"]), res=result)
    return {"status": "done", "result": result}


def approvals_take(db, aid, status, user):
    """Claim the plan's approval for one decision: True when this one won (approvals._take)."""
    from . import approvals

    return approvals._take(db, aid, status, user)


def queue(db, cfg, rid, p, user=None):
    """An approved plan, waiting for its run: started now, unless the person already has one going."""
    db.q(
        "UPDATE $r SET status = 'queued', plan = $p, stage = 'gather', approved_by = $u, approved_at = $t, updated_at = $t",
        r=R("research", rid),
        p=p,
        u=(user or {}).get("email"),
        t=store.now(),
    )
    r = get(db, rid)
    if not _busy(db, r["account"], rid):
        launch(db, cfg, rid)


def _busy(db, account, rid=None):
    """Whether the person has another run going (one at a time each)."""
    return bool(
        db.rows(
            "SELECT id FROM research WHERE account = $a AND status IN ['running', 'held'] AND id != $r LIMIT 1",
            a=account,
            r=R("research", int(rid or 0)),
        )
    )


def launch(db, cfg, rid):
    """Run it in the background, in this process, if it claims it."""
    threading.Thread(target=run, args=(db, cfg, rid), daemon=True, name=f"research {rid}").start()


def request_stop(db, cfg, rid):
    """Ask a run to stop after the step it's on; a run that hasn't started (or waits on a budget) stops now. False when
    it's already over."""
    r = get(db, rid)
    if r["status"] in ("queued", "held"):
        took = db.rows(
            "UPDATE $r SET status = 'stopped', ended = 'stopped', finished_at = $t, updated_at = $t WHERE status IN ['queued', 'held'] RETURN id",
            r=R("research", rid),
            t=store.now(),
        )
        if took and (r.get("findings") or []):
            # what it found before it was held stays: saved as a report of its findings, without another model call
            _save_found(db, cfg, get(db, rid))
        return bool(took)
    if r["status"] == "planned":
        return False
    return bool(db.rows("UPDATE $r SET stop_requested = true WHERE status = 'running' RETURN id", r=R("research", rid)))


def go_on(db, cfg, rid, user=None):
    """Let a run held by a budget go on this once (the person's pick, as budgets.py's "run it once")."""
    took = db.rows(
        "UPDATE $r SET status = 'queued', over_ok = true, held = NONE, updated_at = $t WHERE status = 'held' RETURN id",
        r=R("research", rid),
        t=store.now(),
    )
    if not took:
        raise ValueError("only a run held by a budget can go on")
    launch(db, cfg, rid)
    return get(db, rid)


# ---------- claiming a run ----------
def _ago(seconds):
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=seconds)).isoformat(timespec="seconds")


def claim(db, rid, owner):
    """Take a run for this process (CREATE-as-claim on research_run:<id>): True when it's ours. A claim whose
    heartbeat stopped STALE seconds ago is another process gone, so it's taken over."""
    rec = R("research_run", int(rid))
    for _ in range(2):
        try:
            db.q(
                "CREATE $r CONTENT $d",
                r=rec,
                d={"research": int(rid), "owner": owner, "heartbeat_at": store.now(), "claimed_at": store.now()},
            )
            return True
        except Exception as e:  # noqa: BLE001 - only a claim that's already there means someone else has it
            if "already exists" not in str(e):
                raise
        if not db.rows("DELETE $r WHERE heartbeat_at < $s RETURN BEFORE", r=rec, s=_ago(STALE)):
            return False
    return False


def beat(db, rid, owner):
    """The run's heartbeat: False when the claim isn't ours any more (another process took it over)."""
    return bool(
        db.rows("UPDATE $r SET heartbeat_at = $t WHERE owner = $o RETURN id", r=R("research_run", int(rid)), t=store.now(), o=owner)
    )


def release(db, rid, owner):
    db.q("DELETE $r WHERE owner = $o", r=R("research_run", int(rid)), o=owner)


def sweep(db, cfg, log=None):
    """Runs no process is on: an approved one waiting its turn, one whose process went away (its claim gone stale) and
    one a budget held, once the budget allows it. Each is started here, in the background."""
    rows = db.rows("SELECT record::id(id) AS id, account, status, created_at FROM research WHERE status IN $s ORDER BY id", s=list(ACTIVE))
    claimed = {
        c["research"]: c
        for c in db.rows("SELECT research, heartbeat_at FROM research_run WHERE research IN $ids", ids=[r["id"] for r in rows])
    }
    going = {r["account"] for r in rows if r["status"] == "running" and r["id"] in claimed}
    started = []
    for r in rows:
        c = claimed.get(r["id"])
        if c and (c.get("heartbeat_at") or "") >= _ago(STALE):
            continue  # a process is on it
        if r["status"] == "held":
            full = get(db, r["id"])
            if _over(db, cfg, full):
                continue
            db.q("UPDATE $r SET status = 'queued', held = NONE WHERE status = 'held'", r=R("research", r["id"]))
        elif r["account"] in going and r["status"] == "queued":
            continue  # their other run first
        going.add(r["account"])
        started.append(r["id"])
        launch(db, cfg, r["id"])
    if started and log:
        log(f"research: started {', '.join(map(str, started))}")
    return started


# ---------- running ----------
def account_of(db, aid):
    a = auth.get_account(db, aid)
    if not a or a.get("disabled") or a.get("removed_at"):
        return None
    return {"id": a["id"], "email": a["email"], "admin": bool(a.get("admin"))}


def toolbox(db, cfg, r, account, sub=None):
    """The person's tools for the run: only the plan's sources' (and of those, what the conversation's choice of tools
    keeps), and its outbound tools allowed without asking again (the plan approved them)."""
    from . import ai_tools, sandbox

    if not account:
        return None
    roles = auth.roles(db, account)
    readable, editable = set(roles), {s for s in roles if auth.allows(roles, s, "editor")}
    user = {"id": account["id"], "email": account["email"]}
    p = r.get("plan") or {}
    box = ai_tools.Toolbox(
        db,
        cfg,
        user,
        readable,
        editable,
        r.get("scope"),
        r.get("chat"),
        None,
        account["admin"],
        said=f"{r.get('goal') or ''} {sub or ''}",
        only=r.get("tools"),
    )
    keep = box.chosen()
    names = {t for s in p.get("sources") or [] for t in SOURCE_TOOLS.get(s, [])}
    box.only = names if keep is None else names & keep
    out = set()
    if "web" in (p.get("sources") or []):
        out |= set(SOURCE_TOOLS["web"])
    if "code" in (p.get("sources") or []) and sandbox.sends_out(cfg):
        out |= {"run_python", "run_bash"}
    box.allowed_out = out
    return box


def _over(db, cfg, r):
    """Why the run must wait for a budget, or None: the budgets of the scope's namespaces and `all` (budgets.check)."""
    if r.get("over_ok"):
        return None
    names = {v: k for k, v in store.space_names(db).items()}
    spaces = [f"space:{names[n]}" for n in (r.get("scope") or {}).get("namespaces") or [] if n in names]
    got = budgets.check(db, cfg, spaces)
    return None if got["go"] else got


class Run:
    """One process's turn at a run: its claim, kept by a heartbeat every HEARTBEAT seconds (from a thread of its own,
    so a long model call doesn't let the claim go stale), the time it has run, and what stops it."""

    def __init__(self, db, cfg, rid, owner):
        self.db, self.cfg, self.rid, self.owner = db, cfg, rid, owner
        self.r = get(db, rid)
        self.t0, self.ran = time.monotonic(), float(self.r.get("ran_s") or 0)
        self.lost, self.done = False, threading.Event()
        self.limit = settings(cfg)["max_minutes"] * 60
        threading.Thread(target=self._beat, daemon=True, name=f"research {rid} heartbeat").start()

    def _beat(self):
        while not self.done.wait(HEARTBEAT):
            try:
                if not beat(self.db, self.rid, self.owner):
                    self.lost = True
                    return
                self.db.q("UPDATE $r SET ran_s = $s", r=R("research", self.rid), s=round(self.elapsed(), 1))
            except Exception:  # noqa: BLE001 - the next beat tries again; a claim that goes stale is taken over
                log.warning("research %s: no heartbeat", self.rid, exc_info=True)

    def end(self):
        self.done.set()
        try:
            self.db.q("UPDATE $r SET ran_s = $s", r=R("research", self.rid), s=round(self.elapsed(), 1))
        except Exception:  # noqa: BLE001 - only the time it ran is lost
            log.warning("research %s: couldn't save the time it ran", self.rid, exc_info=True)

    def elapsed(self):
        return self.ran + time.monotonic() - self.t0

    def tick(self):
        """Between steps: whether another process took the run over, or the person asked it to stop."""
        if self.lost:
            raise Stopped("lost")
        row = self.db.one("SELECT stop_requested FROM $r", r=R("research", self.rid)) or {}
        if row.get("stop_requested"):
            raise Stopped("stopped")

    def out_of_time(self):
        return self.elapsed() >= self.limit


def run(db, cfg, rid, owner=None):
    """Claim the run and carry it out, from its last finished stage, until it's done, held, stopped or failed. False
    when another process has it (or it isn't waiting for one)."""
    owner = owner or uuid.uuid4().hex
    r = get(db, rid)
    if r["status"] not in ("queued", "running") or not claim(db, rid, owner):
        return False
    try:
        r = get(db, rid)  # as it is now we hold it
        if r["status"] not in ("queued", "running"):
            return False
        if r["status"] == "queued" and _busy(db, r["account"], rid):
            return False  # their other run first; the sweep starts this one after it
        db.q(
            "UPDATE $r SET status = 'running', started_at = started_at ?? $t, updated_at = $t",
            r=R("research", rid),
            t=store.now(),
        )
        turn = Run(db, cfg, rid, owner)
        try:
            with activity.scope(db, f"chat:{r['chat']}", f"research:{rid}", cfg=cfg):
                _stages(db, cfg, turn)
        finally:
            turn.end()
        return True
    except Exception as e:  # noqa: BLE001 - a run that breaks says why, keeps what it found, and frees the person's turn
        log.exception("research %s failed", rid)
        why = str(e) if isinstance(e, llm.LLMError) else f"{type(e).__name__}: {e}"
        took = db.rows(
            "UPDATE $r SET status = 'failed', error = $e, finished_at = $t, updated_at = $t WHERE status = 'running' RETURN id",
            r=R("research", rid),
            e=why[:500],
            t=store.now(),
        )
        if took:
            try:
                _save_found(db, cfg, get(db, rid), "error")
            except Exception:  # noqa: BLE001 - the run's row keeps its findings and the error
                log.exception("research %s: couldn't save what it found", rid)
        return True
    finally:
        release(db, rid, owner)
        _next(db, cfg, rid)


def _next(db, cfg, rid):
    """The person's next approved run, once this one is over."""
    r = db.one("SELECT account, status FROM $r", r=R("research", rid)) or {}
    if r.get("status") in ("running", "held", "queued") or not r.get("account"):
        return
    nxt = db.one("SELECT record::id(id) AS id FROM research WHERE account = $a AND status = 'queued' ORDER BY id LIMIT 1", a=r["account"])
    if nxt:
        launch(db, cfg, nxt["id"])


def _hold(db, rid, got):
    """A budget is over: the run waits (on_over ask), saying why, or stops with what it has (skip)."""
    if got["action"] == "skip":
        raise Stopped("budget")
    db.q(
        "UPDATE $r SET status = 'held', held = $h, updated_at = $t",
        r=R("research", rid),
        h={"why": got["why"], "budget": got["resource"]},
        t=store.now(),
    )


def _stages(db, cfg, run):
    rid = run.rid
    account = account_of(db, run.r["account"])
    if not account:
        raise RuntimeError("the person who asked for it can't sign in any more")
    try:
        while True:
            r = get(db, rid)
            stage = r.get("stage") or "gather"
            if stage == "done":
                return
            got = _over(db, cfg, r)
            if got:
                _hold(db, rid, got)
                return
            if stage == "gather":
                _gather(db, cfg, run, r, account)
                db.q("UPDATE $r SET stage = 'write'", r=R("research", rid))
            elif stage == "write":
                _write(db, cfg, run, r)
                db.q("UPDATE $r SET stage = 'check'", r=R("research", rid))
            elif stage == "check":
                _check(db, cfg, run, r)
                db.q("UPDATE $r SET stage = 'done'", r=R("research", rid))
                _finish(db, cfg, run, get(db, rid), "timeout" if r.get("ended") == "timeout" else None)
                return
    except activity.OverBudget as e:  # the overall budget's hard limit, reached mid-stage: it waits, as before a stage
        _hold(db, rid, {"action": "ask", "why": str(e), "resource": budgets.ALL})
    except Stopped as s:
        if s.why == "lost":  # another process has it now, and goes on from the last finished stage
            return
        db.q(
            "UPDATE $r SET status = 'stopped', ended = $w, stop_requested = false, finished_at = $t, updated_at = $t",
            r=R("research", rid),
            w=s.why,
            t=store.now(),
        )
        _save_found(db, cfg, get(db, rid))


def _model(db, cfg, r):
    """The model the run uses: research.model, else the conversation's (while it's still offered), else the configured one."""
    if settings(cfg)["model"]:
        return settings(cfg)["model"]
    m = r.get("model") or (db.one("SELECT model FROM $r", r=R("chat", r["chat"])) or {}).get("model")
    return m if m and m in chat.model_choices(cfg) else None


def _gather(db, cfg, run, r, account):
    """Each sub-question not yet answered, with its share of the tool calls that are left."""
    p = r["plan"]
    findings = list(r.get("findings") or [])
    used = int((r.get("used") or {}).get("tool_calls") or 0)
    model = _model(db, cfg, r)
    most = int(cfg["ai"].get("max_tool_calls") or 50)
    for i, sub in enumerate(p["sub_questions"]):
        if i < len(findings):
            continue
        if run.out_of_time():
            db.q("UPDATE $r SET ended = 'timeout'", r=R("research", run.rid))
            return
        run.tick()
        left = max(0, p["max_tool_calls"] - used)
        if left <= 0:
            break
        share = max(1, min(most, left // (len(p["sub_questions"]) - i)))
        box = toolbox(db, cfg, r, account, sub)
        q = FIND.format(goal=p["goal"], sub=sub)
        record = chat.Record(cfg, model, r.get("scope"), picked=p["sources"], tool_calls=share, research=run.rid)
        text, steps = "", []
        try:
            for kind, data in chat.tool_answer(cfg, box, q, (), share, model, record=record, max_tool_calls=share):
                if kind == "step":
                    steps.append(data)
                    db.q(
                        "UPDATE $r SET used = $u, sources_now = $s",
                        r=R("research", run.rid),
                        u={"tool_calls": used + record.used["tool_calls"]},
                        s=len(box.refs),
                    )
                    run.tick()
                    if run.out_of_time():
                        break
                else:
                    text = data
            cited = box.cited(text)
        except llm.ToolsUnsupported:  # a model that can't call tools: answered from a search of the archive
            passages = chat.retrieve(db, sub, box.readable, r.get("scope"), cfg=cfg)
            text = llm.chat(cfg, chat.messages_for(q, passages, cfg=cfg), model=model)
            cited = [x for x in chat.cited(text, passages) if x["used"]]
        used += record.used["tool_calls"]
        findings.append(
            {"question": sub, "text": text or "(nothing found)", "passages": cited, "tool_calls": record.used["tool_calls"], "steps": steps}
        )
        db.q(
            "UPDATE $r SET findings = $f, used = $u, sources_now = 0, updated_at = $t",
            r=R("research", run.rid),
            f=findings,
            u={"tool_calls": used},
            t=store.now(),
        )
        if run.out_of_time():
            db.q("UPDATE $r SET ended = 'timeout'", r=R("research", run.rid))
            return


def _key(p):
    return (p.get("url") or "", p.get("recording_id"), p.get("t0"), p.get("page"))


def renumber(findings):
    """Every finding's citations as one numbered list: (the passages, each finding's text cited by their new numbers).
    The same moment or page cited in two findings keeps one number."""
    out, at, texts = [], {}, []
    for f in findings:
        local = {}
        for x in f.get("passages") or []:
            k = _key(x)
            if k not in at:
                at[k] = len(out) + 1
                out.append({**{k2: v for k2, v in x.items() if k2 != "used"}, "n": at[k]})
            local[x["n"]] = at[k]
        texts.append(re.sub(r"\[(\d+)\]", lambda m: f"[{local[int(m.group(1))]}]" if int(m.group(1)) in local else "", f.get("text") or ""))
    return out, texts


def _write(db, cfg, run, r):
    """The report: one call, from every finding, following the outline."""
    p = r["plan"]
    passages, texts = renumber(r.get("findings") or [])
    run.tick()
    if not passages and not any(t.strip() for t in texts):
        report = f"# {p['title']}\n\nNothing turned up for this goal in {', '.join(SOURCE_LABELS[s] for s in p['sources'])}."
    else:
        found = "\n\n".join(
            f"## {f['question']}\n" + untrusted.wrap(t, "research finding", cfg) for f, t in zip(r.get("findings") or [], texts)
        )
        outline = "\n".join(f"- {s}" for s in p.get("report_outline") or []) or "- (your choice)"
        user = f"Goal: {p['goal']}\nTitle: {p['title']}\nOutline:\n{outline}\n\nFindings:\n\n{found}\n\nExcerpts:\n\n" + (
            chat.excerpts(passages, cfg) or "(none)"
        )
        if r.get("ended") == "timeout":
            user += "\n\nThe research ran out of time before every sub-question was looked into: say which weren't."
        report = llm.chat(
            cfg,
            [{"role": "system", "content": WRITE_SYSTEM + untrusted.rules(cfg)}, {"role": "user", "content": user}],
            model=_model(db, cfg, r),
        )
    db.q("UPDATE $r SET report = $t, passages = $p, updated_at = $n", r=R("research", run.rid), t=report, p=passages, n=store.now())


def _check(db, cfg, run, r):
    """Each cited claim of the report against its excerpts; an unsupported one is rewritten once, or marked."""
    report, passages = r.get("report") or "", r.get("passages") or []
    run.tick()
    if not passages:
        db.q("UPDATE $r SET check = $c", r=R("research", run.rid), c={"claims": 0, "supported": 0, "verdicts": [], "uncited": []})
        return
    got = chat.check_sources(cfg, report, passages)
    bad = [v["claim"] for v in got["verdicts"] if not v.get("supported") and v.get("claim") in report]
    if bad:
        run.tick()
        try:
            fixes = llm.json_out(
                cfg,
                REWRITE_SYSTEM + untrusted.rules(cfg),
                f"Excerpts:\n\n{chat.excerpts(passages, cfg)}\n\nSentences:\n" + "\n".join(f"- {c}" for c in bad),
                REWRITE_SCHEMA,
                model=_model(db, cfg, r),
            )["rewrites"]
        except llm.LLMError:
            fixes = []
        by = {f.get("claim"): (f.get("text") or "").strip() for f in fixes if isinstance(f, dict)}
        for c in bad:
            new = by.get(c)
            report = report.replace(c, new if new and new != c else c + UNSUPPORTED, 1)
    db.q("UPDATE $r SET report = $t, check = $c", r=R("research", run.rid), t=report, c={**got, "rewritten": len(bad)})


def _record(db, cfg, r, outcome):
    """The report's record (chat.Record): the model, the plan's sources and tool calls, what it used, how it ended,
    and the tokens and cost of every call the run made (research:<id>)."""
    model = _model(db, cfg, r)
    rec = chat.Record(
        cfg,
        model,
        r.get("scope"),
        picked=(r.get("plan") or {}).get("sources"),
        tool_calls=(r.get("plan") or {}).get("max_tool_calls"),
        research=r["id"],
    )
    rec.offered = sorted({t for s in (r.get("plan") or {}).get("sources") or [] for t in SOURCE_TOOLS.get(s, [])})
    rec.used = {"rounds": len(r.get("findings") or []), "tool_calls": int((r.get("used") or {}).get("tool_calls") or 0)}
    meta = rec.meta(db, outcome, f"research:{r['id']}")
    meta["ms"] = int(float(r.get("ran_s") or 0) * 1000)
    return meta


def _steps(r):
    return [s for f in r.get("findings") or [] for s in f.get("steps") or []]


ANSWERING_WAIT = 120  # seconds a report waits for an answer being written in the conversation to end


def _where(db, cid):
    """Where the report goes in the conversation: after its last message, once an answer being written there ends (a
    question waiting for its answer isn't followed by the report)."""
    end = time.monotonic() + ANSWERING_WAIT
    while time.monotonic() < end and (db.one("SELECT answering FROM $r", r=R("chat", cid)) or {}).get("answering"):
        time.sleep(1)
    return chat.head(db, cid)


def _answer(db, cfg, r, text, passages, outcome, stopped=False):
    """The report saved in the conversation, where it is now, as an answer of kind research with its record."""
    parent = _where(db, r["chat"])
    mid = db.next_id("chat_message")
    meta = _record(db, cfg, r, outcome)
    wrote = _model(db, cfg, r) or (cfg["llm"].get("model") if llm.configured(cfg) else None)
    chat.add(
        db,
        r["chat"],
        "assistant",
        text,
        chat.cited(text, passages),
        stopped=stopped,
        steps=_steps(r),
        model=wrote,
        parent=parent,
        mid=mid,
        meta=meta,
        kind="research",
    )
    db.q("UPDATE $r SET answer = $m, updated_at = $t", r=R("research", r["id"]), m=mid, t=store.now())
    return mid


def _finish(db, cfg, run, r, ended):
    _answer(db, cfg, r, r.get("report") or "(no report)", r.get("passages") or [], "limit" if ended else "answered")
    db.q("UPDATE $r SET status = 'done', finished_at = $t, updated_at = $t", r=R("research", run.rid), t=store.now())


def _save_found(db, cfg, r, outcome="stopped"):
    """A run that stopped (or broke off: `outcome` error) keeps what it found: its report when it got that far, else its
    findings, saved as the answer without another model call."""
    if r.get("answer"):
        return
    passages, texts = renumber(r.get("findings") or [])
    said = "Stopped" if outcome == "stopped" else f"It broke off ({r.get('error') or 'an error'})"
    if r.get("report"):
        text, passages = r["report"], r.get("passages") or passages
    elif any(t.strip() for t in texts):
        text = f"# {r['plan']['title']}\n\n{said}. What it found so far:\n\n" + "\n\n".join(
            f"## {f['question']}\n{t}" for f, t in zip(r.get("findings") or [], texts)
        )
    else:
        text = f"# {r['plan']['title']}\n\n{said} before it found anything."
    _answer(db, cfg, r, text, passages, outcome, stopped=outcome == "stopped")


# ---------- after: a note, a podcast ----------
def mention(p):
    """A citation as a note links it: an asset's moment as a mention, a web page as a link."""
    if p.get("source") == "web" and p.get("url"):
        return f"[{_label(p.get('title') or p.get('site') or p['url'])}]({p['url']})"
    if p.get("recording_id") is not None:
        return f"@[{_label(p.get('title') or 'Asset')}](recording:{p['recording_id']})"
    return ""


def _label(text):
    return re.sub(r"[\[\]\n]", " ", str(text or "")).strip()[:200] or "source"


def note_body(r, report, passages, title):
    """The report as a note: each [n] becomes the link to what it cites, and the note links back to the conversation."""
    by = {p["n"]: p for p in passages}

    def cite(m):
        p = by.get(int(m.group(1)))
        return f" ({mention(p)})" if p and mention(p) else ""

    body = re.sub(r"\s*\[(\d+)\]", cite, report or "")
    return body.rstrip() + f"\n\n@[Research: {_label(title)}](chat:{r['chat']})\n"


def note_spaces(db, r, editable):
    """The namespaces the note can go in: the scope's (or, when it's over everything, those of the archive it cites),
    of those the person can edit."""
    names = store.space_names(db)
    scoped = (r.get("scope") or {}).get("namespaces") or []
    if scoped:
        ids = [sid for sid, n in names.items() if n in scoped]
    else:
        recs = [p["recording_id"] for p in r.get("passages") or [] if p.get("recording_id") is not None]
        ids = sorted(
            {x["space"] for x in db.rows("SELECT space FROM recording WHERE id IN $ids", ids=[R("recording", i) for i in recs])}
            if recs
            else set()
        )
        if not ids:
            ids = sorted(editable)
    return [sid for sid in ids if sid in editable]


def to_note(db, r, account, editable, namespace=None):
    """Save the report as a notebook page; its id and namespace. ValueError when it has none yet, or the namespace
    must be picked (several are possible) or can't be written."""
    if not r.get("answer") and not r.get("report"):
        raise ValueError("the report isn't ready yet")
    report = _report_text(db, r)
    choices = note_spaces(db, r, editable)
    names = store.space_names(db)
    if namespace:
        sid = next((s for s, n in names.items() if n == namespace), None)
        if sid is None or sid not in editable:
            raise PermissionError(f"you can't write notes in {namespace}")
    elif len(choices) == 1:
        sid = choices[0]
    elif not choices:
        raise PermissionError("you can't write notes in any namespace this research looked in")
    else:
        raise ValueError("pick a namespace: " + ", ".join(names[s] for s in choices))
    title = (r.get("plan") or {}).get("title") or "Research"
    pid = notebook.create(db, sid, account, title, note_body(r, report, r.get("passages") or [], title), author="assistant")
    db.q("UPDATE $r SET notes = array::union(notes ?? [], [$n])", r=R("research", r["id"]), n=pid)
    return {"page": pid, "namespace": names[sid]}


def _report_text(db, r):
    """The report as it was saved in the conversation (the person may have stopped the run)."""
    if r.get("answer"):
        m = db.one("SELECT content FROM $r", r=R("chat_message", r["answer"])) or {}
        if m.get("content"):
            return m["content"]
    return r.get("report") or ""


def podcast_sources(db, r, readable):
    """The assets the report cites, that the person can still read: an episode's selection."""
    used = {p["n"] for p in chat.cited(_report_text(db, r), r.get("passages") or []) if p["used"]}
    ids = list(
        dict.fromkeys(
            p["recording_id"] for p in r.get("passages") or [] if p.get("recording_id") is not None and (p["n"] in used or not used)
        )
    )
    if not ids:
        return []
    ok = {
        x["id"]
        for x in db.rows("SELECT record::id(id) AS id, space FROM recording WHERE id IN $ids", ids=[R("recording", i) for i in ids])
        if readable is None or x["space"] in readable
    }
    return [i for i in ids if i in ok]
