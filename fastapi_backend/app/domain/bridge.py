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

The assistant's reversible changes (entity merges and edits, new namespaces: ROOM_SAFE) can be approved by replying
"yes" (or "no") in the room, when bridge.approve is "low_risk" and the sender is one of bridge.users; everything else
waits for approval in the web app, linked from the answer.

What happens in a room is the same in every chat channel (channels.py): Matterbridge is one adapter, Telegram's own
bot (telegram.py) another. handle() takes a message from any of them, with their voice notes (transcribed) and files
(held for the assistant to import), and answers through the adapter it came from.

Refine later: one Lens account per chat user.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import urllib.error
import urllib.request

from . import ai_tools, auth, auto_scope, channels, chat, graph_history, llm, matterbridge, ns_assistant, store, untrusted
from .store import R

log = logging.getLogger(__name__)
TIMEOUT = 15
MAX_CHARS = 4000


class BridgeError(RuntimeError):
    pass


def ready(cfg):
    """Why the bridge can't run, or None when it can."""
    b = cfg.get("bridge") or {}
    if not b.get("enabled"):
        return "off"
    if not b.get("url") and not matterbridge.running(cfg):
        return "no Matterbridge API address"
    if not b.get("account"):
        return "no Lens account to answer as"
    return None


def endpoint(cfg):
    """(Matterbridge's API address, its token): the one Lens runs (matterbridge.py), else bridge.url and bridge.token."""
    if matterbridge.running(cfg):
        return matterbridge.api_url(), matterbridge.api_token(cfg)
    b = cfg["bridge"]
    return b.get("url"), b.get("token")


# ---------- Matterbridge's API ----------
def _call(cfg, method, path, body=None):
    url, token = endpoint(cfg)
    headers = {"Content-Type": "application/json", "User-Agent": "Lens"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        url.rstrip("/") + path, data=json.dumps(body).encode() if body is not None else None, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raise BridgeError(f"Matterbridge answered HTTP {e.code}" + (" (check the token)" if e.code == 401 else "")) from None
    except (urllib.error.URLError, OSError) as e:
        hint = " (is the matterbridge service running? docker compose --profile matterbridge up -d)" if matterbridge.running(cfg) else ""
        raise BridgeError(f"can't reach Matterbridge at {url}: {getattr(e, 'reason', e)}{hint}") from None
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
    if matterbridge.running(cfg):
        problem = matterbridge.write(cfg)
        if problem:
            return problem
    elif not b.get("url"):
        return "set Matterbridge's API address first"
    try:
        _call(cfg, "GET", "/api/health")
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
    if m.get("direct"):  # a private chat, or a reply or mention the channel resolved: for Lens whatever it says
        return rx.sub("", text, count=1).strip() or None
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


def conversation(db, account, m, namespace=None, label="Matterbridge"):
    """The conversation for this person in this room, started on their first message; scoped to `namespace` (the
    room's namespace assistant), which follows bridge.rooms when it changes. In a room no namespace is given to, the
    scope is routing's (route). `label` names the channel in its title."""
    who = m["thread"] if m.get("thread") else [m.get("gateway"), m.get("channel"), m.get("account"), m.get("username")]
    key = hashlib.sha1(json.dumps(who).encode()).hexdigest()
    scope = {"namespaces": [namespace]} if namespace else {}
    cid = db.one("SELECT VALUE record::id(id) FROM chat WHERE bridge = $k AND account = $a LIMIT 1", k=key, a=account["id"])
    if cid:
        if namespace:
            db.q("UPDATE $r SET scope = $s, room = 'given' WHERE (scope ?? {}) != $s", r=R("chat", cid), s=scope)
        else:  # the room was given to a namespace and no longer is: routing starts again
            db.q("UPDATE $r SET scope = {}, room = NONE, pinned = NONE WHERE room = 'given'", r=R("chat", cid))
        return cid
    where = " · ".join(x for x in [m.get("channel"), (m.get("username") or "").strip("<> ")] if x)
    cid = chat.create(db, account["id"], title=f"{label} · {where}" if where else label, scope=scope, kind="bridge")
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
def answer(db, cfg, base, account, cid, q, files=(), act_on=(), outside=None):
    """Answer a question in a conversation as the account would be answered in the web app (without streaming), with
    the files sent with it (held uploads: channels.hold); `act_on` are the changes made at once (Channel.acts), and
    `outside` where the question's text came from outside Lens (Channel.outside, a Matterbridge room's by default), so
    that changes ask first. Returns (text, approvals): the text as the room gets it, its links safe to preview
    (`safe_links`)."""
    if outside is None:
        outside = MATTERBRIDGE.outside(cfg, {})
    text, approvals, keep = _answer(db, cfg, base, account, cid, q, files, act_on, outside)
    safe = safe_links(cfg, db, text, keep)
    if safe != text:  # what an injection may have tried, for admins to see
        broken = len(untrusted.links(text) - untrusted.links(safe))
        auth.audit(db, {"id": account["id"], "email": account["email"]}, "bridge.links_broken", f"chat:{cid}", {"links": broken})
    return safe, approvals


def safe_links(cfg, db, text, keep=()):
    """`text` with the web addresses the answer built itself broken up (untrusted.defang), when bridge.safe_links is
    on (the default): chat apps preview what's linked, so an address an injection had the model fill with what it
    read would send it out unclicked. Addresses the person, the archive or a tool wrote, and Lens's own, stay."""
    if not cfg["bridge"].get("safe_links", True):
        return text
    from app.email import app_url

    return untrusted.defang(text, keep, [app_url(cfg, db)])


def _answer(db, cfg, base, account, cid, q, files, act_on, outside):
    roles = auth.roles(db, account)
    readable = set(roles)
    editable = {s for s in roles if auth.allows(roles, s, "editor")}
    try:  # a conversation that fills the model's context is folded into its summary before it's answered
        chat.compact(db, cfg, cid, full_only=True)
    except Exception:  # noqa: BLE001 - answered with what fits; tried again after the answer
        log.warning("bridge: couldn't update the summary of chat %s", cid, exc_info=True)
    past = chat.history(db, cid)
    summary = chat.memory(db, cfg, cid)
    scope = (db.one("SELECT scope FROM $r", r=R("chat", cid)) or {}).get("scope") or None
    chat.add(db, cid, "user", q, attachments=list(files) or None)
    asked = q + chat.attached_note(list(files))
    passages = chat.retrieve(db, q, readable, scope, cfg=cfg)
    # the web addresses written by someone other than the model: the person, now and before, and the archive
    keep = untrusted.links("\n".join([q] + [m["content"] or "" for m in past if m["role"] == "user"] + [p["text"] for p in passages]))
    steps, approvals, notice = [], [], None
    wrote = cfg["llm"].get("model") if llm.configured(cfg) else None
    if llm.configured(cfg) and cfg["ai"].get("tools"):
        box = ai_tools.Toolbox(
            db,
            cfg,
            {"id": account["id"], "email": account["email"]},
            readable,
            editable,
            scope,
            cid,
            base,
            account["admin"],
            said=q,
            outside=list(outside) + ai_tools.read_before(past, summary),
            act_on=act_on,
        )
        try:
            text = None
            for kind, data in chat.tool_answer(cfg, box, asked, past, cfg["ai"].get("max_steps") or 6, summary=summary):
                if kind == "step":
                    steps.append(data)
                elif kind == "direct" and passages and not box.cited(data):  # unless it cites what it remembers
                    break
                else:
                    text = data
            if text is not None:
                box.after_answer(text)
                chat.add(db, cid, "assistant", text or "(no answer)", box.cited(text), steps=steps, model=wrote)
                return text, box.approvals, keep | box.links
        except llm.ToolsUnsupported:
            notice = "This model can't use tools, so the answer comes from a search instead."
        except llm.LLMError as e:
            chat.add(db, cid, "assistant", "(no answer)", [], steps=steps, error=str(e), model=wrote)
            return "I couldn't answer that: " + str(e), box.approvals, keep | box.links
    error = None
    try:
        text = llm.chat(cfg, chat.messages_for(asked, passages, past, summary, cfg)) if llm.configured(cfg) else chat.fallback(passages)
    except llm.LLMError as e:
        text, error = "I couldn't answer that: " + str(e), str(e)
    chat.add(db, cid, "assistant", text or "(no answer)", chat.cited(text, passages), steps=steps, notice=notice, error=error, model=wrote)
    return text, approvals, keep


# ---------- approving in the room ----------
# what can be approved by a reply in the room: changes to the graph (history keeps them, and they can be undone) and
# new namespaces; settings, imports, batch runs, extensions and forgetting are approved in the web app
ROOM_SAFE = {"propose_entity_change", "create_namespace"}
YES_RX = re.compile(r"^(?:yes|y|yep|approve|approved|ok|okay|do it|go ahead)(?:\s+#?(\d+|all))?\s*[.!]*$", re.I)
NO_RX = re.compile(r"^(?:no|n|nope|decline|cancel|don't)(?:\s+#?(\d+|all))?\s*[.!]*$", re.I)


def room_approver(cfg, m=None):
    """Whether the sender of `m` (anyone in bridge.users, when m is None) may approve in the room: only with
    bridge.approve on and a list of who may talk to Lens, since a room's other people aren't the account's."""
    b = cfg["bridge"]
    allowed = {u.strip().lower() for u in b.get("users") or [] if u.strip()}
    if b.get("approve", "low_risk") == "off" or not allowed:
        return False
    if m is None:
        return True
    return (m.get("username") or "").strip("<> ").lower() in allowed or (m.get("userid") or "").lower() in allowed


def pending(db, cid):
    return db.rows("SELECT record::id(id) AS id, tool, summary FROM approval WHERE chat = $c AND status = 'pending' ORDER BY id", c=cid)


def decide_in_room(db, cfg, base, account, cid, m, q):
    """A "yes" or "no" to what this conversation is waiting on: carried out (the ROOM_SAFE ones) or declined, and the
    reply to post. None when q isn't one, or nothing waits."""
    yes, no = YES_RX.match(q.strip()), NO_RX.match(q.strip())
    if not (yes or no) or not room_approver(cfg, m):
        return None
    waiting = pending(db, cid)
    if not waiting:
        return None
    pick = (yes or no).group(1)
    if pick and pick.lower() != "all":
        waiting = [a for a in waiting if a["id"] == int(pick)]
        if not waiting:
            return f"Nothing is waiting as #{pick}."
    elif not pick and len(waiting) > 1 and yes:  # "yes" to several things is ambiguous: say which
        listed = "; ".join(f"#{a['id']} {a['summary']}" for a in waiting)
        return f"Several things are waiting: {listed}. Say “yes all” or “yes <number>”."
    roles = auth.roles(db, account)
    readable, editable = set(roles), {s for s in roles if auth.allows(roles, s, "editor")}
    who = (m.get("username") or "").strip("<> ")
    user = {"id": account["id"], "email": account["email"]}
    lines, web = [], []
    for a in waiting:
        if yes and a["tool"] not in ROOM_SAFE:
            web.append(a)
            continue
        decision = "approve" if yes else "decline"
        try:
            with graph_history.acting(why=f"approved in a chat room by {who}" if yes else None):
                out = ai_tools.approve(db, cfg, a["id"], user, editable, decision, base, account["admin"], readable)
        except (ValueError, PermissionError, KeyError) as e:
            lines.append(f"Couldn't do “{a['summary']}”: {e}")
            continue
        auth.audit(db, user, "assistant.approval", f"approval:{a['id']}", {"decision": decision, "via": "chat room", "by": who, **out})
        lines.append(("Done: " if yes else "Declined: ") + a["summary"])
    if web:
        lines.append(_web_line(cfg, db, cid, web))
    return "\n".join(lines)


def _web_line(cfg, db, cid, approvals):
    from app.email import app_url

    return "Waiting for approval in Lens: " + "; ".join(a["summary"] for a in approvals) + f"\n{app_url(cfg, db)}/chat/{cid}"


def reply_text(cfg, text, approvals, cid, db=None):
    """The answer as a chat message: what it proposed can be approved by replying "yes" (ROOM_SAFE, for the people
    allowed to: room_approver), or waits for approval in the web app, linked (through the tunnel, when there is one:
    the room is often far from home)."""
    out = (text or "").strip() or "(no answer)"
    here = [a for a in approvals if a.get("tool") in ROOM_SAFE] if room_approver(cfg) else []
    web = [a for a in approvals if a not in here]
    if len(here) == 1:
        out += f"\n\nWaiting for your yes: {here[0]['summary']}. Reply “yes” to do it, or “no”."
    elif here:
        listed = "; ".join(f"#{a['id']} {a['summary']}" for a in here)
        out += f"\n\nWaiting for your yes: {listed}. Reply “yes all”, “yes <number>” or “no”."
    if web:
        out += "\n\n" + _web_line(cfg, db, cid, web)
    return out


def sources(db, cfg, cid):
    """The excerpts the conversation's last answer cites, a line each with its link into the web app, for a channel
    that shows them under the answer (Channel.sources); "" when it cites none."""
    from app.email import app_url

    row = db.one("SELECT id, passages FROM chat_message WHERE chat = $c AND role = 'assistant' ORDER BY id DESC LIMIT 1", c=cid) or {}
    used = [p for p in row.get("passages") or [] if isinstance(p, dict) and p.get("used") and p.get("recording_id")]
    if not used:
        return ""
    base, lines = app_url(cfg, db), []
    for p in used:
        at = f"?page={p['page'] + 1}" if p.get("page") is not None else f"?t={int((p.get('t0') or 0) // 1000)}"
        where = ", ".join(str(x) for x in (p.get("title") or "Untitled", p.get("time")) if x)
        lines.append(f"[{p['n']}] {where}: {base}/resources/{p['recording_id']}{at}")
    return "Sources:\n" + "\n".join(lines)


def _media(db, cfg, ch, m):
    """A message's voice note as its text (when it has none and is for Lens), and a note when there's nothing to say
    but files: the message as handle() reads it, and what to say instead of answering (or None)."""
    direct = m.get("direct") or cfg["bridge"].get("answer") == "all"
    if not direct or (m.get("text") or "").strip():
        return m, None
    if m.get("voice"):
        try:
            return {**m, "text": channels.heard(cfg, ch, m["voice"]), "heard": True}, None
        except channels.ChannelError as e:
            return m, str(e)
    if m.get("files"):
        return {**m, "text": "I attached " + ("this file." if len(m["files"]) == 1 else "these files.")}, None
    return m, None


def _hold(db, cfg, ch, account, m):
    """The files sent with a message, held for the assistant to import, and a line for each that couldn't be."""
    files, failed = [], []
    for f in m.get("files") or []:
        try:
            files.append(channels.hold(db, cfg, ch, account, f))
        except (ValueError, OSError, channels.ChannelError) as e:
            failed.append(f"Couldn't take {f.get('filename') or 'that file'}: {e}")
    return files, failed


def handle(db, cfg, base, m, ch=None):
    """One message from a room of channel `ch` (Matterbridge when None): answered when it's for Lens. True when it
    was."""
    ch = ch or MATTERBRIDGE
    cfg = channels.view(cfg, ch)
    m, cant = _media(db, cfg, ch, m)
    if cant:
        if addressed(cfg, {**m, "text": "?"}):  # only said to who could have been answered
            ch.send(cfg, m, cant)
            return True
        return False
    ns = m.get("namespace") or room_namespace(cfg, m)
    sid = {v: k for k, v in store.space_names(db).items()}.get(ns) if ns else None
    home = ns_assistant.profile(db, sid) if sid is not None else None
    by_name = named(db, cfg, m) if sid is None else None
    q = by_name[0] if by_name else addressed(cfg, m, [home["name"]] if home and home["enabled"] else ())
    if not q:
        return False
    account = _account(db, cfg)
    cid = conversation(db, account, m, ns if sid is not None else None, ch.label)
    note = None
    cmd = chat.command(q)
    if cmd:  # /clear or /compact, for this room's conversation
        try:
            said = chat.run_command(db, cfg, cid, *cmd)[0]
        except Exception:  # noqa: BLE001 - still say something in the room
            log.exception("%s: /%s failed", ch.key, cmd[0])
            said = f"Couldn't {cmd[0]} this conversation. Try again."
        ch.send(cfg, m, said)
        return True
    files, failed = _hold(db, cfg, ch, account, m)
    try:
        decided = decide_in_room(db, cfg, base, account, cid, m, q)
        if decided:
            chat.add(db, cid, "user", q)
            chat.add(db, cid, "assistant", decided)
            ch.send(cfg, m, decided)
            return True
        if sid is None:
            said, note = route(db, cfg, account, cid, q, by_name[1] if by_name else None)
            if said:
                chat.add(db, cid, "user", q)
                chat.add(db, cid, "assistant", said)
                ch.send(cfg, m, said)
                return True
        text, approvals = answer(db, cfg, base, account, cid, q[:MAX_CHARS], files, ch.acts(cfg), ch.outside(cfg, m))
    except Exception:  # noqa: BLE001 - still say something in the room
        log.exception("%s: answering failed", ch.key)
        text, approvals, note = "Something went wrong while answering. Try again.", [], None
    out = reply_text(cfg, text, approvals, cid, db)
    if m.get("heard"):  # what the voice note was heard as, so a mishearing shows
        out = f"(Heard: “{q}”)\n\n{out}"
    out = "\n".join([*failed, out]) if failed else out
    cites = sources(db, cfg, cid) if ch.sources else ""
    ch.send(cfg, m, out + (f"\n\n{cites}" if cites else "") + (f"\n\n{note}" if note else ""))
    try:  # a room conversation goes on for good: older messages are folded into its summary
        chat.compact(db, cfg, cid)
    except Exception:  # noqa: BLE001 - tried again after the next answer
        log.warning("%s: couldn't update the summary of chat %s", ch.key, cid, exc_info=True)
    return True


# ---------- Matterbridge as a channel ----------
class Matterbridge(channels.Channel):
    key, label, lease = "bridge", "Matterbridge", "lease"

    def ready(self, cfg):
        return ready(cfg)

    def check(self, db, cfg):
        return check(db, cfg)

    def fetch(self, db, cfg):
        if matterbridge.running(cfg):  # the config follows the settings; the service restarts Matterbridge on a change
            problem = matterbridge.write(cfg)
            if problem:
                raise BridgeError(problem)
        return fetch(cfg)

    def send(self, cfg, m, text):
        post(cfg, m.get("gateway"), text, m.get("channel"))

    def outside(self, cfg, m):
        # anyone in a room with no list of who may talk to Lens is someone the account doesn't vouch for
        return [] if [u for u in cfg["bridge"].get("users") or [] if u.strip()] else ["chat room"]


MATTERBRIDGE = Matterbridge()


# ---------- the thread (channels.py) ----------
def lease(db, me):
    """True while this process is the one reading Matterbridge (only one may: it forgets messages once read)."""
    return channels.lease(db, me, MATTERBRIDGE)


def release(db, me):
    channels.release(db, me, MATTERBRIDGE)


def status(db, cfg):
    """How the bridge is doing (channels.status)."""
    return channels.status(db, cfg, MATTERBRIDGE)


def tick(db, cfg, base, me):
    """One look at Matterbridge: how many messages were answered."""
    return channels.tick(db, cfg, base, me, MATTERBRIDGE)


def start(db, cfg_fn, stop, base_fn=None, name=None, log_fn=None):
    """The bridge thread, until `stop` is set. It does nothing while the bridge is off."""
    return channels.start(db, cfg_fn, stop, MATTERBRIDGE, base_fn, name, log_fn)
