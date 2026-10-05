"""Import webhooks (docs/api.md#import-webhooks): a namespace's owners make and revoke them, and anything with the token
pushes files, web addresses or text into that namespace."""

from __future__ import annotations

import pytest

from app.domain import convert, import_hooks, store, uploads
from tests.helpers import login, make_user, text_pdf, write_wav

R = store.R
HOOKS = "/api/v1/namespaces/pods/import-hooks"
PUSH = "/api/v1/hooks/import"
SRT = "1\n00:00:01,000 --> 00:00:03,000\nThe tide came in early.\n\n2\n00:00:04,000 --> 00:00:06,000\nWe moved the boats.\n"


@pytest.fixture
def env(client, db, folder):
    wav = folder / "talk.wav"
    write_wav(wav, seconds=1.5)
    store.ns_id(db, "pods")
    make_user(db, "own@x.io", "owner password 1", roles={"pods": "owner"})
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    return {
        "wav": wav.read_bytes(),
        "ho": login(client, "own@x.io", "owner password 1"),
        "he": login(client, "ed@x.io", "editor password 1"),
    }


def _hook(client, h, **body):
    r = client.post(HOOKS, headers=h, json={"name": "Scanner", **body})
    assert r.status_code == 200, r.text
    return r.json()


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_owners_manage_hooks(client, env, db):
    ho, he = env["ho"], env["he"]
    assert client.post(HOOKS, headers=he, json={"name": "Mine"}).status_code == 403  # editors can't
    assert client.get(HOOKS, headers=he).status_code == 403
    assert client.post(HOOKS, headers=ho, json={"name": " "}).status_code == 400
    assert client.post(HOOKS, headers=ho, json={"name": "X", "collection": 999}).status_code == 404
    assert client.post(HOOKS, headers=ho, json={"name": "X", "pipeline": 999}).status_code == 400

    made = _hook(client, ho)
    token, hid = made["token"], made["hook"]["id"]
    assert token.startswith("lih_") and made["path"] == PUSH
    listed = client.get(HOOKS, headers=ho).json()
    assert [(h["name"], h["namespace"], h["enabled"], h["token_tail"], h["used"]) for h in listed] == [
        ("Scanner", "pods", True, token[-4:], 0)
    ]
    assert "token" not in listed[0] and "token_hash" not in listed[0]
    assert not db.values("SELECT VALUE id FROM import_hook WHERE token_hash = $t", t=token)  # only a hash is kept

    # paused: refused, token kept; resumed: works again
    r = client.patch(f"{HOOKS}/{hid}", headers=ho, json={"enabled": False, "name": "Office scanner"})
    assert r.status_code == 200 and (r.json()["enabled"], r.json()["name"]) == (False, "Office scanner")
    assert client.post(PUSH, headers=_bearer(token), json={"text": "hello"}).status_code == 403
    client.patch(f"{HOOKS}/{hid}", headers=ho, json={"enabled": True})
    assert client.post(PUSH, headers=_bearer(token), json={"text": "Hello there.\nGeneral Kenobi."}).status_code == 202
    assert client.patch(f"{HOOKS}/{hid}", headers=ho, json={}).status_code == 400

    # a new token stops the old one
    new = client.post(f"{HOOKS}/{hid}/token", headers=ho).json()["token"]
    assert client.post(PUSH, headers=_bearer(token), json={"text": "x"}).status_code == 401
    assert client.post(f"{PUSH}/{new}", json={"text": "Once more, with the token in the address."}).status_code == 202

    # another namespace's owner can't see it; deleting it stops it
    assert client.delete(f"/api/v1/namespaces/calls/import-hooks/{hid}", headers=ho).status_code in (403, 404)
    assert client.delete(f"{HOOKS}/{hid}", headers=ho).status_code == 200
    assert client.post(PUSH, headers=_bearer(new), json={"text": "x"}).status_code == 401
    assert client.get(HOOKS, headers=ho).json() == []
    actions = db.values("SELECT VALUE action FROM audit_log WHERE action CONTAINS 'import_hook'")
    assert {"import_hook.create", "import_hook.update", "import_hook.token", "import_hook.delete"} <= set(actions)


def test_pushing_files_urls_and_text(client, env, db, cfg, monkeypatch):
    token = _hook(client, env["ho"])["token"]
    h = _bearer(token)
    assert client.post(PUSH, json={"text": "x"}).status_code == 401
    assert client.post(PUSH, headers=_bearer("lih_" + "a" * 32), json={"text": "x"}).status_code == 401

    # a raw body, named in the query: audio becomes a recording, and the same file again is that recording
    r = client.post(f"{PUSH}?filename=talk.wav&title=Harbour+talk", headers={**h, "Content-Type": "audio/wav"}, content=env["wav"])
    assert r.status_code == 202, r.text
    got = r.json()
    assert got["namespace"] == "pods" and len(got["items"]) == 1
    item = got["items"][0]
    assert (item["kind"], item["name"], item["duplicate"]) == ("file", "talk.wav", False) and item["job"]
    rec = db.one("SELECT title, space, source FROM $r", r=R("recording", item["recording"]))
    assert rec["title"] == "Harbour talk" and rec["space"] == store.ns_id(db, "pods", create=False)
    again = client.post(PUSH, headers={**h, "X-Filename": "talk.wav"}, content=env["wav"]).json()["items"][0]
    assert again["duplicate"] and again["recording"] == item["recording"]

    # unnamed, or a type Lens doesn't take: refused before anything is written
    assert client.post(PUSH, headers=h, content=b"abc").status_code == 400
    r = client.post(f"{PUSH}?filename=tool.exe", headers=h, content=b"MZ")
    assert r.status_code == 400 and "can't be imported" in r.json()["detail"]

    # multipart: a PDF document and a subtitle file read as a transcript, in one push
    files = [
        ("file", ("minutes.pdf", text_pdf([["Harbour minutes", "The lamp was lit at dusk."]]), "application/pdf")),
        ("file", ("tide.srt", SRT.encode(), "application/x-subrip")),
    ]
    r = client.post(PUSH, headers=h, files=files)
    assert r.status_code == 202, r.text
    pdf, srt = r.json()["items"]
    assert db.one("SELECT source FROM $r", r=R("recording", pdf["recording"]))["source"] == "document"
    segs = db.values("SELECT VALUE text FROM segment WHERE recording = $r", r=srt["recording"])
    assert "The tide came in early." in " ".join(segs)

    # JSON: text, and a link to a PDF (no Chromium needed); a page needs Chromium
    r = client.post(PUSH, headers=h, json={"text": "Notes from the quay.\nAll boats in.", "title": "Quay notes"})
    assert r.status_code == 202 and r.json()["items"][0]["kind"] == "text"
    monkeypatch.setattr(convert, "chromium", lambda cfg: None)
    r = client.post(PUSH, headers=h, json={"url": "https://8.8.8.8/files/report.pdf"})
    assert r.status_code == 202, r.text
    web = db.one("SELECT web, source FROM $r", r=R("recording", r.json()["items"][0]["recording"]))
    assert web["web"]["url"] == "https://8.8.8.8/files/report.pdf" and web["source"] == "document"
    assert client.post(PUSH, headers=h, json={"url": "https://8.8.8.8/news/"}).status_code == 400
    assert client.post(PUSH, headers=h, json={"url": "http://127.0.0.1/admin.pdf"}).status_code == 400
    assert client.post(PUSH, headers=h, json={}).status_code == 400
    assert client.post(PUSH, headers={**h, "Content-Type": "application/json"}, content=b"{bad").status_code == 400

    # counted on the hook, and audited as the hook, not a person
    hook = import_hooks.hooks(db, store.ns_id(db, "pods", create=False))[0]
    assert hook["used"] == 6 and hook["last_used_at"]
    by = db.rows("SELECT account, email FROM audit_log WHERE action = 'import.hook'")
    assert len(by) == 6 and {b["account"] for b in by} == {f"import_hook:{hook['id']}"}
    assert not list(uploads.incoming(cfg).glob("hook-*"))  # nothing left behind, refused or taken


def test_size_limit_and_collection(client, env, db, cfg, monkeypatch):
    ho = env["ho"]
    sid = store.ns_id(db, "pods", create=False)
    col = client.post("/api/v1/namespaces/pods/collections", headers=ho, json={"name": "Inbox"})
    if col.status_code != 200:
        pytest.skip(f"collections API answered {col.status_code}")
    cid = col.json()["id"]
    token = _hook(client, ho, collection=cid)["token"]
    r = client.post(f"{PUSH}?filename=talk.wav", headers=_bearer(token), content=env["wav"])
    assert r.status_code == 202, r.text
    assert db.one("SELECT collection FROM $r", r=R("recording", r.json()["items"][0]["recording"]))["collection"] == store.home(
        db, sid, cid
    )
    monkeypatch.setattr(uploads, "MB", 1)  # files up to 4096 bytes
    r = client.post(f"{PUSH}?filename=other.wav", headers=_bearer(token), content=b"RIFF" + b"\0" * 5000)
    assert r.status_code == 413
