"""Analytics (docs/analytics.md): views, plays, searches, downloads and comments are kept with the account and counted
per day; a namespace's owners see its numbers, a collection's admins theirs, admins everything, each person their
own; nothing about addresses or browsers is kept; what's past its retention is purged."""

from __future__ import annotations

import datetime as dt

from app.domain import jobs, telemetry
from tests.helpers import login, make_user, seed

TODAY = dt.datetime.now(dt.UTC).date().isoformat()


def _people(client, db, cfg, folder):
    ids = seed(db, cfg, folder)
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "own@x.io", "owner password 1", roles={"pods": "owner"})
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    make_user(db, "out@x.io", "outsider password 1")
    h = {
        n: login(client, f"{n}@x.io", f"{p} password 1")
        for n, p in (("root", "root"), ("own", "owner"), ("ed", "editor"), ("vi", "viewer"), ("out", "outsider"))
    }
    return ids, h


def _numbers(client, h, **params):
    r = client.get("/api/v1/analytics", headers=h, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_what_people_do_is_counted_for_the_namespaces_owners(client, new_client, db, cfg, folder):
    ids, h = _people(client, db, cfg, folder)
    a, b, call = ids
    # a viewer opens a resource (twice: one view), plays it, searches, downloads its transcript and comments
    for _ in range(2):
        assert client.get(f"/api/v1/resources/{a}/player", headers=h["vi"]).status_code == 200
    assert client.post(f"/api/v1/resources/{a}/played", headers=h["vi"]).status_code == 204
    assert client.get("/api/v1/search", headers=h["vi"], params={"q": "capsid", "ns": "pods"}).status_code == 200
    assert (
        client.get("/api/v1/search", headers=h["vi"], params={"q": "capsid", "ns": "pods", "offset": 50}).status_code == 200
    )  # its next page
    assert (
        client.get("/api/v1/search", headers=h["vi"], params={"q": "capsid"}).status_code == 200
    )  # every namespace: nobody's in particular
    assert client.get(f"/api/v1/resources/{a}/export.txt", headers=h["vi"]).status_code == 200
    assert client.post(f"/api/v1/resources/{a}/comments", headers=h["vi"], json={"text": "Is this sourced?"}).status_code == 200
    # an editor opens both; what can't be opened isn't counted
    for rid in (a, b):
        assert client.get(f"/api/v1/resources/{rid}/player", headers=h["ed"]).status_code == 200
    assert client.get(f"/api/v1/resources/{call}/player", headers=h["vi"]).status_code == 404
    assert client.post(f"/api/v1/resources/{call}/played", headers=h["vi"]).status_code == 404
    assert client.post(f"/api/v1/resources/{a}/played").status_code == 401

    n = _numbers(client, h["own"], ns="pods")
    assert n["totals"] == {"view": 3, "play": 1, "search": 1, "download": 1, "comment": 1}
    assert (n["people"], n["anonymous"], n["namespace"], n["from"] <= TODAY == n["to"]) == (2, 0, "pods", True)
    assert len(n["days"]) == 30 and n["days"][-1] == {"day": TODAY, "view": 3, "play": 1, "search": 1, "download": 1, "comment": 1}
    assert n["days"][0]["view"] == 0
    assert [(c["name"], c["path"], c["view"], c["play"]) for c in n["collections"]] == [("General", ["General"], 3, 1)]
    assert [(r["id"], r["title"], r["view"], r["play"], r["download"], r["comment"]) for r in n["resources"]] == [
        (a, "ep1", 2, 1, 1, 1),
        (b, "ep2", 1, 0, 0, 0),
    ]
    assert "namespaces" not in n or n["namespaces"] is None
    assert len(_numbers(client, h["own"], ns="pods", days=7)["days"]) == 7
    assert _numbers(client, h["own"], ns="pods", **{"from": "2020-01-01", "to": "2020-01-31"})["totals"]["view"] == 0

    # owners only; a namespace without a role isn't there; the whole archive is the admins'
    get = lambda who, **p: client.get("/api/v1/analytics", headers=h[who], params=p).status_code  # noqa: E731
    assert (get("ed", ns="pods"), get("vi", ns="pods"), get("out", ns="pods"), get("own", ns="calls")) == (403, 403, 404, 404)
    assert (get("own"), get("own", collection=1), get("own", ns="nowhere")) == (403, 400, 404)
    assert get("own", ns="pods", **{"from": "2026-02-01", "to": "2026-01-01"}) == 400
    assert client.get("/api/v1/analytics", params={"ns": "pods"}).status_code == 401
    everything = _numbers(client, h["root"])
    assert everything["totals"]["search"] == 2  # with the search of every namespace
    assert [(s["name"], s["view"]) for s in everything["namespaces"]] == [("pods", 3)] and everything["collections"] is None
    assert _numbers(client, h["root"], ns="calls")["totals"]["view"] == 0

    # nothing but the account, the resource and the time is kept
    row = db.one("SELECT * FROM activity WHERE action = 'play'")
    assert set(row) - {"id"} == {"at", "action", "account", "recording", "space", "collection"}


def test_a_collections_admin_sees_their_collection(client, db, cfg, folder):
    ids, h = _people(client, db, cfg, folder)
    made = client.post("/api/v1/namespaces/pods/collections", headers=h["own"], json={"name": "Season 2"})
    assert made.status_code == 200, made.text
    cid = made.json()["id"]
    assert (
        client.post("/api/v1/resources/collection", headers=h["own"], json={"recordings": [ids[1]], "collection": cid}).status_code == 200
    )
    r = client.put(f"/api/v1/namespaces/pods/collections/{cid}/members", headers=h["own"], json={"email": "out@x.io", "role": "admin"})
    assert r.status_code == 200, r.text
    h["out"] = login(client, "out@x.io", "outsider password 1")
    for rid in ids[:2]:
        assert client.get(f"/api/v1/resources/{rid}/player", headers=h["vi"]).status_code == 200
    mine = _numbers(client, h["out"], ns="pods", collection=cid)
    assert mine["totals"]["view"] == 1 and [r["id"] for r in mine["resources"]] == [ids[1]] and mine["collection"] == cid
    assert [c["name"] for c in mine["collections"]] == ["Season 2"]
    # not the namespace's, and not another collection's
    assert client.get("/api/v1/analytics", headers=h["out"], params={"ns": "pods"}).status_code == 404
    general = next(c["id"] for c in client.get("/api/v1/namespaces/pods/collections", headers=h["own"]).json() if c["name"] == "General")
    assert client.get("/api/v1/analytics", headers=h["out"], params={"ns": "pods", "collection": general}).status_code == 404
    assert client.get("/api/v1/analytics", headers=h["vi"], params={"ns": "pods", "collection": cid}).status_code == 403
    assert client.get("/api/v1/analytics", headers=h["own"], params={"ns": "pods", "collection": 9999}).status_code == 404
    assert _numbers(client, h["own"], ns="pods", collection=cid)["totals"]["view"] == 1
    assert _numbers(client, h["own"], ns="pods")["totals"]["view"] == 2


def test_visitors_count_without_an_account(client, new_client, db, cfg, folder):
    ids, h = _people(client, db, cfg, folder)
    a = ids[0]
    assert client.put(f"/api/v1/resources/{a}/access", headers=h["root"], json={"access": "public"}).status_code == 200
    anon = new_client()
    for _ in range(2):  # nobody to recognise: each opening counts
        assert anon.get(f"/api/v1/public/recordings/{a}").status_code == 200
    assert anon.post(f"/api/v1/public/recordings/{a}/played").status_code == 204
    assert anon.get("/api/v1/public/search", params={"q": "capsid"}).status_code == 200
    assert anon.get("/api/v1/public/search", params={"q": " "}).status_code == 200  # nothing asked
    # what a visitor can't see isn't counted, and isn't said to exist
    assert anon.get(f"/api/v1/public/recordings/{ids[1]}").status_code == 404
    assert anon.post(f"/api/v1/public/recordings/{ids[1]}/played").status_code == 404
    assert anon.post("/api/v1/public/recordings/999/played").status_code == 404
    # a signed-in person without a role is an account like any other
    assert client.get(f"/api/v1/public/recordings/{a}", headers=h["out"]).status_code == 200
    n = _numbers(client, h["own"], ns="pods")
    assert (n["totals"]["view"], n["totals"]["play"], n["anonymous"], n["people"]) == (3, 1, 3, 1)
    assert _numbers(client, h["root"])["totals"]["search"] == 1
    assert {r.get("account") for r in db.rows("SELECT account FROM activity WHERE action = 'play'")} == {None}


def test_each_person_sees_their_own_activity(client, db, cfg, folder):
    ids, h = _people(client, db, cfg, folder)
    assert client.get(f"/api/v1/resources/{ids[0]}/player", headers=h["vi"]).status_code == 200
    assert client.get("/api/v1/search", headers=h["vi"], params={"q": "capsid", "ns": "pods"}).status_code == 200
    assert client.get(f"/api/v1/resources/{ids[1]}/player", headers=h["ed"]).status_code == 200
    mine = client.get("/api/v1/analytics/me", headers=h["vi"]).json()
    assert sorted((e["action"], e["resource"], e["title"], e["namespace"]) for e in mine) == [
        ("search", None, None, "pods"),
        ("view", ids[0], "ep1", "pods"),
    ]
    assert [e["resource"] for e in client.get("/api/v1/analytics/me", headers=h["ed"]).json()] == [ids[1]]
    assert client.get("/api/v1/analytics/me", headers=h["out"]).json() == []
    assert client.get("/api/v1/analytics/me").status_code == 401
    assert client.get("/api/v1/analytics/me", headers=h["vi"], params={"before": mine[-1]["at"]}).json() == []
    assert len(client.get("/api/v1/analytics/me", headers=h["vi"], params={"limit": 1}).json()) == 1
    # a resource they can no longer read keeps its place in their history, without its title
    assert client.put("/api/v1/namespaces/pods/members", headers=h["own"], json={"email": "vi@x.io", "role": None}).status_code == 200
    h["vi"] = login(client, "vi@x.io", "viewer password 1")
    after = client.get("/api/v1/analytics/me", headers=h["vi"]).json()
    assert sorted((e["action"], e["resource"], e["title"], e["namespace"]) for e in after) == [
        ("search", None, None, None),
        ("view", ids[0], None, None),
    ]


def test_retention_and_purging(client, db, cfg, folder):
    ids, h = _people(client, db, cfg, folder)
    put = lambda body, who="root": client.put("/api/v1/settings/analytics", headers=h[who], json=body).status_code  # noqa: E731
    assert (
        put({"retention_days": 30}, "own"),
        put({"retention_days": 0}),
        put({"retention_days": 4000}),
        put({"retention_days": "90"}),
    ) == (403, 400, 400, 400)
    for rid in ids[:2]:
        assert client.get(f"/api/v1/resources/{rid}/player", headers=h["vi"]).status_code == 200
    old = (dt.datetime.now(dt.UTC) - dt.timedelta(days=100)).isoformat(timespec="seconds")
    db.q("UPDATE activity SET at = $t WHERE recording = $r", t=old, r=ids[0])
    status = client.get("/api/v1/admin/analytics", headers=h["root"]).json()
    assert status == {"events": 2, "oldest": old, "days": 2, "retention_days": 90}
    assert client.get("/api/v1/admin/analytics", headers=h["own"]).status_code == 403
    assert client.post("/api/v1/admin/analytics/purge", headers=h["own"], json={}).status_code == 403
    # what's past 90 days goes; the daily counts, which name nobody, stay
    assert client.post("/api/v1/admin/analytics/purge", headers=h["root"], json={}).json() == {"ok": True, "deleted": 1}
    assert client.get("/api/v1/admin/analytics", headers=h["root"]).json()["events"] == 1
    assert _numbers(client, h["own"], ns="pods")["totals"]["view"] == 2
    assert [e["resource"] for e in client.get("/api/v1/analytics/me", headers=h["vi"]).json()] == [ids[1]]
    # a shorter retention is applied by the workers' hourly purge
    assert put({"retention_days": 1}) == 200
    db.q("UPDATE activity SET at = $t", t=(dt.datetime.now(dt.UTC) - dt.timedelta(days=2)).isoformat(timespec="seconds"))
    w = jobs.Worker(db, client.app.state.settings.current)
    import threading

    stop = threading.Event()
    stop.set()
    w.loop(stop)  # stopped already: nothing runs
    assert telemetry.purge(db, client.app.state.settings.current()) == 1
    assert client.get("/api/v1/admin/analytics", headers=h["root"]).json() == {"events": 0, "oldest": None, "days": 2, "retention_days": 1}
    # everything: the counts too
    assert client.post("/api/v1/admin/analytics/purge", headers=h["root"], json={"everything": True}).json()["deleted"] == 0
    assert client.get("/api/v1/admin/analytics", headers=h["root"]).json()["days"] == 0
    purges = [a["detail"] for a in client.get("/api/v1/audit", headers=h["root"]).json() if a["action"] == "analytics.purge"]
    assert sorted(p["everything"] for p in purges) == [False, True]

    # deleting a resource takes its analytics; unknown actions and resources are ignored, and nothing ever raises
    assert client.get(f"/api/v1/resources/{ids[0]}/player", headers=h["vi"]).status_code == 200
    assert client.delete(f"/api/v1/resources/{ids[0]}", headers=h["root"]).status_code == 200
    assert not db.values("SELECT VALUE id FROM activity") and not db.values("SELECT VALUE id FROM activity_day")
    current = client.app.state.settings.current()
    assert telemetry.record(db, current, "dance") is False and telemetry.record(db, current, "view", 1, 999) is False
    assert telemetry.record(None, current, "search") is False
