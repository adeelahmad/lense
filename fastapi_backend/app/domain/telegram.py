"""The assistant in Telegram, through a bot of your own: paste its token from @BotFather in Settings › Telegram and
turn it on. No Matterbridge, and no public address: Lens asks Telegram for new messages (getUpdates, long polling)
and answers with sendMessage.

Everything that happens in a room is bridge.py's, as for Matterbridge (channels.py): it answers as one Lens account
(telegram.account), with a conversation per person per chat; a chat can be given to a namespace's assistant
(telegram.rooms, "<chat id> = namespace", the ids listed in Settings once Lens has seen the chat); reversible changes
can be approved by replying "yes" (telegram.users); voice notes are transcribed by the server's speech-to-text, and
files are held for the assistant to import.

Anyone can find a bot and message it, so it answers only the people in telegram.users (their Telegram user ids, or
usernames): someone else who writes to it is told once that they need to be let in, and shows in Settings with an
Allow button. Private chats with the bot are always for Lens. In a group it answers what names it ("Lens, …"), mentions the bot
(@your_bot) or replies to it; with BotFather's privacy mode on (the default), Telegram only sends it those anyway.

Refine later: buttons for approvals (inline keyboards), answers read aloud as voice notes, Markdown formatting.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request

from . import auth, channels

API = "https://api.telegram.org"
TIMEOUT = 15
LONG_POLL = 10  # seconds Telegram holds getUpdates open waiting for a message (under channels.LEASE_SECONDS)
MAX_CHARS = 4000  # Telegram allows 4096 per message
MAX_DOWNLOAD = 20 * 1024 * 1024  # what the Bot API hands out
TOKEN_RX = re.compile(r"^[0-9]{3,20}:[A-Za-z0-9_-]{30,64}$")
_BOTS: dict = {}  # token: getMe's answer


class TelegramError(channels.ChannelError):
    pass


def _api(cfg):
    return ((cfg.get("telegram") or {}).get("api_url") or API).rstrip("/")


def _call(cfg, method, body=None, timeout=TIMEOUT):
    token = (cfg.get("telegram") or {}).get("token")
    req = urllib.request.Request(
        f"{_api(cfg)}/bot{token}/{method}",
        data=json.dumps(body or {}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "Lens"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            got = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            said = json.loads(e.read() or b"{}").get("description") or ""
        except ValueError:
            said = ""
        if e.code in (401, 404):
            raise TelegramError("Telegram doesn't know that bot token (check it with @BotFather)") from None
        if e.code == 409:
            raise TelegramError("another program is reading this bot's messages (a webhook, or Matterbridge with the same bot)") from None
        raise TelegramError(f"Telegram answered HTTP {e.code}" + (f": {said}" if said else "")) from None
    except (urllib.error.URLError, OSError) as e:
        raise TelegramError(f"can't reach Telegram at {_api(cfg)}: {getattr(e, 'reason', e)}") from None
    if not got.get("ok"):
        raise TelegramError(f"Telegram said: {got.get('description') or 'no'}")
    return got.get("result")


def bot(cfg):
    """The bot the token is for: {id, username, first_name}."""
    token = cfg["telegram"]["token"]
    if token not in _BOTS:
        _BOTS[token] = _call(cfg, "getMe")
    return _BOTS[token]


# ---------- what was said ----------
def _strip_mention(text, username):
    """The text without the bot's @mention (and /command@bot as /command); whether it was there."""
    if not username:
        return text, False
    rx = re.compile(rf"@{re.escape(username)}\b", re.I)
    found = bool(rx.search(text))
    return re.sub(r"\s{2,}", " ", rx.sub("", text)).strip(" ,:") if found else text, found


def _files(msg):
    out = []
    d = msg.get("document")
    if d:
        out.append({"ref": d["file_id"], "filename": d.get("file_name") or f"file-{msg['message_id']}", "size": d.get("file_size")})
    if msg.get("photo"):  # the sizes it was sent in: the largest
        p = max(msg["photo"], key=lambda x: x.get("file_size") or x.get("width", 0))
        out.append({"ref": p["file_id"], "filename": f"photo-{msg['message_id']}.jpg", "size": p.get("file_size")})
    v = msg.get("video")
    if v:
        out.append({"ref": v["file_id"], "filename": v.get("file_name") or f"video-{msg['message_id']}.mp4", "size": v.get("file_size")})
    return out


def message(me, msg):
    """A Telegram message as a channel message (channels.py), or None for what isn't for anyone (a bot's, a service
    message)."""
    chat, who = msg.get("chat") or {}, msg.get("from") or {}
    if not chat or who.get("is_bot"):
        return None
    private = chat.get("type") == "private"
    text, mentioned = _strip_mention(msg.get("text") or msg.get("caption") or "", me.get("username"))
    if text.split(" ")[0] == "/start":  # what Telegram sends when someone opens the bot
        text = "Hello! What can you do?"
    replied = ((msg.get("reply_to_message") or {}).get("from") or {}).get("id") == me.get("id")
    vn = msg.get("voice") or msg.get("audio") or msg.get("video_note") or {}
    name = who.get("username") or " ".join(x for x in [who.get("first_name"), who.get("last_name")] if x) or str(who.get("id"))
    return {
        "gateway": str(chat["id"]),
        "channel": "" if private else (chat.get("title") or chat.get("username") or ""),
        "account": "telegram",
        "username": name,
        "userid": str(who.get("id") or ""),
        "text": text,
        "event": "",
        "direct": private or mentioned or replied,
        "voice": vn.get("file_id"),
        "files": _files(msg),
        "thread": ["telegram", chat["id"], who.get("id")],
        "reply_to": None if private else msg.get("message_id"),
    }


def _seen(db, msgs):
    """The chats and people Lens has seen, latest first, for Settings (a chat's id is what telegram.rooms names, a
    person's what telegram.users does)."""
    k = channels.state(db, TELEGRAM)
    chats = [c for c in k.get("chats") or [] if isinstance(c, dict)]
    people = [p for p in k.get("people") or [] if isinstance(p, dict)]
    for m in msgs:
        chats = [c for c in chats if c.get("id") != m["gateway"]]
        chats.insert(0, {"id": m["gateway"], "title": m["channel"] or f"private chat with {m['username']}"})
        told = next((p.get("told") for p in people if p.get("id") == m["userid"]), None)
        people = [p for p in people if p.get("id") != m["userid"]]
        people.insert(0, {"id": m["userid"], "name": m["username"], "told": told})
    channels.keep(db, TELEGRAM, chats=chats[:50], people=people[:50])


def allowed(cfg, m):
    """Whether Lens talks to the sender of `m`: they're in telegram.users, by user id or username."""
    users = {u.strip().lstrip("@").lower() for u in cfg["telegram"].get("users") or [] if u.strip()}
    return bool(users) and (m.get("userid") in users or (m.get("username") or "").lower() in users)


STRANGER = (
    "Hi! I'm a private assistant and only talk to people my owner has let in. "
    "They can let you in from Lens, in Settings › Telegram, where you now show."
)


# ---------- the channel ----------
class Telegram(channels.Channel):
    key, label, lease = "telegram", "Telegram", "telegram"

    def ready(self, cfg):
        t = cfg.get("telegram") or {}
        if not t.get("enabled"):
            return "off"
        if not t.get("token"):
            return "the bot's token from @BotFather"
        if not t.get("account"):
            return "a Lens account to answer as"
        return None

    def check(self, db, cfg):
        t = cfg.get("telegram") or {}
        if not t.get("token"):
            return "paste the bot's token from @BotFather first"
        mb = cfg.get("matterbridge") or {}
        if mb.get("run") and mb.get("telegram_token") == t["token"]:
            return "Matterbridge reads this bot too; give Lens a bot of its own, or take it out of Matterbridge"
        _BOTS.pop(t["token"], None)
        try:
            bot(cfg)
        except TelegramError as e:
            return str(e)
        if not t.get("account"):
            return "choose the Lens account it answers as"
        u = auth.find_account(db, t["account"])
        if not u or u.get("disabled"):
            return f"there's no active Lens account {t['account']}"
        return None

    def fetch(self, db, cfg):
        me = bot(cfg)
        offset = channels.state(db, self).get("offset")
        body = {"timeout": LONG_POLL, "allowed_updates": ["message"]}
        if offset:
            body["offset"] = offset
        got = _call(cfg, "getUpdates", body, timeout=TIMEOUT + LONG_POLL) or []
        if got:  # the next look confirms these, and Telegram forgets them
            channels.keep(db, self, offset=max(u["update_id"] for u in got) + 1)
        msgs = [m for m in (message(me, u["message"]) for u in got if u.get("message")) if m]
        if msgs:
            _seen(db, msgs)
        return msgs

    def handle(self, db, cfg, base, m):
        if allowed(cfg, m):
            return super().handle(db, cfg, base, m)
        if m.get("direct"):  # someone not let in: told once, and listed in Settings to allow
            people = channels.state(db, self).get("people") or []
            if not any(p.get("id") == m["userid"] and p.get("told") for p in people):
                self.send(cfg, m, STRANGER)
                channels.keep(db, self, people=[{**p, "told": True} if p.get("id") == m["userid"] else p for p in people])
        return False

    def send(self, cfg, m, text):
        text = text or "(no answer)"
        for i in range(0, len(text), MAX_CHARS):
            body = {"chat_id": m["gateway"], "text": text[i : i + MAX_CHARS]}
            if m.get("reply_to") and i == 0:
                body["reply_parameters"] = {"message_id": m["reply_to"], "allow_sending_without_reply": True}
            _call(cfg, "sendMessage", body)

    def download(self, cfg, ref):
        f = _call(cfg, "getFile", {"file_id": ref})
        if (f.get("file_size") or 0) > MAX_DOWNLOAD or not f.get("file_path"):
            raise TelegramError("Telegram only hands bots files up to 20 MB; upload it in the web app instead")
        url = f"{_api(cfg)}/file/bot{cfg['telegram']['token']}/{urllib.parse.quote(f['file_path'])}"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Lens"}), timeout=60) as r:
                data = r.read(MAX_DOWNLOAD + 1)
        except (urllib.error.URLError, OSError) as e:
            raise TelegramError(f"couldn't fetch the file from Telegram: {getattr(e, 'reason', e)}") from None
        if len(data) > MAX_DOWNLOAD:
            raise TelegramError("Telegram only hands bots files up to 20 MB; upload it in the web app instead")
        return data

    def pause(self, cfg, reading, failed):
        if failed:
            return 5
        return 0 if reading else 2  # reading: getUpdates did the waiting


TELEGRAM = Telegram()


def status(db, cfg):
    """channels.status, with the bot's name and the chats and people it has seen (whether each is let in)."""
    s = channels.status(db, cfg, TELEGRAM)
    k = channels.state(db, TELEGRAM)
    token = (cfg.get("telegram") or {}).get("token")
    s["bot"] = (_BOTS.get(token) or {}).get("username") if token else None
    s["chats"] = [c for c in k.get("chats") or [] if isinstance(c, dict)]
    s["people"] = [
        {"id": p["id"], "name": p.get("name") or p["id"], "allowed": allowed(cfg, {"userid": p["id"], "username": p.get("name")})}
        for p in k.get("people") or []
        if isinstance(p, dict) and p.get("id")
    ]
    return s
