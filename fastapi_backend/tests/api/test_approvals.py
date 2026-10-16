"""The approvals inbox (docs/automations.md, Approvals): an automation's action that asks first waits as an approval
the person decides (approve, decline, edit, always), told once in their chat app, capped, expiring, and only the
person approves it: an API key, an app or an agent can only decline."""

from __future__ import annotations

import datetime as dt
import time

import pytest
from fastapi.testclient import TestClient

from app.domain import actions, ai_tools, approvals, auth, channels, store, telegram
from tests import fake_telegram
from tests.helpers import login, make_user, seed

update = fake_telegram.update


@pytest.fixture
def tg():
    srv, url = fake_telegram.start()
    telegram._BOTS.clear()
    yield fake_telegram.Handler, url
    srv.shutdown()


@pytest.fixture
def people(app, db, cfg, folder):
    seed(db, cfg, folder)
    return {
        "me": make_user(db, "me@x.io", "my password 1", roles={"pods": "editor"}),
        "other": make_user(db, "other@x.io", "other password 1", roles={"pods": "editor"}),
        "pods": store.ns_id(db, "pods"),
    }


@pytest.fixture
def posted():
    """A test action that reaches other people (effect out) and keeps what it was asked to post."""
    sent = []

    def perform(db, cfg, intent, user, dry):
        if intent["params"]["text"] == "boom":
            raise ValueError("the room is gone")
        if not dry:
            sent.append({"text": intent["params"]["text"], "room": intent["params"]["room"], "as": user["email"]})
        return {"posted": intent["params"]["text"]}

    actions.register(
        actions.ActionSpec(
            name="test_post",
            label="Post in a room",
            group="Messages",
            params={
                "type": "object",
                "properties": {
                    "room": {"type": "string", "title": "Room", "default": "family"},
                    "text": {"type": "string", "title": "Text", "maxLength": 200},
                    "times": {"type": "integer", "title": "Times", "default": 1},
                },
            },
            required=("text",),
            effect="out",
            perform=perform,
            describe=lambda p: f"Post “{p.get('text')}” to the {p.get('room', 'family')} room",
        )
    )
    yield sent
    actions._ACTIONS.pop("test_post", None)


@pytest.fixture
def studio(monkeypatch):
    """Stored automations' update_intent and set_policy (the engine's), recording what they were told."""
    calls = {"intents": [], "policy": []}
    fns = {
        "update_intent": lambda db, run, key, status, result=None: calls["intents"].append((run, key, status, result)),
        "set_policy": lambda db, aid, node, value, by: calls["policy"].append((aid, node, value, by)),
    }
    monkeypatch.setattr(approvals, "_studio", fns.get)
    return calls


def intent(people, n=7, run=51, automation=4, **kw):
    return {
        "key": f"{run}-n{n}",
        "action": "test_post",
        "params": {"text": "Trip on Friday, bring a packed lunch", "room": "family"},
        "effect": "out",
        "risk": "ask",
        "untrusted": True,
        "spaces": [people["pods"]],
        "run_as": people["me"],
        "node": f"n{n}",
        "automation": automation,
        "run": run,
        "when": None,
        "status": "waiting",
        **kw,
    }


EVENT = {
    "id": "create-recording-7",
    "kind": "email.received",
    "summary": "Work Gmail received “School trip on Friday”",
    "published": "2026-10-15T16:21:58Z",
    "space": None,
    "object": {"id": "lens:recording/7", "url": "/resources/7"},
}


def test_an_automation_action_waits_as_an_approval_and_the_person_is_told_once(db, cfg, people, posted, tg):
    h, url = tg
    cfg["telegram"] = {
        **cfg["telegram"],
        "enabled": True,
        "api_url": url,
        "token": fake_telegram.TOKEN,
        "account": "me@x.io",
        "users": ["alice"],
    }
    h.updates = [update("hi")]  # alice, privately: where me@x.io is told things
    channels.tick(db, cfg, cfg, "api-1", telegram.TELEGRAM)
    h.sent = []
    aid = approvals.ask(db, cfg, intent(people), {**EVENT, "space": people["pods"]})
    a = approvals.get(db, aid)
    assert a["status"] == "pending" and a["account"] == people["me"] and a["tool"] == "action"
    assert a["origin"] == {"automation": 4, "run": 51, "node": "n7"} and a["space"] == people["pods"]
    assert a["summary"] == "Post “Trip on Friday, bring a packed lunch” to the family room"
    assert a["automation"] == "Automation 4" and a["why"]["activity"] == "create-recording-7" and a["why"]["url"] == "/resources/7"
    made = dt.datetime.fromisoformat(a["created_at"])
    assert dt.datetime.fromisoformat(a["expires_at"]) - made == dt.timedelta(days=7)
    assert not posted  # nothing happens until the person picks
    text = h.sent[-1]["text"]
    assert text.startswith("Automation 4 wants to post “Trip on Friday, bring a packed lunch” to the family room. Open ")
    assert text.endswith(f"/approvals?id={aid} to decide.")
    assert [t["channel"] for t in a["told"]] == ["telegram"] and a["told"][0]["conversation"]
    # handed over again (the engine tried twice): the same approval, nobody told twice
    assert approvals.ask(db, cfg, intent(people)) == aid and len(h.sent) == 1
    # a dry run never asks; nobody to act for is refused
    with pytest.raises(ValueError):
        approvals.ask(db, cfg, intent(people, run=None))
    with pytest.raises(ValueError):
        approvals.ask(db, cfg, intent(people, run_as=999))


def test_with_no_chat_app_it_waits_in_the_inbox(db, cfg, people, posted):
    aid = approvals.ask(db, cfg, intent(people))
    assert approvals.get(db, aid)["told"] is None and approvals.count(db, people["me"]) == {"waiting": 1, "automations": 1}


def test_approve_carries_the_action_out_and_the_run_records_it(app, client, db, cfg, people, posted, studio):
    h = login(client, "me@x.io", "my password 1")
    aid = approvals.ask(db, cfg, intent(people))
    got = client.get(f"/api/v1/approvals/{aid}", headers=h).json()
    assert got["intent"]["action"] == "test_post" and got["can"] == {"approve": True, "decline": True, "edit": True, "always": True}
    ho = login(TestClient(app, base_url="http://127.0.0.1"), "other@x.io", "other password 1")
    assert client.post(f"/api/v1/approvals/{aid}", headers=ho, json={"decision": "approve"}).status_code == 404  # not theirs
    out = client.post(f"/api/v1/approvals/{aid}", headers=h, json={"decision": "approve"}).json()
    assert out["status"] == "done" and out["result"] == {"posted": "Trip on Friday, bring a packed lunch"}
    assert posted == [{"text": "Trip on Friday, bring a packed lunch", "room": "family", "as": "me@x.io"}]  # for its person
    assert studio["intents"] == [(51, "51-n7", "done", {"posted": "Trip on Friday, bring a packed lunch"})]
    a = approvals.get(db, aid)
    assert a["status"] == "done" and a["decided_by"] == "me@x.io" and a["intent"]["status"] == "done"
    assert client.post(f"/api/v1/approvals/{aid}", headers=h, json={"decision": "approve"}).status_code == 400  # only once
    assert len(posted) == 1
    # one that fails is failed, said on the run, and not tried again
    bad = approvals.ask(db, cfg, intent(people, n=8, params={"text": "boom"}))
    out = client.post(f"/api/v1/approvals/{bad}", headers=h, json={"decision": "approve"}).json()
    assert out == {"status": "failed", "error": "the room is gone", "batch": None, "result": None, "always": None}
    assert studio["intents"][-1] == (51, "51-n8", "failed", {"error": "the room is gone"})


def test_decline_and_edit(client, db, cfg, people, posted, studio):
    h = login(client, "me@x.io", "my password 1")
    aid = approvals.ask(db, cfg, intent(people))
    assert client.post(f"/api/v1/approvals/{aid}", headers=h, json={"decision": "decline"}).json()["status"] == "declined"
    assert not posted and studio["intents"] == [(51, "51-n7", "declined", None)]
    assert approvals.get(db, aid)["status"] == "declined"
    aid = approvals.ask(db, cfg, intent(people, n=8))
    for bad, why in (
        ({"times": "twice"}, "times is an integer"),
        ({"times": "{{ steps.n3.count }}"}, "times is an integer"),  # a template isn't filled in any more
        ({"colour": "red"}, "no param colour"),
        ({"text": "x" * 201}, "at most 200"),
    ):
        r = client.post(f"/api/v1/approvals/{aid}", headers=h, json={"decision": "edit", "params": bad})
        assert r.status_code == 400 and why in r.json()["detail"], (bad, r.text)
    assert client.post(f"/api/v1/approvals/{aid}", headers=h, json={"decision": "edit"}).status_code == 400  # no params
    assert approvals.get(db, aid)["status"] == "pending" and not posted
    out = client.post(
        f"/api/v1/approvals/{aid}", headers=h, json={"decision": "edit", "params": {"text": "Trip on Friday", "room": "school"}}
    )
    assert out.json()["status"] == "done"
    assert posted == [{"text": "Trip on Friday", "room": "school", "as": "me@x.io"}]
    assert approvals.get(db, aid)["intent"]["params"] == {"text": "Trip on Friday", "room": "school", "times": 1}


def test_always_lets_the_node_run_on_its_own_and_only_signed_in(app, client, db, cfg, people, posted, studio):
    h = login(client, "me@x.io", "my password 1")
    _, key = auth.create_token(db, people["me"], "script", scope="write")
    hk = {"Authorization": f"Bearer {key}"}
    aid = approvals.ask(db, cfg, intent(people))
    got = client.get(f"/api/v1/approvals/{aid}", headers=hk).json()
    assert got["can"] == {"approve": False, "decline": True, "edit": False, "always": False}  # an API key only declines
    for decision in ("always", "approve", "edit"):
        r = client.post(f"/api/v1/approvals/{aid}", headers=hk, json={"decision": decision, "params": {"text": "x"}})
        assert r.status_code == 403, decision
    assert not posted and not studio["policy"]
    out = client.post(f"/api/v1/approvals/{aid}", headers=h, json={"decision": "always"}).json()
    assert out["status"] == "done" and out["always"] is True and len(posted) == 1
    assert studio["policy"] == [(4, "n7", "auto", "me@x.io")]
    # only an action that reaches outside Lens is ever allowed from now on
    lens = approvals.ask(db, cfg, intent(people, n=8, effect="lens"))
    r = client.post(f"/api/v1/approvals/{lens}", headers=h, json={"decision": "always"})
    assert r.status_code == 400 and "outside Lens" in r.json()["detail"]
    # an API key still declines
    assert client.post(f"/api/v1/approvals/{lens}", headers=hk, json={"decision": "decline"}).json()["status"] == "declined"


def test_always_waits_for_saved_automations(client, db, cfg, people, posted, monkeypatch):
    monkeypatch.setattr(approvals, "_studio", lambda fn: None)  # before automations are stored
    h = login(client, "me@x.io", "my password 1")
    aid = approvals.ask(db, cfg, intent(people))
    assert client.get(f"/api/v1/approvals/{aid}", headers=h).json()["can"]["always"] is False
    r = client.post(f"/api/v1/approvals/{aid}", headers=h, json={"decision": "always"})
    assert r.status_code == 400 and "this once" in r.json()["detail"] and not posted
    # and the outcome stays on the approval itself meanwhile
    assert client.post(f"/api/v1/approvals/{aid}", headers=h, json={"decision": "approve"}).json()["status"] == "done"
    assert approvals.get(db, aid)["intent"]["status"] == "done"


def test_undecided_approvals_expire_and_nothing_runs(db, cfg, people, posted, studio):
    cfg["automations"]["approval_days"] = 2
    aid = approvals.ask(db, cfg, intent(people))
    a = approvals.get(db, aid)
    assert dt.datetime.fromisoformat(a["expires_at"]) - dt.datetime.fromisoformat(a["created_at"]) == dt.timedelta(days=2)
    chat_one = db.next_id("approval")  # the assistant's own approvals never expire
    db.q(
        "CREATE $r CONTENT $d",
        r=store.R("approval", chat_one),
        d={"account": people["me"], "tool": "run_template", "status": "pending", "created_at": "2020-01-01T00:00:00+00:00"},
    )
    now = dt.datetime.now(dt.UTC)
    assert approvals.sweep(db, cfg, now + dt.timedelta(days=1)) == 0
    assert approvals.sweep(db, cfg, now + dt.timedelta(days=3)) == 1
    a = approvals.get(db, aid)
    assert a["status"] == "expired" and a["intent"]["status"] == "expired" and not posted
    assert studio["intents"] == [(51, "51-n7", "expired", None)]
    assert approvals.get(db, chat_one)["status"] == "pending"
    with pytest.raises(ValueError):
        ai_tools.approve(db, cfg, aid, {"id": people["me"], "email": "me@x.io"}, {people["pods"]})
    # throttled to once an hour when called on schedule
    approvals._swept["at"] = 0.0
    assert approvals.sweep(db, cfg) == 0
    assert approvals.sweep(db, cfg) is None


def test_a_flood_of_approvals_is_capped_per_automation(db, cfg, people, posted, studio, monkeypatch):
    monkeypatch.setattr(approvals, "CAP", 3)
    ids = [approvals.ask(db, cfg, intent(people, n=n)) for n in range(1, 4)]
    assert all(ids) and approvals.waiting(db, 4) == 3
    assert approvals.ask(db, cfg, intent(people, n=9)) is None  # dropped, said on the run
    run, key, status, result = studio["intents"][-1]
    assert (run, key, status) == (51, "51-n9", "failed") and result["error"].startswith("dropped")
    assert approvals.ask(db, cfg, intent(people, n=9, automation=5)) is not None  # another automation's still ask
    ai_tools.approve(db, cfg, ids[0], {"id": people["me"], "email": "me@x.io"}, set(), "decline")
    assert approvals.ask(db, cfg, intent(people, n=10)) is not None  # room again once one is decided


def test_the_inbox_lists_both_kinds_with_filters_paging_and_a_count(client, db, cfg, people, posted):
    h = login(client, "me@x.io", "my password 1")
    chat_one = db.next_id("approval")
    db.q(
        "CREATE $r CONTENT $d",
        r=store.R("approval", chat_one),
        d={
            "chat": 3,
            "account": people["me"],
            "tool": "run_template",
            "summary": "Run Meeting notes",
            "status": "pending",
            "created_at": store.now(),
        },
    )
    a1 = approvals.ask(db, cfg, intent(people, n=1))
    a2 = approvals.ask(db, cfg, intent(people, n=2, automation=5))
    approvals.ask(db, cfg, intent(people, n=3, run_as=people["other"]))  # someone else's
    ai_tools.approve(db, cfg, a1, {"id": people["me"], "email": "me@x.io"}, set(), "decline")

    def ids(**q):
        return [a["id"] for a in client.get("/api/v1/approvals", headers=h, params=q).json()]

    assert ids() == [a2, a1, chat_one]
    assert ids(status="pending") == [a2, chat_one]
    assert ids(origin="automation") == [a2, a1] and ids(origin="chat") == [chat_one]
    assert ids(automation=5) == [a2] and ids(chat_id=3) == [chat_one]
    assert ids(limit=1) == [a2] and ids(limit=1, before=a2) == [a1] and ids(before=chat_one) == []
    assert client.get("/api/v1/approvals/count", headers=h).json() == {"waiting": 2, "automations": 1}
    one = client.get(f"/api/v1/approvals/{chat_one}", headers=h).json()
    assert one["can"] == {"approve": True, "decline": True, "edit": False, "always": False} and one["intent"] is None
    assert client.get(f"/api/v1/approvals/{a2 + 1}", headers=h).status_code == 404  # someone else's
    assert client.post(f"/api/v1/approvals/{chat_one}", headers=h, json={"decision": "edit", "params": {}}).status_code == 400


def test_credential_headers_stay_hidden_and_sealed_through_an_edit(db, cfg, people):
    sealed = "lens:sealed:abc"
    i = intent(
        people,
        action="http_request",
        params={"method": "POST", "url": "https://api.example.com/x", "headers": {"Authorization": sealed, "Accept": "a"}},
    )
    shown = approvals.shown(i)
    assert shown["params"]["headers"] == {"Authorization": "••••", "Accept": "a"}
    got = approvals.edited(i, {"headers": {"Authorization": "••••", "Accept": "b"}})
    assert got["headers"] == {"Authorization": sealed, "Accept": "b"}


def test_how_long_approvals_wait_is_a_setting(app, client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    h = login(client, "root@x.io", "root password 1")
    assert client.get("/api/v1/settings", headers=h).json()["automations"]["values"]["approval_days"] == 7
    assert client.put("/api/v1/settings/automations", json={"approval_days": 14}, headers=h).status_code == 200
    assert approvals.days(app.state.settings.current()) == 14
    for bad in ({"approval_days": 0}, {"approval_days": 366}, {"approval_days": "7"}, {"approval_days": True}, {"days": 1}):
        assert client.put("/api/v1/settings/automations", json=bad, headers=h).status_code == 400, bad


def test_waiting_and_decided_are_activities_their_person_alone_sees(client, db, cfg, people, posted, studio):
    from app.domain import events

    pods = people["pods"]
    me, other = events.viewer(people["me"], {pods}), events.viewer(people["other"], {pods})
    admin = events.viewer(999, None, admin=True)
    assert events.follow(db, cfg, "approvals-test", ["approval.waiting", "approval.decided"])[0] == []  # marks the start
    start = store.now()
    while store.now() == start:  # what happens in the second it started in isn't read
        time.sleep(0.05)
    aid = approvals.ask(db, cfg, intent(people), {**EVENT, "space": pods})
    a = events.get(db, cfg, f"ask-approval-{aid}", me)
    assert a["kind"] == "approval.waiting" and a["type"] == "Create" and a["actor"]["id"] == "lens:automation/4"
    assert a["object"]["url"] == f"/approvals?id={aid}" and a["object"]["about"] == "create-recording-7"
    assert a["object"]["name"] == "Post “Trip on Friday, bring a packed lunch” to the family room"
    assert a["lens"]["cause"] == {"automation": 4, "run": 51, "depth": 1} and a["lens"]["audience"] == "owner"
    assert "object.name" in a["lens"]["untrusted"]  # the run's outside text, marked for automations reading it
    assert events.get(db, cfg, f"ask-approval-{aid}", other) is None and events.get(db, cfg, f"ask-approval-{aid}", admin) is None
    mine = [x["id"] for x in events.page(db, cfg, me, limit=50)["items"]]
    assert f"ask-approval-{aid}" in mine
    assert not [x for x in events.page(db, cfg, other, limit=50)["items"] if "approval" in x["id"]]
    assert not [x for x in events.page(db, cfg, admin, limit=50)["items"] if "approval" in x["id"]]

    h = login(client, "me@x.io", "my password 1")
    assert client.post(f"/api/v1/approvals/{aid}", headers=h, json={"decision": "decline"}).json()["status"] == "declined"
    d = events.get(db, cfg, f"decide-approval-{aid}", me)
    assert d["kind"] == "approval.decided" and d["type"] == "Reject" and d["actor"]["type"] == "Person"
    assert d["summary"].endswith("declined: Post “Trip on Friday, bring a packed lunch” to the family room")
    assert d["object"]["status"] == "declined" and events.get(db, cfg, f"decide-approval-{aid}", other) is None
    done = approvals.ask(db, cfg, intent(people, n=8))
    client.post(f"/api/v1/approvals/{done}", headers=h, json={"decision": "approve"})
    d = events.get(db, cfg, f"decide-approval-{done}", me)
    assert d["type"] == "Accept" and d["object"]["status"] == "done" and " approved: Post" in d["summary"]
    late = approvals.ask(db, cfg, intent(people, n=9))
    assert events.get(db, cfg, f"decide-approval-{late}", me) is None  # nothing decided yet
    approvals.sweep(db, cfg, dt.datetime.now(dt.UTC) + dt.timedelta(days=8))
    d = events.get(db, cfg, f"decide-approval-{late}", me)
    assert d["type"] == "Reject" and d["actor"]["id"] == "lens:app" and d["summary"].startswith("Expired with no pick")
    got = events.get_many(db, cfg, [f"ask-approval-{late}", f"decide-approval-{late}", f"decide-approval-{aid}"], other)
    assert got == {}
    # an automation following them gets each once
    seen, commit = events.follow(db, cfg, "approvals-test", ["approval.waiting", "approval.decided"])
    commit()
    ids = sorted(x["id"] for x in seen)
    assert ids == sorted(f"{k}-approval-{n}" for k in ("ask", "decide") for n in (aid, done, late))
    again, _ = events.follow(db, cfg, "approvals-test", ["approval.waiting", "approval.decided"])
    assert again == []


def test_an_approval_waiting_is_a_notification_when_asked_for(app, client, db, cfg, people, posted):
    from app.domain import notify
    from tests.api.test_notifications import Inbox, begin

    inbox = Inbox()
    try:
        make_user(db, "root@x.io", "root password 1", admin=True)
        make_user(db, "own@x.io", "owner password 1", roles={"pods": "owner"})
        hr, ho = login(client, "root@x.io", "root password 1"), login(client, "own@x.io", "owner password 1")
        client.put("/api/v1/settings/notifications", json={"networks": ["127.0.0.0/8"]}, headers=hr)
        url = "/api/v1/namespaces/pods/notifications"
        t = client.post(url, json={"name": "Chat", "kind": "slack", "url": inbox.url}, headers=ho).json()["target"]
        assert "approval.waiting" not in t["events"]  # off unless someone asks for it
        c = app.state.settings.current()
        c = {**c, "notifications": {**c["notifications"], "app_url": "https://lens.example"}}
        begin(db, c)
        first = approvals.ask(db, c, intent(people, n=7))
        assert notify.scan(db, c) == 0
        ai_tools.approve(db, c, first, {"id": people["me"], "email": "me@x.io"}, set(), "decline")
        client.patch(f"{url}/{t['id']}", json={"events": ["approval.waiting"]}, headers=ho)
        begin(db, c)
        aid = approvals.ask(db, c, intent(people, n=8, params={"text": "see https://evil.example/x"}))
        decided = approvals.ask(db, c, intent(people, n=9))
        ai_tools.approve(db, c, decided, {"id": people["me"], "email": "me@x.io"}, set(), "decline")
        assert notify.scan(db, c) == 1 and notify.scan(db, c) == 0  # once, and not one decided before it was seen
        assert notify.deliver_due(db, c) == 1
        text = inbox.json()["text"]
        assert text.startswith("Automation 4 waits for a pick in pods: Post “see hxxps://evil[.]example/x”")
        assert "https://evil.example" not in text and text.endswith(f"\nhttps://lens.example/approvals?id={aid}")
    finally:
        inbox.srv.shutdown()
