"""The automations journey end to end (docs/automations.md): an email from a mailbox watch is email.received in the
stream; an automation saved over the API whose trigger matches it runs once when the stream is followed; its model
step answers (a stand-in model), telling me happens at once, and posting to a chat room waits as an approval; approved
over the API it is posted; and the timeline lists the email, the run and the decision, each linked to what caused it.
Only the model, my chat app and the chat-room bridge are stand-ins: everything between is the real code."""

from __future__ import annotations

import time

import pytest

from app.domain import automations, channels, events, events_content, sources, store
from tests import fake_llm, fake_matterbridge
from tests.api._assist import start_llm
from tests.helpers import login, make_user

R = store.R
MAIL = {
    "path": "INBOX/7-12.eml",
    "title": "School trip on Friday",
    "size": 2048,
    "modified": "2026-10-15T16:21:58+00:00",
    "when": "2026-10-15T16:20:41+00:00",
    "key": "message-id:trip@stmarys.org",
    "mail": {"from": ["office@stmarys.org"], "to": ["me@x.io"], "subject": "School trip on Friday", "mailbox": "INBOX"},
}


def node(nid, t, **config):
    return {"id": nid, "type": t, "config": config}


def edge(a, b):
    return {"source": a, "target": b}


SCHOOL = {
    "name": "School emails",
    "triggers": [
        {
            "as": "mail",
            "on": "event",
            "kinds": ["email.received"],
            "where": {"all": [{"path": "object.from.0", "op": "ends_with", "value": "@stmarys.org"}]},
        }
    ],
    "graph": {
        "nodes": [
            node("i", "input"),
            node("ask", "action", action="ask_model", params={"prompt": "Sum up for a parent: {{ event.object.subject }}"}),
            node("me", "action", action="notify", params={"to": "me", "text": "School: {{ steps.ask.text }}"}),
            node("room", "action", action="notify", params={"to": "gateway:family", "text": "From school: {{ steps.ask.text }}"}),
        ],
        "edges": [edge("i", "ask"), edge("ask", "me"), edge("ask", "room")],  # nothing after an action that reaches out
    },
}


@pytest.fixture
def llm(cfg):
    srv = start_llm(cfg)
    yield fake_llm.Handler
    fake_llm.Handler.usage = None
    srv.shutdown()


@pytest.fixture
def room():
    """Matterbridge's API, keeping what is posted to its gateways."""
    srv, url = fake_matterbridge.start()
    fake_matterbridge.Handler.posted = []
    yield fake_matterbridge.Handler, url
    srv.shutdown()


@pytest.fixture
def app(cfg, db, llm, room):
    """The app with the chat-room bridge on, answering as me@x.io (set before the app first reads its settings)."""
    from app.main import create_app

    cfg["bridge"] = {**cfg["bridge"], "enabled": True, "url": room[1], "token": "mb-token", "account": "me@x.io"}
    return create_app(cfg, db, background=False)


@pytest.fixture
def told(monkeypatch):
    """My chat app: what channels.tell would send me, kept rather than sent."""
    got = []

    def tell(db, cfg, account, text, links=()):
        got.append({"account": account["id"], "text": text if isinstance(text, str) else text("telegram", {}, {})})
        return {"sent": [{"channel": "telegram", "label": "Telegram", "chat": "private chat with me"}], "failed": [], "error": None}

    monkeypatch.setattr(channels, "tell", tell)
    return got


@pytest.fixture(autouse=True)
def fresh():
    """Nothing seen, claimed or named in an earlier test carries over."""
    events_content.forget_names()
    events._seen.clear()
    events._claimed.clear()
    automations._swept["at"] = 0.0
    yield
    events_content.forget_names()


def tick():
    """Wait for the next second, as store.now() counts them: a follower doesn't read the second it started in."""
    start = store.now()
    while store.now() == start:
        time.sleep(0.05)


def mailbox(db, space):
    """An IMAP account watched into namespace `space`: (the source, the watch's id)."""
    sid = db.next_id("storage_source")
    db.q(
        "CREATE $r CONTENT $d", r=R("storage_source", sid), d={"name": "Work mail", "type": "imap", "params": {}, "created_at": store.now()}
    )
    wid = db.next_id("watch_path")
    db.q("CREATE $r CONTENT $d", r=R("watch_path", wid), d={"source": sid, "path": "", "space": space})
    return sources.get(db, sid), wid


def test_an_email_runs_an_automation_that_tells_me_asks_before_posting_and_shows_on_the_timeline(client, db, cfg, llm, room, told):
    pods = store.ns_id(db, "pods")
    me = make_user(db, "me@x.io", "my password 1", roles={"pods": "editor"})
    h = login(client, "me@x.io", "my password 1")
    # saved over the API, on from the start, in the namespace I edit
    r = client.post("/api/v1/automations", headers=h, json=SCHOOL)
    assert r.status_code == 200, r.text
    aid = r.json()["id"]
    saved = client.get(f"/api/v1/automations/{aid}", headers=h).json()
    assert saved["enabled"] and saved["spaces"] == [pods] and saved["run_as"] == me
    assert automations.dispatch(db, cfg) == 0  # the first look marks where the follower starts
    tick()

    # an email arrives from the mailbox watch: email.received, by the mailbox, in its namespace
    src, wid = mailbox(db, pods)
    rid, job = sources._ingest(db, cfg, src, MAIL, "document", pods, f"watch:{wid}")
    assert job
    email = f"create-recording-{rid}"
    got = client.get(f"/api/v1/activities/{email}", headers=h).json()
    assert got["kind"] == "email.received" and got["space"] == pods and got["actor"]["id"] == f"lens:source/{src['id']}"
    assert got["object"]["from"] == ["office@stmarys.org"] and got["object"]["subject"] == "School trip on Friday"

    # following the stream runs the automation once, on that email
    assert automations.dispatch(db, cfg) == 1
    [listed] = client.get("/api/v1/automations/runs", headers=h, params={"activity": email}).json()
    assert listed["automation"] == aid and listed["event"] == email and listed["trigger"] == "mail"
    run = client.get(f"/api/v1/automations/runs/{listed['id']}", headers=h).json()
    assert run["status"] == "waiting" and run["counts"] == {"done": 2, "waiting": 1, "scheduled": 0, "failed": 0}
    ask, tell, post = run["intents"]
    # the model step answered (the stand-in model), with the email's text marked as outside text
    assert ask["action"] == "ask_model" and ask["status"] == "done" and ask["result"] == {"text": "OK"} and ask["untrusted"]
    assert "School trip on Friday" in ask["params"]["prompt"] and "<untrusted-" in ask["params"]["prompt"]
    assert llm.seen[-1]["messages"][1]["content"] == ask["params"]["prompt"]
    ledger = db.rows("SELECT model FROM activity WHERE resources CONTAINS $r", r=f"automation_run:{run['id']}")
    assert [x["model"] for x in ledger if x.get("model")] == ["fake"]  # its model call is in the activity ledger, under the run
    # telling me ran at once, in my chat app
    assert tell["node"] == "me" and tell["effect"] == "self" and tell["status"] == "done"
    assert tell["result"]["sent"][0]["channel"] == "telegram" and told[0] == {"account": me, "text": "School: OK"}
    # posting to the family room reaches other people, so it waits as an approval; I'm told it waits, nothing is posted
    assert post["node"] == "room" and post["effect"] == "out" and post["risk"] == "ask" and post["status"] == "waiting"
    [waiting] = client.get("/api/v1/approvals", headers=h, params={"status": "pending"}).json()
    assert waiting["id"] == post["result"]["approval"] and waiting["origin"] == {"automation": aid, "run": run["id"], "node": "room"}
    assert waiting["summary"] == "Post in the Matterbridge gateway family: “From school: OK”" and waiting["why"]["activity"] == email
    assert waiting["can"]["approve"] and told[1]["text"].startswith("School emails wants to post in the Matterbridge gateway family")
    assert room[0].posted == []

    # approved over the API, it is posted, and the run finishes
    out = client.post(f"/api/v1/approvals/{waiting['id']}", headers=h, json={"decision": "approve"}).json()
    assert out["status"] == "done" and out["result"]["sent"] == [{"channel": "bridge", "label": "Matterbridge", "gateway": "family"}]
    assert room[0].posted == [{"text": "From school: OK", "username": "Lens", "gateway": "family"}]
    run = client.get(f"/api/v1/automations/runs/{run['id']}", headers=h).json()
    assert run["status"] == "done" and run["counts"] == {"done": 3, "waiting": 0, "scheduled": 0, "failed": 0}
    assert run["intents"][2]["status"] == "done" and run["intents"][2]["result"]["sent"][0]["gateway"] == "family"
    assert automations.dispatch(db, cfg) == 0  # what the run caused doesn't start it again

    # the timeline lists the email, the run and the decision, each pointing at what caused it
    items = {a["id"]: a for a in client.get("/api/v1/activities", headers=h).json()["items"]}
    ran, decided = items[f"ran-automation_run-{run['id']}"], items[f"decide-approval-{waiting['id']}"]
    assert items[email]["kind"] == "email.received" and items[email]["lens"]["cause"] is None  # from outside: no cause
    assert ran["kind"] == "automation.ran" and ran["object"]["event"] == email and ran["summary"] == "School emails ran: 3 done"
    assert ran["lens"]["cause"] == {"automation": aid, "run": run["id"], "depth": 1}
    assert decided["kind"] == "approval.decided" and decided["type"] == "Accept" and decided["object"]["about"] == email
    assert decided["lens"]["cause"] == {"automation": aid, "run": run["id"], "depth": 2}  # a step further from the email
    assert decided["summary"] == "me approved: Post in the Matterbridge gateway family: “From school: OK”"
