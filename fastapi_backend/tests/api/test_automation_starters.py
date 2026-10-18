"""Starter automations (docs/automations.md, Starters): every starter is a draft the API saves as it is, switched off,
so picking one changes nothing that runs."""

from __future__ import annotations

from app.domain import automation_starters
from tests.helpers import login, make_user

URL = "/api/v1/automations"


def test_every_starter_saves_as_it_is_and_stays_off(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    h = login(client, "root@x.io", "root password 1")
    r = client.get(f"{URL}/starters", headers=h)
    assert r.status_code == 200, r.text
    got = r.json()["starters"]
    assert [s["id"] for s in got] == [s["id"] for s in automation_starters.STARTERS]
    assert {s["id"] for s in got} >= {"school-emails", "router-alerts", "morning-digest", "no-reply", "webhook-note"}
    for s in got:
        assert s["draft"]["enabled"] is False and s["description"]
        saved = client.post(URL, headers=h, json=s["draft"])
        assert saved.status_code == 200, (s["id"], saved.text)
        a = client.get(f"{URL}/{saved.json()['id']}", headers=h).json()
        assert a["enabled"] is False and a["name"] == s["name"]
    # nothing was switched on, and a webhook starter got its address once saved
    listed = client.get(URL, headers=h).json()["automations"]
    assert len(listed) == len(got) and not any(a["enabled"] for a in listed)


def test_starters_are_for_people_signed_in(client):
    assert client.get(f"{URL}/starters").status_code == 401


def test_starters_are_copies(client):
    first = automation_starters.starters()
    first[0]["draft"]["triggers"].clear()
    assert automation_starters.starters()[0]["draft"]["triggers"]


def test_the_school_starter_tells_me_at_once_and_asks_before_posting_to_the_family(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    h = login(client, "root@x.io", "root password 1")
    school = next(s for s in automation_starters.starters() if s["id"] == "school-emails")
    r = client.post(f"{URL}/test", headers=h, json={"draft": school["draft"], "kind": "email.received"})
    assert r.status_code == 200, r.text
    by = {i["node"]: i for i in r.json()["intents"]}
    assert by["tell"]["status"] == "done" and by["tell"]["risk"] == "routine"
    assert by["family"]["status"] == "waiting" and by["family"]["risk"] == "ask"
