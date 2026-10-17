"""Deciding an automation's approval from a chat app (docs/automations.md, Approvals): a yes in the private chat the
person was told in decides an action that changes something inside Lens, when the channel lets them approve and
it's the one thing waiting there; one that reaches outside Lens is decided on its card; the assistant's own
approvals in a room work as before."""

from __future__ import annotations

import pytest

from app.domain import actions, approvals, bridge, channels, store, telegram
from tests import fake_llm, fake_telegram
from tests.api._assist import start_llm
from tests.helpers import make_user, seed

update = fake_telegram.update


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
    return {"me": make_user(db, "me@x.io", "my password 1", roles={"pods": "editor"}), "pods": store.ns_id(db, "pods")}


@pytest.fixture
def bot(people, db, cfg):
    """The Telegram bot answering as me@x.io, letting alice in; alice has said hi privately, so that's where I'm told."""
    srv, url = fake_telegram.start()
    telegram._BOTS.clear()
    h = fake_telegram.Handler
    cfg["telegram"] = {
        **cfg["telegram"],
        "enabled": True,
        "api_url": url,
        "token": fake_telegram.TOKEN,
        "account": "me@x.io",
        "users": ["alice"],
    }
    h.updates = [update("hi")]
    channels.tick(db, cfg, cfg, "api-1", telegram.TELEGRAM)
    h.sent = []
    yield h
    srv.shutdown()


@pytest.fixture
def done():
    """Two test actions, one changing something inside Lens and one reaching other people, keeping what they did."""
    did = []

    def perform(db, cfg, intent, user, dry):
        did.append((intent["action"], intent["params"]["text"]))
        return {"ok": True}

    for name, effect in (("test_tag", "lens"), ("test_post", "out")):
        actions.register(
            actions.ActionSpec(
                name=name,
                label=name,
                group="Lens" if effect == "lens" else "Messages",
                params={"type": "object", "properties": {"text": {"type": "string"}}},
                required=("text",),
                effect=effect,
                perform=perform,
                describe=lambda p, n=name: f"{'Tag the asset' if n == 'test_tag' else 'Post'} “{p.get('text')}”",
            )
        )
    yield did
    for name in ("test_tag", "test_post"):
        actions._ACTIONS.pop(name, None)


def intent(people, action="test_tag", n=7, text="school"):
    return {
        "key": f"51-n{n}",
        "action": action,
        "params": {"text": text},
        "effect": "lens" if action == "test_tag" else "out",
        "risk": "ask",
        "untrusted": True,
        "spaces": [people["pods"]],
        "run_as": people["me"],
        "node": f"n{n}",
        "automation": 4,
        "run": 51,
    }


def say(db, cfg, bot, text):
    bot.sent = []
    bot.updates = [update(text)]
    channels.tick(db, cfg, cfg, "api-1", telegram.TELEGRAM)
    return bot.sent[-1]["text"] if bot.sent else None


def test_a_yes_in_my_chat_decides_an_action_inside_lens(db, cfg, people, bot, done):
    aid = approvals.ask(db, cfg, intent(people))
    told = bot.sent[-1]["text"]
    assert told.startswith("Automation 4 wants to tag the asset “school”. Reply “yes” to do it or “no” not to, or open ")
    assert say(db, cfg, bot, "yes") == "Done: Tag the asset “school”"
    assert done == [("test_tag", "school")]
    a = approvals.get(db, aid)
    assert a["status"] == "done" and a["decided_by"] == "me@x.io"
    assert "yes" in [m["content"] for m in db.rows("SELECT content FROM chat_message WHERE role = 'user'")]


def test_an_action_reaching_outside_lens_is_decided_on_its_card(db, cfg, people, bot, done):
    aid = approvals.ask(db, cfg, intent(people, "test_post"))
    told = bot.sent[-1]["text"]
    assert "Reply" not in told and told.endswith(f"/approvals?id={aid} to decide.")
    said = say(db, cfg, bot, "yes")
    assert said.startswith("Decide in Lens: Post “school”") and said.endswith(f"/approvals?id={aid}")
    assert not done and approvals.get(db, aid)["status"] == "pending"
    assert say(db, cfg, bot, "no") == "Declined: Post “school”"  # declining does nothing, wherever it's said
    assert approvals.get(db, aid)["status"] == "declined" and not done


def test_with_two_waiting_a_bare_yes_names_them_and_a_number_picks_one(db, cfg, people, bot, done):
    first = approvals.ask(db, cfg, intent(people, n=1, text="one"))
    assert "Reply “yes”" in bot.sent[-1]["text"]
    second = approvals.ask(db, cfg, intent(people, n=2, text="two"))
    assert "Reply" not in bot.sent[-1]["text"]  # a bare yes would be ambiguous now
    said = say(db, cfg, bot, "yes")
    assert said.startswith("Several things are waiting:") and f"#{first}" in said and not done
    said = say(db, cfg, bot, "yes all")  # an automation's action is picked on its own
    assert said.startswith("Still waiting, one at a time:") and not done
    assert say(db, cfg, bot, f"yes {second}") == "Done: Tag the asset “two”"
    assert done == [("test_tag", "two")] and approvals.get(db, first)["status"] == "pending"
    assert say(db, cfg, bot, "yes") == "Done: Tag the asset “one”"  # alone again


def test_a_yes_counts_only_from_someone_let_in_to_approve(db, cfg, people, bot, done):
    cfg["telegram"]["approve"] = "off"
    aid = approvals.ask(db, cfg, intent(people))
    assert "Reply" not in bot.sent[-1]["text"]
    account = {"id": people["me"], "email": "me@x.io", "admin": False}
    route = db.one("SELECT * FROM chat_route:telegram")
    v = channels.view(cfg, telegram.TELEGRAM)
    m = {"username": "alice", "userid": "7", "private": True}
    assert bridge.decide_in_room(db, v, cfg, account, route["conversation"], m, "yes") is None
    cfg["telegram"]["approve"] = "low_risk"
    assert bridge.decide_in_room(db, v, cfg, account, route["conversation"], {"username": "eve"}, "yes") is None
    assert bridge.decide_in_room(db, v, cfg, account, route["conversation"] + 1, m, "yes") is None  # not where it told me
    assert not done and approvals.get(db, aid)["status"] == "pending"
    assert bridge.decide_in_room(db, v, cfg, account, route["conversation"], m, "yes") == "Done: Tag the asset “school”"


def test_the_assistants_own_room_approvals_work_as_before(db, cfg, people, bot, done):
    route = db.one("SELECT * FROM chat_route:telegram")
    aid = db.next_id("approval")
    db.q(
        "CREATE $r CONTENT $d",
        r=store.R("approval", aid),
        d={
            "chat": route["conversation"],
            "account": people["me"],
            "tool": "run_template",
            "args": {},
            "summary": "Run notes",
            "status": "pending",
        },
    )
    said = say(db, cfg, bot, "yes")
    assert said.startswith("Waiting for approval in Lens: Run notes") and said.endswith(f"/chat/{route['conversation']}")
    assert say(db, cfg, bot, "no") == "Declined: Run notes"
