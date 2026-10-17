"""Automation actions that tell people something (docs/automations.md, Actions): `notify`.

`notify` sends a message to one of three places, named by its `to`:

- `me`: the automation's own person, in their chat apps (channels.tell: the private chat each Telegram bot or Slack app
  that answers as their account last had with them). Only they read it, so it runs without asking (effect `self`).
- `target:<id>`: one of the namespace's notification targets (notify.py: a signed webhook, Matterbridge, Slack or
  Discord), sent the way notifications are.
- `gateway:<name>`: a Matterbridge gateway (a room) through the chat-room bridge (bridge.py).

The last two reach other people, so they ask first unless the person set that node to run on its own (effect `out`).
Web addresses in the message are broken up (untrusted.defang) unless Lens wrote them or the run read nothing from
outside Lens, since chat apps fetch what they link before anyone clicks: an address built from an email's text would
carry it out.
"""

from __future__ import annotations

import re

from . import actions, activity, auth, bridge, channels, notify, store, untrusted

TARGET_RX = re.compile(r"^(?:target:)?(\d{1,12})$")
GATEWAY_RX = re.compile(r"^gateway:(\S{1,120})$")
MAX_TEXT = 4000


def _to(p):
    return str(p.get("to") or "me").strip()


def _effect(p):
    return "self" if _to(p) == "me" else "out"


def _link(db, cfg, link):
    """A link as people open it: Lens's own paths (/resources/7) on the web app's address."""
    link = str(link or "").strip()
    if link.startswith("/") and not link.startswith("//"):
        from app.email import app_url

        return app_url(cfg, db) + link
    return link


def _own(cfg, db):
    from app.email import app_url

    return [app_url(cfg, db)]


def _text(db, cfg, p):
    """(the message, its link), as sent."""
    text = str(p.get("text") or "").strip()[:MAX_TEXT]
    if not text:
        raise ValueError("notify: the message is empty")
    return text, _link(db, cfg, p.get("link"))


def _keep(intent, text):
    """The web addresses that stay as written: all of them when the run read nothing from outside Lens (the person
    and Lens wrote the message), else only Lens's own (left to defang)."""
    return () if intent.get("untrusted") else untrusted.links(text)


def _me(db, cfg, intent, user, dry, text):
    acc = actions.person(db, intent, user)
    if dry:
        found = [channels.where(ch, r) for ch, _, r in channels.routes(db, cfg, acc)]
        return {"to": "me", "text": text, "sent": [], "would": found, "error": None if found else channels.NOT_LINKED}
    got = channels.tell(db, cfg, acc, text, _keep(intent, text))
    if not got["sent"]:
        raise ValueError(f"couldn't tell {acc['name']}: {got['error']}")
    return {"to": "me", "text": text, "sent": got["sent"], "failed": got["failed"]}


def _target(db, cfg, intent, user, dry, tid, text, link):
    try:
        t = notify.get(db, tid)
    except KeyError:
        raise ValueError(f"there's no notification target {tid}") from None
    if intent["spaces"] and t["space"] not in intent["spaces"]:
        raise ValueError(f"notification target {t['name']} isn't in this automation's namespaces")
    acc = actions.person(db, intent, user)
    if not auth.allows(auth.roles(db, acc), t["space"], "editor"):
        raise ValueError(f"{acc['name']} can't send to {t['name']}: it needs someone who can edit its namespace")
    if not t.get("enabled"):
        raise ValueError(f"notification target {t['name']} is switched off")
    out = {"to": f"target:{tid}", "text": text}
    if dry:
        return {**out, "sent": [], "would": [{"target": tid, "name": t["name"], "kind": t["kind"]}]}
    safe = untrusted.defang(text, _keep(intent, text), _own(cfg, db))
    ns = store.space_names(db).get(t["space"])
    event = notify._event(
        "automation.message",
        f"automation-{intent['key']}",
        t["space"],
        ns,
        safe,
        link or None,
        {"automation": intent.get("automation"), "run": intent.get("run"), "node": intent["node"]},
    )
    got = notify.send(db, cfg, t, event, event["id"])
    if not got["ok"]:
        raise ValueError(f"notification target {t['name']}: {got['error']}")
    return {**out, "sent": [{"target": tid, "name": t["name"], "kind": t["kind"], "code": got["code"]}]}


def _gateway(db, cfg, intent, user, dry, gateway, text):
    why = bridge.ready(cfg)
    if why:
        raise ValueError("the chat-room bridge isn't on" if why == "off" else f"the chat-room bridge isn't set up: {why}")
    acc = actions.person(db, intent, user)
    answers_as = (cfg["bridge"].get("account") or "").strip().lower()
    if not acc["admin"] and answers_as != acc["email"].lower():
        raise ValueError("Matterbridge answers as someone else: only they or an admin can post to its gateways")
    out = {"to": f"gateway:{gateway}", "text": text}
    if dry:
        return {**out, "sent": [], "would": [{"gateway": gateway}]}
    error = None
    try:
        bridge.post(cfg, gateway, bridge.safe_links(cfg, db, text, _keep(intent, text)))
    except (RuntimeError, OSError, ValueError) as e:
        error = str(e) or type(e).__name__
    activity.record("out", "chat.bridge", [], cfg, db, ok=error is None, error="unreachable" if error else None)
    if error:
        raise ValueError(f"Matterbridge gateway {gateway}: {error}")
    return {**out, "sent": [{"channel": "bridge", "label": "Matterbridge", "gateway": gateway}]}


def _notify(db, cfg, intent, user, dry):
    p = intent["params"]
    to = _to(p)
    text, link = _text(db, cfg, p)
    if to == "me":
        return _me(db, cfg, intent, user, dry, text + (f"\n{link}" if link else ""))
    m = TARGET_RX.match(to)
    if m:
        return _target(db, cfg, intent, user, dry, int(m.group(1)), text, link)
    m = GATEWAY_RX.match(to)
    if m:
        return _gateway(db, cfg, intent, user, dry, m.group(1), text + (f"\n{link}" if link else ""))
    raise ValueError(f"notify: send to me, target:<id> or gateway:<name>, not {to}")


def _short(text, n=80):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _describe(p):
    to, said = _to(p), _short(p.get("text"))
    if to == "me":
        return f"Tell me in my chat app: “{said}”"
    m = GATEWAY_RX.match(to)
    if m:
        return f"Post in the Matterbridge gateway {m.group(1)}: “{said}”"
    m = TARGET_RX.match(to)
    return f"Send to notification target {m.group(1)}: “{said}”" if m else f"Send to {to}: “{said}”"


actions.register(
    actions.ActionSpec(
        name="notify",
        label="Send a message",
        group="Messages",
        description=(
            "Tells you in your own chat app (Telegram or Slack, where you last talked to Lens privately), or sends to a "
            "notification target or a Matterbridge room, which asks first."
        ),
        params={
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "title": "To",
                    "default": "me",
                    "maxLength": 200,
                    "description": "me, target:<notification target id> or gateway:<Matterbridge gateway>",
                },
                "text": {"type": "string", "title": "Message", "maxLength": MAX_TEXT},
                "link": {
                    "type": "string",
                    "title": "Link",
                    "maxLength": 2000,
                    "description": "Lens's own paths (/resources/7) become full links",
                },
            },
        },
        required=("text",),
        effect=_effect,
        output={
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "text": {"type": "string"},
                "sent": {"type": "array", "items": {"type": "object"}},
                "failed": {"type": "array", "items": {"type": "object"}},
            },
        },
        perform=_notify,
        describe=_describe,
    )
)
