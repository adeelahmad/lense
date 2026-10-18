"""Projects (docs/projects.md): a goal, the namespaces it draws on, and the conversations working toward it.

A project is someone's own: its name, the goal they set, standing instructions, and one or more namespaces. Its
conversations are ordinary ones (chat.py) with `project` set, so they show in Chat too and answer the same way, in the
project's namespaces (each one's scope follows the project's when that changes):

- the coordinator, the project's own conversation, made with it (and made again if it was deleted): talking about the
  project as a whole, it is told about every thread;
- threads, each about one part of the work; one marked done stays to read. A thread can start with a brief (from the
  person, an agent or the coordinator's start_thread), which the assistant answers in the background (work), with the
  person's tools and approvals; a process that stops mid-answer leaves the thread marked by the sweep, not stuck.

Every answer in a project's conversation reads what the project is (projects.note): its name, goal and instructions,
which conversation this is, and, for the coordinator, the threads. The goal and instructions are the person's own
words; thread titles and what threads said are marked as data, as they may carry what a document said (untrusted.py).

Light by design: no model calls of its own and no index beyond the conversation's project.

Refine later: members sharing a project, a project's own memory, its outputs (notes, files) listed together, chat
rooms and email routed to a project, a project template.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading

from . import activity, auth, chat, llm, store, untrusted

log = logging.getLogger("lens")

R = store.R
NAME_MAX = 120
GOAL_MAX = 4000
INSTRUCTIONS_MAX = 4000
SPACES_MAX = 20
PER_ACCOUNT = 500
THREADS_MAX = 500  # per project
NOTE_THREADS = 30  # threads the coordinator is told about, the most recent first
LAST_CHARS = 300  # of a thread's last message, in the coordinator's note and in listings
BRIEF_MAX = 4000
STARTS_PER_ANSWER = 3  # threads one answer of the coordinator may start
BRIEF_STEPS = 12  # rounds of tool calls a brief's answer may take, at most
STALE_SECONDS = 600  # a brief's answer with no sign of life for this long was cut off (the process stopped)
SWEEP_SECONDS = 60
STOPPED = "Lens stopped before this was answered: ask again."
FIELDS = (
    "record::id(id) AS id, account, name, goal ?? '' AS goal, instructions ?? '' AS instructions, spaces ?? [] AS spaces, "
    "coordinator, archived ?? false AS archived, created_at, updated_at"
)


def _name(name):
    name = " ".join(str(name or "").split())
    if not name:
        raise ValueError("A project needs a name.")
    if len(name) > NAME_MAX:
        raise ValueError(f"A project's name can have up to {NAME_MAX} characters.")
    return name


def _text(text, most, what):
    text = str(text or "").strip()
    if len(text) > most:
        raise ValueError(f"The {what} can have up to {most} characters.")
    return text


def _spaces(spaces):
    out = list(dict.fromkeys(int(s) for s in spaces or []))
    if not out:
        raise ValueError("A project draws on one namespace or more: name at least one.")
    if len(out) > SPACES_MAX:
        raise ValueError(f"A project draws on up to {SPACES_MAX} namespaces.")
    return out


def scope_of(db, p):
    """The scope of the project's conversations: its namespaces, by name (those that still exist)."""
    names = store.space_names(db)
    return {"namespaces": sorted(names[s] for s in p.get("spaces") or [] if s in names)}


def namespaces(db, p):
    names = store.space_names(db)
    return [names[s] for s in p.get("spaces") or [] if s in names]


# ---------- projects ----------
def create(db, account, name, goal, spaces, instructions=""):
    """A project of `account`'s, with its coordinator conversation. `spaces`: namespace ids the caller checked they can
    read."""
    name, goal = _name(name), _text(goal, GOAL_MAX, "goal")
    instructions, spaces = _text(instructions, INSTRUCTIONS_MAX, "instructions"), _spaces(spaces)
    if len(db.values("SELECT VALUE id FROM project WHERE account = $a", a=account)) >= PER_ACCOUNT:
        raise ValueError(f"You have {PER_ACCOUNT} projects already: delete some first.")
    pid = db.next_id("project")
    t = store.now()
    db.q(
        "CREATE $r CONTENT $d",
        r=R("project", pid),
        d=store.clean(
            {
                "account": account,
                "name": name,
                "goal": goal,
                "instructions": instructions,
                "spaces": spaces,
                "archived": False,
                "created_at": t,
                "updated_at": t,
            }
        ),
    )
    coordinator(db, get(db, pid, account))
    return pid


def get(db, pid, account):
    """The project, if it's `account`'s; KeyError otherwise."""
    p = db.one(f"SELECT {FIELDS} FROM $r", r=R("project", int(pid)))
    if not p or p["account"] != account:
        raise KeyError(pid)
    return p


def mine(db, account, archived=None):
    """`account`'s projects, the most recently changed first; `archived` True or False for only those."""
    rows = db.rows(f"SELECT {FIELDS} FROM project WHERE account = $a", a=account)
    if archived is not None:
        rows = [p for p in rows if p["archived"] == archived]
    rows.sort(key=lambda p: (p.get("updated_at") or "", p["id"]), reverse=True)
    return rows


def update(db, pid, account, name=None, goal=None, instructions=None, spaces=None, archived=None):
    """Change what is given; the rest stays. New namespaces become every one of its conversations' scope."""
    p = get(db, pid, account)
    sets = {"updated_at": store.now()}
    if name is not None:
        sets["name"] = _name(name)
    if goal is not None:
        sets["goal"] = _text(goal, GOAL_MAX, "goal")
    if instructions is not None:
        sets["instructions"] = _text(instructions, INSTRUCTIONS_MAX, "instructions")
    if spaces is not None:
        sets["spaces"] = _spaces(spaces)
    if archived is not None:
        sets["archived"] = bool(archived)
    db.q("UPDATE $r MERGE $d", r=R("project", int(pid)), d=sets)
    p = get(db, pid, account)
    if spaces is not None:
        db.q("UPDATE chat SET scope = $s WHERE project = $p", s=scope_of(db, p), p=int(pid))
    if name is not None and p.get("coordinator") is not None:  # the coordinator is called after the project
        db.q("UPDATE $r SET title = $t WHERE project = $p", r=R("chat", p["coordinator"]), t=p["name"], p=int(pid))
    return p


def delete(db, pid, account):
    """Delete the project. Its conversations stay, as ordinary ones in Chat."""
    get(db, pid, account)
    db.run(["UPDATE chat SET project = NONE, done = NONE WHERE project = $p", "DELETE $r"], p=int(pid), r=R("project", int(pid)))


def touch(db, pid):
    db.q("UPDATE $r SET updated_at = $t", r=R("project", int(pid)), t=store.now())


# ---------- its conversations ----------
def coordinator(db, p):
    """The project's own conversation, made when it has none (or its was deleted)."""
    cid = p.get("coordinator")
    if cid is not None and db.one("SELECT id FROM $r WHERE project = $p", r=R("chat", cid), p=p["id"]):
        return cid
    cid = chat.create(db, p["account"], p["name"], scope_of(db, p), title_by="person")
    db.run(
        ["UPDATE $c SET project = $p", "UPDATE $r SET coordinator = $id"],
        c=R("chat", cid),
        p=p["id"],
        r=R("project", p["id"]),
        id=cid,
    )
    p["coordinator"] = cid
    return cid


def start_thread(db, p, title=None, model=None, tools=None, max_tool_calls=None):
    """A new thread in the project: a conversation in its namespaces, titled `title` (else after its first answer)."""
    if len(db.values("SELECT VALUE id FROM chat WHERE project = $p", p=p["id"])) >= THREADS_MAX:
        raise ValueError(f"This project has {THREADS_MAX} conversations already: delete some first.")
    title = " ".join(str(title or "").split())[:120] or None
    cid = chat.create(db, p["account"], title, scope_of(db, p), model, "chat", tools, max_tool_calls, "person" if title else None)
    db.q("UPDATE $r SET project = $p", r=R("chat", cid), p=p["id"])
    touch(db, p["id"])
    return cid


def thread(db, p, cid):
    """One of the project's conversations ({id, title, done, ...}); KeyError when it isn't one."""
    row = db.one(
        "SELECT record::id(id) AS id, account, project, title, done ?? false AS done, answering ?? false AS answering, "
        "created_at, updated_at FROM $r",
        r=R("chat", int(cid)),
    )
    if not row or row.get("project") != p["id"] or row["account"] != p["account"]:
        raise KeyError(cid)
    return row


def _last(db, heads):
    """{conversation: its last message ({role, text, at})}, from {conversation: its head (the message showing last)}."""
    if not heads:
        return {}
    out = {}
    for m in db.rows(
        "SELECT chat, role, content, created_at FROM chat_message WHERE id IN $ids",
        ids=[R("chat_message", h) for h in heads.values() if h is not None],
    ):
        text = " ".join(str(m.get("content") or "").split())
        out[m["chat"]] = {
            "role": m["role"],
            "text": text if len(text) <= LAST_CHARS else text[: LAST_CHARS - 1] + "…",
            "at": m.get("created_at"),
        }
    return out


def threads(db, p, coordinator_too=False):
    """The project's threads, the most recently added to first: each one's id, title, done, whether an answer is being
    written, when, how many messages and its last one. With `coordinator_too`, the coordinator is one of them."""
    rows = db.rows(
        "SELECT record::id(id) AS id, title, title_by, head, done ?? false AS done, answering ?? false AS answering, "
        "created_at, updated_at FROM chat WHERE project = $p AND account = $a",
        p=p["id"],
        a=p["account"],
    )
    if not coordinator_too:
        rows = [t for t in rows if t["id"] != p.get("coordinator")]
    rows.sort(key=lambda t: (t.get("updated_at") or "", t["id"]), reverse=True)
    counts = {}
    if rows:
        for m in db.rows("SELECT chat, count() AS n FROM chat_message WHERE chat IN $c GROUP BY chat", c=[t["id"] for t in rows]):
            counts[m["chat"]] = m["n"]
    last = _last(db, {t["id"]: t.pop("head", None) for t in rows})
    for t in rows:
        t["messages"] = counts.get(t["id"], 0)
        t["last"] = last.get(t["id"])
        t["coordinator"] = t["id"] == p.get("coordinator")
    return rows


def set_done(db, p, cid, done=True):
    """Mark a thread done (it stays to read and to go on with), or not done."""
    thread(db, p, cid)
    db.q("UPDATE $r SET done = $d", r=R("chat", int(cid)), d=bool(done) or None)
    touch(db, p["id"])
    return thread(db, p, cid)


def adopt(db, p, cid):
    """Bring one of the person's own conversations into the project: it draws on the project's namespaces from now on."""
    row = db.one("SELECT account, project FROM $r", r=R("chat", int(cid)))
    if not row or row["account"] != p["account"]:
        raise KeyError(cid)
    if row.get("project") not in (None, p["id"]):
        raise ValueError("That conversation is in another project.")
    db.q("UPDATE $r SET project = $p, scope = $s", r=R("chat", int(cid)), p=p["id"], s=scope_of(db, p))
    touch(db, p["id"])
    return thread(db, p, cid)


def release(db, p, cid):
    """Take a thread out of the project: it stays, as an ordinary conversation in Chat."""
    if int(cid) == p.get("coordinator"):
        raise ValueError("The coordinator is the project's own conversation: delete the project instead.")
    thread(db, p, cid)
    db.q("UPDATE $r SET project = NONE, done = NONE", r=R("chat", int(cid)))
    touch(db, p["id"])


def for_chat(db, cid):
    """The project a conversation is in (as get gives it), or None."""
    if cid is None:
        return None
    row = db.one("SELECT account, project FROM $r", r=R("chat", int(cid)))
    if not row or row.get("project") is None:
        return None
    try:
        return get(db, row["project"], row["account"])
    except KeyError:
        return None


# ---------- briefs: a question answered in the background ----------
def brief(db, cfg, p, cid, text, by="person", via="api"):
    """Ask `text` in thread `cid`, where it goes on from, and answer it in the background (launch). `by`: person, or
    coordinator when the coordinator's start_thread wrote it (then it isn't taken for the person's own message). The
    question's id."""
    text = str(text or "").strip()
    if not text:
        raise ValueError("A brief says what to do.")
    if len(text) > BRIEF_MAX:
        raise ValueError(f"A brief can have up to {BRIEF_MAX} characters.")
    t = thread(db, p, cid)
    if t["answering"]:
        raise ValueError("An answer is being written in that thread: wait for it, or stop it.")
    c = db.one("SELECT title, title_by, head FROM $r", r=R("chat", int(cid))) or {}
    if c.get("head") is None and chat.lens_titles(c):
        chat.title_from_question(db, int(cid), text)
    origin = {"project": p["id"], "by": "coordinator"} if by == "coordinator" else None
    qid = chat.add(db, int(cid), "user", text, origin=origin, meta=chat.question_record(via))
    db.q(
        "UPDATE $r SET answering = true, stop_requested = false, working = $w",
        r=R("chat", int(cid)),
        w={"question": qid, "beat": store.now()},
    )
    touch(db, p["id"])
    launch(db, cfg, int(cid), qid)
    return qid


def launch(db, cfg, cid, qid):
    """Answer the question in the background, on a thread of its own."""
    threading.Thread(target=work, args=(db, cfg, cid, qid), daemon=True, name=f"project thread {cid}").start()


def _account(db, aid):
    a = auth.get_account(db, aid)
    if not a or a.get("disabled") or a.get("removed_at"):
        return None
    return {"id": a["id"], "email": a["email"], "admin": bool(a.get("admin"))}


def _beat(db, cid, qid):
    db.q("UPDATE $r SET working.beat = $t WHERE working.question = $q", r=R("chat", cid), t=store.now(), q=qid)


def _end(db, cid):
    db.q("UPDATE $r SET answering = false, stop_requested = false, working = NONE", r=R("chat", cid))


def work(db, cfg, cid, qid):
    """Answer question `qid` of conversation `cid` as its person, with the tools they have there (the conversation's
    choice of them) and the same approvals: the tool loop (chat.tool_answer), every call counted for the answer in the
    activity ledger, the answer saved with its record. Stop (POST /chats/{cid}/stop) stops it between steps."""
    from . import ai_tools

    try:
        c = db.one(
            "SELECT account, scope, model, tools, max_tool_calls, title, title_by FROM $r WHERE working.question = $q",
            r=R("chat", cid),
            q=qid,
        )
        if not c:
            return
        q = chat.said(db, cid, qid, "user")
        acc = _account(db, c["account"])
        mid = db.next_id("chat_message")
        steps, text, how, error, notice = [], "", "answered", None, None
        record = chat.Record(cfg, c.get("model"), c.get("scope"), c.get("tools"), c.get("max_tool_calls"))
        if acc is None:
            text, how, error = "(no answer)", "error", "the person this thread is for can't sign in anymore"
        elif not llm.configured(cfg):
            text, how, error = "(no answer)", "error", "no model is set up: pick one in Settings › Model"
        else:
            roles = auth.roles(db, acc)
            readable, editable = set(roles), {s for s in roles if auth.allows(roles, s, "editor")}
            past = chat.path(db, cid, q.get("parent")) if q.get("parent") is not None else []
            summary = chat.memory(db, cfg, cid, past)
            box = ai_tools.Toolbox(
                db,
                cfg,
                {"id": acc["id"], "email": acc["email"]},
                readable,
                editable,
                c.get("scope"),
                cid,
                None,
                acc["admin"],
                said=q["content"],
                # a brief the coordinator wrote may carry what it read: tools that send data out ask first
                outside=(["the coordinator's brief"] if (q.get("origin") or {}).get("by") == "coordinator" else [])
                + ai_tools.read_before(past, summary),
                only=c.get("tools"),
            )
            on = chat.Answering(db, cid)  # what POST /chats/{cid}/stop asks of it (_end clears it after)
            try:
                with activity.scope(db, f"chat:{cid}", f"chat_message:{mid}", cfg=cfg):
                    steps_max = max(int(cfg["ai"].get("max_steps") or 6), 6)
                    for kind, data in chat.tool_answer(
                        cfg,
                        box,
                        q["content"],
                        past,
                        min(steps_max, BRIEF_STEPS),
                        c.get("model"),
                        summary=summary,
                        record=record,
                        max_tool_calls=c.get("max_tool_calls"),
                    ):
                        if kind == "step":
                            steps.append(data)
                            _beat(db, cid, qid)
                            if on.stop_requested(force=True):
                                text, how, notice = text or "(stopped)", "stopped", None
                                break
                        else:
                            text, how = data, "direct" if kind == "direct" else "answered"
                    else:
                        box.after_answer(text)
            except llm.ToolsUnsupported:
                text, how, error = "(no answer)", "error", "this model can't use tools: pick one that can in Settings › Model"
            except (llm.LLMError, activity.OverBudget) as e:
                text, how, error = text or "(no answer)", "error", str(e)
            cited = box.cited(text) if how != "error" else []
            with activity.scope(db, f"chat:{cid}", f"chat_message:{mid}", cfg=cfg):
                meta = record.meta(db, how, f"chat_message:{mid}")
            wrote = c.get("model") or cfg["llm"].get("model")
            chat.add(
                db,
                cid,
                "assistant",
                text or "(no answer)",
                cited,
                steps=steps,
                notice=notice,
                error=error,
                model=wrote,
                parent=qid,
                mid=mid,
                meta=meta,
                stopped=how == "stopped",
            )
            if how not in ("error", "stopped") and q.get("parent") is None and chat.lens_titles(c):
                chat.auto_title(db, cfg, cid, q["content"], text)
            return
        chat.add(db, cid, "assistant", text, [], steps=steps, error=error, parent=qid, mid=mid, meta=record.meta(db, how))
    except Exception:  # noqa: BLE001 - the thread says so rather than waiting for an answer that won't come
        log.exception("project thread %s: the brief's answer failed", cid)
        try:
            chat.add(db, cid, "assistant", "(no answer)", [], error="Something went wrong while answering. Try again.", parent=qid)
        except Exception:  # noqa: BLE001
            log.warning("project thread %s: couldn't save the error", cid, exc_info=True)
    finally:
        _end(db, cid)


def sweep(db, cfg, log=None):
    """Briefs whose answer stopped with its process (no step for STALE_SECONDS): the thread says so and is free again."""
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=STALE_SECONDS)).isoformat(timespec="seconds")
    for row in db.rows("SELECT record::id(id) AS id, working FROM chat WHERE working != NONE AND working.beat < $c", c=cutoff):
        qid = (row.get("working") or {}).get("question")
        took = db.rows(
            "UPDATE $r SET working = NONE, answering = false WHERE working.question = $q RETURN id", r=R("chat", row["id"]), q=qid
        )
        if took and qid is not None:
            chat.add(db, row["id"], "assistant", "(no answer)", [], error=STOPPED, parent=qid)
            if log:
                log(f"project thread {row['id']}: its brief's answer was cut off")


# ---------- what an answer in a project is told ----------
def note(db, p, cid, cfg=None):
    """What the model is told in one of the project's conversations: the project, its goal and instructions (the
    person's own words), which conversation this is, and in the coordinator the threads (marked as data)."""
    here = "its coordinator: the conversation about the project as a whole" if cid == p.get("coordinator") else "one of its threads"
    out = (
        f"\n\nThis conversation is {here}, in the project “{p['name']}”, which draws on the namespaces "
        f"{', '.join(namespaces(db, p)) or '(none left)'}. Work toward the project's goal: plan, look things up, and propose "
        "what moves it on. Anything that changes data or settings still waits for the person's approval."
    )
    if p.get("goal"):
        out += f"\n\nThe project's goal, in its owner's words:\n{p['goal']}"
    if p.get("instructions"):
        out += f"\n\nInstructions for this project, from its owner:\n{p['instructions']}"
    if cid == p.get("coordinator"):
        rows = threads(db, p)[:NOTE_THREADS]
        lines = [
            f"- thread {t['id']}: {t['title']}"
            + (" (done)" if t["done"] else "")
            + (f" (last: {t['last']['text']})" if t.get("last") else "")
            for t in rows
        ]
        out += "\n\nThe project's threads, the most recent first:\n" + (
            untrusted.wrap("\n".join(lines), "project threads", cfg) if lines else "(none yet)"
        )
    return out
