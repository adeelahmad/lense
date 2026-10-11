"""The assistant in Slack, through a Slack app of your own over Socket Mode (https://api.slack.com/apis/socket-mode):
no Matterbridge, and no public address.

Setting it up: Settings › Slack has a link that creates the app from its manifest (manifest()) in one click. Install
it to the workspace and paste two tokens back: the bot token (xoxb-…, from OAuth & Permissions) and an app-level token
with connections:write (xapp-…, from Basic Information). Then invite it to channels with /invite, or message it.

Everything that happens in a room is bridge.py's, as for the other chat channels (channels.py): it answers as one Lens
account (slack.account), with a conversation per person per channel; a channel can be given to a namespace's
assistant (slack.rooms, "slack/<channel name> = namespace"); reversible changes can be approved by replying "yes"
(slack.users); voice clips are transcribed and files are held for the assistant to import. A direct message is always
for Lens; in a channel it answers what names it ("Lens, …" or @mentions the app), or everything with answer "all".
The slash command (/lens <question>, /lens clear, /lens compact) answers in the channel it was typed in.

The process holding the channel's lease keeps the socket open (Listener) and acknowledges every envelope as it comes
(Slack wants that within 3 seconds); fetch() hands what was said to the channel thread, which answers in order.

Refine later: Block Kit buttons for approvals, the app's Home tab, streaming an answer by editing the message.
"""

from __future__ import annotations

import json
import logging
import queue
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import auth, channels

log = logging.getLogger(__name__)
API = "https://slack.com/api"
TIMEOUT = 15
MAX_CHARS = 3900  # Slack shows up to 4,000 characters of a message's text before cutting it
MAX_DOWNLOAD = 50 * 1024 * 1024
FILE_HOSTS = ("files.slack.com",)  # where the bot token may be sent to fetch a file
RESPONSE_HOSTS = ("hooks.slack.com",)  # a slash command's response address
SCOPES = [
    "app_mentions:read",
    "channels:history",
    "channels:read",
    "chat:write",
    "commands",
    "files:read",
    "groups:history",
    "groups:read",
    "im:history",
    "im:read",
    "mpim:history",
    "mpim:read",
    "users:read",
]
EVENTS = ["message.channels", "message.groups", "message.im", "message.mpim"]
AUDIO = ("audio/", "video/mp4", "video/webm", "video/quicktime")  # Slack's voice clips are audio or video files


class SlackError(channels.ChannelError):
    pass


def _s(cfg):
    return cfg.get("slack") or {}


# ---------- the app ----------
def command(cfg):
    """The slash command: /lens, or the assistant's name in lower case."""
    name = re.sub(r"[^a-z0-9_-]+", "-", (_s(cfg).get("name") or "Lens").strip().lower()).strip("-")
    return "/" + (name or "lens")[:31]


def manifest(cfg):
    """The Slack app, as a manifest (https://api.slack.com/reference/manifests): a bot that reads the channels it's
    in and its direct messages, with a slash command, over Socket Mode."""
    name = (_s(cfg).get("name") or "Lens").strip()[:35]
    return {
        "display_information": {
            "name": name,
            "description": "Lens: intelligence for your agent. Ask about everything Lens knows, with sources.",
        },
        "features": {
            "app_home": {"messages_tab_enabled": True, "messages_tab_read_only_enabled": False},
            "bot_user": {"display_name": name, "always_online": True},
            "slash_commands": [
                {
                    "command": command(cfg),
                    "description": f"Ask {name}",
                    "usage_hint": "[question] | clear | compact",
                    "should_escape": False,
                }
            ],
        },
        "oauth_config": {"scopes": {"bot": SCOPES}},
        "settings": {
            "event_subscriptions": {"bot_events": EVENTS},
            "interactivity": {"is_enabled": False},
            "org_deploy_enabled": False,
            "socket_mode_enabled": True,
            "token_rotation_enabled": False,
        },
    }


def create_url(cfg):
    """Where the app is created from its manifest, in one click."""
    return "https://api.slack.com/apps?" + urllib.parse.urlencode({"new_app": 1, "manifest_json": json.dumps(manifest(cfg))})


# ---------- Slack's Web API ----------
HINTS = {
    "invalid_auth": " (check the token)",
    "not_authed": " (check the token)",
    "not_allowed_token_type": " (the app-level token starts xapp-, the bot token xoxb-)",
    "not_in_channel": " (invite the app to the channel: /invite @Lens)",
    "missing_scope": " (create the app again from the link in Settings › Slack)",
}


def api(token, method, body=None):
    """Call a Web API method; its answer, which says ok."""
    req = urllib.request.Request(
        f"{API}/{method}",
        data=json.dumps(body or {}).encode(),
        headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": "Lens", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            got = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise SlackError(f"Slack answered HTTP {e.code} to {method}") from None
    except (urllib.error.URLError, OSError) as e:
        raise SlackError(f"can't reach Slack: {getattr(e, 'reason', e)}") from None
    except ValueError:
        raise SlackError(f"Slack's answer to {method} wasn't JSON") from None
    if not got.get("ok"):
        err = got.get("error") or "unknown error"
        raise SlackError(f"Slack said {err} to {method}{HINTS.get(err, '')}")
    return got


def respond(url, text):
    """Answer a slash command, in the channel it was typed in, through its response address."""
    if urllib.parse.urlsplit(url).hostname not in RESPONSE_HOSTS:
        raise SlackError("that isn't a Slack response address")
    req = urllib.request.Request(
        url,
        data=json.dumps({"response_type": "in_channel", "text": text}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "Lens"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT):
            pass
    except (urllib.error.URLError, OSError) as e:
        raise SlackError(f"can't answer the slash command: {getattr(e, 'reason', e)}") from None


def bot(cfg):
    """Who the bot token is: {user_id, user, team, team_id}."""
    return api(_s(cfg)["bot_token"], "auth.test")


# ---------- what was said ----------
class Names:
    """Slack ids to names (people and channels), each looked up once."""

    def __init__(self, token):
        self.token, self.people, self.rooms = token, {}, {}

    def person(self, uid):
        if uid not in self.people:
            try:
                u = api(self.token, "users.info", {"user": uid}).get("user") or {}
            except SlackError:
                return uid
            p = u.get("profile") or {}
            self.people[uid] = u.get("name") or p.get("display_name") or p.get("real_name") or uid
        return self.people[uid]

    def room(self, cid):
        if cid not in self.rooms:
            try:
                c = api(self.token, "conversations.info", {"channel": cid}).get("channel") or {}
            except SlackError:
                return cid
            self.rooms[cid] = "" if c.get("is_im") else c.get("name") or cid
        return self.rooms[cid]


def plain(text, me, names):
    """Slack's markup as plain text, and whether it mentions the bot (`me`): other people's mentions become their
    names, channels #name, links their text and address."""
    found = bool(me) and bool(re.search(rf"<@{re.escape(me)}(?:\|[^>]*)?>", text))
    if me:
        text = re.sub(rf"\s*<@{re.escape(me)}(?:\|[^>]*)?>[,:]?\s*", " ", text)
    text = re.sub(r"<@([A-Z0-9]+)(?:\|([^>]*))?>", lambda m: "@" + (m.group(2) or names.person(m.group(1))), text)
    text = re.sub(r"<#[A-Z0-9]+\|([^>]*)>", r"#\1", text)
    text = re.sub(r"<(https?://[^|>]+)\|([^>]+)>", r"\2 (\1)", text)
    text = re.sub(r"<(https?://[^>]+)>", r"\1", text)
    text = re.sub(r"<!(here|channel|everyone)(?:\|[^>]*)?>", r"@\1", text)
    return re.sub(r"\s{2,}", " ", text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")).strip(), found


def _clip(f):
    return bool(f.get("url_private")) and (f.get("subtype") == "slack_audio" or (f.get("mimetype") or "").startswith(AUDIO))


def message(me, team, event, names):
    """A Slack message event as a channel message (channels.py), or None for what isn't for anyone (its own or another
    bot's, an edit, someone joining)."""
    if event.get("type") != "message" or event.get("bot_id") or event.get("subtype") not in (None, "file_share", "thread_broadcast"):
        return None
    uid, cid = event.get("user"), event.get("channel")
    if not uid or uid == me or not cid:
        return None
    text, mentioned = plain(event.get("text") or "", me, names)
    private = event.get("channel_type") == "im"
    files = [f for f in event.get("files") or [] if f.get("url_private") and f.get("mode") != "tombstone"]
    clip = next((f for f in files if _clip(f)), None) if not text else None  # a voice clip with nothing typed
    return {
        "gateway": "slack",
        "channel": "" if private else names.room(cid),
        "account": "slack",
        "username": names.person(uid),
        "userid": uid,
        "text": text,
        "event": "",
        "direct": private or mentioned,
        "voice": clip["url_private"] if clip else None,
        "files": [
            {"ref": f["url_private"], "filename": f.get("name") or f.get("title") or "file", "size": f.get("size")}
            for f in files
            if f is not clip
        ],
        "thread": ["slack", team, cid, uid],
        "to": {"channel": cid, "thread": event.get("thread_ts")},
    }


def slash(me, team, p, names):
    """A slash command as a channel message, always for Lens: /lens what's new; /lens clear and /lens compact [what to
    keep] are the chat commands."""
    words = (p.get("text") or "").strip()
    first = words.split(" ", 1)[0].lower()
    text = "/" + first + words[len(first) :] if first in ("clear", "compact") else plain(words, me, names)[0]
    cid, uid = p.get("channel_id") or "", p.get("user_id") or ""
    private = p.get("channel_name") == "directmessage"
    return {
        "gateway": "slack",
        "channel": "" if private else (p.get("channel_name") or names.room(cid)),
        "account": "slack",
        "username": p.get("user_name") or names.person(uid),
        "userid": uid,
        "text": text or "Hello! What can you do?",
        "event": "",
        "direct": True,
        "voice": None,
        "files": [],
        "thread": ["slack", team, cid, uid],
        "to": {"channel": cid, "response_url": p.get("response_url")},
    }


# ---------- the socket ----------
def _connect(url):  # replaced in tests
    from websockets.sync.client import connect

    return connect(url, open_timeout=TIMEOUT, close_timeout=2, max_size=2**22)


class Listener:
    """The Socket Mode connection of this process, while it holds the channel's lease: every envelope acknowledged as
    it comes, and the messages in it queued for fetch(). It closes when the tokens change, `stop` is set, or nobody
    has fetched for a while (another process took the lease)."""

    IDLE = channels.LEASE_SECONDS

    def __init__(self, cfg):
        s = _s(cfg)
        self.key = (s.get("bot_token"), s.get("app_token"))
        self.todo: queue.Queue = queue.Queue()
        self.stop = threading.Event()
        self.fetched = time.monotonic()
        self.error = None
        self.taken = 0  # envelopes read off the socket and dealt with
        self.me = bot(cfg)  # SlackError when the bot token is wrong: said before anything starts
        self.names = Names(s["bot_token"])
        self.thread = threading.Thread(target=self.run, args=(cfg,), daemon=True, name="slack-socket")
        self.thread.start()

    def alive(self):
        return self.thread.is_alive()

    def take(self, wait=1.0):
        """What was said since the last take, waiting up to `wait` seconds for the first."""
        self.fetched = time.monotonic()
        out = []
        try:
            out.append(self.todo.get(timeout=wait))
            while True:
                out.append(self.todo.get_nowait())
        except queue.Empty:
            pass
        return out

    def envelope(self, ws, env):
        """One envelope: acknowledged, and its message queued. False when Slack asks to reconnect."""
        if env.get("type") == "disconnect":
            return False
        if env.get("envelope_id"):
            ws.send(json.dumps({"envelope_id": env["envelope_id"]}))
        p, m = env.get("payload") or {}, None
        me, team = self.me.get("user_id"), self.me.get("team_id")
        if env.get("type") == "events_api":
            m = message(me, team, p.get("event") or {}, self.names)
        elif env.get("type") == "slash_commands":
            m = slash(me, team, p, self.names)
        if m:
            self.todo.put(m)
        self.taken += 1
        return True

    def run(self, cfg):
        pause = 1
        while not self.stop.is_set() and time.monotonic() - self.fetched < self.IDLE:
            try:
                url = api(_s(cfg)["app_token"], "apps.connections.open")["url"]
                with _connect(url) as ws:
                    self.error, pause = None, 1
                    while not self.stop.is_set() and time.monotonic() - self.fetched < self.IDLE:
                        try:
                            raw = ws.recv(timeout=1)
                        except TimeoutError:
                            continue
                        try:
                            env = json.loads(raw)
                        except ValueError:
                            continue
                        if not self.envelope(ws, env):
                            break
            except Exception as e:  # noqa: BLE001 - said in the status; tried again, waiting longer each time
                self.error = str(e)
                log.warning("slack: %s", e)
                self.stop.wait(pause)
                pause = min(60, pause * 2)


_LISTENERS: dict = {}  # the channel's Listener in this process
_LOCK = threading.Lock()


def listener(cfg):
    """This process's Listener for the current tokens, started (again) when needed."""
    s = _s(cfg)
    with _LOCK:
        old = _LISTENERS.get("socket")
        if old and old.alive() and old.key == (s.get("bot_token"), s.get("app_token")):
            return old
        if old:
            old.stop.set()
        _LISTENERS["socket"] = Listener(cfg)
        return _LISTENERS["socket"]


class _SlackOnly(urllib.request.HTTPRedirectHandler):
    """Redirects followed only within Slack's file hosts: urllib carries the Authorization header along."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).hostname not in FILE_HOSTS:
            raise SlackError("Slack sent the file somewhere else; download it in Slack and upload it in the web app")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# ---------- the channel ----------
class Slack(channels.Channel):
    key, label, lease = "slack", "Slack", "slack"

    def ready(self, cfg):
        s = _s(cfg)
        if not s.get("enabled"):
            return "off"
        if not s.get("bot_token"):
            return "the app's bot token (xoxb-…)"
        if not s.get("app_token"):
            return "the app-level token (xapp-…)"
        if not s.get("account"):
            return "a Lens account to answer as"
        return None

    def check(self, db, cfg):
        s = _s(cfg)
        if not s.get("bot_token") or not s.get("app_token"):
            return "paste both tokens first: the bot token (xoxb-…) and the app-level token (xapp-…)"
        mb = cfg.get("matterbridge") or {}
        if mb.get("run") and mb.get("slack_token") == s["bot_token"]:
            return "Matterbridge uses this Slack app too; give Lens an app of its own, or take it out of Matterbridge"
        try:
            bot(cfg)
            api(s["app_token"], "apps.connections.open")
        except SlackError as e:
            return str(e)
        if not s.get("account"):
            return "choose the Lens account it answers as"
        u = auth.find_account(db, s["account"])
        if not u or u.get("disabled"):
            return f"there's no active Lens account {s['account']}"
        return None

    def fetch(self, db, cfg):
        lst = listener(cfg)
        got = lst.take()
        channels.keep(db, self, team=lst.me.get("team"), bot=lst.me.get("user"))
        if lst.error and not got:
            raise SlackError(lst.error)
        return got

    def send(self, cfg, m, text):
        text = text or "(no answer)"
        to = m.get("to") or {}
        for i in range(0, len(text), MAX_CHARS):
            part = text[i : i + MAX_CHARS]
            if to.get("response_url"):
                respond(to["response_url"], part)
                continue
            body = {"channel": to["channel"], "text": part, "unfurl_links": False, "unfurl_media": False}
            if to.get("thread"):
                body["thread_ts"] = to["thread"]
            api(_s(cfg)["bot_token"], "chat.postMessage", body)

    def download(self, cfg, ref):
        if urllib.parse.urlsplit(ref).hostname not in FILE_HOSTS:  # the bot token goes only to Slack
            raise SlackError("that isn't a Slack file address")
        req = urllib.request.Request(ref, headers={"Authorization": f"Bearer {_s(cfg)['bot_token']}", "User-Agent": "Lens"})
        try:
            with urllib.request.build_opener(_SlackOnly).open(req, timeout=60) as r:
                data = r.read(MAX_DOWNLOAD + 1)
        except (urllib.error.URLError, OSError) as e:
            raise SlackError(f"couldn't fetch the file from Slack: {getattr(e, 'reason', e)}") from None
        if len(data) > MAX_DOWNLOAD:
            raise SlackError("that file is over 50 MB; upload it in the web app instead")
        return data

    def pause(self, cfg, reading, failed):
        if failed:
            return 5
        return 0 if reading else 2  # reading: take() did the waiting


SLACK = Slack()


def status(db, cfg):
    """channels.status, with the workspace and bot it's connected as, the slash command and the link that creates the
    app."""
    s = channels.status(db, cfg, SLACK)
    k = channels.state(db, SLACK)
    return {**s, "team": k.get("team"), "bot": k.get("bot"), "command": command(cfg), "create_url": create_url(cfg)}
