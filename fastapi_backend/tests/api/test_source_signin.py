"""Connecting Google Drive by signing in with Google in the browser (source_oauth.py), against a fake Google."""

from __future__ import annotations

import json
import urllib.parse

import pytest

from app.domain import external_login, settings, source_oauth, sources
from tests.fake_oidc import Provider
from tests.helpers import login, make_user

WEB = {"x-forwarded-host": "localhost:3000", "x-forwarded-proto": "http"}
ORIGIN = "http://localhost:3000"
ADA = {"sub": "ada-1", "email": "ada@gmail.com", "email_verified": True}
APP = {"kind": "google", "client_id": "lens", "client_secret": "s3cret", "enabled": False}


@pytest.fixture
def google(monkeypatch):
    p = Provider()
    monkeypatch.setitem(
        external_login.ENDPOINTS,
        "google",
        {"authorize": p.base + "/authorize", "token": p.base + "/token", "userinfo": p.base + "/userinfo", "scope": "openid email profile"},
    )
    yield p
    p.close()


@pytest.fixture
def admin(app, client, db, monkeypatch):
    monkeypatch.setattr(sources, "test", lambda *a, **k: {"ok": True})  # no Drive to list in tests
    make_user(db, "root@x.io", "root password 1", admin=True)
    return login(client, "root@x.io", "root password 1")


def _query(url):
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))


def _back(r):
    assert r.status_code == 303, r.text
    url = urllib.parse.urlsplit(r.headers["location"])
    return f"{url.scheme}://{url.netloc}{url.path}", dict(urllib.parse.parse_qsl(url.query))


def _sign_in(client, google, headers, body, profile=ADA):
    r = client.post("/api/v1/sources/signin", json=body, headers={**WEB, **headers})
    assert r.status_code == 200, r.text
    code, state = google.issue(r.json()["url"], profile)
    return client.get("/api/v1/auth/external/google/callback", params={"code": code, "state": state}, headers=WEB, follow_redirects=False)


def test_drive_signs_in_with_google_and_rclone_refreshes_with_that_app(client, db, cfg, admin, google):
    drive = client.get("/api/v1/sources/backends", headers=admin).json()["drive"]
    assert drive["signin"] == {
        "kind": "google",
        "label": "Google",
        "ready": False,
        "callback_path": "/api/v1/auth/external/google/callback",
    }
    assert client.get("/api/v1/sources/backends", headers=admin).json()["s3"]["signin"] is None
    # no Google app yet: it says where to set one up
    r = client.post("/api/v1/sources/signin", json={"type": "drive"}, headers={**WEB, **admin})
    assert r.status_code == 400 and "Google app" in r.json()["detail"]
    # the app, with signing in to Lens with Google left off
    assert client.post("/api/v1/auth/providers", json=APP, headers=admin).status_code == 200
    assert client.get("/api/v1/auth/external").json() == []
    assert client.get("/api/v1/sources/backends", headers=admin).json()["drive"]["signin"]["ready"]

    body = {"type": "drive", "params": {"root_folder_id": "1AbC"}, "next": "/import?tab=source"}
    r = client.post("/api/v1/sources/signin", json=body, headers={**WEB, **admin})
    assert r.status_code == 200 and r.json()["bound"] and "lens_external" in r.headers["set-cookie"]
    q = _query(r.json()["url"])
    assert q["redirect_uri"] == ORIGIN + "/api/v1/auth/external/google/callback"  # the sign-in's own: nothing more to register
    assert q["scope"] == "openid email https://www.googleapis.com/auth/drive.readonly"
    assert q["access_type"] == "offline" and q["prompt"] == "consent" and q["code_challenge_method"] == "S256"
    code, state = google.issue(r.json()["url"], ADA)
    page, back = _back(
        client.get("/api/v1/auth/external/google/callback", params={"code": code, "state": state}, headers=WEB, follow_redirects=False)
    )
    assert page == ORIGIN + "/import" and back["tab"] == "source" and back["connected"]
    sid = int(back["connected"])

    s = next(s for s in client.get("/api/v1/sources", headers=admin).json() if s["id"] == sid)
    assert s["name"] == "Google Drive · ada@gmail.com" and s["type"] == "drive"
    assert s["signed_in"]["provider"] == "Google" and s["signed_in"]["email"] == "ada@gmail.com"
    assert s["secrets"]["token"]["set"] and not s["secrets"]["service_account_credentials"]["set"]
    assert s["params"]["root_folder_id"] == "1AbC" and s["params"]["scope"] == "drive.readonly"
    refresh = next(iter(google.refresh))
    assert refresh not in json.dumps(db.rows("SELECT * FROM storage_source"))  # sealed

    # rclone gets the app's client id and secret: Google refreshes a token only for the app that issued it
    _, conf = sources._config(cfg, sources.get(db, sid))
    assert "client_id = lens\n" in conf and "client_secret = s3cret\n" in conf and "root_folder_id = 1AbC\n" in conf
    tok = json.loads(next(l for l in conf.splitlines() if l.startswith("token = ")).split(" = ", 1)[1])
    assert tok["refresh_token"] == refresh and tok["token_type"] == "Bearer" and tok["expiry"].endswith("Z")
    assert "source.signin" in db.values("SELECT VALUE action FROM audit_log")

    # the link is used once
    again = client.get("/api/v1/auth/external/google/callback", params={"code": code, "state": state}, headers=WEB, follow_redirects=False)
    assert "already used" in urllib.parse.unquote_plus(again.headers["location"])
    # cancelling at Google comes back with why, and makes nothing
    r = client.post("/api/v1/sources/signin", json={"type": "drive"}, headers={**WEB, **admin})
    state = _query(r.json()["url"])["state"]
    _, back = _back(
        client.get(
            "/api/v1/auth/external/google/callback", params={"error": "access_denied", "state": state}, headers=WEB, follow_redirects=False
        )
    )
    assert back == {"connect_error": "signing in didn't finish: you cancelled"}
    assert len(client.get("/api/v1/sources", headers=admin).json()) == 1


def test_a_link_started_in_one_browser_cant_be_finished_in_another(client, new_client, db, admin, google):
    client.post("/api/v1/auth/providers", json=APP, headers=admin)
    r = client.post("/api/v1/sources/signin", json={"type": "drive"}, headers={**WEB, **admin})
    code, state = google.issue(r.json()["url"], ADA)
    other = new_client()
    r = other.get("/api/v1/auth/external/google/callback", params={"code": code, "state": state}, headers=WEB, follow_redirects=False)
    assert "another browser" in urllib.parse.unquote_plus(r.headers["location"])
    assert client.get("/api/v1/sources", headers=admin).json() == []


def test_an_agent_gets_a_link_its_person_opens(client, new_client, db, admin, google):
    """From an API token the link isn't tied to a browser: the person it's handed to finishes it in theirs, once."""
    client.post("/api/v1/auth/providers", json=APP, headers=admin)
    token = client.post("/api/v1/tokens", json={"name": "agent", "scope": "write"}, headers=admin).json()["token"]
    agent = {"Authorization": f"Bearer {token}"}
    r = client.post("/api/v1/sources/signin", json={"type": "drive", "name": "Ada's Drive"}, headers={**WEB, **agent})
    assert r.status_code == 200 and not r.json()["bound"] and "set-cookie" not in r.headers
    code, state = google.issue(r.json()["url"], ADA)
    person = new_client()  # another browser, with no cookie from the start
    _, back = _back(
        person.get("/api/v1/auth/external/google/callback", params={"code": code, "state": state}, headers=WEB, follow_redirects=False)
    )
    s = client.get("/api/v1/sources", headers=admin).json()
    assert back == {"connected": str(s[0]["id"])} and s[0]["name"] == "Ada's Drive"
    # a read-only token can't connect anything
    ro = client.post("/api/v1/tokens", json={"name": "ro", "scope": "read"}, headers=admin).json()["token"]
    r = client.post("/api/v1/sources/signin", json={"type": "drive"}, headers={**WEB, "Authorization": f"Bearer {ro}"})
    assert r.status_code == 403


def test_signing_in_again_replaces_a_service_account_and_a_pasted_token_replaces_the_sign_in(client, db, cfg, admin, google):
    client.post("/api/v1/auth/providers", json=APP, headers=admin)
    key = {
        "type": "service_account",
        "private_key": "-----BEGIN " + "PRIVATE KEY-----\nMIIEv...\n-----END " + "PRIVATE KEY-----\n",  # split: not a real key
        "client_email": "lens@lens-test.iam.gserviceaccount.com",
    }
    body = {"name": "Team drive", "type": "drive", "secrets": {"service_account_credentials": json.dumps(key)}}
    sid = client.post("/api/v1/sources", json=body, headers=admin).json()["id"]
    _, back = _back(_sign_in(client, google, admin, {"source": sid, "params": {"scope": "drive"}}))
    assert back == {"connected": str(sid)}
    s = client.get("/api/v1/sources", headers=admin).json()[0]
    assert s["name"] == "Team drive" and s["params"]["scope"] == "drive" and s["signed_in"]["email"] == "ada@gmail.com"
    assert s["secrets"]["token"]["set"] and not s["secrets"]["service_account_credentials"]["set"]
    assert google.posts[-1]["grant_type"] == "authorization_code" and google.posts[-1]["client_secret"] == "s3cret"
    # editing other options keeps the sign-in
    client.patch(f"/api/v1/sources/{sid}", json={"params": {"root_folder_id": "1X"}}, headers=admin)
    assert client.get("/api/v1/sources", headers=admin).json()[0]["signed_in"]
    # a token pasted from rclone is rclone's own: its app refreshes it, not ours
    pasted = json.dumps({"access_token": "a", "token_type": "Bearer", "refresh_token": "r", "expiry": "2030-01-01T00:00:00Z"})
    client.patch(f"/api/v1/sources/{sid}", json={"secrets": {"token": pasted}}, headers=admin)
    assert client.get("/api/v1/sources", headers=admin).json()[0]["signed_in"] is None
    assert "client_id" not in sources._config(cfg, sources.get(db, sid))[1]
    # unknown sources and types that don't sign in here
    assert client.post("/api/v1/sources/signin", json={"source": 999}, headers={**WEB, **admin}).status_code == 404
    r = client.post("/api/v1/sources/signin", json={"type": "dropbox"}, headers={**WEB, **admin})
    assert r.status_code == 400 and "doesn't sign in here" in r.json()["detail"]
    r = client.post("/api/v1/sources/signin", json={"type": "drive", "params": {"scope": "gmail"}}, headers={**WEB, **admin})
    assert r.status_code == 400 and "scope" in r.json()["detail"]
    assert client.post("/api/v1/sources/signin", json={}, headers={**WEB, **admin}).status_code == 400


def test_fresh_token_refreshes_it_when_it_runs_out(client, db, cfg, admin, google):
    client.post("/api/v1/auth/providers", json=APP, headers=admin)
    _sign_in(client, google, admin, {"type": "drive"})
    src = sources.get(db, client.get("/api/v1/sources", headers=admin).json()[0]["id"])
    first = json.loads(settings.unseal(cfg, src["sealed"]["token"], f"source:{src['id']}:token"))
    calls = len(google.posts)
    assert source_oauth.fresh_token(db, cfg, src) == first["access_token"] and len(google.posts) == calls  # still good
    newer = source_oauth.fresh_token(db, cfg, src, margin=4000)  # as if it ran out within the hour
    assert newer != first["access_token"] and google.posts[-1]["grant_type"] == "refresh_token"
    kept = json.loads(settings.unseal(cfg, sources.get(db, src["id"])["sealed"]["token"], f"source:{src['id']}:token"))
    assert kept["access_token"] == newer and kept["refresh_token"] == first["refresh_token"]  # saved, refresh token kept
    google.refresh.clear()  # revoked at Google
    with pytest.raises(ValueError, match="expired or revoked"):
        source_oauth.fresh_token(db, cfg, sources.get(db, src["id"]), margin=4000)
    # the app removed: nothing refreshes it any more
    client.delete("/api/v1/auth/providers/google", headers=admin)
    with pytest.raises(ValueError, match="sign in again"):
        source_oauth.fresh_token(db, cfg, sources.get(db, src["id"]))
