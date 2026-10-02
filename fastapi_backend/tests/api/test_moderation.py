"""Flags on comments (docs/api.md#comments): a decision model judges new and edited comments for spam, abuse and
personal details; owners see the flags, keep the comment or delete it. Nothing is hidden by a flag."""

from __future__ import annotations

import pytest

from app.domain import decide, ingest
from tests import fake_decide
from tests.helpers import login, make_user


@pytest.fixture
def env(client, db, cfg):
    srv, url = fake_decide.start()
    decide.recovered()
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "own@x.io", "owner password 1", roles={"pods": "owner"})
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor", "calls": "owner"})
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    rid = ingest.import_text(db, cfg, "pods", "Ana|N|The shipment leaves on Friday.", title="Shipping notes")
    ingest.import_text(db, cfg, "calls", "Cy|N|Hello there.", title="A call")
    h = {
        k: login(client, f"{k}@x.io", f"{p} password 1")
        for k, p in (("root", "root"), ("own", "owner"), ("ed", "editor"), ("vi", "viewer"))
    }
    yield {"h": h, "rid": rid, "url": url, "jev": fake_decide.Handler}
    srv.shutdown()
    decide.recovered()


def _on(client, env, **more):
    r = client.put(
        "/api/v1/settings/decisions", headers=env["h"]["root"], json={"enabled": True, "base_url": env["url"], "model": "fake-jev", **more}
    )
    assert r.status_code == 200, r.text


def _say(client, env, who, text):
    r = client.post(f"/api/v1/recordings/{env['rid']}/comments", headers=env["h"][who], json={"text": text})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _comments(client, env, who):
    return {c["id"]: c for c in client.get(f"/api/v1/recordings/{env['rid']}/comments", headers=env["h"][who]).json()}


def _audit(db, action):
    return db.rows("SELECT target, detail FROM audit_log WHERE action = $a", a=action)


def test_nothing_is_judged_without_a_decision_model(client, env):
    cid = _say(client, env, "vi", "Buy cheap watches at my shop")
    assert env["jev"].seen == [] and _comments(client, env, "own")[cid]["flagged"] == []
    assert client.get("/api/v1/comments/flagged", headers=env["h"]["own"]).json() == []
    _on(client, env, moderate=False)
    _say(client, env, "vi", "Buy cheap watches at my shop again")
    assert env["jev"].seen == []


def test_flags_are_for_owners_to_review(client, db, env):
    rid, jev, h = env["rid"], env["jev"], env["h"]
    _on(client, env)
    jev.script = {"spam": {"noul": 0.93}, "abuse": {"noul": 0.02}, "personal": {"noul": 0.75}}
    cid = _say(client, env, "vi", "Buy cheap watches, call 555 0100")
    asked = jev.seen[-1]
    assert asked["state"] == {"comment": "Buy cheap watches, call 555 0100"} and set(asked["questions"]) == {"spam", "abuse", "personal"}
    # owners see why; everyone else sees the comment as it is
    mine = _comments(client, env, "own")[cid]
    assert [(f["reason"], f["label"], f["p"]) for f in mine["flagged"]] == [("spam", "Spam", 0.93), ("personal", "Personal details", 0.75)]
    for who in ("ed", "vi"):
        seen = _comments(client, env, who)[cid]
        assert seen["flagged"] == [] and seen["text"] == "Buy cheap watches, call 555 0100"
    jev.script = {"spam": {"noul": 0.1}, "abuse": {"noul": 0.1}, "personal": {"noul": 0.1}}
    fine = _say(client, env, "ed", "The shipment date changed, see line two.")
    assert _comments(client, env, "own")[fine]["flagged"] == []
    # the review list: owners of the namespace (and admins), not its editors
    queue = client.get("/api/v1/comments/flagged", headers=h["own"]).json()
    assert [(q["id"], q["recording"], q["title"], q["namespace"], q["created_by"]) for q in queue] == [
        (cid, rid, "Shipping notes", "pods", "vi@x.io")
    ]
    assert queue[0]["flagged"][0]["reason"] == "spam" and queue[0]["flagged_at"]
    assert [q["id"] for q in client.get("/api/v1/comments/flagged", headers=h["root"]).json()] == [cid]
    assert client.get("/api/v1/comments/flagged", headers=h["ed"]).json() == []  # owns calls, where nothing is flagged
    assert client.get("/api/v1/comments/flagged?ns=calls", headers=h["root"]).json() == []
    # keeping it: owners only, audited, and the same text isn't flagged again
    assert client.delete(f"/api/v1/recordings/{rid}/comments/{cid}/flag", headers=h["ed"]).status_code == 403
    assert client.delete(f"/api/v1/recordings/{rid}/comments/{fine}/flag", headers=h["own"]).status_code == 404
    kept = client.delete(f"/api/v1/recordings/{rid}/comments/{cid}/flag", headers=h["own"])
    assert kept.status_code == 200 and kept.json()["flagged"] == [] and kept.json()["text"].startswith("Buy cheap")
    assert [(a["target"], a["detail"]) for a in _audit(db, "comment.flag.keep")] == [
        (f"recording:{rid}", {"comment": cid, "reasons": ["spam", "personal"]})
    ]
    assert client.get("/api/v1/comments/flagged", headers=h["own"]).json() == []
    # an edit is judged again
    jev.script = {"spam": {"noul": 0.1}, "abuse": {"noul": 0.88}, "personal": {"noul": 0.1}}
    r = client.patch(f"/api/v1/recordings/{rid}/comments/{cid}", headers=h["vi"], json={"text": "You are all idiots"})
    assert r.status_code == 200
    assert [f["reason"] for f in _comments(client, env, "own")[cid]["flagged"]] == ["abuse"]
    # resolving doesn't ask the model
    before = len(jev.seen)
    client.patch(f"/api/v1/recordings/{rid}/comments/{cid}", headers=h["own"], json={"resolved": True})
    assert len(jev.seen) == before
    # an owner deletes it: gone from the list
    assert client.delete(f"/api/v1/recordings/{rid}/comments/{cid}", headers=h["own"]).status_code == 200
    assert client.get("/api/v1/comments/flagged", headers=h["own"]).json() == []


def test_the_threshold_and_a_model_that_cant_answer(client, env):
    jev = env["jev"]
    _on(client, env, flag_above=0.9)
    jev.script = {"spam": {"noul": 0.85}, "abuse": {"noul": 0.1}, "personal": {"noul": 0.1}}
    cid = _say(client, env, "vi", "Visit my shop")
    assert _comments(client, env, "own")[cid]["flagged"] == []
    jev.fail = (529, {"detail": "overloaded"})
    decide.recovered()
    late = _say(client, env, "vi", "Visit my other shop")  # commenting works; it just isn't judged
    assert _comments(client, env, "own")[late]["flagged"] == []
