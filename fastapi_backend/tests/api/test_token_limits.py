"""How long API keys last: admins set the default, the most and whether keys may never expire; they see and revoke
anyone's keys."""

from __future__ import annotations

import datetime as dt

from tests.helpers import login, make_user


def _days(t):
    made, ends = (dt.datetime.fromisoformat(t[k]) for k in ("created_at", "expires_at"))
    return round((ends - made).total_seconds() / 86400)


def test_key_lifetimes_follow_the_settings(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "ada@x.io", "ada password 1")
    hr, ha = login(client, "root@x.io", "root password 1"), login(client, "ada@x.io", "ada password 1")
    new = lambda **b: client.post("/api/v1/tokens", headers=ha, json={"name": "k", **b})  # noqa: E731
    assert client.get("/api/v1/tokens/limits", headers=ha).json() == {"default_days": 90, "max_days": 365, "never_expire": False}
    # by default: 90 days, at most 365, and every key expires
    assert new().status_code == 200
    r = new(days=0)
    assert r.status_code == 400 and "have to expire" in r.json()["detail"]
    assert new(days=366).status_code == 400
    assert new(days=365).status_code == 200
    assert sorted(_days(t) for t in client.get("/api/v1/tokens", headers=ha).json()) == [90, 365]

    # admins change them; they're checked
    put = lambda body, h=hr: client.put("/api/v1/settings/tokens", headers=h, json=body)  # noqa: E731
    assert put({"default_days": 30}, ha).status_code == 403
    assert put({"default_days": 0}).status_code == 400
    assert put({"max_days": 4000}).status_code == 400
    assert put({"default_days": 400}).status_code == 400  # more than the most
    assert put({"never_expire": "yes"}).status_code == 400
    assert put({"lifetime": 3}).status_code == 400
    assert put({"default_days": 30, "max_days": 60, "never_expire": True}).status_code == 200
    assert client.get("/api/v1/tokens/limits", headers=ha).json() == {"default_days": 30, "max_days": 60, "never_expire": True}
    assert new(days=61).status_code == 400
    forever = new(days=0).json()
    assert new().status_code == 200
    mine = {t["id"]: t for t in client.get("/api/v1/tokens", headers=ha).json()}
    assert mine[forever["id"]]["expires_at"] is None
    assert sorted(_days(t) for t in mine.values() if t["expires_at"]) == [30, 90, 365]  # earlier keys keep theirs
    audit = [a["action"] for a in client.get("/api/v1/audit", headers=hr).json()]
    assert "settings.save" in audit


def test_admins_see_and_revoke_anyones_keys(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "ada@x.io", "ada password 1")
    hr, ha = login(client, "root@x.io", "root password 1"), login(client, "ada@x.io", "ada password 1")
    ada = client.post("/api/v1/tokens", headers=ha, json={"name": "sync", "scope": "write"}).json()
    root = client.post("/api/v1/tokens", headers=hr, json={"name": "ci"}).json()
    listed = client.get("/api/v1/admin/tokens", headers=hr).json()
    assert {(t["name"], t["email"], t["scope"]) for t in listed} == {("sync", "ada@x.io", "write"), ("ci", "root@x.io", "read")}
    assert all("hash" not in t and t["prefix"].startswith("la_") for t in listed)
    assert client.get("/api/v1/admin/tokens", headers=ha).status_code == 403
    assert client.delete(f"/api/v1/admin/tokens/{ada['id']}", headers=ha).status_code == 403
    bearer = {"Authorization": f"Bearer {ada['token']}"}
    assert client.get("/api/v1/auth/me", headers=bearer).status_code == 200
    assert client.delete(f"/api/v1/admin/tokens/{ada['id']}", headers=hr).status_code == 200
    assert client.get("/api/v1/auth/me", headers=bearer).status_code == 401  # stops working at once
    assert client.delete(f"/api/v1/admin/tokens/{ada['id']}", headers=hr).status_code == 404
    # people revoke their own; both are audited
    assert client.delete(f"/api/v1/tokens/{root['id']}", headers=hr).status_code == 200
    assert client.get("/api/v1/admin/tokens", headers=hr).json() == []
    revokes = [a for a in client.get("/api/v1/audit", headers=hr).json() if a["action"] == "token.revoke"]
    assert sorted(a["target"] for a in revokes) == sorted([f"api_token:{ada['id']}", f"api_token:{root['id']}"])
