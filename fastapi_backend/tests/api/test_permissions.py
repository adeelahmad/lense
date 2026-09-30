"""Permission on one recording (docs/access.md): owners give it to people without a role in the namespace, who then see
all of the recording on the pages visitors see and in IIIF, whatever its access."""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from app.domain import analyze, ingest
from tests.api.test_iiif import BASE, CLIP, VIEWER, sign_in
from tests.helpers import login, make_user, quiet, seed, write_wav


@pytest.fixture
def env(app, db, cfg, folder, client):
    a, b, call = seed(db, cfg, folder)
    wav, tr = folder / "clip.wav", folder / "clip.txt"
    write_wav(wav)
    tr.write_text(CLIP)
    clip = ingest.import_transcript(db, cfg, "pods", tr, audio=wav, log=quiet)  # private: the namespace's default
    analyze.analyze_pending(db, cfg, log=quiet)
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "own@x.io", "owner password 1", roles={"pods": "owner"})
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "guest@x.io", "guest password 1")  # no role anywhere
    make_user(db, "gone@x.io", "gone password 1")
    db.q("UPDATE account SET disabled = true WHERE email = 'gone@x.io'")
    return {
        "app": app,
        "a": a,
        "b": b,
        "call": call,
        "clip": clip,
        "wav": wav,
        "ho": login(client, "own@x.io", "owner password 1"),
        "he": login(client, "ed@x.io", "editor password 1"),
        "hg": login(client, "guest@x.io", "guest password 1"),
    }


def test_owners_give_and_take_permission(client, new_client, env, db):
    clip, ho, he, hg = env["clip"], env["ho"], env["he"], env["hg"]
    url = f"/api/v1/recordings/{clip}/permissions"
    page = f"/api/v1/public/recordings/{clip}"
    # a private recording: not there for someone without a role
    assert client.get(page, headers=hg).status_code == 404
    assert client.get("/api/v1/public/collections/pods", headers=hg).status_code == 404
    # giving permission is for owners, like publishing
    assert client.post(url, headers=he, json={"email": "guest@x.io"}).status_code == 403
    assert client.get(url, headers=he).status_code == 403
    assert client.post(url, headers=hg, json={"email": "guest@x.io"}).status_code == 404
    for email, status in (("nobody@x.io", 404), ("gone@x.io", 404), ("ed@x.io", 400), ("root@x.io", 400)):
        assert client.post(url, headers=ho, json={"email": email}).status_code == status, email
    people = client.post(url, headers=ho, json={"email": " Guest@X.io "}).json()
    assert [(p["email"], p["by"]) for p in people] == [("guest@x.io", "own@x.io")]
    assert client.post(url, headers=ho, json={"email": "guest@x.io"}).json() == people  # giving it again changes nothing
    assert client.get(url, headers=ho).json() == people

    # they see all of it: the page, the media, the transcript, in lists and search
    d = client.get(page, headers=hg).json()
    assert (d["view"], d["granted"], d["member"], d["access"], d["closed"]) == ("full", True, False, "private", [])
    assert d["media"]["url"] and d["transcript"]["segments"]
    home = client.get("/api/v1/public/home", headers=hg).json()
    assert [c["id"] for c in home["shared"]] == [clip]
    assert [(c["name"], c["recordings"], c["member"]) for c in home["collections"]] == [("pods", 1, False)]
    coll = client.get("/api/v1/public/collections/pods", headers=hg).json()
    assert [(x["id"], x["view"]) for x in coll["items"]] == [(clip, "full")]
    assert [x["id"] for x in client.get("/api/v1/public/search", params={"q": "capsid"}, headers=hg).json()["items"]] == [clip]
    # nobody else does
    anon = new_client()
    assert anon.get(page).status_code == 404 and anon.get("/api/v1/public/home").json()["shared"] == []

    # taking it away
    assert client.delete(f"{url}/999999", headers=ho).status_code == 404
    guest = people[0]["account"]
    assert client.delete(f"{url}/{guest}", headers=he).status_code == 403
    assert client.delete(f"{url}/{guest}", headers=ho).json() == []
    assert client.get(page, headers=hg).status_code == 404
    assert client.delete(f"{url}/{guest}", headers=ho).status_code == 404
    audit = sorted(
        (x["action"], x["detail"]["email"]) for x in db.rows("SELECT action, detail FROM audit_log WHERE action CONTAINS 'permission'")
    )
    assert audit == [
        ("recording.permission.give", "guest@x.io"),
        ("recording.permission.give", "guest@x.io"),
        ("recording.permission.take", "guest@x.io"),
    ]


def test_permission_opens_iiif(client, env):
    clip = env["clip"]
    c = TestClient(env["app"], base_url=BASE)
    assert c.get(f"/iiif/{clip}/manifest", headers=env["hg"]).status_code == 404  # private
    client.post(f"/api/v1/recordings/{clip}/permissions", headers=env["ho"], json={"email": "guest@x.io"})
    # the manifest and the closed content, with the person's token
    assert c.get(f"/iiif/{clip}/manifest", headers=env["hg"]).status_code == 200
    assert c.get(f"/iiif/{clip}/transcript.vtt", headers=env["hg"]).status_code == 200
    assert c.get(f"/iiif/{clip}/transcript.vtt").status_code == 404
    items = c.get("/iiif/collection/pods", headers=env["hg"]).json()["items"]
    assert [i["id"].rsplit("/", 2)[-2] for i in items] == [str(clip)]
    # and through the IIIF Authorization Flow: signed in on the access page, a viewer's probe gets the media
    assert c.get(f"/iiif/{clip}/manifest").status_code == 404
    sign_in(c, "guest@x.io", "guest password 1", VIEWER)
    assert c.get(f"/iiif/{clip}/manifest").status_code == 200  # the IIIF access cookie carries their permission
    tok = c.get("/iiif/auth/token", params={"messageId": "g", "origin": VIEWER}).text
    msg = json.loads(re.search(r"postMessage\((\{.*?\}), ", tok).group(1))
    viewer = TestClient(env["app"], base_url=BASE)
    res = viewer.get(f"/iiif/auth/probe/{clip}/audio", headers={"Authorization": f"Bearer {msg['accessToken']}"}).json()
    assert res["status"] == 302
    got = viewer.get(res["location"]["id"].replace(BASE, ""), headers={"Range": "bytes=0-9"})
    assert (got.status_code, got.content) == (206, env["wav"].read_bytes()[:10])
    # other recordings stay closed to them
    res = viewer.get(f"/iiif/auth/probe/{env['a']}/transcript", headers={"Authorization": f"Bearer {msg['accessToken']}"}).json()
    assert res["status"] == 403


def test_asking_for_access(client, new_client, env, db, cfg, monkeypatch):
    from app.api.v1.routes import public as public_routes
    from app.domain import metadata

    sent = []
    monkeypatch.setattr(public_routes, "send_access_request_email", lambda *args: sent.append(args))
    clip, call, ho, he, hg = env["clip"], env["call"], env["ho"], env["he"], env["hg"]
    hr = login(client, "root@x.io", "root password 1")
    metadata.save(db, cfg, clip, {"access": "public", "open": ["transcript"]})  # the audio and chapters are closed
    metadata.save(db, cfg, call, {"access": "restricted"})
    page = f"/api/v1/public/recordings/{clip}"
    ask = f"{page}/request"

    d = client.get(page, headers=hg).json()
    assert (d["can_request"], d["request"]) == (True, None)
    assert new_client().post(ask, json={}).status_code == 401  # visitors sign in first
    r = client.post(ask, headers=hg, json={"message": "  For my thesis on capsids.  "})
    assert r.status_code == 200 and (r.json()["status"], r.json()["message"]) == ("pending", "For my thesis on capsids.")
    assert [(s[0], s[1], s[2]) for s in sent] == [(["own@x.io"], "guest (guest@x.io)", clip)]  # the namespace's owners hear of it
    assert client.get(page, headers=hg).json()["request"]["status"] == "pending"
    client.post(ask, headers=hg, json={"message": "Please?"})
    assert len(sent) == 1  # asking again the same day updates the message, without another email

    # owners see it on the recording and in their inbox; editors don't answer requests
    reqs = client.get(f"/api/v1/recordings/{clip}/requests", headers=ho).json()
    assert [(x["email"], x["status"], x["message"]) for x in reqs] == [("guest@x.io", "pending", "Please?")]
    assert client.get(f"/api/v1/recordings/{clip}/requests", headers=he).status_code == 403
    assert [x["recording"] for x in client.get("/api/v1/access-requests", headers=ho).json()] == [clip]
    assert client.get("/api/v1/access-requests", headers=he).json() == []
    guest = reqs[0]["account"]
    assert client.post(f"/api/v1/recordings/{clip}/requests/{guest}/approve", headers=he).status_code == 403
    # approving gives permission
    done = client.post(f"/api/v1/recordings/{clip}/requests/{guest}/approve", headers=ho).json()
    assert (done[0]["status"], done[0]["decided_by"]) == ("approved", "own@x.io")
    d = client.get(page, headers=hg).json()
    assert (d["view"], d["granted"], d["can_request"], d["media"] is not None) == ("full", True, False, True)
    assert [p["email"] for p in client.get(f"/api/v1/recordings/{clip}/permissions", headers=ho).json()] == ["guest@x.io"]
    assert client.post(f"/api/v1/recordings/{clip}/requests/{guest}/approve", headers=ho).status_code == 404
    assert client.get("/api/v1/access-requests", headers=ho).json() == []
    assert client.post(ask, headers=hg, json={}).status_code == 400  # they see all of it now

    # a restricted recording: its namespace has no owner, so admins hear of it; declined, they may ask again
    r = client.post(f"/api/v1/public/recordings/{call}/request", headers=hg, json={})
    assert r.json()["status"] == "pending" and sent[-1][:3] == (["root@x.io"], "guest (guest@x.io)", call)
    assert client.post(f"/api/v1/recordings/{call}/requests/{guest}/decline", headers=hr).json()[0]["status"] == "declined"
    d = client.get(f"/api/v1/public/recordings/{call}", headers=hg).json()
    assert (d["view"], d["can_request"], d["request"]["status"]) == ("locked", True, "declined")
    client.post(f"/api/v1/public/recordings/{call}/request", headers=hg, json={})
    assert len(sent) == 3  # a new request after a decline is news again
    actions = sorted(x["action"] for x in db.rows("SELECT action FROM audit_log WHERE action CONTAINS 'request'"))
    assert actions == ["recording.request.approve", "recording.request.decline"]

    # nothing to ask for: everything open, a private recording, or a member
    metadata.save(db, cfg, env["a"], {"access": "public"})
    assert client.post(f"/api/v1/public/recordings/{env['a']}/request", headers=hg, json={}).status_code == 400
    assert client.post(f"/api/v1/public/recordings/{env['b']}/request", headers=hg, json={}).status_code == 404
    assert client.post(ask, headers=he, json={}).status_code == 400
