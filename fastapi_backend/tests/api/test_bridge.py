"""The assistant in chat rooms through Matterbridge: set up in the app, it answers what's said to it in a room as the
account it was given, in a conversation per person, and only one server process reads the rooms."""

from __future__ import annotations

import pytest

from app.domain import bridge, settings
from tests import fake_llm, fake_matterbridge
from tests.api._assist import start_llm
from tests.api.test_setup_assistant import call
from tests.helpers import login, make_user, seed

said = fake_matterbridge.said


@pytest.fixture
def mb():
    srv, url = fake_matterbridge.start()
    yield fake_matterbridge.Handler, url
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
def room(app, db, cfg, folder, mb):
    """Seeded recordings, a viewer of pods for the bridge to answer as, and the bridge set up for them."""
    seed(db, cfg, folder)
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "bot@x.io", "bot password 1", roles={"pods": "viewer"})
    h, url = mb
    cfg["bridge"] = {**cfg["bridge"], "enabled": True, "url": url, "token": "mb-token", "account": "bot@x.io"}
    return h


def run(db, cfg, me="api-1"):
    return bridge.tick(db, cfg, cfg, me)


def test_the_bridge_is_set_up_and_checked_in_the_app(client, db, cfg, mb):
    h, url = mb
    make_user(db, "root@x.io", "root password 1", admin=True)
    a = login(client, "root@x.io", "root password 1")
    assert client.get("/api/v1/settings/bridge", headers=a).json()["state"] == "off"
    assert client.post("/api/v1/settings/bridge/test", headers=a).json() == {"ok": False, "error": "set Matterbridge's API address first"}
    r = client.put("/api/v1/settings/bridge", headers=a, json={"url": url + "/", "token": "wrong", "account": "Root@x.io", "enabled": True})
    assert r.status_code == 200, r.text
    saved = settings.Settings(db, cfg).current()["bridge"]
    assert saved["url"] == url and saved["account"] == "root@x.io" and saved["token"] == "wrong"
    assert "wrong" not in client.get("/api/v1/settings", headers=a).text  # the token is a secret
    assert "401" in client.post("/api/v1/settings/bridge/test", headers=a).json()["error"]
    client.put("/api/v1/settings/bridge", headers=a, json={"token": "mb-token"})
    assert client.post("/api/v1/settings/bridge/test", headers=a).json() == {"ok": True, "error": None}
    assert client.get("/api/v1/settings/bridge", headers=a).json()["state"] == "starting"
    client.put("/api/v1/settings/bridge", headers=a, json={"account": None, "enabled": False})
    client.put("/api/v1/settings/bridge", headers=a, json={"enabled": True})  # who turns it on is who it answers as
    assert settings.Settings(db, cfg).current()["bridge"]["account"] == "root@x.io"
    for bad in (
        {"url": "matterbridge:4242"},
        {"account": "bot"},
        {"answer": "sometimes"},
        {"approve": "always"},
        {"poll_seconds": 0},
        {"users": "alice"},
    ):
        assert client.put("/api/v1/settings/bridge", headers=a, json=bad).status_code == 400, bad


def test_it_answers_what_is_said_to_it_and_nothing_else(db, cfg, room, llm):
    cfg["ai"]["tools"] = False
    room.waiting = [
        said("Lens, what leaves on Friday?"),
        said("lunch anyone?", username="bob"),
        said("@Lens when does the shipment leave?", username="bob"),
        said("I answered that", username="Lens"),
        said("Lens: hello", gateway="other"),
    ]
    assert run(db, cfg) == 3
    assert [p["gateway"] for p in room.posted] == ["team", "team", "other"]
    assert all(p["username"] == "Lens" and p["text"] == "OK" for p in room.posted)  # the fake model's answer
    asked = [m["messages"][-1]["content"] for m in llm.seen if m.get("stream") is not None or m.get("messages")]
    assert any(c.endswith("Question: what leaves on Friday?") for c in asked)
    assert any(c.endswith("Question: when does the shipment leave?") for c in asked)
    # a conversation per person in a room, the account's own, listed with its chats
    rows = db.rows("SELECT title, kind, account FROM chat WHERE kind = 'bridge' ORDER BY title")
    assert [r["title"] for r in rows] == ["Matterbridge · general · alice"] * 2 + ["Matterbridge · general · bob"]
    assert {r["kind"] for r in rows} == {"bridge"} and {r["account"] for r in rows} == {rows[0]["account"]}
    room.waiting = [said("Lens, and on Monday?")]
    run(db, cfg)
    assert len(db.rows("SELECT id FROM chat WHERE kind = 'bridge'")) == 3  # alice keeps hers
    # only one gateway, only some people, every message
    cfg["bridge"].update(gateway="team", users=["alice"], answer="all")
    room.posted = []
    room.waiting = [said("what leaves?"), said("Lens, hi", username="bob"), said("Lens, hi", gateway="other")]
    assert run(db, cfg) == 1 and len(room.posted) == 1


def test_it_reads_only_what_its_account_can_and_asks_before_changing_anything(client, db, cfg, room, llm):
    llm.tool_script = [
        {"content": "", "tool_calls": [call(1, "search_transcripts", {"query": "capsid"})]},
        {"content": "The capsid model won [1]."},
    ]
    room.waiting = [said("Lens, who won?")]
    run(db, cfg)
    assert room.posted[-1]["text"] == "The capsid model won [1]."
    tools = {t["function"]["name"] for t in llm.seen[-1]["tools"]}
    assert "change_settings" not in tools  # not an admin: no server tools
    cid = db.one("SELECT VALUE record::id(id) FROM chat WHERE kind = 'bridge' LIMIT 1")
    b = login(client, "bot@x.io", "bot password 1")
    chats = client.get("/api/v1/chats", headers=b).json()
    assert [c["kind"] for c in chats] == ["bridge"]
    msgs = client.get(f"/api/v1/chats/{cid}", headers=b).json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"] and msgs[1]["steps"][0]["tool"] == "search_transcripts"
    # an admin account: what it proposes waits for approval in the app, linked
    cfg["bridge"]["account"] = "root@x.io"
    llm.tool_script = [
        {"content": "", "tool_calls": [call(1, "change_settings", {"section": "workers", "changes": {"inline": 2}})]},
        {"content": "I asked to run two workers."},
    ]
    room.waiting = [said("Lens, run two workers")]
    run(db, cfg)
    text = room.posted[-1]["text"]
    assert text.startswith("I asked to run two workers.\n\nWaiting for approval in Lens: ") and "/chat/" in text
    assert settings.Settings(db, cfg).current()["workers"]["inline"] != 2


def test_low_risk_changes_are_approved_by_replying_in_the_room(client, db, cfg, room, llm):
    from app.domain import store

    cfg["bridge"].update(account="root@x.io", users=["alice"])

    def ask(text, *script, **who):
        llm.tool_script = list(script)
        room.waiting = [said(text, **who)]
        run(db, cfg)
        return room.posted[-1]["text"]

    family = {"content": "", "tool_calls": [call(1, "create_namespace", {"name": "family", "graph": "isolated"})]}
    text = ask("Lens, make a family namespace", family, {"content": "I asked to create it."})
    assert text.startswith(
        "I asked to create it.\n\nWaiting for your yes: Create the namespace family (isolated graph). Reply “yes” to do it"
    )
    posted = len(room.posted)
    ask("Lens, yes", username="bob")  # only the people in bridge.users talk to Lens, and approve
    assert len(room.posted) == posted
    assert "family" not in store.space_names(db).values()
    assert ask("Lens, yes") == "Done: Create the namespace family (isolated graph)"
    assert "family" in store.space_names(db).values()
    audit = db.rows("SELECT detail FROM audit_log WHERE action = 'assistant.approval'")
    assert audit[-1]["detail"]["via"] == "chat room" and audit[-1]["detail"]["by"] == "alice"

    # "no" declines; with nothing waiting, "yes" is just said to the assistant
    kids = {"content": "", "tool_calls": [call(1, "create_namespace", {"name": "kids", "graph": "shared"})]}
    ask("Lens, and kids", kids, {"content": "Asked."})
    assert ask("Lens, no") == "Declined: Create the namespace kids (shared graph)"
    assert ask("Lens, yes", {"content": "Yes to what?"}) == "Yes to what?"

    # anything else is approved in the web app, even when "yes" is said in the room
    workers = {"content": "", "tool_calls": [call(1, "change_settings", {"section": "workers", "changes": {"inline": 2}})]}
    text = ask("Lens, run two workers", workers, {"content": "I asked to run two workers."})
    assert "Waiting for approval in Lens: " in text and "/chat/" in text and "Reply" not in text
    assert ask("Lens, yes").startswith("Waiting for approval in Lens: ")
    assert settings.Settings(db, cfg).current()["workers"]["inline"] != 2

    # turned off, or no list of who may talk to Lens: the web app only
    for change in ({"approve": "off"}, {"users": []}):
        cfg["bridge"].update({"users": ["alice"], "approve": "low_risk", **change})
        text = ask(
            "Lens, make a pets namespace", {**family, "tool_calls": [call(1, "create_namespace", {"name": "pets"})]}, {"content": "Asked."}
        )
        assert "Waiting for approval in Lens: " in text and "Reply" not in text
        db.q("UPDATE approval SET status = 'declined' WHERE status = 'pending'")


def test_one_process_reads_the_rooms(db, cfg, room):
    cfg["ai"]["tools"] = False
    room.waiting = [said("Lens, hi")]
    assert run(db, cfg, "api-1") == 1
    room.waiting = [said("Lens, hi again")]
    assert run(db, cfg, "worker-2") == 0 and room.waiting  # api-1 holds it: worker-2 leaves the messages alone
    assert run(db, cfg, "api-1") == 1
    s = bridge.status(db, cfg)
    assert s["state"] == "running" and s["holder"] == "api-1" and s["answered"] == 2
    cfg["bridge"]["enabled"] = False
    run(db, cfg, "api-1")  # turned off: it lets go
    cfg["bridge"]["enabled"] = True
    room.waiting = [said("Lens, hi")]
    assert run(db, cfg, "worker-2") == 1


def test_a_matterbridge_that_fails_is_reported(db, cfg, room):
    room.token = "other"
    with pytest.raises(bridge.BridgeError, match="401"):
        run(db, cfg)
    assert bridge.status(db, cfg)["state"] == "error"
    room.token = "mb-token"
    cfg["bridge"]["account"] = "gone@x.io"  # an account that isn't there: reported, and the look goes on
    room.waiting = [said("Lens, hi"), said("Lens, hello", username="bob")]
    assert run(db, cfg) == 0 and not room.waiting
    assert bridge.status(db, cfg)["error"] == "there's no active Lens account gone@x.io to answer as"
    cfg["bridge"]["account"] = None
    assert bridge.status(db, cfg) == {"state": "incomplete", "error": "no Lens account to answer as"}


def test_the_thread_answers_on_its_own(db, cfg, room):
    import threading
    import time

    cfg["ai"]["tools"] = False
    cfg["bridge"]["poll_seconds"] = 1
    room.waiting = [said("Lens, anyone there?")]
    stop = threading.Event()
    th = bridge.start(db, lambda: cfg, stop, name="api-9")
    try:
        for _ in range(100):
            if room.posted:
                break
            time.sleep(0.05)
        assert room.posted and room.posted[0]["text"] == "OK"
    finally:
        stop.set()
        th.join(5)
    assert bridge.status(db, cfg)["state"] == "starting"  # it let go when it stopped


def test_a_room_conversation_is_summarised_as_it_goes_on(db, cfg, room, llm):
    from app.domain import chat

    cfg["ai"]["tools"] = False
    for i in range((chat.RECENT + chat.COMPACT_AFTER) // 2 + 1):
        room.waiting = [said(f"Lens, question {i}?")]
        run(db, cfg)
    cid = db.one("SELECT VALUE record::id(id) FROM chat WHERE kind = 'bridge' LIMIT 1")
    assert chat.memory(db, cfg, cid)["text"] == "OK"  # the fake model's summary
    last = [b for b in llm.seen if b["messages"][0]["content"].startswith(chat.SYSTEM)][-1]["messages"]
    assert "Earlier in this conversation" in last[0]["content"] and "question 0?" not in str(last[1:])


def test_a_room_given_to_a_namespace_assistant(client, db, cfg, room, llm):
    from app.domain import ns_assistant, store

    pods = store.ns_id(db, "pods")
    ns_assistant.save_profile(db, pods, enabled=True, name="Podpal", instructions="Be brief.")
    ns_assistant.remember(db, pods, "Episodes ship on Fridays.", 1, author="person")
    a = login(client, "root@x.io", "root password 1")
    bad = client.put("/api/v1/settings/bridge", headers=a, json={"rooms": ["team"]})
    assert bad.status_code == 400
    got = client.put("/api/v1/settings/bridge", headers=a, json={"rooms": ["Team/General=Pods", " ", "other = calls"]})
    assert got.status_code == 200
    cfg["bridge"]["rooms"] = settings.Settings(db, cfg).current()["bridge"]["rooms"]
    assert cfg["bridge"]["rooms"] == ["Team/General = pods", "other = calls"]
    room.waiting = [said("Podpal, when do episodes ship?"), said("Podpal, hi", gateway="other")]
    assert run(db, cfg) == 1  # not in a room given to another namespace
    asked = [m for m in llm.seen if m.get("tools")][-1]
    system = next(m["content"] for m in asked["messages"] if m["role"] == "system")
    assert "You are Podpal, the assistant of the pods namespace" in system and "Episodes ship on Fridays." in system
    rows = db.rows("SELECT scope FROM chat WHERE kind = 'bridge'")
    assert [r["scope"] for r in rows] == [{"namespaces": ["pods"]}]
    # Lens's own name still works there, and the room follows the setting when it changes: open again, its
    # conversations are routed per question (test_an_open_room_routes_each_question)
    cfg["bridge"]["rooms"] = []
    cfg["ai"]["tools"] = False
    room.waiting = [said("Lens, and Mondays?")]
    assert run(db, cfg) == 1
    assert db.one("SELECT VALUE room FROM chat WHERE kind = 'bridge' LIMIT 1") is None
    assert bridge.room_namespace({"bridge": {"rooms": ["team = pods", "team/x = calls"]}}, {"gateway": "team", "channel": "x"}) == "calls"


def test_an_open_room_routes_each_question(db, cfg, room, llm):
    """In a room no namespace is given to, Lens routes: an assistant's name, a sure pick, a hint, or the person's pick."""
    from app.domain import chat, ns_assistant, store

    cfg["ai"]["tools"] = False
    make_user(db, "bot2@x.io", "bot password 1", roles={"pods": "viewer", "calls": "viewer"})
    cfg["bridge"]["account"] = "bot2@x.io"
    ns_assistant.save_profile(db, store.ns_id(db, "pods"), enabled=True, name="Podpal")

    def ask(text):
        room.posted = []
        room.waiting = [said(text)]
        assert run(db, cfg) == 1
        return room.posted[-1]["text"], db.one("SELECT VALUE scope FROM chat WHERE kind = 'bridge' LIMIT 1")

    # a namespace's assistant by name, in any open room
    assert ask("Podpal, when do episodes ship?") == ("OK", {"namespaces": ["pods"]})
    # sure of the namespace: scoped, and said once
    llm.decision = {"choice": "calls", "confidence": 0.99}
    text, scope = ask("Lens, when does the shipment leave?")
    assert scope == {"namespaces": ["calls"]} and text.endswith("(Looked in calls. Say “use everything” to look everywhere.)")
    assert ask("Lens, and the next one?") == ("OK", {"namespaces": ["calls"]})
    # unsure: everywhere, with the likeliest to pick, hinted once
    llm.decision = {"choice": "pods", "confidence": 0.3}
    text, scope = ask("Lens, what about Friday?")
    assert scope == {} and "If this is about calls or pods, say “use calls” to keep this conversation there" in text
    assert ask("Lens, and Saturday?") == ("OK", {})
    # the person's pick sticks until they widen it
    assert ask("Lens, use pods") == ("OK, this conversation stays in pods until you say “use everything”.", {"namespaces": ["pods"]})
    llm.decision = {"choice": "calls", "confidence": 0.99}
    assert ask("Lens, when does the shipment leave?") == ("OK", {"namespaces": ["pods"]})
    assert ask("Lens, use everything")[1] == {}
    assert ask("Lens, use the force")[0].startswith("OK\n")  # not a namespace: a question, answered
    cid = db.one("SELECT VALUE record::id(id) FROM chat WHERE kind = 'bridge' LIMIT 1")
    assert "use pods" in [m["content"] for m in chat.history(db, cid)]  # kept in the conversation like any message
