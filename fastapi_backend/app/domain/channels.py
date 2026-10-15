"""Chat channels: the assistant in chat apps, one adapter per kind of connection, all sharing what happens in a room
(bridge.py: who a message is for, which namespace it's about, approving by reply) and the thread that reads each.

An adapter (a Channel) knows only its own connection: whether it's set up (ready), checking it (check), what was said
since the last look (fetch), posting a reply (send) and fetching a voice note or file sent with a message
(download). Its settings section holds the room settings bridge.py reads: account, name, answer, users, rooms,
approve. Matterbridge (bridge.MATTERBRIDGE), Telegram (telegram.py), Slack (slack.py) and the assistant's mailbox
(mailbox.py) are adapters; each has its own thread, lease and status, so one being down doesn't stop the other.

Messages are dicts in Matterbridge's shape (gateway, channel, account, username, userid, text, event), and may say:

- direct: it's for the assistant whatever it says (a private chat, a reply to it, a mention the adapter has resolved)
- voice: a voice note to transcribe (the adapter's reference to it, for download)
- files: [{ref, filename, size}], files sent with it, held for the assistant to import (import_files)
- thread: what identifies the conversation (else gateway, channel, account and username)
- namespace: the namespace it's for, when the adapter knows (else the channel's rooms setting says, or Lens picks)
- private: it was said in a private chat with Lens, not in a room (route)

Telling a person something nobody asked about yet (an automation's message, tell()): each channel keeps the private chat
it last had with someone it vouches for (chat_route:<channel>), and tell() sends there, through every channel that
answers as that person's account. A room is never used: it reaches other people too.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import secrets
import threading

from . import activity, auth, store, uploads, voice
from .store import R

log = logging.getLogger(__name__)
LEASE_SECONDS = 30


class ChannelError(RuntimeError):
    pass


class Channel:
    """One kind of chat connection. `key` is its settings section, `label` names it in conversation titles, `lease`
    is its bridge_state record (which process reads it, and how that's going)."""

    key = "bridge"
    label = "Chat"
    lease = "lease"
    sources = False  # whether answers list the excerpts they cite, with links (bridge.sources)

    def ready(self, cfg):
        """Why it can't run, or None when it can ("off" while it's turned off)."""
        raise NotImplementedError

    def check(self, db, cfg):
        """None when the connection and the account it answers as check out, else what's wrong."""
        raise NotImplementedError

    def fetch(self, db, cfg):
        """The messages said since the last look; once returned they aren't returned again."""
        raise NotImplementedError

    def send(self, cfg, m, text):
        """Post `text` where `m` was said."""
        raise NotImplementedError

    def download(self, cfg, ref):
        """The bytes of a voice note or file sent with a message."""
        raise ChannelError("this chat can't send files to Lens")

    def acts(self, cfg):
        """The assistant's changes made at once in this channel rather than waiting for approval (none by default)."""
        return ()

    def outside(self, cfg, m):
        """Where the text of `m` came from outside Lens (Toolbox.outside), so that changes ask first: anyone in a room
        with no list of who may talk to Lens is someone the account doesn't vouch for. An adapter can add its own, such
        as what was forwarded."""
        return [] if [u for u in cfg["bridge"].get("users") or [] if u.strip()] else ["chat room"]

    def pause(self, cfg, reading, failed):
        """Seconds to wait before the next look: `reading` when this process holds the lease, `failed` after an error."""
        return max(1, int((cfg.get(self.key) or {}).get("poll_seconds") or 2))

    def handle(self, db, cfg, base, m):
        from . import bridge

        return bridge.handle(db, cfg, base, m, self)

    def route(self, m):
        """Where to tell the account's own person something later, from a message `m` they sent: what send() needs to
        reach its private chat again, or None for a message said in a room (which reaches other people too)."""
        if not m.get("private"):
            return None
        out = {k: m[k] for k in ("gateway", "channel", "account", "username", "userid", "thread") if m.get(k) is not None}
        to = m.get("to") or {}
        if to.get("channel"):  # a new message in the chat, never in a thread or through a slash command's address
            out["to"] = {"channel": to["channel"]}
        return out

    def vouched(self, cfg, m):
        """Whether the channel vouches for who sent `m`: they're on its list of people who may talk to Lens (`users`,
        by name or id). `cfg` is the channel's view (view())."""
        users = {u.strip().lstrip("@").lower() for u in cfg["bridge"].get("users") or [] if u.strip()}
        who = (m.get("username") or "").strip("<> ").lstrip("@").lower()
        return bool(users) and (who in users or str(m.get("userid") or "").lower() in users)


def view(cfg, ch):
    """The settings as bridge.py reads them: the channel's own section as `bridge`."""
    if ch.key == "bridge":
        return cfg
    return {**cfg, "bridge": cfg.get(ch.key) or {}}


# ---------- voice notes and files ----------
def heard(cfg, ch, ref):
    """What was said in a voice note, transcribed by the server's speech-to-text (voice.py). ChannelError, saying why,
    when it can't be."""
    if not voice.can_transcribe(cfg):
        raise ChannelError("I can't listen to voice notes here: no speech-to-text engine is set up. Type it instead.")
    try:
        text = voice.transcribe(cfg, ch.download(cfg, ref))["text"]
    except ValueError as e:
        raise ChannelError(f"I couldn't listen to that: {e}") from None
    if not text:
        raise ChannelError("I couldn't make out anything in that voice note.")
    return text


def hold(db, cfg, ch, account, f):
    """A file sent with a message, kept as a held upload of the account's (as a file dropped into a conversation in the
    web app), for the assistant to import. Returns {id, filename, size}; ValueError when it can't be taken."""
    name = uploads.clean_name(f.get("filename") or "file")
    data = ch.download(cfg, f["ref"])
    path = uploads.incoming(cfg) / f"chat-{secrets.token_hex(8)}"
    path.write_bytes(data)
    try:
        up = uploads.take(db, cfg, "", path, name, {"id": account["id"], "email": account["email"]}, hold=True)
    finally:
        path.unlink(missing_ok=True)
    return {"id": up["id"], "filename": up["filename"], "size": up["size"]}


# ---------- the thread that reads a channel ----------
def _later(seconds):
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=seconds)).isoformat(timespec="seconds")


def lease(db, me, ch):
    """True while this process is the one reading the channel (only one may: what's read is gone)."""
    r = R("bridge_state", ch.lease)
    try:
        db.q("CREATE $r CONTENT $d", r=r, d={"holder": me, "until": _later(LEASE_SECONDS)})
        return True
    except Exception as e:  # noqa: BLE001 - it's there already: take it if it's ours or has run out
        if "already exists" not in str(e):
            raise
    return bool(
        db.rows(
            "UPDATE $r SET holder = $me, until = $u WHERE holder = $me OR holder = NONE OR until < $now RETURN id",
            r=r,
            me=me,
            u=_later(LEASE_SECONDS),
            now=store.now(),
        )
    )


def release(db, me, ch):
    db.q("UPDATE $r SET holder = NONE, until = NONE WHERE holder = $me", r=R("bridge_state", ch.lease), me=me)


def state(db, ch):
    """The channel's bridge_state record: the lease, and whatever its adapter keeps there."""
    return db.one("SELECT * OMIT id FROM $r", r=R("bridge_state", ch.lease)) or {}


def keep(db, ch, **kw):
    """Keep something in the channel's bridge_state record (an adapter's offset, the chats it has seen)."""
    db.q("UPSERT $r MERGE $d", r=R("bridge_state", ch.lease), d=kw)


def status(db, cfg, ch):
    """How the channel is doing: off, incomplete (what's missing), starting, running (in which process, when it last
    looked, how many messages it answered), or error (what went wrong last)."""
    why = ch.ready(cfg)
    if why == "off":
        return {"state": "off"}
    if why:
        return {"state": "incomplete", "error": why}
    s = state(db, ch)
    if not s.get("holder") or (s.get("until") or "") < store.now():
        return {"state": "starting", "error": s.get("error")}
    return {"state": "error" if s.get("error") else "running", **{k: s.get(k) for k in ("holder", "at", "error", "answered")}}


def _note(db, me, ch, **kw):
    db.q("UPDATE $r MERGE $d WHERE holder = $me", r=R("bridge_state", ch.lease), d=kw, me=me)


_HELD: set = set()  # (channel, name) this process holds the lease of


def tick(db, cfg, base, me, ch):
    """One look at a channel: how many messages were answered."""
    if ch.ready(cfg):
        if (ch.key, me) in _HELD:
            release(db, me, ch)
            _HELD.discard((ch.key, me))
        return 0
    if not lease(db, me, ch):
        _HELD.discard((ch.key, me))
        return 0
    _HELD.add((ch.key, me))
    try:
        said = ch.fetch(db, cfg)
    except Exception as e:
        _note(db, me, ch, error=str(e), at=store.now())
        raise
    n, failed = 0, None
    for m in said:  # they're gone from the channel: each gets its turn even when one before it failed
        try:
            n += ch.handle(db, cfg, base, m)
        except Exception as e:  # noqa: BLE001 - reported in the status, and the rest are still answered
            log.warning("%s: a message couldn't be answered: %s", ch.key, e)
            failed = str(e)
    if n:
        db.q("UPDATE $r SET answered = (answered ?? 0) + $n WHERE holder = $me", r=R("bridge_state", ch.lease), n=n, me=me)
    _note(db, me, ch, error=failed, at=store.now())
    return n


def start(db, cfg_fn, stop, ch, base_fn=None, name=None, log_fn=None):
    """The channel's thread, until `stop` is set. It does nothing while the channel is off."""
    me = name or f"pid-{os.getpid()}"

    def loop():
        while not stop.is_set():
            cfg = cfg_fn()
            failed = False
            try:
                tick(db, cfg, base_fn() if base_fn else cfg, me, ch)
            except Exception as e:  # noqa: BLE001 - keep listening
                failed = True
                if log_fn:
                    log_fn(f"{ch.key}: {type(e).__name__}: {e}")
            stop.wait(ch.pause(cfg, (ch.key, me) in _HELD, failed))
        if (ch.key, me) in _HELD:
            try:
                release(db, me, ch)
            except Exception:  # noqa: BLE001 - the lease runs out on its own
                pass
            _HELD.discard((ch.key, me))

    th = threading.Thread(target=loop, daemon=True, name=ch.key)
    th.start()
    return th


def every():
    """Every channel Lens has."""
    from . import bridge, mailbox, slack, telegram

    return [bridge.MATTERBRIDGE, telegram.TELEGRAM, slack.SLACK, mailbox.MAILBOX]


def start_all(db, cfg_fn, stop, base_fn=None, name=None, log_fn=None):
    """A thread per channel (each idle while it's off)."""
    return [start(db, cfg_fn, stop, ch, base_fn, name, log_fn) for ch in every()]


# ---------- telling a person ----------
NOT_LINKED = (
    "no chat app linked: message Lens in a private chat in Telegram or Slack, as someone on its list of people who may "
    "talk to Lens, and messages for you go there"
)


def remember(db, cfg, ch, account, m, conversation=None):
    """Keep the private chat `m` was said in as where the account's own person is told things (chat_route:<channel>),
    when the channel vouches for its sender; the latest one wins. `cfg` is the channel's view. True when kept."""
    target = ch.route(m)
    if not target or not ch.vouched(cfg, m):
        return False
    who = (m.get("username") or "").strip("<> ") or str(m.get("userid") or "someone")
    row = {
        "channel": ch.key,
        "account": account["id"],
        "email": account["email"],
        "target": target,
        "chat": f"private chat with {who}",
        "conversation": conversation,
        "at": store.now(),
    }
    db.q("UPSERT $r CONTENT $d", r=R("chat_route", ch.key), d=store.clean(row))
    return True


def _account(db, account):
    if isinstance(account, dict) and account.get("email"):
        return account
    got = auth.get_account(db, account["id"] if isinstance(account, dict) else account)
    return got if got and not got.get("disabled") else None


def routes(db, cfg, account):
    """Where `account`'s own person can be told something now: [(channel, its view, its chat_route)] for each channel
    that is on, answers as that account, and last had a private chat with someone it still vouches for."""
    acc = _account(db, account)
    if not acc:
        return []
    rows = {r["channel"]: r for r in db.rows("SELECT * OMIT id FROM chat_route WHERE account = $a", a=acc["id"])}
    out = []
    for ch in every():
        r = rows.get(ch.key)
        if not r or ch.ready(cfg):
            continue
        v = view(cfg, ch)
        if (v["bridge"].get("account") or "").strip().lower() != (acc.get("email") or "").strip().lower():
            continue  # it answers as someone else now
        if ch.vouched(v, r["target"]):
            out.append((ch, v, r))
    return out


def where(ch, r):
    """Where a message went, as tell() reports it."""
    return store.clean(
        {"channel": ch.key, "label": ch.label, "chat": r.get("chat"), "conversation": r.get("conversation"), "target": r["target"]}
    )


def tell(db, cfg, account, text, links=()):
    """Tell `account`'s own person `text` in their chat apps: through each channel that answers as that account, in the
    private chat it last had with someone it vouches for (routes()). Web addresses in it are broken up as a room's
    answers' are (bridge.safe_links) unless they are in `links` (what the person or Lens wrote) or Lens's own.
    Returns {sent: [{channel, label, chat, conversation, target}], failed: [{channel, error}], error}, `error` saying
    why nothing was sent (NOT_LINKED when no channel can reach them)."""
    from . import bridge

    found = routes(db, cfg, account)
    sent, failed = [], []
    for ch, v, r in found:
        error = None
        try:
            ch.send(v, r["target"], bridge.safe_links(v, db, text, links))
        except (ChannelError, RuntimeError, OSError, ValueError) as e:
            error = str(e) or type(e).__name__
            failed.append({"channel": ch.key, "error": error})
            log.warning("%s: couldn't tell %s: %s", ch.key, r.get("email"), error)
        else:
            sent.append(where(ch, r))
        activity.record(
            "out", f"chat.{ch.key}", [f"account:{r['account']}"], cfg, db, ok=error is None, error="unreachable" if error else None
        )
    error = None if sent else ("; ".join(f"{f['channel']}: {f['error']}" for f in failed) if failed else NOT_LINKED)
    return {"sent": sent, "failed": failed, "error": error}
