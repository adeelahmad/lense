"""OAuth for API and MCP clients (docs/authentication.md#oauth): apps register, people consent, tokens act as them
with their roles, refresh tokens rotate, and access is taken away at once."""

from __future__ import annotations

import base64
import hashlib
import secrets
import urllib.parse

from fastapi.testclient import TestClient

from app.domain import auth, oauth, store
from tests.helpers import login, make_user, seed

BACK = "https://app.example/callback"
O = "/api/v1/oauth"


def _pkce():
    verifier = secrets.token_urlsafe(48)
    return verifier, base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


def _register(client, **over):
    r = client.post(f"{O}/register", json={"client_name": "Harbour Notes", "redirect_uris": [BACK], **over})
    assert r.status_code == 201, r.text
    return r.json()


def _ask(app, challenge, **over):
    return {"client_id": app["client_id"], "redirect_uri": BACK, "code_challenge": challenge, "code_challenge_method": "S256", **over}


def _code(client, h, app, challenge, **over):
    r = client.post(f"{O}/authorize", headers=h, json={**_ask(app, challenge), "approve": True, "state": "xyz", **over})
    assert r.status_code == 200, r.text
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(r.json()["redirect_to"]).query))
    assert q["state"] == "xyz"
    return q["code"]


def _tokens(client, app, code, verifier, **over):
    form = {
        "grant_type": "authorization_code",
        "client_id": app["client_id"],
        "code": code,
        "redirect_uri": BACK,
        "code_verifier": verifier,
    }
    return client.post(f"{O}/token", data={**form, **over})


def _grant(client, h, app=None, **over):
    """An app with access: (app, tokens)."""
    app = app or _register(client)
    verifier, challenge = _pkce()
    r = _tokens(client, app, _code(client, h, app, challenge, **over), verifier)
    assert r.status_code == 200, r.text
    return app, r.json()


def _bearer(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def test_discovery_says_where_everything_is(client, app):
    meta = client.get("/.well-known/oauth-authorization-server").json()
    assert meta["issuer"] == "http://127.0.0.1"
    assert meta["token_endpoint"] == "http://127.0.0.1/api/v1/oauth/token"
    assert meta["registration_endpoint"] == "http://127.0.0.1/api/v1/oauth/register"
    assert meta["authorization_endpoint"] == "http://localhost:3000/oauth/authorize"  # the web app (FRONTEND_URL)
    assert meta["code_challenge_methods_supported"] == ["S256"] and meta["response_types_supported"] == ["code"]
    # through the web app, everything is on its address: the one it reports when it's a trusted proxy
    via = {"x-forwarded-host": "lens.example.org", "x-forwarded-proto": "https"}
    web = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))
    r = web.get("/.well-known/oauth-authorization-server", headers=via)
    meta = r.json()
    assert r.headers["cache-control"] == "no-store"
    assert meta["issuer"] == "https://lens.example.org"
    assert meta["authorization_endpoint"] == "https://lens.example.org/oauth/authorize"
    assert meta["revocation_endpoint"] == "https://lens.example.org/api/v1/oauth/revoke"
    res = web.get("/.well-known/oauth-protected-resource/mcp", headers=via).json()
    assert res == {
        "resource": "https://lens.example.org/mcp",
        "authorization_servers": ["https://lens.example.org"],
        "bearer_methods_supported": ["header"],
        "scopes_supported": ["read", "write"],
        "resource_name": "Lens",
    }
    assert web.get("/.well-known/oauth-protected-resource/a b<c", headers=via).status_code == 404
    assert client.get("/.well-known/oauth-protected-resource").json()["resource"] == "http://127.0.0.1"
    # a forwarded host that isn't a host name, or one from an address that isn't a trusted proxy, names nothing:
    # the web app is then where FRONTEND_URL says
    bad = {"x-forwarded-host": "evil.example/x?y", "x-forwarded-proto": "https"}
    assert web.get("/.well-known/oauth-authorization-server", headers=bad).json()["issuer"] == "http://localhost:3000"
    far = TestClient(app, base_url="http://127.0.0.1", client=("203.0.113.9", 50000))
    meta = far.get("/.well-known/oauth-authorization-server", headers={"x-forwarded-host": "evil.example"}).json()
    assert meta["issuer"] == "http://localhost:3000" and meta["token_endpoint"] == "http://localhost:3000/api/v1/oauth/token"


def test_an_app_gets_access_and_acts_with_the_persons_roles(client, db, cfg, folder):
    ids = seed(db, cfg, folder)
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    hv, he, hr = (
        login(client, "vi@x.io", "viewer password 1"),
        login(client, "ed@x.io", "editor password 1"),
        login(client, "root@x.io", "root password 1"),
    )
    app = _register(client)
    assert app["client_id"].startswith("lc_") and "client_secret" not in app and app["token_endpoint_auth_method"] == "none"
    verifier, challenge = _pkce()

    # the consent page's question: signed in, as a person
    assert client.get(f"{O}/authorize", params=_ask(app, challenge)).status_code == 401
    info = client.get(f"{O}/authorize", headers=hv, params={**_ask(app, challenge), "scope": "read write offline_access"}).json()
    assert info == {
        "client": {"id": app["client_id"], "name": "Harbour Notes", "uri": None},
        "redirect_uri": BACK,
        "scope": "read write",
        "granted": None,
    }
    assert client.get(f"{O}/authorize", headers=hv, params=_ask(app, challenge)).json()["scope"] == "read"  # nothing asked: read

    # a viewer's app reads what the viewer reads, and nothing else
    _, tv = _grant(client, hv, app, scope="read write")
    assert tv["token_type"] == "Bearer" and tv["scope"] == "read write" and tv["expires_in"] == 3600
    assert tv["access_token"].startswith("lo_") and tv["refresh_token"].startswith("lr_")
    me = client.get("/api/v1/auth/me", headers=_bearer(tv)).json()
    assert (me["user"]["email"], me["roles"], me["via"], me["scope"]) == ("vi@x.io", {"pods": "viewer"}, "oauth", "write")
    assert client.get(f"/api/v1/resources/{ids[0]}", headers=_bearer(tv)).status_code == 200
    assert client.get(f"/api/v1/resources/{ids[2]}", headers=_bearer(tv)).status_code == 404  # calls: no role
    assert client.patch(f"/api/v1/resources/{ids[0]}", headers=_bearer(tv), json={"title": "Mine"}).status_code == 403  # a viewer
    assert client.get("/api/v1/audit", headers=_bearer(tv)).status_code == 403
    # an admin's app has the admin's roles in every namespace, and none of the administration
    _, ta = _grant(client, hr, app, scope="read write")
    assert client.get(f"/api/v1/resources/{ids[2]}", headers=_bearer(ta)).status_code == 200
    for r in (
        client.get("/api/v1/audit", headers=_bearer(ta)),
        client.get("/api/v1/users", headers=_bearer(ta)),
        client.put("/api/v1/settings/tokens", headers=_bearer(ta), json={"max_days": 3650}),
        client.post("/api/v1/users", headers=_bearer(ta), json={"email": "new@x.io", "password": "a new password 1", "admin": True}),
    ):
        assert (r.status_code, "apps given access" in r.json()["detail"]) == (403, True)

    # an editor's app edits when it was given write, and only reads when it was given read
    _, te = _grant(client, he, app, scope="read write")
    assert client.patch(f"/api/v1/resources/{ids[0]}", headers=_bearer(te), json={"title": "Renamed by an app"}).status_code == 200
    _, ro = _grant(client, he, app, scope="read write", grant="read")  # asked for both, given read
    assert ro["scope"] == "read"
    assert client.get("/api/v1/auth/me", headers=_bearer(te)).status_code == 401  # the new grant replaced the first
    r = client.patch(f"/api/v1/resources/{ids[0]}", headers=_bearer(ro), json={"title": "No"})
    assert (r.status_code, r.json()["detail"]) == (403, "this API token is read-only")
    _, more = _grant(client, he, app, scope="read", grant="read write")  # asked for read: never given more
    assert more["scope"] == "read"

    # an app is no person: no keys, no password, no consent on its own
    assert client.post("/api/v1/tokens", headers=_bearer(tv), json={"name": "k"}).status_code == 403
    assert (
        client.post("/api/v1/auth/password", headers=_bearer(tv), json={"current_password": "x", "new_password": "y" * 12}).status_code
        == 403
    )
    assert client.post(f"{O}/authorize", headers=_bearer(tv), json={**_ask(app, challenge), "approve": True}).status_code == 403
    assert client.get(f"{O}/authorize", headers=_bearer(tv), params=_ask(app, challenge)).status_code == 403
    # a refresh token opens nothing by itself
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {tv['refresh_token']}"}).status_code == 401

    # the person sees the app and takes its access away, at once
    mine = client.get(f"{O}/grants", headers=hv).json()
    assert [(g["name"], g["scope"], g["client"]) for g in mine] == [("Harbour Notes", "read write", app["client_id"])]
    assert mine[0]["last_used_at"] and mine[0]["expires_at"] > mine[0]["created_at"]
    assert client.get(f"{O}/authorize", headers=hv, params=_ask(app, challenge)).json()["granted"] == "read write"
    assert client.delete(f"{O}/grants/{mine[0]['id']}", headers=he).status_code == 404  # someone else's
    assert client.delete(f"{O}/grants/{mine[0]['id']}", headers=_bearer(tv)).status_code == 403
    assert client.delete(f"{O}/grants/{mine[0]['id']}", headers=hv).status_code == 200
    assert client.get("/api/v1/auth/me", headers=_bearer(tv)).status_code == 401
    r = client.post(f"{O}/token", data={"grant_type": "refresh_token", "client_id": app["client_id"], "refresh_token": tv["refresh_token"]})
    assert (r.status_code, r.json()["error"]) == (400, "invalid_grant")
    assert client.get(f"{O}/grants", headers=hv).json() == []

    audit = sorted(
        (a["action"], a.get("email"), (a.get("detail") or {}).get("scope")) for a in client.get("/api/v1/audit", headers=hr).json()
    )
    assert ("oauth.client.register", None, None) in audit
    assert ("oauth.grant", "vi@x.io", "read write") in audit and ("oauth.grant", "ed@x.io", "read") in audit
    assert ("oauth.revoke", "vi@x.io", None) in audit


def test_refresh_tokens_rotate_and_a_copied_one_ends_the_grant(client, db):
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    h = login(client, "vi@x.io", "viewer password 1")
    app, first = _grant(client, h)
    renew = lambda t: client.post(f"{O}/token", data={"grant_type": "refresh_token", "client_id": app["client_id"], "refresh_token": t})  # noqa: E731
    r = renew(first["refresh_token"])
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    second = r.json()
    assert second["refresh_token"] != first["refresh_token"] and second["access_token"] != first["access_token"]
    assert client.get("/api/v1/auth/me", headers=_bearer(second)).status_code == 200
    # two requests at once may both carry the old one: within a minute it still works
    assert renew(first["refresh_token"]).status_code == 200
    # another app can't use it, and an access token isn't a refresh token
    other = _register(client, client_name="Other")
    r = client.post(
        f"{O}/token", data={"grant_type": "refresh_token", "client_id": other["client_id"], "refresh_token": second["refresh_token"]}
    )
    assert r.json()["error"] == "invalid_grant"
    assert renew(second["access_token"]).json()["error"] == "invalid_grant"
    # later, the old one coming back means it was copied: the grant ends, the newest tokens with it
    db.q("UPDATE $r SET rotated_at = $t", r=store.R("oauth_token", auth.sha(first["refresh_token"])), t=oauth._later(-120))
    assert renew(first["refresh_token"]).json()["error"] == "invalid_grant"
    assert client.get("/api/v1/auth/me", headers=_bearer(second)).status_code == 401
    assert renew(second["refresh_token"]).status_code == 400
    assert client.get(f"{O}/grants", headers=h).json() == []
    assert not db.values("SELECT VALUE id FROM oauth_token")


def test_tokens_end_when_they_expire_or_the_account_is_disabled(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    uid = make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    hr, h = login(client, "root@x.io", "root password 1"), login(client, "vi@x.io", "viewer password 1")
    # admins set how long they last
    put = lambda body: client.put("/api/v1/settings/tokens", headers=hr, json=body)  # noqa: E731
    assert put({"oauth_access_minutes": 4}).status_code == 400
    assert put({"oauth_access_minutes": 1441}).status_code == 400
    assert put({"oauth_access_minutes": "60"}).status_code == 400
    assert put({"oauth_refresh_days": 0}).status_code == 400
    assert put({"oauth_refresh_days": 366}).status_code == 400  # more than the most a key may last
    assert put({"oauth_access_minutes": 10, "oauth_refresh_days": 7}).status_code == 200
    app, t = _grant(client, h)
    assert t["expires_in"] == 600
    g = client.get(f"{O}/grants", headers=h).json()[0]
    assert 6 <= (store_time(g["expires_at"]) - store_time(g["created_at"])).days <= 7
    # an expired access token stops; its refresh token still renews
    db.q("UPDATE $r SET expires_at = $t", r=store.R("oauth_token", auth.sha(t["access_token"])), t=oauth._later(-5))
    assert client.get("/api/v1/auth/me", headers=_bearer(t)).status_code == 401
    r = client.post(f"{O}/token", data={"grant_type": "refresh_token", "client_id": app["client_id"], "refresh_token": t["refresh_token"]})
    assert r.status_code == 200
    t2 = r.json()
    assert not db.values("SELECT VALUE id FROM $r", r=store.R("oauth_token", auth.sha(t["access_token"])))  # swept
    # an expired refresh token doesn't
    db.q("UPDATE $r SET expires_at = $t", r=store.R("oauth_token", auth.sha(t2["refresh_token"])), t=oauth._later(-5))
    r = client.post(f"{O}/token", data={"grant_type": "refresh_token", "client_id": app["client_id"], "refresh_token": t2["refresh_token"]})
    assert r.json()["error"] == "invalid_grant"
    # a disabled account's apps stop too
    assert client.get("/api/v1/auth/me", headers=_bearer(t2)).status_code == 200
    assert client.patch(f"/api/v1/users/{uid}", headers=hr, json={"disabled": True}).status_code == 200
    assert client.get("/api/v1/auth/me", headers=_bearer(t2)).status_code == 401


def store_time(s):
    import datetime as dt

    return dt.datetime.fromisoformat(s)


def test_codes_work_once_with_the_right_verifier(client, db):
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    h = login(client, "vi@x.io", "viewer password 1")
    app = _register(client)
    verifier, challenge = _pkce()
    err = lambda r: (r.status_code, r.json()["error"])  # noqa: E731

    assert err(_tokens(client, app, _code(client, h, app, challenge), "x" * 43)) == (400, "invalid_grant")  # the wrong verifier
    assert err(_tokens(client, app, _code(client, h, app, challenge), "short")) == (400, "invalid_grant")
    assert err(_tokens(client, app, _code(client, h, app, challenge), verifier, redirect_uri=BACK + "/other")) == (400, "invalid_grant")
    assert err(_tokens(client, app, "not-a-code", verifier)) == (400, "invalid_grant")
    assert err(_tokens(client, app, _code(client, h, app, challenge), verifier, client_id="lc_nobody")) == (401, "invalid_client")
    other = _register(client, client_name="Other")
    assert err(_tokens(client, app, _code(client, h, app, challenge), verifier, client_id=other["client_id"])) == (400, "invalid_grant")
    assert err(_tokens(client, app, _code(client, h, app, challenge), verifier, grant_type="password")) == (400, "unsupported_grant_type")
    assert _tokens(client, app, "", verifier).status_code == 400
    assert client.post(f"{O}/token", data={"client_id": app["client_id"]}).status_code == 422  # no grant_type

    # each failed try used its code up: none of them left a grant
    assert client.get(f"{O}/grants", headers=h).json() == []
    code = _code(client, h, app, challenge)
    assert _tokens(client, app, code, verifier).status_code == 200
    assert err(_tokens(client, app, code, verifier)) == (400, "invalid_grant")  # once
    # and only for five minutes
    late = _code(client, h, app, challenge)
    db.q("UPDATE $r SET expires_at = $t", r=store.R("oauth_code", auth.sha(late)), t=oauth._later(-1))
    assert err(_tokens(client, app, late, verifier)) == (400, "invalid_grant")


def test_consent_refuses_requests_it_cant_answer(client, db):
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    h = login(client, "vi@x.io", "viewer password 1")
    app = _register(client)
    _, challenge = _pkce()
    ask = lambda **over: client.get(f"{O}/authorize", headers=h, params={**_ask(app, challenge), **over})  # noqa: E731
    assert ask().status_code == 200
    assert "isn't registered" in ask(client_id="lc_nobody").json()["detail"]
    assert "isn't one it registered" in ask(redirect_uri="https://evil.example/callback").json()["detail"]
    assert "authorization code" in ask(response_type="token").json()["detail"]
    assert "PKCE" in ask(code_challenge_method="plain").json()["detail"]
    assert "PKCE" in ask(code_challenge="short").json()["detail"]
    assert ask(code_challenge="").status_code == 400
    # the answer is checked the same way, and never sends anyone to an address the app didn't register
    r = client.post(
        f"{O}/authorize", headers=h, json={**_ask(app, challenge), "redirect_uri": "https://evil.example/callback", "approve": True}
    )
    assert r.status_code == 400
    assert client.post(f"{O}/authorize", headers=h, json={**_ask(app, challenge), "approve": True, "extra": 1}).status_code == 422
    assert client.post(f"{O}/authorize", json={**_ask(app, challenge), "approve": True}).status_code == 401
    # no: the app hears so, with its state, and gets no code
    r = client.post(f"{O}/authorize", headers=h, json={**_ask(app, challenge), "approve": False, "state": "s 1"})
    assert r.json() == {"redirect_to": BACK + "?error=access_denied&state=s+1"}
    assert not db.values("SELECT VALUE id FROM oauth_code")
    assert not [a for a in db.rows("SELECT action FROM audit_log") if a["action"] == "oauth.grant"]


def test_registering_apps(client, db):
    reg = lambda **body: client.post(f"{O}/register", json=body)  # noqa: E731
    bad = lambda *uris: reg(redirect_uris=list(uris)).json().get("error")  # noqa: E731
    assert bad("javascript:alert(1)") == "invalid_redirect_uri"
    assert bad("data:text/html,x") == "invalid_redirect_uri"
    assert bad("http://example.org/callback") == "invalid_redirect_uri"  # http only on this machine
    assert bad("https://app.example/cb#frag") == "invalid_redirect_uri"
    assert bad("https:///nohost") == "invalid_redirect_uri"
    assert bad("no scheme") == "invalid_redirect_uri"
    assert bad("https://user@app.example/cb") == "invalid_redirect_uri"
    assert bad("http://evil.example\\@localhost/cb") == "invalid_redirect_uri"
    assert bad("ms-msdt:/id") == "invalid_redirect_uri" and bad("intent://x#Intent;end") == "invalid_redirect_uri"
    assert bad() == "invalid_redirect_uri"
    assert bad(*[f"https://app.example/{n}" for n in range(11)]) == "invalid_redirect_uri"
    assert reg(client_name="X").status_code == 422
    assert reg(redirect_uris=[BACK], token_endpoint_auth_method="private_key_jwt").json()["error"] == "invalid_client_metadata"
    assert not db.values("SELECT VALUE id FROM oauth_client")

    # metadata Lens has no use for is ignored; names are tidied; apps on this machine and with their own scheme register
    r = reg(
        client_name="  Desk   App ",
        redirect_uris=["http://127.0.0.1:7777/cb", "cursor://anysphere.cursor/oauth/callback"],
        client_uri="https://desk.example",
        logo_uri="https://desk.example/logo.png",
        grant_types=["authorization_code", "refresh_token"],
        software_id="desk",
    )
    assert r.status_code == 201 and r.headers["cache-control"] == "no-store"
    desk = r.json()
    assert (desk["client_name"], desk["client_uri"], desk["client_secret_expires_at"]) == ("Desk App", "https://desk.example", 0)
    assert reg(redirect_uris=[BACK]).json()["client_name"] == "An app"
    assert reg(redirect_uris=[BACK], client_uri="javascript:alert(1)").json().get("client_uri") is None

    # on this machine an app listens on whichever port is free
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    h = login(client, "vi@x.io", "viewer password 1")
    _, challenge = _pkce()
    ask = lambda uri: client.get(f"{O}/authorize", headers=h, params={**_ask(desk, challenge), "redirect_uri": uri}).status_code  # noqa: E731
    assert ask("http://127.0.0.1:51234/cb") == 200
    assert ask("http://127.0.0.1:51234/other") == 400
    assert ask("http://localhost:7777/cb") == 400
    assert ask("http://evil.example\\@127.0.0.1:7777/cb") == 400  # browsers read the backslash as a slash
    assert ask("http://evil.example@127.0.0.1:7777/cb") == 400
    assert ask("cursor://anysphere.cursor/oauth/callback") == 200
    assert ask("cursor://anysphere.cursor/oauth/elsewhere") == 400

    # apps nobody gave access to are forgotten after a week; ones in use stay
    used, _ = _grant(client, h)
    db.q("UPDATE oauth_client SET created_at = $t", t=oauth._later(-8 * 86400))
    assert reg(redirect_uris=[BACK]).status_code == 201
    left = db.values("SELECT VALUE record::id(id) FROM oauth_client WHERE created_at < $t", t=oauth._later(-86400))
    assert left == [used["client_id"]]

    # registering is open, so it's throttled per address
    auth._FAILS.clear()
    codes = [reg(redirect_uris=[BACK]).status_code for _ in range(9)]
    assert codes == [201] * 8 + [429]


def test_apps_with_a_secret_have_to_show_it(client, db):
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    h = login(client, "vi@x.io", "viewer password 1")
    app = _register(client, token_endpoint_auth_method="client_secret_post")
    assert app["client_secret"].startswith("ls_")
    assert not db.values("SELECT VALUE id FROM oauth_client WHERE secret = $s", s=app["client_secret"])  # only its hash is kept
    verifier, challenge = _pkce()
    r = _tokens(client, app, _code(client, h, app, challenge), verifier)
    assert (r.status_code, r.json()["error"], r.headers["www-authenticate"]) == (401, "invalid_client", "Basic")
    assert _tokens(client, app, _code(client, h, app, challenge), verifier, client_secret="ls_wrong").status_code == 401
    t = _tokens(client, app, _code(client, h, app, challenge), verifier, client_secret=app["client_secret"]).json()
    assert t["scope"] == "read"
    # or in the Authorization header (client_secret_basic)
    basic = lambda secret: {"Authorization": "Basic " + base64.b64encode(f"{app['client_id']}:{secret}".encode()).decode()}  # noqa: E731
    renew = {"grant_type": "refresh_token", "refresh_token": t["refresh_token"]}
    assert client.post(f"{O}/token", data=renew, headers=basic("ls_wrong")).status_code == 401
    assert client.post(f"{O}/token", data=renew, headers={"Authorization": "Basic !!!"}).status_code == 401
    t = client.post(f"{O}/token", data=renew, headers=basic(app["client_secret"])).json()

    # the app hands its token back: the grant ends; unknown tokens answer the same
    revoke = lambda token, **over: client.post(f"{O}/revoke", data={"token": token, "client_id": app["client_id"], **over})  # noqa: E731
    assert revoke(t["refresh_token"]).status_code == 401  # without its secret
    assert revoke("lr_unknown", client_secret=app["client_secret"]).json() == {"ok": True}
    assert client.get("/api/v1/auth/me", headers=_bearer(t)).status_code == 200
    other = _register(client, client_name="Other")
    assert client.post(f"{O}/revoke", data={"token": t["access_token"], "client_id": other["client_id"]}).json() == {"ok": True}
    assert client.get("/api/v1/auth/me", headers=_bearer(t)).status_code == 200  # another app's token: left alone
    assert revoke(t["access_token"], client_secret=app["client_secret"]).json() == {"ok": True}
    assert client.get("/api/v1/auth/me", headers=_bearer(t)).status_code == 401
    assert client.get(f"{O}/grants", headers=h).json() == []
    row = db.one("SELECT email, detail FROM audit_log WHERE action = 'oauth.revoke'")
    assert row["email"] == "vi@x.io" and row["detail"]["by"] == "app"


def test_redirects_keep_the_apps_own_query():
    assert oauth.with_params("https://a.example/cb?x=1", code="c d", state=None) == "https://a.example/cb?x=1&code=c+d"
    assert oauth.scopes("write") == "read write" and oauth.scopes("email profile") == "read" and oauth.scopes(None) == "read"
    assert not oauth.redirect_ok(["https://a.example/cb"], "http://[bad")
    assert oauth.token_account(None, "la_not_oauth") is None and oauth.get_client(None, "nope") is None
