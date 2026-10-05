"""The assistant in chat rooms, through Matterbridge (https://github.com/42wim/matterbridge): Slack, Discord,
Mattermost, Telegram, Matrix, IRC, WhatsApp and the rest, by way of one Matterbridge API account.

A thread in one server process (whichever holds the lease) reads what was said in the bridged rooms
(GET /api/messages), answers what was said to it (a message naming it, or every message when bridge.answer is "all")
and posts the answer back to the same gateway (POST /api/message). It answers as one Lens account (bridge.account):
it reads what that account can read, and changes anything only after someone approves it in the web app, like any
chat. Each person in each room is a conversation of that account's, listed with its other chats.

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

from . import ai_tools, auth, chat, llm, store
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
def addressed(cfg, m):
    """The question in a message, when it's for Lens; None when it isn't (or is Lens's own)."""
    b = cfg["bridge"]
    name = (b.get("name") or "Lens").strip()
    text = (m.get("text") or "").strip()
    who = (m.get("username") or "").strip()
    if not text or (m.get("event") or "") not in ("", "user_action") or who.strip("<> ").lower() == name.lower():
        return None
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


def conversation(db, account, m):
    """The conversation for this person in this room, started on their first message."""
    key = hashlib.sha1(json.dumps([m.get("gateway"), m.get("channel"), m.get("account"), m.get("username")]).encode()).hexdigest()
    cid = db.one("SELECT VALUE record::id(id) FROM chat WHERE bridge = $k AND account = $a LIMIT 1", k=key, a=account["id"])
    if cid:
        return cid
    where = " · ".join(x for x in [m.get("channel"), (m.get("username") or "").strip("<> ")] if x)
    cid = chat.create(db, account["id"], title=f"Matterbridge · {where}" if where else "Matterbridge", kind="bridge")
    db.q("UPDATE $r SET bridge = $k", r=R("chat", cid), k=key)
    return cid


# ---------- answering ----------
def answer(db, cfg, base, account, cid, q):
    """Answer a question in a conversation as the account would be answered in the web app (without streaming).
    Returns (text, approvals)."""
    roles = auth.roles(db, account)
    readable = set(roles)
    editable = {s for s in roles if auth.allows(roles, s, "editor")}
    past = chat.history(db, cid)
    summary = chat.memory(db, cfg, cid)
    chat.add(db, cid, "user", q)
    passages = chat.retrieve(db, q, readable, None, cfg=cfg)
    steps, approvals, notice = [], [], None
    wrote = cfg["llm"].get("model") if llm.configured(cfg) else None
    if llm.configured(cfg) and cfg["ai"].get("tools"):
        box = ai_tools.Toolbox(
            db, cfg, {"id": account["id"], "email": account["email"]}, readable, editable, None, cid, base, account["admin"], said=q
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


def reply_text(cfg, text, approvals, cid):
    """The answer as a chat message: what it proposed waits for approval in the web app, linked."""
    from app.email import app_url

    out = (text or "").strip() or "(no answer)"
    if approvals:
        out += "\n\nWaiting for approval in Lens: " + "; ".join(a["summary"] for a in approvals)
        out += f"\n{app_url(cfg)}/chat/{cid}"
    return out


def handle(db, cfg, base, m):
    """One message from a room: answered when it's for Lens. True when it was."""
    q = addressed(cfg, m)
    if not q:
        return False
    account = _account(db, cfg)
    cid = conversation(db, account, m)
    try:
        text, approvals = answer(db, cfg, base, account, cid, q[:MAX_CHARS])
    except Exception:  # noqa: BLE001 - still say something in the room
        log.exception("bridge: answering failed")
        text, approvals = "Something went wrong while answering. Try again.", []
    post(cfg, m.get("gateway"), reply_text(cfg, text, approvals, cid), m.get("channel"))
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
