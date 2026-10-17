"""Google Drive connected with Connect with Google (google_auth.connect_start and connect_finish, gdrive.py): the
account is kept as rclone keeps it, with the client that renews it, against tests/fake_google.py, never Google."""

from __future__ import annotations

import json
import urllib.parse

import pytest

from app.domain import gdrive, google_auth, sources
from tests import fake_google
from tests.helpers import login, make_user

MARA = "mara@harbour.example"


@pytest.fixture
def google(monkeypatch):
    g = fake_google.Google()
    monkeypatch.setattr(google_auth, "TOKEN_URL", g.base + "/token")
    monkeypatch.setattr(google_auth, "AUTH_URL", g.base + "/auth")
    monkeypatch.setattr(sources, "test", lambda *a, **k: {"ok": True})  # rclone isn't run in tests
    g.clients.add(("lens-web", "web secret 1"))
    yield g
    g.close()


@pytest.fixture
def admin(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    h = login(client, "root@x.io", "root password 1")
    r = client.post("/api/v1/auth/providers", headers=h, json={"kind": "google", "client_id": "lens-web", "client_secret": "web secret 1"})
    assert r.status_code == 200, r.text
    return h


def _connect(client, google, h, sid):
    url = client.post(f"/api/v1/sources/{sid}/google", headers=h, json={}).json()["url"]
    r = client.get(google.consent(url, MARA), follow_redirects=False)
    assert r.status_code == 303, r.text
    u = urllib.parse.urlsplit(r.headers["location"])
    return url, u.path, dict(urllib.parse.parse_qsl(u.query))


def _conf(cfg, db, sid):
    _, conf = sources._config(cfg, sources.get(db, sid))
    return dict(line.split(" = ", 1) for line in conf.splitlines()[1:])


def test_drive_connects_with_google_for_rclone(google, client, db, cfg, admin):
    h = admin
    body = {"type": "drive", "name": "Mara's Drive", "params": {"root_folder_id": "https://drive.google.com/drive/folders/1AbC"}}
    sid = client.post("/api/v1/sources", headers=h, json=body).json()["id"]
    url, path, back = _connect(client, google, h, sid)
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    assert q["client_id"] == "lens-web" and q["scope"].split() == ["openid", "email", gdrive.READONLY]
    assert path == "/sources" and back == {"google": "connected", "source": str(sid), "account": MARA}

    src = next(s for s in client.get("/api/v1/sources", headers=h).json() if s["id"] == sid)
    assert src["params"]["account"] == MARA and src["params"]["client_id"] == "lens-web"
    assert src["secrets"]["token"]["set"] and src["secrets"]["client_secret"]["set"]
    assert "web secret 1" not in json.dumps(db.rows("SELECT * FROM storage_source"))  # sealed
    # rclone gets its own token shape and the client to renew it with; the account is Lens's, not an rclone option
    conf = _conf(cfg, db, sid)
    tok = json.loads(conf["token"])
    assert tok["refresh_token"] in google.users and tok["token_type"] == "Bearer" and tok["access_token"] and tok["expiry"]
    assert conf["client_id"] == "lens-web" and conf["client_secret"] == "web secret 1"
    assert conf["root_folder_id"] == "1AbC" and "account" not in conf

    # full access asks Google for all of Drive
    client.patch(f"/api/v1/sources/{sid}", headers=h, json={"params": {"scope": "drive"}})
    url, _, back = _connect(client, google, h, sid)
    assert dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))["scope"].split()[-1] == gdrive.FULL
    assert back["google"] == "connected"


def test_a_token_from_rclone_renews_with_rclones_own_client(google, client, db, cfg, admin):
    """Pasting a token (or signing in through rclone) after connecting with Google: the token is rclone's, so the
    Google client and account it was connected with are dropped, or rclone would renew it with the wrong client."""
    h = admin
    key = {"type": "service_account", "private_key": "k", "client_email": "lens@x.iam.gserviceaccount.com"}
    body = {"type": "drive", "secrets": {"service_account_credentials": json.dumps(key)}}
    sid = client.post("/api/v1/sources", headers=h, json=body).json()["id"]
    _connect(client, google, h, sid)
    src = sources.get(db, sid)
    assert set(src["sealed"]) == {"token", "client_secret"}  # the service account gave way to the connected account

    pasted = json.dumps({"access_token": "ya29.x", "token_type": "Bearer", "refresh_token": "1//rclone", "expiry": "2030-01-01T00:00:00Z"})
    r = client.patch(f"/api/v1/sources/{sid}", headers=h, json={"secrets": {"token": pasted}})
    assert r.status_code == 200, r.text
    src = next(s for s in client.get("/api/v1/sources", headers=h).json() if s["id"] == sid)
    assert src["params"]["client_id"] == "" and src["params"]["account"] == ""
    assert not src["secrets"]["client_secret"]["set"]
    conf = _conf(cfg, db, sid)
    assert json.loads(conf["token"])["refresh_token"] == "1//rclone" and "client_id" not in conf and "client_secret" not in conf
    # options alone keep the connection as it is
    _connect(client, google, h, sid)
    client.patch(f"/api/v1/sources/{sid}", headers=h, json={"params": {"root_folder_id": "1Other"}})
    assert _conf(cfg, db, sid)["client_id"] == "lens-web"


def test_drive_scopes():
    assert google_auth.scopes_for({"type": "drive", "params": {"scope": "drive.readonly"}}) == (gdrive.READONLY,)
    assert google_auth.scopes_for({"type": "drive", "params": {"scope": "drive"}}) == (gdrive.FULL,)
    assert google_auth.scopes_for({"type": "drive", "params": {}}) == (gdrive.READONLY,)
