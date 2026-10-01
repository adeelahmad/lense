"""Comments and highlights on resources (docs/api.md#comments): everyone who can read a resource reads and joins its
comment threads, resolved by their writer or an editor and deleted by their writer or an owner; editors mark passages
in colour for everyone; both move with their resource and go when it's deleted; destructive and moderating actions
are audited."""

from __future__ import annotations

import pytest

from app.domain import comments, highlights, store
from tests.helpers import login, make_user, seed

R = store.R


@pytest.fixture
def env(client, db, cfg, folder):
    a, b, call = seed(db, cfg, folder)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    make_user(db, "vi2@x.io", "viewer password 2", roles={"pods": "viewer"})
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "own@x.io", "owner password 1", roles={"pods": "owner", "calls": "editor"})
    make_user(db, "out@x.io", "outsider password 1", roles={"calls": "editor"})
    db.q("UPDATE account SET name = 'Ana' WHERE email = 'ed@x.io'")
    return {
        "a": a,
        "b": b,
        "call": call,
        "hv": login(client, "vi@x.io", "viewer password 1"),
        "hv2": login(client, "vi2@x.io", "viewer password 2"),
        "he": login(client, "ed@x.io", "editor password 1"),
        "ho": login(client, "own@x.io", "owner password 1"),
        "hx": login(client, "out@x.io", "outsider password 1"),
    }


def _ids(client, url, h):
    return [c["id"] for c in client.get(url, headers=h).json()]


def _audits(db, prefix):
    return db.rows(f"SELECT action, target, detail FROM audit_log WHERE string::starts_with(action, '{prefix}')")


def test_comment_threads(client, env, db):
    a, hv, hv2, he = env["a"], env["hv"], env["hv2"], env["he"]
    url = f"/api/v1/resources/{a}/comments"
    # a viewer comments on a passage, with the words picked; everyone who can read the resource sees it
    r = client.post(
        url, headers=hv, json={"text": "  Is this claim sourced? ", "t0": 4000, "t1": 9000, "quote": "the capsid\n model  beat"}
    )
    assert r.status_code == 200, r.text
    c1 = r.json()
    assert {
        k: c1[k]
        for k in ("recording", "parent", "text", "t0", "t1", "quote", "resolved", "created_by", "mine", "can_resolve", "can_delete")
    } == {
        "recording": a,
        "parent": None,
        "text": "Is this claim sourced?",
        "t0": 4000,
        "t1": 9000,
        "quote": "the capsid model beat",
        "resolved": False,
        "created_by": "vi@x.io",
        "mine": True,
        "can_resolve": True,
        "can_delete": True,
    }
    whole = client.post(url, headers=hv2, json={"text": "Good episode"}).json()
    assert (whole["t0"], whole["t1"], whole["quote"]) == (None, None, None)
    # replies go on the thread, a reply to a reply too, and are about what it's about
    r1 = client.post(url, headers=he, json={"text": "Yes, see the paper", "parent": c1["id"], "t0": 99, "quote": "ignored"})
    assert r1.status_code == 200, r1.text
    r1 = r1.json()
    assert (r1["parent"], r1["t0"], r1["quote"], r1["created_by"], r1["created_by_name"], r1["mine"]) == (
        c1["id"],
        None,
        None,
        "ed@x.io",
        "Ana",
        True,
    )
    r2 = client.post(url, headers=hv, json={"text": "Thanks!", "parent": r1["id"]}).json()
    assert r2["parent"] == c1["id"]
    # the whole resource first, then by moment, each thread followed by its replies
    assert _ids(client, url, hv2) == [whole["id"], c1["id"], r1["id"], r2["id"]]
    seen = {c["id"]: (c["mine"], c["can_resolve"], c["can_delete"]) for c in client.get(url, headers=hv2).json()}
    assert seen == {
        whole["id"]: (True, True, True),
        c1["id"]: (False, False, False),
        r1["id"]: (False, False, False),
        r2["id"]: (False, False, False),
    }

    # checked
    assert client.post(url, headers=hv, json={"text": "   "}).status_code == 400
    assert client.post(url, headers=hv, json={"text": ""}).status_code == 422
    assert client.post(url, headers=hv, json={"text": "x" * 5001}).status_code == 422
    assert client.post(url, headers=hv, json={"text": "x", "colour": "red"}).status_code == 422
    assert client.post(url, headers=hv, json={"text": "x", "t1": 5000}).status_code == 400  # an end needs a start
    assert client.post(url, headers=hv, json={"text": "x", "t0": 5000, "t1": 10}).status_code == 400
    end = db.one("SELECT duration_ms FROM $r", r=R("recording", a))["duration_ms"]
    assert client.post(url, headers=hv, json={"text": "x", "t0": end + 1}).status_code == 400
    assert client.post(url, headers=hv, json={"text": "x", "parent": 424242}).status_code == 400
    other = client.post(f"/api/v1/resources/{env['b']}/comments", headers=hv, json={"text": "On ep2"}).json()
    r = client.post(url, headers=hv, json={"text": "x", "parent": other["id"]})
    assert (r.status_code, r.json()["detail"]) == (400, "The comment replied to isn't on this resource.")

    # its writer changes the text; nobody else, not even an editor
    r = client.patch(f"{url}/{c1['id']}", headers=hv, json={"text": "Is this claim sourced anywhere?"})
    assert r.status_code == 200 and r.json()["text"] == "Is this claim sourced anywhere?" and r.json()["edited_at"]
    assert client.patch(f"{url}/{c1['id']}", headers=he, json={"text": "no"}).status_code == 403
    assert client.patch(f"{url}/{c1['id']}", headers=hv, json={}).status_code == 400
    assert client.patch(f"{url}/{c1['id']}", headers=hv, json={"text": " "}).status_code == 400
    # a comment belongs to its resource
    assert client.patch(f"/api/v1/resources/{env['b']}/comments/{c1['id']}", headers=hv, json={"text": "x"}).status_code == 404
    assert client.delete(f"{url}/99999", headers=hv).status_code == 404


def test_who_reaches_comments(client, env):
    a, call = env["a"], env["call"]
    url = f"/api/v1/resources/{a}/comments"
    # no role on it: the resource isn't there
    assert client.get(url, headers=env["hx"]).status_code == 404
    assert client.post(url, headers=env["hx"], json={"text": "x"}).status_code == 404
    assert client.get(f"/api/v1/resources/{call}/comments", headers=env["hv"]).status_code == 404
    assert client.get(f"/api/v1/resources/{call}/highlights", headers=env["hv"]).status_code == 404
    assert client.get("/api/v1/resources/999999/comments", headers=env["hv"]).status_code == 404
    assert client.get(url).status_code == 401
    assert client.get(f"/api/v1/resources/{a}/highlights").status_code == 401
    # a read-only API token reads them but can't write
    tok = client.post("/api/v1/tokens", json={"name": "ro", "scope": "read"}, headers=env["hv"]).json()["token"]
    ro = {"Authorization": f"Bearer {tok}"}
    client.post(url, headers=env["hv"], json={"text": "Mine"})
    assert [c["text"] for c in client.get(url, headers=ro).json()] == ["Mine"]
    assert client.post(url, headers=ro, json={"text": "x"}).status_code == 403
    assert client.get(f"/api/v1/resources/{a}/highlights", headers=ro).json() == []
    assert client.post(f"/api/v1/resources/{a}/highlights", headers=ro, json={"t0": 0}).status_code == 403


def test_resolving_threads(client, env, db):
    a, hv, hv2, he = env["a"], env["hv"], env["hv2"], env["he"]
    url = f"/api/v1/resources/{a}/comments"
    c = client.post(url, headers=hv, json={"text": "Check the dates", "t0": 12000}).json()
    reply = client.post(url, headers=hv2, json={"text": "Looking", "parent": c["id"]}).json()
    # another reader can't resolve it; its writer and an editor can, and it says who did
    assert client.patch(f"{url}/{c['id']}", headers=hv2, json={"resolved": True}).status_code == 403
    r = client.patch(f"{url}/{c['id']}", headers=he, json={"resolved": True})
    assert r.status_code == 200, r.text
    assert (r.json()["resolved"], r.json()["resolved_by"], r.json()["resolved_by_name"]) == (True, "ed@x.io", "Ana")
    assert r.json()["resolved_at"]
    # resolving it again changes nothing; a reply isn't resolved on its own
    assert client.patch(f"{url}/{c['id']}", headers=he, json={"resolved": True}).status_code == 200
    r = client.patch(f"{url}/{reply['id']}", headers=he, json={"resolved": True})
    assert (r.status_code, r.json()["detail"]) == (400, "A reply can't be resolved on its own: resolve its thread.")
    # its writer reopens it
    r = client.patch(f"{url}/{c['id']}", headers=hv, json={"resolved": False}).json()
    assert (r["resolved"], r["resolved_by"], r["resolved_at"]) == (False, None, None)
    assert sorted((x["action"], x["detail"]) for x in _audits(db, "comment.")) == [
        ("comment.reopen", {"comment": c["id"]}),
        ("comment.resolve", {"comment": c["id"]}),
    ]


def test_deleting_comments(client, env, db):
    a, hv, hv2, ho = env["a"], env["hv"], env["hv2"], env["ho"]
    url = f"/api/v1/resources/{a}/comments"
    c = client.post(url, headers=hv, json={"text": "Check the dates", "t0": 12000}).json()
    r1 = client.post(url, headers=hv2, json={"text": "Looking", "parent": c["id"]}).json()
    client.post(url, headers=hv, json={"text": "Any news?", "parent": c["id"]})
    mine = client.post(url, headers=hv2, json={"text": "Good episode"}).json()
    # a reader deletes their own, not others'; an editor can't delete others' either
    assert client.delete(f"{url}/{r1['id']}", headers=hv).status_code == 403
    assert client.delete(f"{url}/{c['id']}", headers=env["he"]).status_code == 403
    assert client.delete(f"{url}/{mine['id']}", headers=hv2).status_code == 200
    # an owner deletes a thread, replies and all
    assert {x["id"]: x["can_delete"] for x in client.get(url, headers=ho).json()} == {c["id"]: True, r1["id"]: True, c["id"] + 2: True}
    assert client.delete(f"{url}/{c['id']}", headers=ho).status_code == 200
    assert client.get(url, headers=hv).json() == []
    assert sorted(((x["target"], x["detail"]) for x in _audits(db, "comment.delete")), key=lambda x: x[1]["comment"]) == [
        (f"recording:{a}", {"comment": c["id"], "writer": False, "replies": 2}),
        (f"recording:{a}", {"comment": mine["id"], "writer": True, "replies": 0}),
    ]


def test_highlights(client, env, db):
    a, hv, he, ho = env["a"], env["hv"], env["he"], env["ho"]
    url = f"/api/v1/resources/{a}/highlights"
    # readers see highlights but editors make them
    assert client.post(url, headers=hv, json={"t0": 4000, "t1": 9000}).status_code == 403
    r = client.post(url, headers=he, json={"t0": 4000, "t1": 9000, "quote": "the capsid\n model  beat", "label": "  Key claim "})
    assert r.status_code == 200, r.text
    h1 = r.json()
    assert {
        k: h1[k] for k in ("recording", "t0", "t1", "quote", "colour", "label", "created_by", "created_by_name", "mine", "can_edit")
    } == {
        "recording": a,
        "t0": 4000,
        "t1": 9000,
        "quote": "the capsid model beat",
        "colour": "yellow",
        "label": "Key claim",
        "created_by": "ed@x.io",
        "created_by_name": "Ana",
        "mine": True,
        "can_edit": True,
    }
    h2 = client.post(url, headers=ho, json={"t0": 1000, "colour": "green"}).json()
    assert (h2["t1"], h2["label"], h2["quote"]) == (1000, None, None)
    # by passage, for everyone who can read it
    assert [(h["id"], h["mine"], h["can_edit"]) for h in client.get(url, headers=hv).json()] == [
        (h2["id"], False, False),
        (h1["id"], False, False),
    ]
    assert client.get(url, headers=env["hx"]).status_code == 404

    # checked
    assert client.post(url, headers=he, json={"t1": 9000}).status_code == 422  # a passage needs its start
    r = client.post(url, headers=he, json={"t0": 4000, "colour": "pink"})
    assert r.status_code == 422
    assert client.post(url, headers=he, json={"t0": 5000, "t1": 10}).status_code == 400
    assert client.post(url, headers=he, json={"t0": 0, "label": "x" * 201}).status_code == 422
    assert client.post(url, headers=he, json={"t0": 0, "shared": True}).status_code == 422
    end = db.one("SELECT duration_ms FROM $r", r=R("recording", a))["duration_ms"]
    assert client.post(url, headers=he, json={"t0": end + 1}).status_code == 400
    late = client.post(url, headers=he, json={"t0": end - 10, "t1": end + 60000, "colour": "blue"}).json()
    assert late["t1"] == end

    # any editor changes one: its colour, its label (an empty label clears it); readers don't
    assert client.patch(f"{url}/{h1['id']}", headers=hv, json={"colour": "red"}).status_code == 403
    r = client.patch(f"{url}/{h1['id']}", headers=ho, json={"colour": "red", "label": "Disputed claim"})
    assert r.status_code == 200 and (r.json()["colour"], r.json()["label"]) == ("red", "Disputed claim")
    assert client.patch(f"{url}/{h1['id']}", headers=he, json={"label": ""}).json()["label"] is None
    assert client.patch(f"{url}/{h1['id']}", headers=he, json={}).status_code == 400
    assert client.patch(f"{url}/{h1['id']}", headers=he, json={"colour": "pink"}).status_code == 422
    assert client.patch(f"/api/v1/resources/{env['b']}/highlights/{h1['id']}", headers=he, json={"colour": "red"}).status_code == 404
    # and deletes one, audited
    assert client.delete(f"{url}/{h2['id']}", headers=hv).status_code == 403
    assert client.delete(f"{url}/{h2['id']}", headers=he).status_code == 200
    assert client.delete(f"{url}/{h1['id']}", headers=he).status_code == 200
    assert client.delete(f"{url}/{h1['id']}", headers=he).status_code == 404
    assert _ids(client, url, hv) == [late["id"]]
    assert sorted(((x["target"], x["detail"]) for x in _audits(db, "highlight.")), key=lambda x: x[1]["highlight"]) == [
        (f"recording:{a}", {"highlight": h1["id"], "writer": True}),
        (f"recording:{a}", {"highlight": h2["id"], "writer": False}),
    ]


def test_comments_and_highlights_move_and_go_with_their_resource(client, env, db):
    a, ho = env["a"], env["ho"]
    c = client.post(f"/api/v1/resources/{a}/comments", headers=env["hv"], json={"text": "Check"}).json()
    client.post(f"/api/v1/resources/{a}/comments", headers=env["he"], json={"text": "Checked", "parent": c["id"]})
    h = client.post(f"/api/v1/resources/{a}/highlights", headers=env["he"], json={"t0": 0, "t1": 2000}).json()
    r = client.post(f"/api/v1/resources/{a}/move", headers=ho, json={"namespace": "calls"})
    assert r.status_code == 200, r.text
    calls = store.ns_id(db, "calls")
    assert set(db.values("SELECT VALUE space FROM comment WHERE recording = $r", r=a)) == {calls}
    assert db.values("SELECT VALUE space FROM highlight WHERE recording = $r", r=a) == [calls]
    # readers of its new namespace see them; pods' viewers no longer reach the resource
    assert [x["text"] for x in client.get(f"/api/v1/resources/{a}/comments", headers=env["hx"]).json()] == ["Check", "Checked"]
    assert [x["id"] for x in client.get(f"/api/v1/resources/{a}/highlights", headers=env["hx"]).json()] == [h["id"]]
    assert client.get(f"/api/v1/resources/{a}/comments", headers=env["hv"]).status_code == 404
    make_user(db, "root@x.io", "root password 1", admin=True)
    assert client.delete(f"/api/v1/resources/{a}", headers=login(client, "root@x.io", "root password 1")).status_code == 200
    assert db.rows("SELECT * FROM comment WHERE recording = $r", r=a) == []
    assert db.rows("SELECT * FROM highlight WHERE recording = $r", r=a) == []


def test_comment_and_highlight_limits(db, cfg, folder, monkeypatch):
    a, _b, _c = seed(db, cfg, folder)
    space = store.ns_id(db, "pods")
    monkeypatch.setattr(comments, "MAX_COMMENTS", 2)
    root = comments.create(db, a, space, 7, "one")
    comments.create(db, a, space, 7, "two", parent=root)
    with pytest.raises(ValueError, match="2 comments on this resource already"):
        comments.create(db, a, space, 7, "three")
    comments.create(db, a, space, 8, "someone else's", parent=root)  # per person
    assert comments.delete(db, root) == 2
    assert comments.on(db, a) == []
    with pytest.raises(KeyError):
        comments.get(db, root)
    monkeypatch.setattr(highlights, "MAX_HIGHLIGHTS", 1)
    hid = highlights.create(db, a, space, 7, 0, 10, "word " * 400, "Blue", " a  label ")
    assert (highlights.get(db, hid)["colour"], highlights.get(db, hid)["label"]) == ("blue", "a label")
    assert len(highlights.get(db, hid)["quote"]) == 1000
    with pytest.raises(ValueError, match="1 highlights already"):
        highlights.create(db, a, space, 7, 0, 10)
    with pytest.raises(ValueError, match="needs the passage"):
        highlights.create(db, a, space, 7, None)
    with pytest.raises(KeyError):
        highlights.get(db, 424242)
