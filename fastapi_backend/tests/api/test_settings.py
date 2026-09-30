"""Settings saved in the app: typed, checked, secrets encrypted at rest and never shown, and no locking yourself out."""

from __future__ import annotations

from tests.helpers import login, make_user


def test_settings_are_editable_and_secret(app, client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    h = login(client, "root@x.io", "root password 1")
    r = client.put("/api/v1/settings/llm", json={"base_url": "http://127.0.0.1:1234/v1", "model": "m", "api_key": "sk-test-123"}, headers=h)
    assert r.status_code == 200, r.text
    v = client.get("/api/v1/settings", headers=h)
    assert "sk-test-123" not in v.text
    assert v.json()["llm"]["values"]["api_key"] == {"secret": True, "set": True}
    assert "sk-test-123" not in str(db.rows("SELECT * FROM app_setting"))  # encrypted at rest
    assert app.state.settings.current()["llm"]["api_key"] == "sk-test-123"
    assert client.put("/api/v1/settings/speakers", json={"match_threshold": 2}, headers=h).status_code == 400
    assert client.put("/api/v1/settings/database", json={"url": "ws://x"}, headers=h).status_code == 400
    assert client.put("/api/v1/settings/server", json={"allowed_hosts": ["archive.example"]}, headers=h).status_code == 400  # lock-out
    assert client.put("/api/v1/settings/search", json={"stemming": "none"}, headers=h).status_code == 200
    assert app.state.settings.current()["search"]["stemming"] == "none"
    # the changes are in the audit log
    audit = client.get("/api/v1/audit", headers=h).json()
    assert {(a["action"], a["target"]) for a in audit} >= {("settings.save", "llm"), ("settings.save", "search")}
    assert client.get("/api/v1/audit", params={"limit": 5000}, headers=h).status_code == 422


def test_settings_need_an_admin(client, db):
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    h = login(client, "vi@x.io", "viewer password 1")
    assert client.get("/api/v1/settings", headers=h).status_code == 403
    assert client.put("/api/v1/settings/search", json={"stemming": "none"}, headers=h).status_code == 403
    assert client.get("/api/v1/audit", headers=h).status_code == 403
    assert client.get("/api/v1/settings").status_code == 401


def test_read_only_token_cant_change_settings(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    h = login(client, "root@x.io", "root password 1")
    tok = client.post("/api/v1/tokens", json={"name": "ci", "scope": "read"}, headers=h).json()["token"]
    bearer = {"Authorization": f"Bearer {tok}"}
    assert client.get("/api/v1/settings", headers=bearer).status_code == 200
    assert client.put("/api/v1/settings/search", json={"stemming": "none"}, headers=bearer).status_code == 403
