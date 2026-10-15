"""Accounts (admins) and namespace members (owners)."""

from __future__ import annotations

from tests.helpers import login, make_user


def test_admins_manage_accounts(client, new_client, db):
    root = make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    h = login(client, "root@x.io", "root password 1")
    people = {p["email"]: p for p in client.get("/api/v1/users", headers=h).json()}
    assert people["ed@x.io"]["roles"] == {"pods": "editor"} and people["root@x.io"]["admin"]
    assert "pw" not in people["ed@x.io"]

    r = client.post("/api/v1/users", json={"email": "New@x.io", "password": "new password 12", "name": "New"}, headers=h)
    assert r.status_code == 200, r.text
    uid = r.json()["id"]
    assert client.post("/api/v1/users", json={"email": "new@x.io", "password": "new password 12"}, headers=h).status_code == 400  # taken
    assert client.post("/api/v1/users", json={"email": "not an email", "password": "new password 12"}, headers=h).status_code == 400
    other = new_client()
    hn = login(other, "new@x.io", "new password 12")

    # you can't lock yourself out
    assert client.patch(f"/api/v1/users/{root}", json={"admin": False}, headers=h).status_code == 400
    assert client.patch(f"/api/v1/users/{root}", json={"disabled": True}, headers=h).status_code == 400
    assert client.patch("/api/v1/users/9999", json={"name": "x"}, headers=h).status_code == 404
    assert client.patch(f"/api/v1/users/{uid}", json={"name": "Renamed", "password": "another password 1"}, headers=h).status_code == 200
    assert other.get("/api/v1/auth/me", headers=hn).status_code == 401  # a new password signs them out everywhere
    login(other, "new@x.io", "another password 1")
    audit = client.get("/api/v1/audit", headers=h).json()
    upd = next(a for a in audit if a["action"] == "user.update")
    assert upd["target"] == f"account:{uid}" and upd["detail"] == {"name": "Renamed"}  # never the password
    assert any(a["action"] == "user.create" for a in audit)

    he = login(new_client(), "ed@x.io", "editor password 1")
    assert client.get("/api/v1/users", headers=he).status_code == 403
    assert client.post("/api/v1/users", json={"email": "z@x.io", "password": "zzz password 1"}, headers=he).status_code == 403


def test_owners_manage_members(client, new_client, db):
    make_user(db, "own@x.io", "owner password 1", roles={"pods": "owner"})
    ed = make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    vi = make_user(db, "vi@x.io", "viewer password 1")
    h = login(client, "own@x.io", "owner password 1")
    he = login(new_client(), "ed@x.io", "editor password 1")
    members = {m["email"]: m["role"] for m in client.get("/api/v1/namespaces/pods/members", headers=h).json()}
    assert members == {"own@x.io": "owner", "ed@x.io": "editor"}
    assert client.get("/api/v1/namespaces/pods/members", headers=he).status_code == 403  # editors can't
    assert client.get("/api/v1/namespaces/calls/members", headers=h).status_code == 404  # invisible namespace
    assert client.get("/api/v1/namespaces/nope/members", headers=h).status_code == 404

    assert client.put("/api/v1/namespaces/pods/members", json={"email": "VI@x.io", "role": "viewer"}, headers=h).status_code == 200
    assert client.put("/api/v1/namespaces/pods/members", json={"account": ed, "role": "owner"}, headers=h).status_code == 200
    assert client.put("/api/v1/namespaces/pods/members", json={"email": "ghost@x.io", "role": "viewer"}, headers=h).status_code == 404
    assert client.put("/api/v1/namespaces/pods/members", json={"account": vi, "role": "admin"}, headers=h).status_code == 400
    assert client.put("/api/v1/namespaces/calls/members", json={"account": vi, "role": "viewer"}, headers=h).status_code == 404
    members = {m["email"]: m["role"] for m in client.get("/api/v1/namespaces/pods/members", headers=h).json()}
    assert members == {"own@x.io": "owner", "ed@x.io": "owner", "vi@x.io": "viewer"}
    hv = login(new_client(), "vi@x.io", "viewer password 1")
    assert client.get("/api/v1/auth/me", headers=hv).json()["roles"] == {"pods": "viewer"}

    assert client.put("/api/v1/namespaces/pods/members", json={"account": vi, "role": None}, headers=h).status_code == 200  # removed
    assert "vi@x.io" not in {m["email"] for m in client.get("/api/v1/namespaces/pods/members", headers=h).json()}
    assert client.get("/api/v1/auth/me", headers=hv).json()["roles"] == {}


def test_members_changes_need_write_access(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    vi = make_user(db, "vi@x.io", "viewer password 1")
    h = login(client, "root@x.io", "root password 1")
    tok = client.post("/api/v1/tokens", json={"name": "ci", "scope": "read"}, headers=h).json()["token"]
    bearer = {"Authorization": f"Bearer {tok}"}
    assert client.get("/api/v1/namespaces/calls/members", headers=bearer).status_code == 200  # admins own every namespace
    body = {"account": vi, "role": "viewer"}
    assert client.put("/api/v1/namespaces/calls/members", json=body, headers=bearer).status_code == 403  # read-only token
    assert client.put("/api/v1/namespaces/calls/members", json=body, headers=h).status_code == 200


def test_admins_remove_people_for_good(client, new_client, db):
    from app.domain import auth, chat, notes, store

    root = make_user(db, "root@x.io", "root password 1", admin=True)
    gone = make_user(db, "gone@x.io", "gone password 12", roles={"pods": "editor"})
    h = login(client, "root@x.io", "root password 1")
    hg = login(new_client(), "gone@x.io", "gone password 12")
    tok = client.post("/api/v1/tokens", json={"name": "ci", "scope": "read"}, headers=hg).json()["token"]
    space = store.ns_id(db, "pods")
    mine = notes.create(db, 1, space, gone, "only mine")
    shared = notes.create(db, 1, space, gone, "for everyone", shared=True)
    cid = chat.create(db, gone, title="private")

    assert client.delete(f"/api/v1/users/{root}", headers=h).status_code == 400  # not yourself
    assert client.delete("/api/v1/users/9999", headers=h).status_code == 404
    assert client.delete(f"/api/v1/users/{gone}", headers=hg).status_code == 403  # admins only
    assert client.delete(f"/api/v1/users/{gone}", headers=h).status_code == 200

    # every way in stops at once
    assert client.get("/api/v1/auth/me", headers=hg).status_code == 401
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"email": "gone@x.io", "password": "gone password 12"}).status_code == 401
    assert client.post(f"/api/v1/users/{gone}/signin-link", headers=h).status_code == 404
    assert client.patch(f"/api/v1/users/{gone}", json={"name": "back"}, headers=h).status_code == 404
    assert client.delete(f"/api/v1/users/{gone}", headers=h).status_code == 404  # once
    # off the list and out of the namespace; private things go, shared ones stay under their name
    assert "gone@x.io" not in {p["email"] for p in client.get("/api/v1/users", headers=h).json()}
    assert not db.values("SELECT VALUE id FROM membership WHERE account = $a", a=gone)
    assert not db.values("SELECT VALUE id FROM api_token WHERE account = $a", a=gone)
    assert not db.values("SELECT VALUE id FROM chat WHERE account = $a", a=gone) and cid
    assert notes.get(db, shared)["text"] == "for everyone"
    with __import__("pytest").raises(KeyError):
        notes.get(db, mine)
    left = auth.get_account(db, gone)
    assert left["removed_at"] and left["disabled"] and not left["admin"] and left["email"] != "gone@x.io"
    audit = client.get("/api/v1/audit", headers=h).json()
    assert any(a["action"] == "user.remove" and a["target"] == f"account:{gone}" for a in audit)

    # the address can be invited again
    assert client.post("/api/v1/users", json={"email": "gone@x.io"}, headers=h).status_code == 200


def test_matterbridge_account_stays(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    bot = make_user(db, "bot@x.io", "bot password 12")
    h = login(client, "root@x.io", "root password 1")
    assert client.put("/api/v1/settings/bridge", json={"account": "bot@x.io"}, headers=h).status_code == 200
    r = client.delete(f"/api/v1/users/{bot}", headers=h)
    assert r.status_code == 409 and "Matterbridge" in r.json()["detail"]


def test_invites_are_emailed_when_mail_is_set_up(client, db, monkeypatch):
    from app import email

    sent = []

    class Mailer:
        def __init__(self, cfg):
            pass

        async def send_message(self, message, template_name=None):
            sent.append(([r.email for r in message.recipients], message.subject, template_name, message.template_body))

    monkeypatch.setattr(email, "_mailer", Mailer)
    make_user(db, "root@x.io", "root password 1", admin=True)
    h = login(client, "root@x.io", "root password 1")
    uid = client.post("/api/v1/users", json={"email": "new@x.io", "name": "New"}, headers=h).json()["id"]
    r = client.post(f"/api/v1/users/{uid}/signin-link?email=true", headers=h).json()
    assert r["emailed"] is False and not sent  # no mail server yet: the link is still there to share
    mail = {"server": "smtp.example.org", "port": 465, "from_address": "lens@example.org", "security": "ssl"}
    assert client.put("/api/v1/settings/mail", json=mail, headers=h).status_code == 200
    assert client.post(f"/api/v1/users/{uid}/signin-link", headers=h).json()["emailed"] is False  # only when asked
    r = client.post(f"/api/v1/users/{uid}/signin-link?email=true", headers=h).json()
    assert r["emailed"] is True
    assert sent[-1][:3] == (["new@x.io"], "You're invited to Lens", "invite.html")
    assert sent[-1][3]["link"].split("#")[1] == r["url"].split("#")[1] and sent[-1][3]["days"] == 3
