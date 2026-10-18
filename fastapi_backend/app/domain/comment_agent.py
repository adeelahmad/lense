"""The agent in a comment thread (docs/timeline.md, Asking the agent in a comment): a comment on the timeline, or on
an asset, that tags @lens (or the assistant's own name) gets an answer from the agent in the same thread.

The agent answers as the person who tagged it, with their permissions, through the assistant's tool loop
(chat.tool_answer) in a conversation of theirs titled after the item, so they can open it and carry on there. It reads
the item the thread is on (the activity, marked as data where it holds text from outside Lens) and the thread so far.
It only reads: every change it proposes becomes an approval the person decides, and the reply says so with a link.
The reply is a comment by the assistant (`author` assistant) on the same thread, with its sources, so everyone who
sees the item reads it. Its model calls run in the ledger against the conversation and the item's namespace, and a
used-up budget stops it before it starts. `ai.answer_comments` (on) turns it off.
"""

from __future__ import annotations

import logging
import re
import threading

from . import actions_ai, activity, auth, bridge, budgets, chat, comments, events, llm, store

R = store.R
log = logging.getLogger(__name__)
NAME = "lens"
THREAD_MAX = 20  # earlier comments of the thread the agent reads
LINE_MAX = 600  # characters of each
MAX_STEPS = 6
NOTE = (
    "\n\nYou were tagged in a comment on {where} in Lens by {person}; everyone who sees the item reads the thread. "
    "Answer their comment briefly and plainly, in the thread's own words, citing what you found. Every change you "
    "propose waits for their approval; say so when you propose one."
)


def names(cfg):
    """The names a comment tags the agent by: lens, and the assistant's own name (Settings › Chat rooms)."""
    own = ((cfg.get("bridge") or {}).get("name") or "").strip().lower()
    return [NAME] + ([own] if own and own != NAME else [])


def _rx(cfg):
    alts = "|".join(re.escape(n) for n in names(cfg))
    return re.compile(rf"(?<![\w@])@({alts})\b", re.I)


def mentioned(cfg, text):
    """Whether the comment tags the agent."""
    return bool(_rx(cfg).search(str(text or "")))


def on(cfg):
    return bool((cfg.get("ai") or {}).get("answer_comments", True))


def question(cfg, text):
    """The comment without the tag, as the question."""
    return " ".join(_rx(cfg).sub("", str(text or "")).split()).strip() or "What do you make of this?"


class Box(actions_ai.Box):
    """The assistant's Toolbox for a comment: every change waits for the person (as an automation's does), and the
    model is told where it was tagged."""

    def read_from(self):
        said = "the agent was tagged in a comment; the person reads the thread, not the tool calls"
        return said + (f", and it read text from outside Lens: {', '.join(self.outside[:4])}" if self.outside else "")


def _person(db, account):
    u = db.one("SELECT record::id(id) AS id, email, name, admin, disabled FROM $r", r=R("account", int(account)))
    if not u or u.get("disabled"):
        raise ValueError("the person who asked is gone")
    return u


def _thread(db, cfg, c):
    """The item's earlier comments, as the model reads them: who said what, the tagged comment's own thread last so
    a fresh "@lens what is this about?" still sees what others said of the item."""
    root = c["parent"] if c.get("parent") is not None else c["id"]
    rows = comments.on_activity(db, c["activity"]) if c.get("activity") else comments.on(db, c["recording"])
    rows = [r for r in rows if r["id"] != c["id"] and r.get("created_at", "") <= c.get("created_at", "")]
    own = [r for r in rows if r["id"] == root or r.get("parent") == root]
    rows = ([r for r in rows if r not in own] + own)[-THREAD_MAX:]
    if not rows:
        return ""
    people = {
        a["id"]: a
        for a in db.rows(
            "SELECT record::id(id) AS id, email, name FROM account WHERE id IN $ids",
            ids=[R("account", a) for a in {r["account"] for r in rows}],
        )
    }
    lines = []
    for r in rows:
        who = (
            "Lens"
            if r.get("author") == "assistant"
            else (people.get(r["account"], {}).get("name") or people.get(r["account"], {}).get("email") or "Someone")
        )
        lines.append(f"- {who}: {' '.join(str(r.get('text') or '').split())[:LINE_MAX]}")
    return "\n\nThe comments on it so far:\n" + "\n".join(lines)


def _item(db, cfg, c, who):
    """(the activity or None, where the thread is, a page for the conversation, the namespace)."""
    a = events.get(db, cfg, c["activity"], who) if c.get("activity") else None
    if a:
        url = (a.get("object") or {}).get("url") if isinstance(a.get("object"), dict) else None
        page = {"url": str(url or "/timeline"), "title": actions_ai._short(a.get("summary") or a["id"], 300)}
        return a, "the timeline", page, a.get("space")
    if c.get("recording") is not None:
        rec = db.one("SELECT title, space FROM $r", r=R("recording", int(c["recording"]))) or {}
        title = actions_ai._short(rec.get("title") or f"asset {c['recording']}", 300)
        return None, f"the asset “{title}”", {"url": f"/resources/{c['recording']}", "title": title}, rec.get("space", c.get("space"))
    raise ValueError("the comment is on nothing the agent can read")


def answer(db, cfg, cid):
    """Answer the tagged comment `cid` in its thread, as the person who wrote it; the reply comment's id. The reply
    says why when the agent can't (no model, over budget, the model can't use tools)."""
    c = comments.get(db, cid)
    if c.get("author") == "assistant":
        return None
    u = _person(db, c["account"])
    roles = auth.roles(db, u)
    readable = set(roles)
    editable = {s for s in roles if auth.allows(roles, s, "editor")}
    who = events.viewer(u["id"], readable, admin=bool(u.get("admin")))
    a, where, page, space = _item(db, cfg, c, who)
    names_ = store.space_names(db)
    scope = {"namespaces": [names_[space]]} if space in names_ else {}
    root = c["parent"] if c.get("parent") is not None else c["id"]
    q = question(cfg, c["text"])
    person = u.get("name") or u["email"]

    def say(text):
        return comments.create(
            db,
            c.get("recording"),
            c.get("space"),
            u["id"],
            text,
            parent=root,
            activity=c.get("activity"),
            about=c.get("about"),
            audience=c.get("audience"),
            owner=c.get("owner"),
            author="assistant",
        )

    if not llm.configured(cfg):
        return say("I can't answer here yet: no model is set up (Settings › Model).")
    why = budgets.used_up(db, [f"space:{space}"] if space is not None else [])
    if why:
        return say(f"I can't answer now: over budget ({why}).")
    # one conversation per thread, the person's own, so later tags carry on in it
    head = comments.get(db, root)
    chat_id = head.get("chat")
    if chat_id is None or not db.one("SELECT id FROM $r", r=R("chat", int(chat_id))):
        chat_id = chat.create(db, u["id"], title=f"{where.capitalize() if a is None else 'Timeline'}: {page['title'][:100]}", scope=scope)
        comments.set_chat(db, root, chat_id)
    outside = (
        [actions_ai.OUTSIDE.replace("started this automation", "the thread is on")] if a and (a.get("lens") or {}).get("untrusted") else []
    )
    box = Box(
        db,
        cfg,
        {"id": u["id"], "email": u["email"]},
        readable,
        editable,
        scope,
        chat_id,
        admin=bool(u.get("admin")),
        said=q,
        outside=outside,
    )
    box.note = NOTE.format(where=where, person=person)
    chat.add(db, chat_id, "user", q, context=page, meta=chat.question_record("comment", comment=c["id"]))
    mid = db.next_id("chat_message")
    record, spent = chat.Record(cfg, scope=scope), f"chat_message:{mid}"
    prompt = q + (actions_ai._context(a, cfg) if a else f"\n\nThis thread is on {where} ({page['url']}).") + _thread(db, cfg, c)
    steps, text, how = [], None, "answered"

    def said(words, cited, outcome, **kw):
        chat.add(db, chat_id, "assistant", words or "(no answer)", cited, steps=steps, mid=mid, meta=record.meta(db, outcome, spent), **kw)

    try:
        with activity.scope(db, f"chat:{chat_id}", spent, cfg=cfg):
            for kind, data in chat.tool_answer(cfg, box, prompt, (), MAX_STEPS, record=record):
                if kind == "step":
                    steps.append(data)
                else:
                    text, how = data, "direct" if kind == "direct" else "answered"
    except llm.ToolsUnsupported:
        said("", [], "error", error="this model can't use tools")
        return say("I can't answer here: this model can't use tools (pick one that can in Settings › Model).")
    except (llm.LLMError, activity.OverBudget) as e:
        said("", [], "error", error=str(e))
        return say(f"I couldn't answer: {e}")
    with activity.scope(db, f"chat:{chat_id}", spent, cfg=cfg):
        box.after_answer(text or "")
    said(text, box.cited(text), how, model=cfg["llm"].get("model"))
    reply = (text or "").strip() or "(no answer)"
    approvals = [a_ for a_ in box.approvals if isinstance(a_, dict)]
    if approvals:
        reply += "\n\n" + bridge._web_line(cfg, db, chat_id, approvals)
    sources = bridge.sources(db, cfg, chat_id)
    if sources:
        reply += "\n\n" + sources
    return say(bridge.safe_links(cfg, db, reply, box.links))


def later(db, cfg, cid):
    """Answer `cid` on a thread of its own, when the comment tags the agent and answering is on; whether it will."""
    if not on(cfg):
        return False

    def run():
        try:
            answer(db, cfg, cid)
        except Exception:  # noqa: BLE001 - the thread stays as it is; the log says why
            log.exception("comment %s: the agent couldn't answer", cid)

    threading.Thread(target=run, daemon=True, name=f"comment-agent-{cid}").start()
    return True


def wake(db, cfg, cid, text):
    """After a comment is made: answer it later when it tags the agent."""
    return mentioned(cfg, text) and later(db, cfg, cid)
