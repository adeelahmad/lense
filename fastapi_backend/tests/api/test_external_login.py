"""Signing in with an outside account (Google, GitHub, Microsoft, OpenID Connect) against a fake provider."""

from __future__ import annotations

import urllib.parse

import pytest

from app.domain import external_login, passkeys
from tests.fake_authenticator import Authenticator
from tests.fake_oidc import Provider
from tests.helpers import login, make_user

WEB = {"x-forwarded-host": "localhost:3000", "x-forwarded-proto": "http"}
ORIGIN = "http://localhost:3000"
ADA = {"sub": "ada-1", "email": "ada@x.io", "email_verified": True, "name": "Ada"}


@pytest.fixture
def idp():
    p = Provider()
    yield p
    p.close()


@pytest.fixture
def admin(app, client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    return login(client, "root@x.io", "root password 1")


def _add(client, admin, idp, **extra):
    body = {"kind": "oidc", "label": "Acme SSO", "issuer": idp.base, "client_id": "lens", "client_secret": "s3cret", **extra}
    r = client.post("/api/v1/auth/providers", json=body, headers=admin)
    assert r.status_code == 200, r.text
    return r.json()


def _fragment(r):
    assert r.status_code == 303, r.text
    url = urllib.parse.urlsplit(r.headers["location"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == ORIGIN + "/external-signin"
    return dict(urllib.parse.parse_qsl(url.fragment))


def _sign_in(client, idp, key, profile, next_path="/", headers=None, start="start"):
    r = client.post(f"/api/v1/auth/external/{key}/{start}", json={"next": next_path}, headers={**WEB, **(headers or {})})
    assert r.status_code == 200, r.text
    url = r.json()["url"]
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    assert q["redirect_uri"] == f"{ORIGIN}/api/v1/auth/external/{key}/callback" and q["code_challenge_method"] == "S256"
    code, state = idp.issue(url, profile)
    return _fragment(
        client.get(f"/api/v1/auth/external/{key}/callback", params={"code": code, "state": state}, headers=WEB, follow_redirects=False)
    )


def _session(client, ticket):
    r = client.post("/api/v1/auth/ticket", json={"ticket": ticket})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_admins_add_a_provider_and_the_secret_stays_sealed(app, client, db, admin, idp):
    p = _add(client, admin, idp)
    assert p["key"] == "acme-sso" and p["secret_set"] and "client_secret" not in p
    assert p["callback_path"] == "/api/v1/auth/external/acme-sso/callback"
    raw = db.one("SELECT client_secret FROM login_provider:`acme-sso`")["client_secret"]
    assert raw.startswith("v1.") and "s3cret" not in raw
    # everyone sees its name on the sign-in page; only admins its settings
    assert client.get("/api/v1/auth/external").json() == [{"key": "acme-sso", "kind": "oidc", "label": "Acme SSO"}]
    assert client.get("/api/v1/auth/providers").status_code == 401
    # changing it without a secret keeps the secret
    r = client.patch("/api/v1/auth/providers/acme-sso", json={"label": "Acme"}, headers=admin)
    assert r.status_code == 200 and r.json()["label"] == "Acme" and r.json()["secret_set"]
    # one Google, but several OpenID Connect providers
    g = {"kind": "google", "client_id": "g", "client_secret": "x"}
    assert client.post("/api/v1/auth/providers", json=g, headers=admin).json()["key"] == "google"
    assert client.post("/api/v1/auth/providers", json=g, headers=admin).status_code == 400
    assert _add(client, admin, idp, label="Acme SSO")["key"] == "acme-sso-2"
    # issuers are https:// (or this machine)
    bad = client.post("/api/v1/auth/providers", json={**g, "kind": "oidc", "issuer": "http://idp.example"}, headers=admin)
    assert bad.status_code == 400 and "https://" in bad.json()["detail"]
    assert client.post("/api/v1/auth/providers", json={"kind": "oidc", "issuer": idp.base}, headers=admin).status_code == 400
    assert client.delete("/api/v1/auth/providers/acme-sso-2", headers=admin).status_code == 200
    assert "login_provider.add" in db.values("SELECT VALUE action FROM audit_log")


def test_signing_in_connects_the_account_with_the_same_verified_email(app, client, db, admin, idp):
    _add(client, admin, idp)
    make_user(db, "ada@x.io", None)
    got = _sign_in(client, idp, "acme-sso", ADA, next_path="/recordings")
    assert got["next"] == "/recordings"
    h = _session(client, got["ticket"])
    me = client.get("/api/v1/auth/me", headers=h).json()
    assert me["user"]["email"] == "ada@x.io"
    ids = client.get("/api/v1/auth/identities", headers=h).json()
    assert [(i["provider"], i["label"], i["email"]) for i in ids] == [("acme-sso", "Acme SSO", "ada@x.io")]
    # the next time it's found by the provider's subject, even if the email there changed
    got = _sign_in(client, idp, "acme-sso", {**ADA, "email": "ada@new.io"})
    assert client.get("/api/v1/auth/me", headers=_session(client, got["ticket"])).json()["user"]["email"] == "ada@x.io"
    log = db.rows("SELECT action, detail FROM audit_log WHERE action = 'login'")
    assert any("external:acme-sso" in (r.get("detail") or []) for r in log)


def test_who_gets_in(app, client, db, admin, idp):
    _add(client, admin, idp)
    # nobody has that email, and sign-up is off
    got = _sign_in(client, idp, "acme-sso", {"sub": "x", "email": "eve@x.io", "email_verified": True})
    assert "no Lens account uses eve@x.io" in got["error"] and got["next"] == "/login"
    # an email the provider doesn't vouch for never matches an account
    make_user(db, "bob@x.io", None)
    got = _sign_in(client, idp, "acme-sso", {"sub": "y", "email": "bob@x.io", "email_verified": False})
    assert "didn't confirm your email" in got["error"]
    # sign-up, only for some domains
    client.patch("/api/v1/auth/providers/acme-sso", json={"signup": True, "domains": ["@Acme.com"]}, headers=admin)
    assert client.get("/api/v1/auth/providers", headers=admin).json()[0]["domains"] == ["acme.com"]
    assert "no Lens account" in _sign_in(client, idp, "acme-sso", {"sub": "z", "email": "zed@other.io", "email_verified": True})["error"]
    got = _sign_in(client, idp, "acme-sso", {"sub": "c", "email": "cy@acme.com", "email_verified": True, "name": "Cy"})
    me = client.get("/api/v1/auth/me", headers=_session(client, got["ticket"])).json()
    assert me["user"]["email"] == "cy@acme.com" and me["user"]["name"] == "Cy" and not me["user"]["admin"]
    assert not passkeys.has_password(db, me["user"]["id"])
    # disabled accounts and providers that are turned off
    uid = me["user"]["id"]
    client.patch(f"/api/v1/users/{uid}", json={"disabled": True}, headers=admin)
    assert "disabled" in _sign_in(client, idp, "acme-sso", {"sub": "c", "email": "cy@acme.com", "email_verified": True})["error"]
    client.patch("/api/v1/auth/providers/acme-sso", json={"enabled": False}, headers=admin)
    assert client.get("/api/v1/auth/external").json() == []
    r = client.post("/api/v1/auth/external/acme-sso/start", json={}, headers=WEB)
    assert r.status_code == 400 and "turned off" in r.json()["detail"]


def test_the_round_trip_is_tied_to_this_browser_and_used_once(app, client, new_client, admin, idp, db):
    _add(client, admin, idp)
    make_user(db, "ada@x.io", None)
    r = client.post("/api/v1/auth/external/acme-sso/start", json={}, headers=WEB)
    url = r.json()["url"]
    code, state = idp.issue(url, ADA)
    # someone else's browser (a link sent to a victim) can't finish it: no cookie from the start
    other = new_client()
    got = _fragment(
        other.get("/api/v1/auth/external/acme-sso/callback", params={"code": code, "state": state}, headers=WEB, follow_redirects=False)
    )
    assert "another browser" in got["error"]
    # the flow is used up even so
    got = _fragment(
        client.get("/api/v1/auth/external/acme-sso/callback", params={"code": code, "state": state}, headers=WEB, follow_redirects=False)
    )
    assert "took too long or was already used" in got["error"]
    # cancelled at the provider
    r = client.post("/api/v1/auth/external/acme-sso/start", json={}, headers=WEB)
    state = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(r.json()["url"]).query))["state"]
    got = _fragment(
        client.get(
            "/api/v1/auth/external/acme-sso/callback",
            params={"error": "access_denied", "state": state},
            headers=WEB,
            follow_redirects=False,
        )
    )
    assert "you cancelled" in got["error"]
    # where to go next is always a page of this site
    got = _sign_in(client, idp, "acme-sso", ADA, next_path="//evil.example/x")
    assert got["next"] == "/"


def test_a_wrong_client_secret_says_so(app, client, admin, idp, db):
    _add(client, admin, idp, client_secret="wrong")
    make_user(db, "ada@x.io", None)
    got = _sign_in(client, idp, "acme-sso", ADA)
    assert "bad client credentials" in got["error"] and "client id and secret" in got["error"]


def test_connecting_and_disconnecting_from_your_profile(app, client, db, admin, idp):
    _add(client, admin, idp)
    make_user(db, "bob@x.io", "bob password 12")
    bob = login(client, "bob@x.io", "bob password 12")
    # the provider doesn't vouch for the email, so Bob connects it while signed in
    unverified = {"sub": "b", "email": "bob@x.io", "email_verified": False}
    got = _sign_in(client, idp, "acme-sso", unverified, headers=bob, start="connect")
    assert got == {"connected": "Acme SSO", "next": "/account"}
    assert (
        client.get("/api/v1/auth/me", headers=_session(client, _sign_in(client, idp, "acme-sso", unverified)["ticket"])).status_code == 200
    )
    # an outside account belongs to one Lens account
    make_user(db, "cy@x.io", "cy password 123")
    cy = login(client, "cy@x.io", "cy password 123")
    got = _sign_in(client, idp, "acme-sso", unverified, headers=cy, start="connect")
    assert "already connected to another Lens account" in got["error"] and got["next"] == "/account"
    # with a password, the outside account can go
    iid = client.get("/api/v1/auth/identities", headers=bob).json()[0]["id"]
    assert client.delete(f"/api/v1/auth/identities/{iid}", headers=bob).status_code == 200
    assert client.get("/api/v1/auth/identities", headers=bob).json() == []
    assert "external.disconnect" in db.values("SELECT VALUE action FROM audit_log")


def test_the_last_way_in_stays(app, client, db, admin, idp):
    """No password and no passkey: the outside account is the only way in, so it can't be disconnected; with one, the
    last passkey can go."""
    _add(client, admin, idp)
    make_user(db, "ada@x.io", None)
    h = _session(client, _sign_in(client, idp, "acme-sso", ADA)["ticket"])
    iid = client.get("/api/v1/auth/identities", headers=h).json()[0]["id"]
    r = client.delete(f"/api/v1/auth/identities/{iid}", headers=h)
    assert r.status_code == 400 and "only way to sign in" in r.json()["detail"]
    device = Authenticator()
    o = client.post("/api/v1/auth/passkeys/options", headers={**h, **WEB}).json()
    client.post("/api/v1/auth/passkeys", json={"flow": o["flow"], "credential": device.create(o["options"], ORIGIN)}, headers=h)
    pid = client.get("/api/v1/auth/passkeys", headers=h).json()[0]["id"]
    assert client.delete(f"/api/v1/auth/passkeys/{pid}", headers={**h, **WEB}).status_code == 200


def test_github_lists_verified_emails(app, client, db, admin, idp, monkeypatch):
    monkeypatch.setitem(
        external_login.ENDPOINTS,
        "github",
        {
            **external_login.ENDPOINTS["github"],
            "token": idp.base + "/token",
            "userinfo": idp.base + "/user",
            "emails": idp.base + "/user/emails",
        },
    )
    client.post("/api/v1/auth/providers", json={"kind": "github", "client_id": "lens", "client_secret": "s3cret"}, headers=admin)
    make_user(db, "ada@x.io", None)
    idp.emails = [{"email": "ada@old.io", "primary": True, "verified": False}, {"email": "ada@x.io", "primary": False, "verified": True}]
    got = _sign_in(client, idp, "github", {"id": 42, "login": "ada"})
    assert client.get("/api/v1/auth/me", headers=_session(client, got["ticket"])).json()["user"]["email"] == "ada@x.io"
    idp.emails = [{"email": "eve@x.io", "primary": True, "verified": False}]
    assert "didn't confirm your email" in _sign_in(client, idp, "github", {"id": 7, "login": "eve"})["error"]


def test_microsoft_vouches_for_emails_only_in_one_organization(app, client, db, admin, idp, monkeypatch):
    ep = {**external_login.ENDPOINTS["microsoft"], "token": idp.base + "/token", "userinfo": idp.base + "/userinfo"}
    monkeypatch.setitem(external_login.ENDPOINTS, "microsoft", ep)
    client.post("/api/v1/auth/providers", json={"kind": "microsoft", "client_id": "lens", "client_secret": "s3cret"}, headers=admin)
    make_user(db, "ada@x.io", None)
    # "common": anyone can make a Microsoft account claiming any email
    assert "didn't confirm your email" in _sign_in(client, idp, "microsoft", {**ADA, "email_verified": None})["error"]
    client.patch("/api/v1/auth/providers/microsoft", json={"tenant": "contoso.onmicrosoft.com"}, headers=admin)
    got = _sign_in(client, idp, "microsoft", {**ADA, "email_verified": None})
    assert "ticket" in got
