"""Namespaces: listing what you can read, creating (admins), settings (owners) and word clouds."""

from __future__ import annotations

from app.domain import pipelines, store
from tests.helpers import login, make_user, seed


def test_list_create_and_tokens(client, new_client, db, cfg, folder):
    seed(db, cfg, folder)
    make_user(db, "root@x.io", "root password 1", admin=True)
    h = login(client, "root@x.io", "root password 1")
    listed = {n["name"]: n for n in client.get("/api/v1/namespaces", headers=h).json()}
    assert set(listed) == {"pods", "calls"}
    assert (listed["pods"]["recordings"], listed["pods"]["analyzed"], listed["pods"]["speakers"], listed["pods"]["role"]) == (
        2,
        2,
        3,
        "owner",
    )
    assert listed["calls"]["graph"] == "isolated"
    assert client.post("/api/v1/namespaces", json={"name": "extra"}, headers=h).status_code == 200
    r = client.post("/api/v1/namespaces", json={"name": "iso", "graph": "isolated"}, headers=h)
    assert r.status_code == 200 and r.json()["id"] == store.ns_id(db, "iso", create=False)
    assert db.one("SELECT graph FROM $r", r=store.R("space", r.json()["id"]))["graph"] == "isolated"
    assert client.post("/api/v1/namespaces", json={"name": "Bad Name"}, headers=h).status_code == 400
    assert client.post("/api/v1/namespaces", json={"name": "x", "graph": "odd"}, headers=h).status_code == 422
    assert "namespace.create" in db.values("SELECT VALUE action FROM audit_log")
    # anonymous callers are refused; a read-only token reads but can't create (legacy test_setup_signin_csrf_tokens)
    tok = client.post("/api/v1/tokens", json={"name": "ci", "scope": "read"}, headers=h).json()["token"]
    anon, bearer = new_client(), {"Authorization": f"Bearer {tok}"}
    assert anon.get("/api/v1/namespaces").status_code == 401
    assert anon.get("/api/v1/namespaces", headers=bearer).status_code == 200
    assert anon.post("/api/v1/namespaces", json={"name": "nope"}, headers=bearer).status_code == 403


def test_owners_edit_namespaces(client, db, cfg, folder):
    seed(db, cfg, folder)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "own@x.io", "owner password 1", roles={"pods": "owner"})
    he, ho = login(client, "ed@x.io", "editor password 1"), login(client, "own@x.io", "owner password 1")
    assert client.post("/api/v1/namespaces", json={"name": "extra"}, headers=ho).status_code == 403  # admins only
    assert client.patch("/api/v1/namespaces/pods", json={"graph": "isolated"}, headers=he).status_code == 403
    assert client.patch("/api/v1/namespaces/calls", json={"graph": "isolated"}, headers=ho).status_code == 404
    assert client.patch("/api/v1/namespaces/pods", json={"graph": "sideways"}, headers=ho).status_code == 422
    assert client.patch("/api/v1/namespaces/pods", json={"graph": None}, headers=ho).status_code == 400
    assert client.patch("/api/v1/namespaces/pods", json={"graph": "isolated"}, headers=ho).status_code == 200
    pods = store.ns_id(db, "pods")
    assert db.one("SELECT graph FROM $r", r=store.R("space", pods))["graph"] == "isolated"
    assert client.patch("/api/v1/namespaces/pods", json={"pipeline": 9999}, headers=ho).status_code == 400
    pid = pipelines.create(db, "just analyze", ["analyze"])
    assert client.patch("/api/v1/namespaces/pods", json={"pipeline": pid}, headers=ho).status_code == 200
    assert db.one("SELECT pipeline FROM $r", r=store.R("space", pods))["pipeline"] == pid
    assert client.patch("/api/v1/namespaces/pods", json={"pipeline": None}, headers=ho).status_code == 200
    assert db.one("SELECT pipeline FROM $r", r=store.R("space", pods)).get("pipeline") is None


def test_namespace_word_cloud(client, db, cfg, folder):
    seed(db, cfg, folder)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    h = login(client, "vi@x.io", "viewer password 1")
    r = client.get("/api/v1/namespaces/pods/wordcloud.svg", headers=h)
    assert r.status_code == 200 and "capsid" in r.text and r.headers["content-type"].startswith("image/svg+xml")
    assert client.get("/api/v1/namespaces/calls/wordcloud.svg", headers=h).status_code == 404
    assert client.get("/api/v1/namespaces/nope/wordcloud.svg", headers=h).status_code == 404


def test_namespace_stats_for_a_range(client, new_client, db, cfg, folder):
    import datetime as dt

    from app.domain import analyze, ingest, library
    from tests.helpers import quiet

    a, b, _call = seed(db, cfg, folder)
    (folder / "undated.txt").write_text("[00:00] Erin: A note without a date.\n[00:04] Frank: Noted, thanks.\n[00:09] Erin: Bye now.")
    undated = ingest.import_transcript(db, cfg, "pods", folder / "undated.txt", log=quiet)
    analyze.analyze_pending(db, cfg, log=quiet)
    for rid, at, ms in ((a, "2026-09-12T10:00:00", 600_000), (b, "2026-06-20T09:00:00", 300_000), (undated, None, 60_000)):
        db.q("UPDATE $r SET recorded_at = $at, duration_ms = $ms", r=store.R("recording", rid), at=at, ms=ms)
    talk = {
        (g["recording"], g["speaker"]): g["ms"]
        for g in db.rows(
            "SELECT recording, speaker, math::sum(dur) AS ms FROM segment WHERE space = $s AND speaker > 0 GROUP BY recording, speaker",
            s=store.ns_id(db, "pods"),
        )
    }
    names = {r["id"]: r["name"] for r in db.rows("SELECT record::id(id) AS id, name ?? label AS name FROM speaker")}
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    make_user(db, "out@x.io", "outsider password 1", roles={"calls": "viewer"})
    hv, ho = login(client, "vi@x.io", "viewer password 1"), login(client, "out@x.io", "outsider password 1")
    url = "/api/v1/namespaces/pods/stats"

    s = client.get(url, params={"from": "2026-04-01", "to": "2026-09-30"}, headers=hv).json()
    assert (s["from"], s["to"], s["recordings"], s["ms"], s["first"], s["last"], s["undated"]) == (
        "2026-04-01",
        "2026-09-30",
        2,
        900_000,
        "2026-06-20",
        "2026-09-12",
        1,
    )
    assert [(m["month"], m["recordings"], m["ms"]) for m in s["months"]] == [
        ("2026-04", 0, 0),
        ("2026-05", 0, 0),
        ("2026-06", 1, 300_000),
        ("2026-07", 0, 0),
        ("2026-08", 0, 0),
        ("2026-09", 1, 600_000),
    ]
    both = {k: sum(v for (rid, spk), v in talk.items() if spk == k and rid in (a, b)) for (_, k) in talk}
    assert s["speakers"] == len({spk for (rid, spk) in talk if rid in (a, b)}) == 3  # Alice, Bob and Carol
    assert [(x["name"], x["talk_ms"]) for x in s["top_speakers"]] == sorted(
        ((names[k], v) for k, v in both.items() if v), key=lambda x: -x[1]
    )
    assert next(x for x in s["top_speakers"] if x["name"] == "Alice")["recordings"] == 2

    # one month: only what was said in it; `to` includes its whole day
    s = client.get(url, params={"from": "2026-09-01", "to": "2026-09-12", "top": 1}, headers=hv).json()
    assert (s["recordings"], s["speakers"], [m["month"] for m in s["months"]]) == (1, 2, ["2026-09"])
    first = min(((k, v) for (rid, k), v in talk.items() if rid == a), key=lambda x: (-x[1], x[0]))
    assert s["top_speakers"] == [{"id": first[0], "name": names[first[0]], "talk_ms": first[1], "recordings": 1}]
    assert client.get(url, params={"from": "2026-09-13"}, headers=hv).json()["recordings"] == 0

    # no range: every recording, the undated one too (it isn't in any month)
    s = client.get(url, headers=hv).json()
    assert (s["from"], s["to"], s["recordings"], s["ms"], s["undated"]) == (None, None, 3, 960_000, 1)
    assert sum(m["recordings"] for m in s["months"]) == 2
    assert s["months"][0]["month"] == "2026-06" and s["months"][-1]["month"] == max(dt.date.today().isoformat()[:7], "2026-09")
    assert s["speakers"] == 5 and {"Erin", "Frank"} < {
        x["name"] for x in client.get(url, params={"top": 50}, headers=hv).json()["top_speakers"]
    }

    # a range that ends before it starts; people without a role there; no sign-in
    assert client.get(url, params={"from": "2026-09-30", "to": "2026-09-01"}, headers=hv).status_code == 400
    assert client.get(url, params={"top": 0}, headers=hv).status_code == 422
    assert client.get(url, headers=ho).status_code == 404
    assert new_client().get(url).status_code == 401
    # a date that isn't a whole day still counts, in no month
    db.q("UPDATE $r SET recorded_at = '2026'", r=store.R("recording", undated))
    s = client.get(url, headers=hv).json()
    assert (s["recordings"], s["undated"], sum(m["recordings"] for m in s["months"])) == (3, 0, 2)
    # the months are capped: the latest twenty years
    long = library.namespace_stats(db, store.ns_id(db, "pods"), "1900-01-01", "2026-09-30")
    assert len(long["months"]) == library.MONTHS_MAX and long["months"][-1]["month"] == "2026-09"
    assert long["months"][0]["month"] == "2006-10" and long["recordings"] == 3  # "2026" sorts like the year's start
