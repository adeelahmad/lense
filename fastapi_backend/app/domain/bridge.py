"""The assistant in chat rooms, through Matterbridge (https://github.com/42wim/matterbridge): Slack, Discord,
Mattermost, Telegram, Matrix, IRC, WhatsApp and the rest, by way of one Matterbridge API account.

A thread in one server process (whichever holds the lease) reads what was said in the bridged rooms
(GET /api/messages), answers what was said to it (a message naming it, or every message when bridge.answer is "all")
and posts the answer back to the same gateway (POST /api/message). It answers as one Lens account (bridge.account):
it reads what that account can read, and changes anything only after someone approves it in the web app, like any
chat. Each person in each room is a conversation of that account's, listed with its other chats.

A room can be given to a namespace's own assistant (bridge.rooms, "gateway = namespace" or "gateway/channel =
namespace"): conversations there are scoped to that namespace, so its assistant answers with its instructions and
memory (ns_assistant.py), and a message naming the assistant is for it too.

In any other room Lens is the way in to every namespace, and routes each question itself: a question naming a
namespace's assistant goes to that namespace; otherwise the decision model picks the namespace it's about
(auto_scope.place), and when it isn't sure the answer looks everywhere and says which namespaces it might be. Saying
"use <namespace>" keeps the conversation in one namespace until "use everything".

Refine later: approving in the room itself, voice notes, files posted in the room, one Lens account per chat user.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import re
import threading
import urllib.error
import urllib.request

from . import ai_tools, auth, auto_scope, chat, llm, ns_assistant, store
from .store import R

log = logging.getLogger(__name__)
LEASE_SECONDS = 30
TIMEOUT = 15
MAX_CHARS = 4000


class BridgeError(RuntimeError):
    pass


def ready(cfg):
    """Why the bridge can't run, or None when it can."""
    b = cfg.get("bridge") or {}
    if not b.get("enabled"):
        return "off"
    if not b.get("url"):
        return "no Matterbridge API address"
    if not b.get("account"):
        return "no Lens account to answer as"
    return None


# ---------- Matterbridge's API ----------
def _call(cfg, method, path, body=None):
    b = cfg["bridge"]
    headers = {"Content-Type": "application/json", "User-Agent": "Lens"}
    if b.get("token"):
        headers["Authorization"] = f"Bearer {b['token']}"
    req = urllib.request.Request(
        b["url"].rstrip("/") + path, data=json.dumps(body).encode() if body is not None else None, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raise BridgeError(f"Matterbridge answered HTTP {e.code}" + (" (check the token)" if e.code == 401 else "")) from None
    except (urllib.error.URLError, OSError) as e:
        raise BridgeError(f"can't reach Matterbridge at {b['url']}: {getattr(e, 'reason', e)}") from None
    return json.loads(raw or b"null")


def fetch(cfg):
    """What was said in the bridged rooms since the last look (Matterbridge forgets them once read)."""
    got = _call(cfg, "GET", "/api/messages")
    return [m for m in got or [] if isinstance(m, dict)]


def post(cfg, gateway, text, channel=None):
    b = cfg["bridge"]
    body = {"text": text[:MAX_CHARS], "username": b.get("name") or "Lens", "gateway": gateway}
    if channel:
        body["channel"] = channel
    _call(cfg, "POST", "/api/message", body)


def check(db, cfg):
    """Whether Matterbridge answers and the account to answer as is there: None when all is well, else what's wrong.
    (It asks /api/health, not /api/messages, which would take the messages waiting for the thread.)"""
    b = cfg.get("bridge") or {}
    if not b.get("url"):
        return "set Matterbridge's API address first"
    try:
        _call({"bridge": b}, "GET", "/api/health")
    except BridgeError as e:
        return str(e)
    except ValueError:
        pass  # it answered, with something other than JSON ("OK")
    if not b.get("account"):
        return "choose the Lens account it answers as"
    u = auth.find_account(db, b["account"])
    if not u or u.get("disabled"):
        return f"there's no active Lens account {b['account']}"
    return None


# ---------- who's talking to it ----------
def room_namespace(cfg, m):
    """The namespace whose assistant a room is given to (bridge.rooms), or None."""
    gw, ch = m.get("gateway") or "", m.get("channel") or ""
    rooms = {}
    for line in cfg["bridge"].get("rooms") or []:
        where, _, ns = line.partition("=")
        rooms.setdefault(where.strip().lower(), ns.strip())
    return rooms.get(f"{gw}/{ch}".lower()) or rooms.get(gw.lower())


def addressed(cfg, m, aliases=()):
    """The question in a message, when it's for Lens (or one of `aliases`: the room's namespace assistant's name); None
    when it isn't (or is Lens's own)."""
    b = cfg["bridge"]
    name = (b.get("name") or "Lens").strip()
    text = (m.get("text") or "").strip()
    who = (m.get("username") or "").strip()
    if not text or (m.get("event") or "") not in ("", "user_action") or who.strip("<> ").lower() == name.lower():
        return None
    for alias in aliases:
        if alias.lower() != name.lower():
            got = addressed({**cfg, "bridge": {**b, "name": alias, "answer": "mention"}}, m)
            if got:
                return got
    gw = b.get("gateway")
    if gw and m.get("gateway") != gw:
        return None
    allowed = {u.strip().lower() for u in b.get("users") or [] if u.strip()}
    if allowed and who.strip("<> ").lower() not in allowed and (m.get("userid") or "").lower() not in allowed:
        return None
    rx = re.compile(rf"^\s*@?{re.escape(name)}\b[\s,:;!?-]*", re.I)
    if rx.match(text):
        return rx.sub("", text, count=1).strip() or None
    if b.get("answer") == "all":
        return text
    if re.search(rf"@{re.escape(name)}\b", text, re.I):
        return re.sub(rf"@{re.escape(name)}\b[,:]?\s*", "", text, flags=re.I).strip() or None
    return None


def _account(db, cfg):
    u = auth.find_account(db, cfg["bridge"]["account"])
    if not u or u.get("disabled"):
        raise BridgeError(f"there's no active Lens account {cfg['bridge']['account']} to answer as")
    return {"id": u["id"], "email": u["email"], "admin": bool(u.get("admin"))}


def conversation(db, account, m, namespace=None):
    """The conversation for this person in this room, started on their first message; scoped to `namespace` (the
    room's namespace assistant), which follows bridge.rooms when it changes. In a room no namespace is given to, the
    scope is routing's (route)."""
    key = hashlib.sha1(json.dumps([m.get("gateway"), m.get("channel"), m.get("account"), m.get("username")]).encode()).hexdigest()
    scope = {"namespaces": [namespace]} if namespace else {}
    cid = db.one("SELECT VALUE record::id(id) FROM chat WHERE bridge = $k AND account = $a LIMIT 1", k=key, a=account["id"])
    if cid:
        if namespace:
            db.q("UPDATE $r SET scope = $s, room = 'given' WHERE (scope ?? {}) != $s", r=R("chat", cid), s=scope)
        else:  # the room was given to a namespace and no longer is: routing starts again
            db.q("UPDATE $r SET scope = {}, room = NONE, pinned = NONE WHERE room = 'given'", r=R("chat", cid))
        return cid
    where = " · ".join(x for x in [m.get("channel"), (m.get("username") or "").strip("<> ")] if x)
    cid = chat.create(db, account["id"], title=f"Matterbridge · {where}" if where else "Matterbridge", scope=scope, kind="bridge")
    db.q("UPDATE $r SET bridge = $k, room = $g", r=R("chat", cid), k=key, g="given" if namespace else None)
    return cid


# ---------- routing: which namespace a question in an open room is for ----------
USE_RX = re.compile(r"^use\s+(?:the\s+)?(everything|all|[a-z0-9][a-z0-9_-]{0,40})(?:\s+namespace)?\s*[.!]?$", re.I)


def _set_scope(db, cid, scope, pinned=None):
    db.q("UPDATE $r SET scope = $s" + (", pinned = $p" if pinned is not None else ""), r=R("chat", cid), s=scope, p=pinned)


def assistants(db, cfg, readable):
    """{assistant name (lower case): namespace} for the namespaces with their own assistant on that the account can read;
    a name two of them share, or Lens's own, addresses neither."""
    lens = (cfg["bridge"].get("name") or "Lens").strip().lower()
    names, shared = {}, set()
    for sid, ns in store.space_names(db).items():
        p = ns_assistant.profile(db, sid) if sid in readable else {"enabled": False}
        n = p["name"].strip().lower() if p["enabled"] else ""
        if not n or n == lens:
            continue
        if n in names:
            shared.add(n)
        names[n] = ns
    return {n: ns for n, ns in names.items() if n not in shared}


def named(db, cfg, m):
    """In a room no namespace is given to: (the question, the namespace) when a message is for a namespace's assistant
    by name, else None."""
    try:
        readable = set(auth.roles(db, _account(db, cfg)))
    except BridgeError:
        return None
    for alias, ns in assistants(db, cfg, readable).items():
        got = addressed({**cfg, "bridge": {**cfg["bridge"], "name": alias, "answer": "mention"}}, m)
        if got:
            return got, ns
    return None


def route(db, cfg, account, cid, q, ns=None):
    """Scope a question in a room no namespace is given to (`ns`: the namespace whose assistant it named). Returns
    (reply to send instead of an answer or None, a note to add to the answer or None)."""
    readable = set(auth.roles(db, account))
    names = store.space_names(db)
    mine = {n for sid, n in names.items() if sid in readable}
    c = db.one("SELECT scope, pinned, hinted FROM $r", r=R("chat", cid)) or {}
    use = USE_RX.match(q.strip())
    if use:
        pick = use.group(1).lower()
        if pick in ("everything", "all"):
            _set_scope(db, cid, {}, pinned=False)
            return "OK, this conversation looks in everything you can read again.", None
        if pick in mine:
            _set_scope(db, cid, {"namespaces": [pick]}, pinned=True)
            return f"OK, this conversation stays in {pick} until you say “use everything”.", None
        if pick in names.values():  # one it can't read; anything else isn't a namespace, so it's a question
            return f"I can't read {pick}. I can look in: {', '.join(sorted(mine)) or 'nothing yet'}.", None
    if ns:  # asked a namespace's assistant by name
        _set_scope(db, cid, {"namespaces": [ns]})
        return None, None
    if c.get("pinned"):
        return None, None
    before = (c.get("scope") or {}).get("namespaces") or []
    passages = chat.retrieve(db, q, readable, None, cfg=cfg)
    placed = auto_scope.place(db, cfg, q, readable, passages, admin=account["admin"])
    if placed and placed[0] == "scoped":
        ns = placed[1]["choice"]
        _set_scope(db, cid, {"namespaces": [ns]})
        changed = before != [ns]  # said once, not under every answer
        return None, f"(Looked in {ns}. Say “use everything” to look everywhere.)" if changed else None
    _set_scope(db, cid, {})
    likely = [s["name"] for s in (placed[1] if placed else []) if not s.get("new")]
    if likely and likely != c.get("hinted"):  # the same hint isn't repeated under every answer
        db.q("UPDATE $r SET hinted = $h", r=R("chat", cid), h=likely)
        options = " or ".join(likely)
        return None, f"(Looked everywhere. If this is about {options}, say “use {likely[0]}” to keep this conversation there.)"
    return None, None


# ---------- answering ----------
def answer(db, cfg, base, account, cid, q):
    """Answer a question in a conversation as the account would be answered in the web app (without streaming).
    Returns (text, approvals)."""
    roles = auth.roles(db, account)
    readable = set(roles)
    editable = {s for s in roles if auth.allows(roles, s, "editor")}
    past = chat.history(db, cid)
    summary = chat.memory(db, cfg, cid)
    scope = (db.one("SELECT scope FROM $r", r=R("chat", cid)) or {}).get("scope") or None
    chat.add(db, cid, "user", q)
    passages = chat.retrieve(db, q, readable, scope, cfg=cfg)
    steps, approvals, notice = [], [], None
    wrote = cfg["llm"].get("model") if llm.configured(cfg) else None
    if llm.configured(cfg) and cfg["ai"].get("tools"):
        box = ai_tools.Toolbox(
            db, cfg, {"id": account["id"], "email": account["email"]}, readable, editable, scope, cid, base, account["admin"], said=q
        )
        try:
            text = None
            for kind, data in chat.tool_answer(cfg, box, q, past, cfg["ai"].get("max_steps") or 6, summary=summary):
                if kind == "step":
                    steps.append(data)
                elif kind == "direct" and passages and not box.cited(data):  # unless it cites what it remembers
                    break
                else:
                    text = data
            if text is not None:
                box.after_answer(text)
                chat.add(db, cid, "assistant", text or "(no answer)", box.cited(text), steps=steps, model=wrote)
                return text, box.approvals
        except llm.ToolsUnsupported:
            notice = "This model can't use tools, so the answer comes from a search instead."
        except llm.LLMError as e:
            chat.add(db, cid, "assistant", "(no answer)", [], steps=steps, error=str(e), model=wrote)
            return "I couldn't answer that: " + str(e), box.approvals
    error = None
    try:
        text = llm.chat(cfg, chat.messages_for(q, passages, past, summary)) if llm.configured(cfg) else chat.fallback(passages)
    except llm.LLMError as e:
        text, error = "I couldn't answer that: " + str(e), str(e)
    chat.add(db, cid, "assistant", text or "(no answer)", chat.cited(text, passages), steps=steps, notice=notice, error=error, model=wrote)
    return text, approvals


def reply_text(cfg, text, approvals, cid, db=None):
    """The answer as a chat message: what it proposed waits for approval in the web app, linked."""
    from app.email import app_url

    out = (text or "").strip() or "(no answer)"
    if approvals:
        out += "\n\nWaiting for approval in Lens: " + "; ".join(a["summary"] for a in approvals)
        out += f"\n{app_url(cfg, db)}/chat/{cid}"  # through the tunnel, when there is one: the room is often far from home
    return out


def handle(db, cfg, base, m):
    """One message from a room: answered when it's for Lens. True when it was."""
    ns = room_namespace(cfg, m)
    sid = {v: k for k, v in store.space_names(db).items()}.get(ns) if ns else None
    home = ns_assistant.profile(db, sid) if sid is not None else None
    by_name = named(db, cfg, m) if sid is None else None
    q = by_name[0] if by_name else addressed(cfg, m, [home["name"]] if home and home["enabled"] else ())
    if not q:
        return False
    account = _account(db, cfg)
    cid = conversation(db, account, m, ns if sid is not None else None)
    note = None
    try:
        if sid is None:
            said, note = route(db, cfg, account, cid, q, by_name[1] if by_name else None)
            if said:
                chat.add(db, cid, "user", q)
                chat.add(db, cid, "assistant", said)
                post(cfg, m.get("gateway"), said, m.get("channel"))
                return True
        text, approvals = answer(db, cfg, base, account, cid, q[:MAX_CHARS])
    except Exception:  # noqa: BLE001 - still say something in the room
        log.exception("bridge: answering failed")
        text, approvals, note = "Something went wrong while answering. Try again.", [], None
    out = reply_text(cfg, text, approvals, cid, db)
    post(cfg, m.get("gateway"), out + (f"\n\n{note}" if note else ""), m.get("channel"))
    try:  # a room conversation goes on for good: older messages are folded into its summary
        chat.compact(db, cfg, cid)
    except Exception:  # noqa: BLE001 - tried again after the next answer
        log.warning("bridge: couldn't update the summary of chat %s", cid, exc_info=True)
    return True


# ---------- the thread ----------
def _later(seconds):
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=seconds)).isoformat(timespec="seconds")


def lease(db, me):
    """True while this process is the one reading Matterbridge (only one may: it forgets messages once read)."""
    r = R("bridge_state", "lease")
    try:
        db.q("CREATE $r CONTENT $d", r=r, d={"holder": me, "until": _later(LEASE_SECONDS)})
        return True
    except Exception as e:  # noqa: BLE001 - it's there already: take it if it's ours or has run out
        if "already exists" not in str(e):
            raise
    return bool(
        db.rows(
            "UPDATE $r SET holder = $me, until = $u WHERE holder = $me OR until < $now RETURN id",
            r=r,
            me=me,
            u=_later(LEASE_SECONDS),
            now=store.now(),
        )
    )


def release(db, me):
    db.q("DELETE $r WHERE holder = $me", r=R("bridge_state", "lease"), me=me)


def status(db, cfg):
    """How the bridge is doing: off, incomplete (what's missing), running (in which process, when it last looked, how
    many messages it answered), or error (what went wrong last)."""
    why = ready(cfg)
    if why == "off":
        return {"state": "off"}
    if why:
        return {"state": "incomplete", "error": why}
    s = db.one("SELECT holder, until, at, error, answered FROM $r", r=R("bridge_state", "lease")) or {}
    if not s or (s.get("until") or "") < store.now():
        return {"state": "starting", "error": s.get("error")}
    return {"state": "error" if s.get("error") else "running", **{k: s.get(k) for k in ("holder", "at", "error", "answered")}}


def _note(db, me, **kw):
    db.q("UPDATE $r MERGE $d WHERE holder = $me", r=R("bridge_state", "lease"), d=kw, me=me)


_HELD: set = set()  # the names this process holds the lease under


def tick(db, cfg, base, me):
    """One look at Matterbridge: how many messages were answered."""
    if ready(cfg):
        if me in _HELD:
            release(db, me)
            _HELD.discard(me)
        return 0
    if not lease(db, me):
        _HELD.discard(me)
        return 0
    _HELD.add(me)
    try:
        said = fetch(cfg)
    except Exception as e:
        _note(db, me, error=str(e), at=store.now())
        raise
    n, failed = 0, None
    for m in said:  # Matterbridge has forgotten them: each gets its turn even when one before it failed
        try:
            n += handle(db, cfg, base, m)
        except Exception as e:  # noqa: BLE001 - reported in the status, and the rest are still answered
            log.warning("bridge: a message couldn't be answered: %s", e)
            failed = str(e)
    if n:
        db.q("UPDATE $r SET answered = (answered ?? 0) + $n WHERE holder = $me", r=R("bridge_state", "lease"), n=n, me=me)
    _note(db, me, error=failed, at=store.now())
    return n


def start(db, cfg_fn, stop, base_fn=None, name=None, log_fn=None):
    """The bridge thread, until `stop` is set. It does nothing while the bridge is off."""
    me = name or f"pid-{os.getpid()}"

    def loop():
        while not stop.is_set():
            cfg = cfg_fn()
            try:
                tick(db, cfg, base_fn() if base_fn else cfg, me)
            except Exception as e:  # noqa: BLE001 - keep listening
                if log_fn:
                    log_fn(f"bridge: {type(e).__name__}: {e}")
            stop.wait(max(1, int((cfg.get("bridge") or {}).get("poll_seconds") or 2)))
        if me in _HELD:
            try:
                release(db, me)
            except Exception:  # noqa: BLE001 - the lease runs out on its own
                pass

    th = threading.Thread(target=loop, daemon=True, name="bridge")
    th.start()
    return th
