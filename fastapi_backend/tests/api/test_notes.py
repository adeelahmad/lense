"""Notes on recordings: yours unless an editor shares them, changed only by their writer, deleted by it or (shared) by
the namespace's owners; they move with their recording and go when it's deleted."""

from __future__ import annotations

import pytest

from app.domain import notes, store
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
    return [n["id"] for n in client.get(url, headers=h).json()]


def test_personal_notes(client, env, db):
    a, hv, hv2, he = env["a"], env["hv"], env["hv2"], env["he"]
    url = f"/api/v1/recordings/{a}/notes"
    # a viewer writes notes: about a moment with the words picked, about one instant, about the whole recording
    r = client.post(
        url, headers=hv, json={"text": "  Check the capsid claim ", "t0": 4000, "t1": 9000, "quote": "the capsid\n model  beat"}
    )
    assert r.status_code == 200, r.text
    n1 = r.json()
    assert {k: n1[k] for k in ("recording", "text", "t0", "t1", "quote", "shared", "created_by", "mine", "can_delete", "edited_at")} == {
        "recording": a,
        "text": "Check the capsid claim",
        "t0": 4000,
        "t1": 9000,
        "quote": "the capsid model beat",
        "shared": False,
        "created_by": "vi@x.io",
        "mine": True,
        "can_delete": True,
        "edited_at": None,
    }
    at = client.post(url, headers=hv, json={"text": "Here", "t0": 1000}).json()
    assert (at["t0"], at["t1"]) == (1000, 1000)
    whole = client.post(url, headers=hv, json={"text": "Good episode"}).json()
    assert (whole["t0"], whole["t1"], whole["quote"]) == (None, None, None)
    # the whole recording first, then by moment
    assert _ids(client, url, hv) == [whole["id"], at["id"], n1["id"]]
    # nobody else sees them, not even an editor
    assert client.get(url, headers=hv2).json() == []
    assert client.get(url, headers=he).json() == []
    assert client.patch(f"{url}/{n1['id']}", headers=hv2, json={"text": "mine now"}).status_code == 404
    assert client.delete(f"{url}/{n1['id']}", headers=hv2).status_code == 404

    # checked
    assert client.post(url, headers=hv, json={"text": "   "}).status_code == 400
    assert client.post(url, headers=hv, json={"text": ""}).status_code == 422
    assert client.post(url, headers=hv, json={"text": "x" * 5001}).status_code == 422
    assert client.post(url, headers=hv, json={"text": "x", "t0": -1}).status_code == 422
    assert client.post(url, headers=hv, json={"text": "x", "quote": "q" * 1001}).status_code == 422
    assert client.post(url, headers=hv, json={"text": "x", "colour": "red"}).status_code == 422
    assert client.post(url, headers=hv, json={"text": "x", "t1": 5000}).status_code == 400  # an end needs a start
    assert client.post(url, headers=hv, json={"text": "x", "t0": 5000, "t1": 10}).status_code == 400
    end = db.one("SELECT duration_ms FROM $r", r=R("recording", a))["duration_ms"]
    assert client.post(url, headers=hv, json={"text": "x", "t0": end + 1}).status_code == 400
    late = client.post(url, headers=hv, json={"text": "The end", "t0": end - 10, "t1": end + 60000}).json()
    assert late["t1"] == end  # a moment stops at the end of the recording

    # its writer changes the text; an empty change says so
    r = client.patch(f"{url}/{n1['id']}", headers=hv, json={"text": "Checked: it holds"})
    assert r.status_code == 200 and r.json()["text"] == "Checked: it holds" and r.json()["edited_at"]
    assert client.patch(f"{url}/{n1['id']}", headers=hv, json={}).status_code == 400
    assert client.patch(f"{url}/{n1['id']}", headers=hv, json={"text": " "}).status_code == 400
    # a note belongs to its recording
    assert client.patch(f"/api/v1/recordings/{env['b']}/notes/{n1['id']}", headers=hv, json={"text": "x"}).status_code == 404
    assert client.delete(f"{url}/99999", headers=hv).status_code == 404
    # its writer deletes it; deleting a personal note isn't audited
    assert client.delete(f"{url}/{at['id']}", headers=hv).status_code == 200
    assert _ids(client, url, hv) == [whole["id"], n1["id"], late["id"]]
    assert db.rows("SELECT * FROM audit_log WHERE string::starts_with(action, 'note.')") == []


def test_who_reaches_notes(client, env, db):
    a, call = env["a"], env["call"]
    url = f"/api/v1/recordings/{a}/notes"
    # no role in its namespace: the recording isn't there
    assert client.get(url, headers=env["hx"]).status_code == 404
    assert client.post(url, headers=env["hx"], json={"text": "x"}).status_code == 404
    assert client.get(f"/api/v1/recordings/{call}/notes", headers=env["hv"]).status_code == 404
    assert client.get("/api/v1/recordings/999999/notes", headers=env["hv"]).status_code == 404
    assert client.get(url).status_code == 401
    # a read-only API token reads them but can't write one
    tok = client.post("/api/v1/tokens", json={"name": "ro", "scope": "read"}, headers=env["hv"]).json()["token"]
    ro = {"Authorization": f"Bearer {tok}"}
    client.post(url, headers=env["hv"], json={"text": "Mine"})
    assert [n["text"] for n in client.get(url, headers=ro).json()] == ["Mine"]
    assert client.post(url, headers=ro, json={"text": "x"}).status_code == 403


def test_shared_notes(client, env, db):
    a, hv, hv2, he, ho = env["a"], env["hv"], env["hv2"], env["he"], env["ho"]
    url = f"/api/v1/recordings/{a}/notes"
    mine = client.post(url, headers=hv, json={"text": "Personal"}).json()
    # sharing needs editor access
    assert client.post(url, headers=hv, json={"text": "Team", "shared": True}).status_code == 403
    assert client.patch(f"{url}/{mine['id']}", headers=hv, json={"shared": True}).status_code == 403
    r = client.post(
        url, headers=he, json={"text": "Ask Bob about the dates", "t0": 12000, "quote": "published last spring", "shared": True}
    )
    assert r.status_code == 200, r.text
    team = r.json()
    assert team["shared"] is True
    # everyone who can read the recording sees it, with its writer; outsiders still don't reach it
    seen = client.get(url, headers=hv2).json()
    assert [(n["text"], n["mine"], n["can_delete"], n["created_by"]) for n in seen] == [
        ("Ask Bob about the dates", False, False, "ed@x.io")
    ]
    assert _ids(client, url, hv) == [mine["id"], team["id"]]
    assert client.get(url, headers=env["hx"]).status_code == 404
    # only its writer changes it, even an owner can't
    assert client.patch(f"{url}/{team['id']}", headers=hv2, json={"text": "no"}).status_code == 403
    assert client.patch(f"{url}/{team['id']}", headers=ho, json={"shared": False}).status_code == 403
    # its writer unshares it (it's theirs again) and shares it again
    assert client.patch(f"{url}/{team['id']}", headers=he, json={"shared": False}).json()["shared"] is False
    assert client.get(url, headers=hv2).json() == []
    assert client.patch(f"{url}/{team['id']}", headers=he, json={"shared": True}).json()["shared"] is True
    # an owner of the namespace deletes a shared note (not anyone's personal one); a viewer can't
    assert client.delete(f"{url}/{team['id']}", headers=hv2).status_code == 403
    assert {n["id"]: n["can_delete"] for n in client.get(url, headers=ho).json()} == {team["id"]: True}
    assert client.delete(f"{url}/{team['id']}", headers=ho).status_code == 200
    assert client.get(url, headers=hv2).json() == []
    audit = db.rows("SELECT action, target, detail FROM audit_log WHERE string::starts_with(action, 'note.')")
    assert sorted(x["action"] for x in audit) == ["note.delete", "note.share", "note.share", "note.unshare"]
    assert {x["target"] for x in audit} == {f"recording:{a}"}
    assert [x["detail"] for x in audit if x["action"] == "note.delete"] == [{"note": team["id"], "writer": False}]


def test_notes_move_and_go_with_their_recording(client, env, db):
    a, ho = env["a"], env["ho"]
    url = f"/api/v1/recordings/{a}/notes"
    client.post(url, headers=env["hv"], json={"text": "Personal"})
    team = client.post(url, headers=env["he"], json={"text": "For the team", "shared": True}).json()
    r = client.post(f"/api/v1/recordings/{a}/move", headers=ho, json={"namespace": "calls"})
    assert r.status_code == 200, r.text
    calls = store.ns_id(db, "calls")
    assert set(db.values("SELECT VALUE space FROM note WHERE recording = $r", r=a)) == {calls}
    # readers of its new namespace see the shared note; pods' viewers no longer reach the recording
    assert [n["id"] for n in client.get(url, headers=env["hx"]).json()] == [team["id"]]
    assert client.get(url, headers=env["hv"]).status_code == 404
    assert client.delete(f"/api/v1/recordings/{a}", headers=ho).status_code == 403  # an editor in calls
    make_user(db, "root@x.io", "root password 1", admin=True)
    assert client.delete(f"/api/v1/recordings/{a}", headers=login(client, "root@x.io", "root password 1")).status_code == 200
    assert db.rows("SELECT * FROM note WHERE recording = $r", r=a) == []


def test_note_limits(db, cfg, folder, monkeypatch):
    a, _b, _c = seed(db, cfg, folder)
    space = store.ns_id(db, "pods")
    monkeypatch.setattr(notes, "MAX_NOTES", 2)
    notes.create(db, a, space, 7, "one")
    notes.create(db, a, space, 7, "two")
    with pytest.raises(ValueError, match="2 notes on this recording already"):
        notes.create(db, a, space, 7, "three")
    notes.create(db, a, space, 8, "someone else's")  # per person
    nid = notes.create(db, a, space, 8, "a long quote", 0, 10, "word " * 400)
    assert len(notes.get(db, nid)["quote"]) == notes.QUOTE_MAX
    with pytest.raises(KeyError):
        notes.get(db, 424242)
