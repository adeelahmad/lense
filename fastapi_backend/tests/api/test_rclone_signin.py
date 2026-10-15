"""Connecting Drive (with no Google app), Dropbox and OneDrive by signing in through rclone on the server
(rclone_signin.py), against a stand-in for `rclone authorize` (tests/fake_rclone_authorize.py)."""

from __future__ import annotations

import json
import pathlib
import stat
import sys
import urllib.parse

import httpx
import pytest

from app.domain import rclone_signin, sources
from tests.helpers import login, make_user

FAKE = pathlib.Path(__file__).resolve().parent.parent / "fake_rclone_authorize.py"


@pytest.fixture
def rclone(tmp_path, monkeypatch, db):
    exe = tmp_path / "rclone"
    exe.write_text(f'#!/bin/sh\nexec {sys.executable} {FAKE} "$@"\n')
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("RCLONE_BINARY", str(exe))
    monkeypatch.setattr(sources, "test", lambda *a, **k: {"ok": True})  # no service to list in tests
    monkeypatch.setattr(sources, "obscure", lambda cfg, v: v)
    yield exe
    rclone_signin.cancel_all(db)


@pytest.fixture
def admin(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    return login(client, "root@x.io", "root password 1")


def _back(r, code="good", **more):
    """The address the service sends the browser back to, as the person pastes it."""
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(r["url"]).query))
    back = {"state": q["state"], **({"code": code} if code else {}), "scope": q.get("scope", ""), **more}
    return f"http://127.0.0.1:{r['port']}/?{urllib.parse.urlencode(back)}"


def _token(cfg, db, sid):
    _, conf = sources._config(cfg, sources.get(db, sid))
    return json.loads(next(l for l in conf.splitlines() if l.startswith("token = ")).split(" = ", 1)[1]), conf


def test_drive_signs_in_through_rclone_with_no_google_app(client, db, cfg, admin, rclone):
    body = {"type": "drive", "params": {"scope": "drive.readonly", "root_folder_id": "https://drive.google.com/drive/folders/1AbC"}}
    r = client.post("/api/v1/sources/rclone-signin", json=body, headers=admin)
    assert r.status_code == 200, r.text
    start = r.json()
    page = urllib.parse.urlsplit(start["url"])
    q = dict(urllib.parse.parse_qsl(page.query))
    assert page.scheme == "https" and q["client_id"] == "rclone-drive" and q["scope"] == "drive.readonly"  # rclone's app, our scope
    # nothing yet: rclone is waiting for the browser
    wait = client.post(f"/api/v1/sources/rclone-signin/{start['id']}", json={}, headers=admin)
    assert wait.status_code == 200 and wait.json() == {"connected": None}
    # an address that isn't this sign-in's leaves it waiting
    for bad in ("http://evil.example/?code=good", f"http://127.0.0.1:{start['port']}/?state=nope&code=good", "not a link"):
        r = client.post(f"/api/v1/sources/rclone-signin/{start['id']}", json={"address": bad}, headers=admin)
        assert r.status_code == 400 and "paste" in r.json()["detail"], r.text
    r = client.post(f"/api/v1/sources/rclone-signin/{start['id']}", json={"address": _back(start)}, headers=admin)
    assert r.status_code == 200, r.text
    sid = r.json()["connected"]
    s = next(s for s in client.get("/api/v1/sources", headers=admin).json() if s["id"] == sid)
    assert s["name"] == "Google Drive"
    assert s["params"]["root_folder_id"] == "1AbC"
    tok, conf = _token(cfg, db, sid)
    assert tok["refresh_token"] == "1//fake" and "client_id" not in conf
    assert "1//fake" not in json.dumps(db.rows("SELECT * FROM storage_source"))  # sealed
    assert "source.signin" in db.values("SELECT VALUE action FROM audit_log")
    # it's over: rclone has stopped, and asking again answers the same connection (the page may ask as it's made)
    with pytest.raises(httpx.HTTPError):
        httpx.get(f"http://127.0.0.1:{start['port']}/", timeout=2, trust_env=False)
    for again in ({}, {"address": _back(start)}):
        assert client.post(f"/api/v1/sources/rclone-signin/{start['id']}", json=again, headers=admin).json() == {"connected": sid}
    assert len(client.get("/api/v1/sources", headers=admin).json()) == 1
    # the next sign-in clears it away
    client.post("/api/v1/sources/rclone-signin", json={"type": "dropbox"}, headers=admin)
    assert client.post(f"/api/v1/sources/rclone-signin/{start['id']}", json={}, headers=admin).status_code == 404


def test_a_browser_on_the_server_reaches_rclone_itself(client, db, cfg, admin, rclone):
    """A native install: the browser runs where Lens does, so the service's redirect reaches rclone and nothing is pasted."""
    start = client.post("/api/v1/sources/rclone-signin", json={"type": "dropbox", "name": "Team Dropbox"}, headers=admin).json()
    httpx.get(_back(start), timeout=10, trust_env=False)
    for _ in range(50):
        r = client.post(f"/api/v1/sources/rclone-signin/{start['id']}", json={}, headers=admin).json()
        if r["connected"]:
            break
    s = client.get("/api/v1/sources", headers=admin).json()
    assert r["connected"] == s[0]["id"] and s[0]["name"] == "Team Dropbox" and s[0]["type"] == "dropbox"
    assert _token(cfg, db, s[0]["id"])[0]["access_token"] == "ya29.fake"  # printed bare: no options were passed


def test_cancelled_failed_and_replaced_sign_ins(client, db, admin, rclone):
    start = client.post("/api/v1/sources/rclone-signin", json={"type": "onedrive"}, headers=admin).json()
    r = client.post(
        f"/api/v1/sources/rclone-signin/{start['id']}", json={"address": _back(start, code="", error="access_denied")}, headers=admin
    )
    assert r.status_code == 410 and r.json()["detail"] == "signing in didn't finish: you cancelled"
    assert db.rows("SELECT * FROM rclone_signin") == []
    # a code the service refuses
    start = client.post("/api/v1/sources/rclone-signin", json={"type": "onedrive"}, headers=admin).json()
    r = client.post(f"/api/v1/sources/rclone-signin/{start['id']}", json={"address": _back(start, code="stale")}, headers=admin)
    assert r.status_code == 410 and "invalid_grant" in r.json()["detail"]
    # starting another ends the one before: rclone has one address to listen on
    first = client.post("/api/v1/sources/rclone-signin", json={"type": "dropbox"}, headers=admin).json()
    second = client.post("/api/v1/sources/rclone-signin", json={"type": "dropbox"}, headers=admin).json()
    assert client.post(f"/api/v1/sources/rclone-signin/{first['id']}", json={}, headers=admin).status_code == 404
    assert client.delete(f"/api/v1/sources/rclone-signin/{second['id']}", headers=admin).status_code == 200
    assert client.post(f"/api/v1/sources/rclone-signin/{second['id']}", json={"address": _back(second)}, headers=admin).status_code == 404
    # ten minutes on, it has run out
    third = client.post("/api/v1/sources/rclone-signin", json={"type": "dropbox"}, headers=admin).json()
    db.q("UPDATE rclone_signin SET expires_at = '2000-01-01T00:00:00+00:00'")
    r = client.post(f"/api/v1/sources/rclone-signin/{third['id']}", json={"address": _back(third)}, headers=admin)
    assert r.status_code == 410 and "ran out" in r.json()["detail"]
    assert client.get("/api/v1/sources", headers=admin).json() == []
    # types that don't sign in in the browser, and nothing said
    assert client.post("/api/v1/sources/rclone-signin", json={"type": "s3"}, headers=admin).status_code == 400
    assert client.post("/api/v1/sources/rclone-signin", json={}, headers=admin).status_code == 400
    assert client.post("/api/v1/sources/rclone-signin", json={"source": 999}, headers=admin).status_code == 404


def test_signing_in_again_and_agents(client, db, cfg, admin, rclone):
    key = {"type": "service_account", "private_key": "k", "client_email": "lens@x.iam.gserviceaccount.com"}
    body = {"name": "Team drive", "type": "drive", "secrets": {"service_account_credentials": json.dumps(key)}}
    sid = client.post("/api/v1/sources", json=body, headers=admin).json()["id"]
    # an agent with a write token starts it and hands its person the link, then posts the address they paste back
    token = client.post("/api/v1/tokens", json={"name": "agent", "scope": "write"}, headers=admin).json()["token"]
    agent = {"Authorization": f"Bearer {token}"}
    start = client.post("/api/v1/sources/rclone-signin", json={"source": sid, "params": {"scope": "drive"}}, headers=agent).json()
    assert dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(start["url"]).query))["scope"] == "drive"
    r = client.post(f"/api/v1/sources/rclone-signin/{start['id']}", json={"address": _back(start)}, headers=agent)
    assert r.json() == {"connected": sid}
    s = client.get("/api/v1/sources", headers=admin).json()[0]
    assert s["name"] == "Team drive" and s["params"]["scope"] == "drive"
    assert s["secrets"]["token"]["set"] and not s["secrets"]["service_account_credentials"]["set"]  # signed in: no service account
    assert _token(cfg, db, sid)[0]["refresh_token"] == "1//fake"  # rclone's encoded answer, decoded
    # a read-only token can't
    ro = client.post("/api/v1/tokens", json={"name": "ro", "scope": "read"}, headers=admin).json()["token"]
    assert (
        client.post("/api/v1/sources/rclone-signin", json={"type": "drive"}, headers={"Authorization": f"Bearer {ro}"}).status_code == 403
    )


def test_without_rclone_it_says_so(client, db, admin, monkeypatch):
    monkeypatch.setenv("RCLONE_BINARY", "")
    monkeypatch.setattr(sources.shutil, "which", lambda *a: None)
    r = client.post("/api/v1/sources/rclone-signin", json={"type": "dropbox"}, headers=admin)
    assert r.status_code == 400 and "rclone isn't installed" in r.json()["detail"]


def test_onedrive_finds_the_accounts_own_drive(client, db, admin, rclone, monkeypatch):
    """rclone needs OneDrive's drive named; signing in fills it in from Microsoft, unless it was given."""
    asked = []
    monkeypatch.setattr(rclone_signin, "_my_drive", lambda token: asked.append(token) or {"drive_id": "b!abc", "drive_type": "personal"})
    start = client.post("/api/v1/sources/rclone-signin", json={"type": "onedrive"}, headers=admin).json()
    sid = client.post(f"/api/v1/sources/rclone-signin/{start['id']}", json={"address": _back(start)}, headers=admin).json()["connected"]
    assert sources.get(db, sid)["params"] == {"drive_id": "b!abc", "drive_type": "personal"}
    assert json.loads(asked[0])["access_token"] == "ya29.fake"
    start = client.post("/api/v1/sources/rclone-signin", json={"source": sid, "params": {"drive_id": "b!other"}}, headers=admin).json()
    client.post(f"/api/v1/sources/rclone-signin/{start['id']}", json={"address": _back(start)}, headers=admin)
    assert sources.get(db, sid)["params"]["drive_id"] == "b!other" and len(asked) == 1
