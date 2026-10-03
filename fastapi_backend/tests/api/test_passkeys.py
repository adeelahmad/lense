"""Passkeys: the first admin with one, signing in, sign-in links, managing them, and installs without passwords."""

from __future__ import annotations

import pytest

from app.domain import auth, passkeys, settings, store
from tests.fake_authenticator import Authenticator
from tests.helpers import login, make_user

WEB = {"x-forwarded-host": "localhost:3000", "x-forwarded-proto": "http"}
ORIGIN = "http://localhost:3000"


def _signin(client, device, cred_id=None, headers=WEB, origin=ORIGIN):
    o = client.post("/api/v1/auth/passkey/options", headers=headers)
    assert o.status_code == 200, o.text
    o = o.json()
    r = client.post(
        "/api/v1/auth/passkey", json={"flow": o["flow"], "credential": device.get(o["options"], origin, cred_id)}, headers=headers
    )
    return r


def _session(client, ticket):
    r = client.post("/api/v1/auth/ticket", json={"ticket": ticket})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _setup(app, client, device, email="ada@x.io"):
    code = app.state.archive.setup_code
    o = client.post("/api/v1/auth/passkey/setup/options", json={"code": code, "email": email, "name": "Ada"}, headers=WEB).json()
    r = client.post(
        "/api/v1/auth/passkey/setup",
        json={"flow": o["flow"], "credential": device.create(o["options"], ORIGIN), "name": "Laptop"},
        headers=WEB,
    )
    assert r.status_code == 200, r.text
    return r.json()["ticket"]


def test_first_admin_with_a_passkey_and_no_password(app, client, db):
    """A fresh install: the first admin is made with a passkey, and has no password at all."""
    device = Authenticator()
    bad = client.post("/api/v1/auth/passkey/setup/options", json={"code": "nope", "email": "ada@x.io"}, headers=WEB)
    assert bad.status_code == 403
    o = client.post(
        "/api/v1/auth/passkey/setup/options", json={"code": app.state.archive.setup_code, "email": "ada@x.io", "name": "Ada"}, headers=WEB
    ).json()
    assert o["options"]["rp"]["id"] == "localhost"
    assert o["options"]["authenticatorSelection"]["residentKey"] == "required"
    assert o["options"]["authenticatorSelection"]["userVerification"] == "required"
    assert auth.account_count(db) == 0  # nothing is made before the passkey checks out
    r = client.post("/api/v1/auth/passkey/setup", json={"flow": o["flow"], "credential": device.create(o["options"], ORIGIN)}, headers=WEB)
    h = _session(client, r.json()["ticket"])
    me = client.get("/api/v1/auth/me", headers=h).json()
    assert me["user"]["email"] == "ada@x.io" and me["user"]["admin"] and me["via"] == "access"
    assert not passkeys.has_password(db, me["user"]["id"])
    assert app.state.archive.setup_code is None
    # setup is closed now, with either kind
    assert client.post("/api/v1/auth/passkey/setup/options", json={"code": "x", "email": "b@x.io"}, headers=WEB).status_code == 403
    # the answer can't be replayed
    assert client.post("/api/v1/auth/passkey/setup", json={"flow": o["flow"], "credential": {}}, headers=WEB).status_code == 403


def test_sign_in_with_a_passkey(app, client, db):
    device = Authenticator()
    ticket = _setup(app, client, device)
    assert client.post("/api/v1/auth/ticket", json={"ticket": ticket}).status_code == 200
    assert client.post("/api/v1/auth/ticket", json={"ticket": ticket}).status_code == 401  # once only

    r = _signin(client, device)
    assert r.status_code == 200, r.text
    h = _session(client, r.json()["ticket"])
    assert client.get("/api/v1/auth/me", headers=h).json()["user"]["email"] == "ada@x.io"
    keys = client.get("/api/v1/auth/passkeys", headers=h).json()
    assert len(keys) == 1 and keys[0]["name"] == "Laptop" and keys[0]["rp_id"] == "localhost" and keys[0]["last_used_at"]


def test_a_passkey_from_another_site_or_a_wrong_signature_is_refused(app, client, db):
    device = Authenticator()
    _setup(app, client, device)
    # signed for another origin (a phishing page relaying the challenge)
    o = client.post("/api/v1/auth/passkey/options", headers=WEB).json()
    cred = device.get(o["options"], "https://evil.example")
    assert client.post("/api/v1/auth/passkey", json={"flow": o["flow"], "credential": cred}, headers=WEB).status_code == 401
    # a stranger's key
    stranger = Authenticator()
    o = client.post("/api/v1/auth/passkey/options", headers=WEB).json()
    stranger.create({"rp": {"id": "localhost"}, "user": {"id": "eA"}, "challenge": "eA"}, ORIGIN)
    r = client.post("/api/v1/auth/passkey", json={"flow": o["flow"], "credential": stranger.get(o["options"], ORIGIN)}, headers=WEB)
    assert r.status_code == 401 and "isn't known" in r.json()["detail"]
    # without user verification (no fingerprint, face or PIN)
    o = client.post("/api/v1/auth/passkey/options", headers=WEB).json()
    r = client.post("/api/v1/auth/passkey", json={"flow": o["flow"], "credential": device.get(o["options"], ORIGIN, uv=False)}, headers=WEB)
    assert r.status_code == 401
    # a cloned key (its counter went backwards)
    assert _signin(client, device).status_code == 200
    cid = next(iter(device.keys))
    device.keys[cid]["count"] = 0
    assert _signin(client, device, cid).status_code == 401


def test_passkeys_only_on_https_or_localhost(app, client):
    o = client.post("/api/v1/auth/passkey/options", headers={"x-forwarded-host": "localhost:3000", "x-forwarded-proto": "http"})
    assert o.status_code == 200
    # a host Lens isn't served at falls back to FRONTEND_URL (http://localhost:3000), never the visitor's choice
    o = client.post("/api/v1/auth/passkey/options", headers={"x-forwarded-host": "evil.example", "x-forwarded-proto": "https"})
    assert o.status_code == 200 and o.json()["options"]["rpId"] == "localhost"
    # called directly at an IP address
    r = client.post("/api/v1/auth/passkey/options")
    assert r.status_code == 400 and "IP address" in r.json()["detail"]
    with pytest.raises(ValueError, match="https"):
        passkeys.site("http://nas.local:3000")
    assert passkeys.site("https://lens.example.com") == ("https://lens.example.com", "lens.example.com")


def test_adding_renaming_and_removing_passkeys(app, client, db):
    laptop, phone = Authenticator(), Authenticator()
    h = _session(client, _setup(app, client, laptop))
    o = client.post("/api/v1/auth/passkeys/options", headers={**h, **WEB}).json()
    # the passkeys it has already are excluded, so the same device isn't added twice
    assert len(o["options"]["excludeCredentials"]) == 1
    r = client.post(
        "/api/v1/auth/passkeys", json={"flow": o["flow"], "credential": phone.create(o["options"], ORIGIN), "name": "Phone"}, headers=h
    )
    assert r.status_code == 200 and r.json()["name"] == "Phone"
    keys = client.get("/api/v1/auth/passkeys", headers=h).json()
    keys.sort(key=lambda k: k["name"])
    assert [k["name"] for k in keys] == ["Laptop", "Phone"]
    assert _signin(client, phone).status_code == 200
    assert client.patch(f"/api/v1/auth/passkeys/{keys[1]['id']}", json={"name": "Pixel"}, headers=h).status_code == 200
    assert client.delete(f"/api/v1/auth/passkeys/{keys[0]['id']}", headers=h).status_code == 200
    # not the last one: there'd be no way in
    last = client.delete(f"/api/v1/auth/passkeys/{keys[1]['id']}", headers=h)
    assert last.status_code == 400 and "last passkey" in last.json()["detail"]
    assert _signin(client, laptop).status_code == 401
    log = db.values("SELECT VALUE action FROM audit_log")
    assert "passkey.add" in log and "passkey.remove" in log


def test_api_tokens_cant_add_passkeys(app, client, db):
    h = _session(client, _setup(app, client, Authenticator()))
    raw = client.post("/api/v1/tokens", json={"name": "t", "scope": "write"}, headers=h).json()["token"]
    assert client.post("/api/v1/auth/passkeys/options", headers={"Authorization": f"Bearer {raw}", **WEB}).status_code == 403


def test_sign_in_links_for_new_people(app, client, db):
    admin = _session(client, _setup(app, client, Authenticator()))
    settings.save(db, app.state.archive.base, "auth", {"passwords": False})
    # passwords are off: a new person has none, and gets a link
    assert client.post("/api/v1/users", json={"email": "ed@x.io", "password": "editor password 1"}, headers=admin).status_code == 400
    uid = client.post("/api/v1/users", json={"email": "ed@x.io", "name": "Ed"}, headers=admin).json()["id"]
    link = client.post(f"/api/v1/users/{uid}/signin-link", headers={**admin, **WEB})
    assert link.status_code == 200, link.text
    url = link.json()["url"]
    assert url.startswith("http://localhost:3000/signin-link#")
    token = url.split("#", 1)[1]
    assert client.post("/api/v1/auth/signin-link/info", json={"token": token}).json() == {"email": "ed@x.io", "name": "Ed"}
    device = Authenticator()
    o = client.post("/api/v1/auth/signin-link/options", json={"token": token}, headers=WEB).json()
    r = client.post(
        "/api/v1/auth/signin-link",
        json={"token": token, "flow": o["flow"], "credential": device.create(o["options"], ORIGIN), "name": "Ed's phone"},
        headers=WEB,
    )
    assert r.status_code == 200, r.text
    h = _session(client, r.json()["ticket"])
    assert client.get("/api/v1/auth/me", headers=h).json()["user"]["email"] == "ed@x.io"
    # used up
    assert client.post("/api/v1/auth/signin-link/info", json={"token": token}).status_code == 404
    assert _signin(client, device).status_code == 200
    people = {p["email"]: p for p in client.get("/api/v1/users", headers=admin).json()}
    assert people["ed@x.io"]["passkeys"] == 1 and people["ed@x.io"]["password"] is False


def test_a_lost_device(app, client, db):
    """An admin removes someone's passkeys (they're signed out everywhere) and sends a new link."""
    admin = _session(client, _setup(app, client, Authenticator()))
    uid = client.post("/api/v1/users", json={"email": "ed@x.io"}, headers=admin).json()["id"]
    token = client.post(f"/api/v1/users/{uid}/signin-link", headers=admin).json()["url"].split("#")[1]
    # a second link replaces the first
    token2 = client.post(f"/api/v1/users/{uid}/signin-link", headers=admin).json()["url"].split("#")[1]
    assert client.post("/api/v1/auth/signin-link/info", json={"token": token}).status_code == 404
    device = Authenticator()
    o = client.post("/api/v1/auth/signin-link/options", json={"token": token2}, headers=WEB).json()
    t = client.post(
        "/api/v1/auth/signin-link",
        json={"token": token2, "flow": o["flow"], "credential": device.create(o["options"], ORIGIN)},
        headers=WEB,
    )
    h = _session(client, t.json()["ticket"])
    assert client.delete(f"/api/v1/users/{uid}/passkeys", headers=admin).status_code == 200
    assert client.get("/api/v1/auth/me", headers=h).status_code == 401
    assert _signin(client, device).status_code == 401
    # disabling someone voids their link
    token3 = client.post(f"/api/v1/users/{uid}/signin-link", headers=admin).json()["url"].split("#")[1]
    client.patch(f"/api/v1/users/{uid}", json={"disabled": True}, headers=admin)
    assert client.post("/api/v1/auth/signin-link/info", json={"token": token3}).status_code == 404


def test_lost_passkey_email(app, client, db, caplog):
    _setup(app, client, Authenticator())
    with caplog.at_level("WARNING", logger="lens.email"):
        assert client.post("/api/v1/auth/signin-link/lost", json={"email": "ada@x.io"}).status_code == 200
        assert client.post("/api/v1/auth/signin-link/lost", json={"email": "nobody@x.io"}).status_code == 200
    assert "sign-in link for ada@x.io: http://localhost:3000/signin-link#" in caplog.text
    assert "nobody" not in caplog.text


def test_passwords_off(app, client, db):
    """Without passwords, password sign-in, changes and resets are refused; turning them off needs an admin's passkey."""
    make_user(db, "ada@x.io", "admin password 1", admin=True)
    h = login(client, "ada@x.io", "admin password 1")
    r = client.put("/api/v1/settings/auth", json={"passwords": False}, headers=h)
    assert r.status_code == 400 and "passkey" in r.json()["detail"]
    o = client.post("/api/v1/auth/passkeys/options", headers={**h, **WEB}).json()
    assert (
        client.post(
            "/api/v1/auth/passkeys", json={"flow": o["flow"], "credential": Authenticator().create(o["options"], ORIGIN)}, headers=h
        ).status_code
        == 200
    )
    assert client.put("/api/v1/settings/auth", json={"passwords": False}, headers=h).status_code == 200
    assert client.get("/api/v1/auth/status").json()["passwords"] is False
    r = client.post("/api/v1/auth/login", json={"email": "ada@x.io", "password": "admin password 1"})
    assert r.status_code == 403 and "passkey" in r.json()["detail"]
    assert (
        client.post("/api/v1/auth/password", json={"current_password": "admin password 1", "new_password": "x" * 12}, headers=h).status_code
        == 403
    )
    assert client.post("/api/v1/auth/password/forgot", json={"email": "ada@x.io"}).status_code == 403


def test_accounts_without_a_password_never_match_one(db):
    """An account made without a password can't be signed into with any password, even the one used for timing."""
    auth.create_account(db, "ed@x.io", None)
    assert auth.login(db, "ed@x.io", "x" * 16) is None
    assert auth.login(db, "ed@x.io", "") is None


def test_upgrades_keep_passwords(folder):
    """An install from before passkeys keeps password sign-in on; a fresh one starts without."""
    from tests.conftest import make_cfg

    cfg = make_cfg(folder, auth={"passwords": False})
    db = store.connect(cfg)
    try:
        assert settings.keep_passwords(db, cfg) is False  # nobody has a password
        auth.create_account(db, "ada@x.io", "admin password 1", admin=True)
        assert settings.keep_passwords(db, cfg) is True
        assert settings.effective(db, cfg)["auth"]["passwords"] is True
        settings.save(db, cfg, "auth", {"passwords": True})
        assert settings.keep_passwords(db, cfg) is False  # saved already: never again
    finally:
        db.close()
