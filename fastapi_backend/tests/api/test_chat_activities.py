"""What people say to Lens as `chat.message` activities (docs/automations.md, Kinds): a question in the web app is its
person's alone; one said to the Telegram bot belongs to the namespace its room answers in, else to the account the bot
answers as; what isn't said to Lens is never stored, so it never is an activity; an automation's own question to its
agent isn't a person's."""

from __future__ import annotations

import time

import pytest

from app.domain import channels, chat, events, store, telegram
from tests import fake_llm, fake_telegram
from tests.api._assist import start_llm
from tests.helpers import make_user, seed

update = fake_telegram.update
KINDS = ["chat.message"]


@pytest.fixture(autouse=True)
def fresh():
    events._seen.clear()
    events._claimed.clear()
    yield


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
def bot(app, db, cfg, folder, tg):
    seed(db, cfg, folder)
    make_user(db, "bot@x.io", "bot password 1", roles={"pods": "viewer"})
    h, url = tg
    cfg["telegram"] = {
        **cfg["telegram"],
        "enabled": True,
        "api_url": url,
        "token": fake_telegram.TOKEN,
        "account": "bot@x.io",
        "users": ["alice", "bob"],
        "rooms": ["-100 = pods"],
    }
    cfg["ai"]["tools"] = False
    return h


def _start(db, cfg, consumer):
    """A follower's first look marks where it starts: what's said in that second isn't seen, so wait for the next."""
    assert events.follow(db, cfg, consumer, kinds=KINDS)[0] == []
    t = store.now()
    while store.now() == t:
        time.sleep(0.05)


def test_a_question_in_the_web_app_is_its_persons_alone(db, cfg):
    me = make_user(db, "me@x.io", "my password 1", roles={"pods": "editor"})
    other = make_user(db, "other@x.io", "other password 1", roles={"pods": "editor"})
    pods = store.ns_id(db, "pods")
    _start(db, cfg, "test.chat")
    cid = chat.create(db, me, "Trip", scope={"namespaces": ["pods"]})
    chat.add(db, cid, "user", "When does the shipment leave?")
    chat.add(db, cid, "assistant", "On Friday.")
    agent = chat.create(db, me, "Automation 4: ...")
    chat.add(db, agent, "user", "File this where it belongs.", origin={"automation": 4, "run": 1, "node": "a"})

    got, commit = events.follow(db, cfg, "test.chat", kinds=KINDS)
    commit()
    assert len(got) == 1
    a = events.check_activity(got[0])
    assert a["kind"] == "chat.message" and a["type"] == "Create" and a["space"] == pods
    assert a["actor"] == {"type": "Person", "id": f"lens:account/{me}", "name": "me"}
    assert a["object"] == {
        "type": "Note",
        "id": f"lens:chat_message/{a['id'].removeprefix('said-')}",
        "name": "When does the shipment leave?",
        "url": f"/chat/{cid}",
        "text": "When does the shipment leave?",
        "channel": "web",
        "chat": cid,
        "direct": True,
    }
    assert a["summary"] == "me wrote: “When does the shipment leave?”"
    assert a["lens"]["audience"] == "owner" and a["lens"]["owner"] == me and "object.text" in a["lens"]["untrusted"]
    # its person's alone, even for admins and others in the namespace
    assert events.get(db, cfg, a["id"], events.viewer(me, [pods]))["id"] == a["id"]
    assert events.get(db, cfg, a["id"], events.viewer(other, [pods])) is None
    assert events.get(db, cfg, a["id"], events.viewer(other, None, admin=True)) is None
    assert [x["id"] for x in events.page(db, cfg, events.viewer(me, [pods]), flt={"kinds": KINDS})["items"]] == [a["id"]]
    assert events.page(db, cfg, events.viewer(other, [pods]), flt={"kinds": KINDS})["items"] == []
    assert events.follow(db, cfg, "test.chat", kinds=KINDS)[0] == []


def test_what_is_said_to_the_telegram_bot(db, cfg, bot, llm):
    pods = store.ns_id(db, "pods")
    account = db.one("SELECT VALUE record::id(id) FROM account WHERE email = 'bot@x.io'")
    _start(db, cfg, "test.telegram")
    bot.updates = [
        update("groceries: milk, eggs"),  # a private chat with alice
        update("lunch anyone?", chat_id=-100, chat_type="group", title="Team", user="bob", user_id=8),  # not for Lens
        update("@lens_test_bot when does the shipment leave?", chat_id=-100, chat_type="group", title="Team", user="bob", user_id=8),
    ]
    assert channels.tick(db, cfg, cfg, "api-1", telegram.TELEGRAM) == 2

    got, commit = events.follow(db, cfg, "test.telegram", kinds=KINDS)
    commit()
    for a in got:
        events.check_activity(a)
    assert [a["object"]["text"] for a in got] == ["groceries: milk, eggs", "when does the shipment leave?"]
    mine, room = got
    assert mine["actor"] == {"type": "Person", "id": "lens:chat_user/telegram:7", "name": "alice"}
    assert {k: mine["object"][k] for k in ("channel", "direct")} == {"channel": "telegram", "direct": True}
    assert "room" not in mine["object"]
    assert mine["space"] is None and mine["lens"]["audience"] == "owner" and mine["lens"]["owner"] == account
    assert mine["summary"] == "alice wrote in Telegram: “groceries: milk, eggs”"
    assert room["actor"]["id"] == "lens:chat_user/telegram:8" and room["actor"]["name"] == "bob"
    assert {k: room["object"][k] for k in ("channel", "room", "direct")} == {"channel": "telegram", "room": "Team", "direct": False}
    assert room["space"] == pods and "audience" not in room["lens"]  # the room answers in pods
    assert room["summary"] == "bob wrote in Telegram · Team: “when does the shipment leave?”"
    assert events.get(db, cfg, room["id"], events.viewer(99, [pods]))["id"] == room["id"]
    assert events.get(db, cfg, mine["id"], events.viewer(99, [pods])) is None
    assert events.get(db, cfg, mine["id"], events.viewer(account, [pods]))["id"] == mine["id"]

    # a follower of one namespace hears only that namespace's rooms
    bot.updates = [
        update("and on Monday?"),
        update("@lens_test_bot and on Monday?", chat_id=-100, chat_type="group", title="Team", user="bob", user_id=8),
    ]
    assert channels.tick(db, cfg, cfg, "api-1", telegram.TELEGRAM) == 2
    got, _ = events.follow(db, cfg, "test.telegram", kinds=KINDS, spaces=[pods])
    assert [(a["actor"]["name"], a["object"]["text"]) for a in got] == [("bob", "and on Monday?")]
