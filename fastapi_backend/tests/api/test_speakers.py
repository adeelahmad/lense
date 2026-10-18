"""Speakers over HTTP: listing, naming, merging and undoing, linking across namespaces."""

from __future__ import annotations

import numpy as np

from app.domain import speakers as spk
from app.domain import store
from tests.helpers import login, make_user, seed, speaker_names


def _sid(db, ns, name):
    return db.values("SELECT VALUE record::id(id) FROM speaker WHERE space = $s AND name = $n", s=store.ns_id(db, ns), n=name)[0]


def test_rename_merge_undo_link(app, client, db, cfg, folder):
    seed(db, cfg, folder)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor", "calls": "editor"})
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    he, hv = login(client, "ed@x.io", "editor password 1"), login(client, "vi@x.io", "viewer password 1")
    d = client.get("/api/v1/speakers", params={"ns": "pods"}, headers=hv).json()
    assert {s["display"] for s in d["speakers"]} == {"Alice", "Bob", "Carol"}
    assert d["merges"] == [] and d["links"] == []
    assert client.get("/api/v1/speakers", params={"ns": "calls"}, headers=hv).status_code == 404
    bob, carol = _sid(db, "pods", "Bob"), _sid(db, "pods", "Carol")
    recs = client.get(f"/api/v1/speakers/{carol}/recordings", headers=hv).json()
    assert len(recs) == 1 and recs[0]["talk_ms"] > 0
    assert client.get(f"/api/v1/speakers/{_sid(db, 'calls', 'Dave')}/recordings", headers=hv).status_code == 404
    assert client.get("/api/v1/speakers/99999/recordings", headers=hv).status_code == 404

    app.state.graph_cache["x"] = 1
    assert client.post(f"/api/v1/speakers/{bob}", json={"name": "Robert"}, headers=hv).status_code == 403
    assert client.post(f"/api/v1/speakers/{bob}", json={"name": "Robert"}, headers=he).status_code == 200
    assert "Robert" in speaker_names(db, "pods") and app.state.graph_cache == {}

    r = client.post(f"/api/v1/speakers/{carol}/merge", json={"into": bob}, headers=he)
    assert r.status_code == 200, r.text
    mid = r.json()["merge_id"]
    assert speaker_names(db, "pods") == {"Alice", "Robert"}
    merges = client.get("/api/v1/speakers", params={"ns": "pods"}, headers=hv).json()["merges"]
    assert merges[0]["id"] == mid and merges[0]["into_name"] == "Robert" and merges[0]["from_name"] == "Carol"
    assert client.post(f"/api/v1/speakers/{bob}/merge", json={"into": _sid(db, "calls", "Dave")}, headers=he).status_code == 400
    assert client.post(f"/api/v1/merges/{mid}/undo", headers=hv).status_code == 403
    assert client.post(f"/api/v1/merges/{mid}/undo", headers=he).status_code == 200
    assert speaker_names(db, "pods") == {"Alice", "Robert", "Carol"}
    assert client.post(f"/api/v1/merges/{mid}/undo", headers=he).status_code == 400  # only once
    assert client.post("/api/v1/merges/99999/undo", headers=he).status_code == 404

    alice_pods, alice_calls = _sid(db, "pods", "Alice"), _sid(db, "calls", "Alice")
    assert client.post(f"/api/v1/speakers/{alice_pods}/link", json={"with": alice_calls}, headers=he).status_code == 200
    links = client.get("/api/v1/speakers", params={"ns": "pods"}, headers=hv).json()["links"]
    assert links == [{"a": alice_pods, "b": alice_calls, "a_name": "Alice", "b_name": "Alice", "a_ns": "pods", "b_ns": "another namespace"}]
    assert "speaker.merge" in db.values("SELECT VALUE action FROM audit_log")


def test_link_needs_both_namespaces(client, db, cfg, folder):
    seed(db, cfg, folder)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    he = login(client, "ed@x.io", "editor password 1")
    alice_pods, alice_calls = _sid(db, "pods", "Alice"), _sid(db, "calls", "Alice")
    assert client.post(f"/api/v1/speakers/{alice_pods}/link", json={"with": alice_calls}, headers=he).status_code == 404
    assert client.post(f"/api/v1/speakers/{alice_pods}/link", json={"with_": alice_calls}, headers=he).status_code == 422
    assert client.post(f"/api/v1/speakers/{alice_pods}/merge", json={}, headers=he).status_code == 422


def test_not_the_same_unlink_and_merge_history(app, client, db, cfg, folder):
    seed(db, cfg, folder)
    pods, talks = store.ns_id(db, "pods"), store.ns_id(db, "talks")  # both share their graph
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor", "talks": "editor"})
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer", "talks": "viewer"})
    he, hv = login(client, "ed@x.io", "editor password 1"), login(client, "vi@x.io", "viewer password 1")
    voice, other = np.eye(8)[0], np.eye(8)[1]
    xav = spk.new_speaker(db, pods, "Xavier", emb=voice, weight=10)
    xav_t = spk.new_speaker(db, talks, "Xavier T", emb=voice, weight=10)
    wendy = spk.new_speaker(db, pods, "Wendy", emb=other, weight=10)
    db.q("CREATE suggestion CONTENT $d", d={"speaker": wendy, "candidate": xav, "score": 0.4, "space": pods})

    # likely the same voice in another shared namespace, with how alike they are
    d = client.get("/api/v1/speakers", params={"ns": "pods"}, headers=hv).json()
    assert d["cross"] == [{"a": xav, "b": xav_t, "a_name": "Xavier", "b_name": "Xavier T", "a_ns": "pods", "b_ns": "talks", "score": 1.0}]
    assert [s["suggestions"] for s in d["speakers"] if s["id"] == wendy] == [[{"id": xav, "name": "Xavier", "score": 0.4}]]

    # "not the same": editors only; the suggestion and the cross-namespace match go, and don't come back
    assert client.post(f"/api/v1/speakers/{wendy}/not-same", json={"with": xav}, headers=hv).status_code == 403
    assert client.post(f"/api/v1/speakers/{wendy}/not-same", json={"with": wendy}, headers=he).status_code == 400
    assert client.post(f"/api/v1/speakers/{wendy}/not-same", json={"with": 99999}, headers=he).status_code == 404
    assert client.post(f"/api/v1/speakers/{wendy}/not-same", json={"with": xav}, headers=he).status_code == 200
    assert client.post(f"/api/v1/speakers/{xav_t}/not-same", json={"with": xav}, headers=he).status_code == 200  # either way round
    d = client.get("/api/v1/speakers", params={"ns": "pods"}, headers=hv).json()
    assert d["cross"] == [] and all(s["suggestions"] == [] for s in d["speakers"])
    db.q("CREATE suggestion CONTENT $d", d={"speaker": xav, "candidate": wendy, "score": 0.41, "space": pods})  # e.g. from an older run
    assert all(s["suggestions"] == [] for s in client.get("/api/v1/speakers", params={"ns": "pods"}, headers=hv).json()["speakers"])

    # unlinking: editors of both namespaces; 404 when they weren't linked
    alice, alice_t = _sid(db, "pods", "Alice"), spk.new_speaker(db, talks, "Alice")
    assert client.post(f"/api/v1/speakers/{alice}/link", json={"with": alice_t}, headers=he).status_code == 200
    assert client.post(f"/api/v1/speakers/{alice}/unlink", json={"with": alice_t}, headers=hv).status_code == 403
    assert client.post(f"/api/v1/speakers/{alice_t}/unlink", json={"with": alice}, headers=he).status_code == 200
    assert client.get("/api/v1/speakers", params={"ns": "pods"}, headers=hv).json()["links"] == []
    assert client.post(f"/api/v1/speakers/{alice}/unlink", json={"with": alice_t}, headers=he).status_code == 404

    # merges keep who made them, how many recordings and lines moved, and who undid them
    bob, carol = _sid(db, "pods", "Bob"), _sid(db, "pods", "Carol")
    lines = len(db.values("SELECT VALUE id FROM segment WHERE speaker = $s", s=carol))
    mid = client.post(f"/api/v1/speakers/{carol}/merge", json={"into": bob}, headers=he).json()["merge_id"]
    m = client.get("/api/v1/speakers", params={"ns": "pods"}, headers=hv).json()["merges"][0]
    assert (m["id"], m["by"], m["recordings"], m["segments"], m["undone_by"]) == (mid, "ed@x.io", 1, lines, None)
    assert client.post(f"/api/v1/merges/{mid}/undo", headers=he).status_code == 200
    m = client.get("/api/v1/speakers", params={"ns": "pods"}, headers=hv).json()["merges"][0]
    assert m["undone"] is True and m["undone_by"] == "ed@x.io" and m["undone_at"]
    acts = set(db.values("SELECT VALUE action FROM audit_log"))
    assert {"speaker.not_same", "speaker.link", "speaker.unlink", "speaker.merge", "speaker.merge.undo"} <= acts


def test_detach_one_assets_voice(client, db, cfg, folder):
    seed(db, cfg, folder)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    he, hv = login(client, "ed@x.io", "editor password 1"), login(client, "vi@x.io", "viewer password 1")
    by = {}
    for r in db.rows(
        "SELECT speaker, recording FROM segment WHERE space = $s AND speaker > 0 GROUP BY speaker, recording", s=store.ns_id(db, "pods")
    ):
        by.setdefault(r["speaker"], []).append(r["recording"])
    sid, (keep, wrong, *_) = next((s, rs) for s, rs in by.items() if len(rs) >= 2)
    rng = np.random.default_rng(5)
    voice, other = (v / np.linalg.norm(v) for v in rng.normal(size=(2, 192)))
    for rid, e in ((keep, voice), (wrong, other)):
        db.q("DELETE appearance WHERE recording = $r AND speaker = $s", r=rid, s=sid)
        db.q(
            "CREATE appearance CONTENT $a",
            a={"recording": rid, "speaker": sid, "local_label": "S0", "method": "voice", "embedding": list(e)},
        )
    db.q("UPDATE $r SET embedding = $e", r=store.R("speaker", sid), e=list((voice + other) / np.linalg.norm(voice + other)))

    path = f"/api/v1/speakers/{sid}/detach"
    assert client.post(path, json={"recording": wrong}, headers=hv).status_code == 403
    r = client.post(path, json={"recording": wrong}, headers=he)
    assert r.status_code == 200, r.text
    new = r.json()["speaker"]
    assert set(db.values("SELECT VALUE speaker FROM segment WHERE recording = $r AND speaker IN [$a, $b]", r=wrong, a=sid, b=new)) == {new}
    assert db.values("SELECT VALUE speaker FROM appearance WHERE recording = $r AND local_label = 'S0'", r=wrong) == [new]
    centre = np.asarray(db.one("SELECT embedding FROM $r", r=store.R("speaker", sid))["embedding"])
    assert float(np.dot(centre, voice)) > 0.99  # their voice without the other one's
    assert db.rows("SELECT a, b FROM not_same") == [{"a": min(sid, new), "b": max(sid, new)}]  # never suggested again
    assert client.post(path, json={"recording": wrong}, headers=he).status_code == 400  # not heard there any more
    if len(by[sid]) == 2:
        assert client.post(path, json={"recording": keep}, headers=he).status_code == 400  # only heard here: rename
    assert client.post("/api/v1/speakers/99999/detach", json={"recording": wrong}, headers=he).status_code == 404

    assert client.post(f"/api/v1/speakers/{new}/merge", json={"into": sid}, headers=he).status_code == 200  # Undo
    assert sid in db.values("SELECT VALUE speaker FROM segment WHERE recording = $r", r=wrong)
