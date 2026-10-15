"""Automations telling people things (docs/automations.md, Actions): each chat channel keeps the private chat it last
had with someone it vouches for, channels.tell sends there as the account's person, and the `notify` action reaches
them, a notification target or a Matterbridge room, with links an injection could fill broken up."""

from __future__ import annotations

import pytest

from app.domain import actions, channels, notify, slack, store, telegram
from tests import fake_llm, fake_matterbridge, fake_slack, fake_telegram
from tests.api._assist import start_llm
from tests.api.test_notifications import Inbox
from tests.helpers import make_user, seed

update = fake_telegram.update
EVIL = "https://evil.example/x?d=secret"


@pytest.fixture
def tg():
    srv, url = fake_telegram.start()
    telegram._BOTS.clear()
    yield fake_telegram.Handler, url
    srv.shutdown()


@pytest.fixture
def llm(cfg):
    srv = start_llm(cfg)
    yield fake_llm.Handler
    srv.shutdown()


@pytest.fixture
def app(cfg, db, llm):
    from app.main import create_app

    return create_app(cfg, db, background=False)


@pytest.fixture
def people(app, db, cfg, folder):
    seed(db, cfg, folder)
    cfg["ai"]["tools"] = False
    return {
        "me": make_user(db, "me@x.io", "my password 1", roles={"pods": "editor"}),
        "other": make_user(db, "other@x.io", "other password 1", roles={"pods": "viewer"}),
        "root": make_user(db, "root@x.io", "root password 1", admin=True),
        "pods": store.ns_id(db, "pods"),
    }


@pytest.fixture
def bot(people, cfg, tg):
    """The Telegram bot answering as me@x.io, letting alice, bob and user 10 in."""
    h, url = tg
    cfg["telegram"] = {
        **cfg["telegram"],
        "enabled": True,
        "api_url": url,
        "token": fake_telegram.TOKEN,
        "account": "me@x.io",
        "users": ["alice", "bob", "10"],
    }
    return h


def tick(db, cfg):
    return channels.tick(db, cfg, cfg, "api-1", telegram.TELEGRAM)


def intent(people, **kw):
    params = kw.pop("params", {})
    return {
        "key": "51-n7",
        "action": "notify",
        "params": params,
        "effect": "self",
        "risk": "routine",
        "untrusted": True,
        "spaces": [people["pods"]],
        "run_as": people["me"],
        "node": "n7",
        "automation": 4,
        "run": 51,
        **kw,
    }


def test_the_private_chat_last_had_with_someone_let_in_is_where_a_person_is_told_things(db, cfg, bot, people):
    bot.updates = [
        update("Lens, hi", chat_id=-100, chat_type="group", title="Team"),  # a room reaches other people
        update("hi", user="eve", user_id=66, chat_id=66),  # not let in
    ]
    tick(db, cfg)
    assert not db.rows("SELECT * FROM chat_route")
    assert channels.tell(db, cfg, people["me"], "hello") == {"sent": [], "failed": [], "error": channels.NOT_LINKED}
    bot.updates = [update("what's new?")]  # alice, privately
    assert tick(db, cfg) == 1
    route = db.one("SELECT * OMIT id FROM chat_route:telegram")
    assert route["account"] == people["me"] and route["chat"] == "private chat with alice" and route["conversation"]
    bot.sent = []
    got = channels.tell(db, cfg, people["me"], f"Trip on Friday {EVIL}")
    assert [(s["channel"], s["chat"]) for s in got["sent"]] == [("telegram", "private chat with alice")] and not got["error"]
    assert bot.sent == [{"chat_id": "42", "text": "Trip on Friday hxxps://evil[.]example/x?d=secret"}]  # defanged
    channels.tell(db, cfg, people["me"], f"Trip {EVIL}", links={EVIL})  # what the person or Lens wrote stays
    assert bot.sent[-1]["text"] == f"Trip {EVIL}"
    # the latest private chat wins; a group message doesn't move it
    bot.updates = [update("hello", user="bob", user_id=8, chat_id=43), update("Lens, hi", user="bob", user_id=8, chat_id=-100, chat_type="group")]
    tick(db, cfg)
    channels.tell(db, cfg, people["me"], "hello bob")
    assert bot.sent[-1]["chat_id"] == "43"
    # only as the account the bot answers as, and only to someone it still lets in
    assert channels.tell(db, cfg, people["other"], "x")["error"] == channels.NOT_LINKED
    cfg["telegram"]["users"] = ["alice"]
    assert not channels.routes(db, cfg, people["me"])
    cfg["telegram"]["users"] = ["alice", "bob"]
    cfg["telegram"]["account"] = "other@x.io"
    assert not channels.routes(db, cfg, people["me"])
    cfg["telegram"]["account"] = "me@x.io"
    cfg["telegram"]["enabled"] = False
    assert not channels.routes(db, cfg, people["me"])


def test_notify_tells_me_in_my_chat_app(db, cfg, bot, people):
    bot.updates = [update("hi")]
    tick(db, cfg)
    bot.sent = []
    notify_ = actions.get("notify")
    ctx = {"automation": {"id": 4, "spaces": [people["pods"]]}, "node": "n7"}
    assert actions.resolve_risk(notify_, {"to": "me", "text": "x"}, ctx) == "routine"  # only they read it
    assert actions.resolve_risk(notify_, {"text": "x"}, ctx) == "routine"  # to me unless said otherwise
    for to in ("target:3", "gateway:family", "{{ vars.room }}"):  # anywhere else, or a reach a template decides, asks
        assert actions.resolve_risk(notify_, {"to": to, "text": "x"}, ctx) == "ask"
    assert actions.resolve_risk(notify_, {"to": "gateway:family", "text": "x"}, ctx, {"n7": "auto"}) == "routine"
    assert actions.describe("notify", {"to": "me", "text": "Trip on Friday"}) == "Tell me in my chat app: “Trip on Friday”"
    i = intent(people, params={"text": f"Trip on Friday, see {EVIL}", "link": "/resources/7"})
    dry = actions.perform(db, cfg, i, {"id": people["me"]}, dry=True)
    assert dry["sent"] == [] and dry["would"][0]["chat"] == "private chat with alice" and not bot.sent
    got = actions.perform(db, cfg, i, {"id": people["me"]})
    assert got["to"] == "me" and got["sent"][0]["channel"] == "telegram"
    from app.email import app_url

    text = bot.sent[-1]["text"]
    assert "hxxps://evil[.]example" in text and text.endswith(f"\n{app_url(cfg, db)}/resources/7")  # Lens's own link stays
    # a run that read nothing from outside Lens: the person wrote every link
    actions.perform(db, cfg, intent(people, untrusted=False, params={"text": f"see {EVIL}"}), {"id": people["me"]})
    assert bot.sent[-1]["text"] == f"see {EVIL}"
    with pytest.raises(ValueError, match="couldn't tell other: no chat app linked"):
        actions.perform(db, cfg, intent(people, run_as=people["other"], params={"text": "x"}), None)
    db.q("UPDATE $r SET disabled = true", r=store.R("account", people["other"]))
    with pytest.raises(ValueError, match="acts for nobody"):
        actions.perform(db, cfg, intent(people, run_as=people["other"], params={"text": "x"}), None)
    with pytest.raises(ValueError, match="send to me, target:<id> or gateway:<name>"):
        actions.perform(db, cfg, intent(people, params={"to": "everyone", "text": "x"}), None)


def test_notify_sends_to_a_notification_target(db, cfg, people):
    inbox = Inbox()
    try:
        cfg["notifications"] = {**(cfg.get("notifications") or {}), "networks": ["127.0.0.0/8"]}
        t, _ = notify.create(db, cfg, people["pods"], "Family hook", "webhook", inbox.url + "/hook")
        i = intent(people, effect="out", risk="ask", params={"to": f"target:{t['id']}", "text": f"Trip {EVIL}", "link": "/resources/7"})
        assert actions.perform(db, cfg, i, None, dry=True)["would"] == [{"target": t["id"], "name": "Family hook", "kind": "webhook"}]
        assert not inbox.got
        got = actions.perform(db, cfg, i, None)
        assert got["sent"] == [{"target": t["id"], "name": "Family hook", "kind": "webhook", "code": 200}]
        body = inbox.json()
        assert body["type"] == "automation.message" and body["text"] == "Trip hxxps://evil[.]example/x?d=secret"
        assert body["url"].endswith("/resources/7") and body["data"] == {"automation": 4, "run": 51, "node": "n7"}
        assert inbox.got[-1]["headers"]["webhook-signature"].startswith("v1,")
        assert actions.perform(db, cfg, {**i, "params": {**i["params"], "to": str(t["id"])}}, None)["sent"]  # a bare id too
        with pytest.raises(ValueError, match="isn't in this automation's namespaces"):
            actions.perform(db, cfg, {**i, "spaces": [store.ns_id(db, "calls")]}, None)
        with pytest.raises(ValueError, match="other can't send to Family hook"):
            actions.perform(db, cfg, {**i, "run_as": people["other"]}, None)  # a viewer of pods
        notify.update(db, cfg, t["id"], people["pods"], {"enabled": False})
        with pytest.raises(ValueError, match="Family hook is switched off"):
            actions.perform(db, cfg, i, None)
        with pytest.raises(ValueError, match="there's no notification target 999"):
            actions.perform(db, cfg, {**i, "params": {"to": "target:999", "text": "x"}}, None)
        inbox.status = 500
        notify.update(db, cfg, t["id"], people["pods"], {"enabled": True})
        with pytest.raises(ValueError, match="Family hook: HTTP 500"):
            actions.perform(db, cfg, i, None)
    finally:
        inbox.srv.shutdown()


def test_notify_posts_in_a_matterbridge_gateway(db, cfg, people):
    srv, url = fake_matterbridge.start()
    try:
        h = fake_matterbridge.Handler
        h.posted = []
        i = intent(people, effect="out", risk="ask", params={"to": "gateway:family", "text": f"Trip {EVIL}"})
        with pytest.raises(ValueError, match="the chat-room bridge isn't on"):
            actions.perform(db, cfg, i, None)
        cfg["bridge"] = {**cfg["bridge"], "enabled": True, "url": url, "token": "mb-token", "account": "root@x.io"}
        with pytest.raises(ValueError, match="Matterbridge answers as someone else"):
            actions.perform(db, cfg, i, None)
        cfg["bridge"]["account"] = "me@x.io"
        assert actions.perform(db, cfg, i, None, dry=True)["would"] == [{"gateway": "family"}] and not h.posted
        got = actions.perform(db, cfg, i, None)
        assert got["sent"] == [{"channel": "bridge", "label": "Matterbridge", "gateway": "family"}]
        assert h.posted[-1]["gateway"] == "family" and h.posted[-1]["text"] == "Trip hxxps://evil[.]example/x?d=secret"
        cfg["bridge"]["account"] = "other@x.io"  # an admin's automation may post as the bridge's account
        assert actions.perform(db, cfg, {**i, "run_as": people["root"]}, None)["sent"]
    finally:
        srv.shutdown()


def test_a_slack_direct_message_is_a_private_chat(monkeypatch, db, cfg, people):
    srv, url = fake_slack.start()
    sock = fake_slack.Socket()
    monkeypatch.setattr(slack, "API", url + "/api")
    monkeypatch.setattr(slack, "_connect", lambda u: sock)
    fake_slack.Handler.url, fake_slack.Handler.socket = url, sock
    try:
        cfg["slack"] = {
            **cfg["slack"],
            "enabled": True,
            "bot_token": fake_slack.BOT,
            "app_token": fake_slack.APP,
            "account": "me@x.io",
            "users": ["alice"],
        }
        for e in (fake_slack.event("Lens, hi"), fake_slack.event("hi", channel="D1", channel_type="im", thread_ts="3.0")):
            sock.envelopes.put(e)
        lst = None
        for _ in range(50):
            channels.tick(db, cfg, cfg, "api-1", slack.SLACK)
            lst = slack._LISTENERS.get("socket")
            if lst and lst.taken >= 2 and lst.todo.empty():
                break
        route = db.one("SELECT * OMIT id FROM chat_route:slack")
        assert route["target"]["to"] == {"channel": "D1"} and route["chat"] == "private chat with alice"
        fake_slack.Handler.posted = []
        assert channels.tell(db, cfg, people["me"], "hello")["sent"][0]["channel"] == "slack"
        assert fake_slack.Handler.posted[-1]["channel"] == "D1" and "thread_ts" not in fake_slack.Handler.posted[-1]
    finally:
        for lst in slack._LISTENERS.values():
            lst.stop.set()
        slack._LISTENERS.clear()
        srv.shutdown()
