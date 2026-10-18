"""Secrets kept in a password manager (docs/secrets.md): a secret field in Settings holds a reference to a Vaultwarden
item instead of the secret, Lens reads the value from the vault and nobody, agents included, ever sees it."""

from __future__ import annotations

import time

import pytest

from app.domain import secret_providers, settings
from tests import fake_vaultwarden as fv
from tests.api.test_mcp import tool, tool_error
from tests.helpers import login, make_user

SETUP = {
    "vaultwarden_email": fv.EMAIL,
    "vaultwarden_client_id": fv.CLIENT_ID,
    "vaultwarden_client_secret": fv.CLIENT_SECRET,
    "vaultwarden_password": fv.PASSWORD,
    "vaultwarden_folder": "Lens",
}


@pytest.fixture
def vault():
    secret_providers.forget()
    v, url = fv.server()
    v.add("OpenAI", username="billing@x.io", password="sk-from-the-vault", uri="https://api.openai.com", notes="the team key")
    v.add("Brave", password="brave-key-1", fields={"Endpoint token": "ep-token-9"})
    v.add("Bank", folder="Personal", password="never-read")
    v.add("Wiki", type=2, notes="a page of notes", own_key=True)
    v.add("Card", type=3, number="4111 1111 1111 1111", code="123", holder="A. Person")
    yield v, url
    v.srv.shutdown()
    secret_providers.forget()


def _admin(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    return login(client, "root@x.io", "root password 1")


def _set_up(client, h, url, **over):
    r = client.put("/api/v1/settings/secrets", json={"vaultwarden_url": url, **SETUP, **over}, headers=h)
    assert r.status_code == 200, r.text


def test_a_secret_field_reads_its_value_from_the_vault(app, client, db, vault):
    v, url = vault
    h = _admin(client, db)
    # before a vault is set up, a reference can't be saved; the built-in store is the default and stays as it was
    r = client.put("/api/v1/settings/llm", json={"api_key": "vaultwarden://OpenAI/password"}, headers=h)
    assert r.status_code == 400 and "Settings › Secrets" in r.text
    _set_up(client, h, url)
    got = client.get("/api/v1/secrets", headers=h).json()
    vw = next(p for p in got["providers"] if p["id"] == "vaultwarden")
    assert (vw["configured"], vw["scheme"], vw["folder"], vw["email"]) == (True, "vaultwarden", "Lens", fv.EMAIL)
    assert next(p for p in got["providers"] if p["id"] == "builtin")["configured"] is True
    # the vault's own sign-in is shown as set, never its values, and never comes from a vault itself
    view = client.get("/api/v1/settings", headers=h).json()["secrets"]["values"]
    assert view["vaultwarden_password"] == {"secret": True, "set": True} and view["vaultwarden_client_id"] == fv.CLIENT_ID
    assert fv.PASSWORD not in client.get("/api/v1/settings", headers=h).text
    r = client.put("/api/v1/settings/secrets", json={"vaultwarden_password": "vaultwarden://Bank"}, headers=h)
    assert r.status_code == 400 and "kept in Lens" in r.text

    # a reference in a secret field: checked against the vault when saved, then read when the settings are built
    r = client.put(
        "/api/v1/settings/llm",
        json={"base_url": "http://127.0.0.1:1234/v1", "model": "m", "api_key": "vaultwarden://OpenAI/password"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert app.state.settings.current()["llm"]["api_key"] == "sk-from-the-vault"
    everything = client.get("/api/v1/settings", headers=h)
    assert "sk-from-the-vault" not in everything.text
    assert "sk-from-the-vault" not in str(db.rows("SELECT * FROM app_setting"))  # only the reference is kept, sealed
    shown = everything.json()["llm"]["values"]["api_key"]
    assert (shown["secret"], shown["set"], shown["ref"], shown["read"], shown["provider"]) == (
        True,
        True,
        "vaultwarden://OpenAI/password",
        True,
        "vaultwarden",
    )
    assert shown["read_at"] and shown["error"] is None
    refs = client.get("/api/v1/secrets", headers=h).json()["references"]
    assert [(r["section"], r["key"], r["ref"], r["read"]) for r in refs] == [("llm", "api_key", "vaultwarden://OpenAI/password", True)]
    fields = client.get("/api/v1/secrets", headers=h).json()["fields"]
    assert {"section": "llm", "key": "api_key", "set": True, "locked": False, "ref": "vaultwarden://OpenAI/password"} in fields
    assert {"section": "web", "key": "api_key", "set": False, "locked": False} in fields

    # every field a reference can name: a login's fields, a custom field, a note's text, a card's number; the item
    # alone is a login's password, a note's text or a card's number
    for ref, value in (
        ("vaultwarden://OpenAI", "sk-from-the-vault"),
        ("vaultwarden://openai/username", "billing@x.io"),
        ("vaultwarden://OpenAI/uri", "https://api.openai.com"),
        ("vaultwarden://OpenAI/notes", "the team key"),
        ("vaultwarden://Brave/Endpoint token", "ep-token-9"),
        ("vaultwarden://Wiki", "a page of notes"),
        ("vaultwarden://Card", "4111 1111 1111 1111"),
        ("vaultwarden://Card/code", "123"),
    ):
        assert client.put("/api/v1/settings/web", json={"api_key": ref}, headers=h).status_code == 200, ref
        assert app.state.settings.current()["web"]["api_key"] == value, ref
    # what can't be read is refused when it's saved, with why
    for ref, why in (
        ("vaultwarden://Bank", "no item called Bank in the folder Lens"),  # another folder: out of Lens's reach
        ("vaultwarden://OpenAI/totp", "has no totp"),
        ("vaultwarden://Nothing/password", "no item called Nothing"),
        ("vaultwarden://", "doesn't name an item"),
    ):
        r = client.put("/api/v1/settings/web", json={"api_key": ref}, headers=h)
        assert r.status_code == 400 and why in r.text, (ref, r.text)
    assert app.state.settings.current()["web"]["api_key"] == "123"  # the last one saved stands
    # a plain value still works as before, sealed in Lens
    assert client.put("/api/v1/settings/web", json={"api_key": "typed-in"}, headers=h).status_code == 200
    assert app.state.settings.current()["web"]["api_key"] == "typed-in"
    assert "ref" not in client.get("/api/v1/settings", headers=h).json()["web"]["values"]["api_key"]
    # the vault saw a sign-in with the API key, and syncs; the master password never went over the wire
    assert [s["form"]["grant_type"] for s in v.seen if s["method"] == "POST"][0] == "client_credentials"
    assert fv.PASSWORD not in str(v.seen)
    # the audit log has the change, not the reference's value
    audit = client.get("/api/v1/audit", headers=h).json()
    assert ("settings.save", "secrets") in {(a["action"], a["target"]) for a in audit}


def test_the_vault_is_read_again_and_what_vanishes_stops_being_used(app, client, db, vault, monkeypatch):
    v, url = vault
    h = _admin(client, db)
    _set_up(client, h, url, cache_minutes=1)
    assert client.put("/api/v1/settings/llm", json={"api_key": "vaultwarden://Brave"}, headers=h).status_code == 200
    assert app.state.settings.current()["llm"]["api_key"] == "brave-key-1"
    syncs = lambda: sum(s["path"].startswith("/api/sync") for s in v.seen)  # noqa: E731
    before = syncs()
    clock = time.time()

    def later(seconds):
        """Move the clock on, let the settings notice, and wait for the read that starts in the background."""
        nonlocal clock
        clock += seconds
        monkeypatch.setattr(secret_providers, "_now", lambda: clock)
        gen, n = secret_providers.generation(), syncs()
        app.state.settings.current()
        for _ in range(200):
            if secret_providers.generation() != gen or (syncs() > n and not secret_providers._STATE["busy"]):
                break
            time.sleep(0.02)
        return app.state.settings.current()["llm"]["api_key"]

    # the key is rotated in the vault: the old one serves until the cache runs out, then the new one is read, in the
    # background, without anyone saving a setting
    v.items[1]["login"]["password"] = v.enc("brave-key-2")
    assert later(30) == "brave-key-1"
    assert syncs() == before  # not asked again before it's due
    assert later(60) == "brave-key-2"
    # the item is moved out of the folder: at the next read Lens stops using it and says so
    v.items[1]["folderId"] = v.folders["Personal"]
    assert later(90) is None
    shown = client.get("/api/v1/settings", headers=h).json()["llm"]["values"]["api_key"]
    assert shown["read"] is False and "no item called Brave" in shown["error"] and shown["missing"] is True
    # back in the folder: read again once the retry is due
    v.items[1]["folderId"] = v.folders["Lens"]
    assert later(secret_providers.RETRY_SECONDS + 1) == "brave-key-2"
    # the vault goes away: what was read before keeps serving, and the status says why it couldn't be read again
    v.refuse = 500
    secret_providers.PROVIDERS["vaultwarden"].reset()  # as if the process had to sign in again
    assert later(90) == "brave-key-2"
    shown = client.get("/api/v1/settings", headers=h).json()["llm"]["values"]["api_key"]
    assert shown["read"] is True and "HTTP 500" in shown["error"] and shown["missing"] is False


def test_the_vault_is_set_up_and_checked_in_the_app(app, client, db, vault):
    v, url = vault
    h = _admin(client, db)
    # not set up yet: the check says what's missing, and the items can't be listed
    assert client.post("/api/v1/secrets/test", json={"provider": "vaultwarden"}, headers=h).json()["ok"] is False
    assert client.get("/api/v1/secrets/items", headers=h).status_code == 409
    for bad in (
        {"vaultwarden_url": "ftp://vault"},
        {"vaultwarden_url": "https://user:pw@vault.example"},
        {"vaultwarden_email": "not an address"},
        {"vaultwarden_folder": ""},
        {"cache_minutes": 0},
    ):
        assert client.put("/api/v1/settings/secrets", json=bad, headers=h).status_code == 400, bad
    _set_up(client, h, url, vaultwarden_password="wrong")
    r = client.post("/api/v1/secrets/test", headers=h).json()
    assert r["ok"] is False and "master password" in r["error"]
    _set_up(client, h, url, vaultwarden_client_secret="wrong")
    r = client.post("/api/v1/secrets/test", headers=h).json()
    assert r["ok"] is False and "refused Lens's sign-in" in r["error"]
    _set_up(client, h, "http://127.0.0.1:9")
    r = client.post("/api/v1/secrets/test", headers=h).json()
    assert r["ok"] is False and "couldn't reach" in r["error"]
    _set_up(client, h, url)
    assert client.post("/api/v1/secrets/test", headers=h).json() == {
        "ok": True,
        "items": 4,
        "folder": "Lens",
        "account": fv.EMAIL,
        "error": None,
    }
    # the items Lens may refer to: names, fields and references, never a value
    r = client.get("/api/v1/secrets/items", headers=h)
    assert r.status_code == 200, r.text
    items = {i["name"]: i for i in r.json()["items"]}
    assert list(items) == ["Brave", "Card", "OpenAI", "Wiki"]  # Bank is in another folder
    assert items["OpenAI"]["type"] == "login" and items["OpenAI"]["fields"] == ["username", "password", "uri", "notes"]
    assert items["OpenAI"]["ref"] == "vaultwarden://OpenAI" and items["OpenAI"]["refs"]["username"] == "vaultwarden://OpenAI/username"
    assert items["Brave"]["refs"]["Endpoint token"] == "vaultwarden://Brave/Endpoint token"
    assert items["Wiki"]["type"] == "note" and items["Wiki"]["ref"] == "vaultwarden://Wiki"
    assert items["Card"]["type"] == "card" and items["Card"]["fields"] == ["cardholderName", "number", "code"]
    for secret in ("sk-from-the-vault", "brave-key-1", "ep-token-9", "4111", "a page of notes", "billing@x.io"):
        assert secret not in r.text
    assert [i["name"] for i in client.get("/api/v1/secrets/items", params={"q": "ope"}, headers=h).json()["items"]] == ["OpenAI"]
    # the folder is the scope: another one, and Bank is what Lens sees
    assert client.put("/api/v1/settings/secrets", json={"vaultwarden_folder": "Personal"}, headers=h).status_code == 200
    assert [i["name"] for i in client.get("/api/v1/secrets/items", headers=h).json()["items"]] == ["Bank"]
    # admins only
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    hv = login(client, "vi@x.io", "viewer password 1")
    assert client.get("/api/v1/secrets", headers=hv).status_code == 403
    assert client.get("/api/v1/secrets/items", headers=hv).status_code == 403
    assert client.post("/api/v1/secrets/test", headers=hv).status_code == 403


def test_agents_refer_to_secrets_and_never_read_them(app, client, db, vault):
    v, url = vault
    h = _admin(client, db)
    _set_up(client, h, url)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    hv = login(client, "vi@x.io", "viewer password 1")
    assert "only admins" in tool_error(client, hv, "list_secrets")
    assert "only admins" in tool_error(client, hv, "list_vault_items")
    got = tool(client, h, "list_vault_items", q="brave")
    assert [i["name"] for i in got["items"]] == ["Brave"] and "brave-key-1" not in str(got)
    # pointing a field at the vault: checked first, and the reference is what's saved
    assert "isn't a secret field" in tool_error(client, h, "set_secret_reference", section="llm", key="model", ref="vaultwarden://OpenAI")
    assert "a value never passes" in tool_error(client, h, "set_secret_reference", section="llm", key="api_key", ref="sk-typed")
    assert "no item called Nope" in tool_error(client, h, "set_secret_reference", section="llm", key="api_key", ref="vaultwarden://Nope")
    out = tool(client, h, "set_secret_reference", section="llm", key="api_key", ref="vaultwarden://OpenAI")
    assert out["saved"] == {"section": "llm", "key": "api_key", "ref": "vaultwarden://OpenAI"} and out["status"]["read"] is True
    assert app.state.settings.current()["llm"]["api_key"] == "sk-from-the-vault"
    listed = tool(client, h, "list_secrets")
    assert [(r["section"], r["key"], r["ref"]) for r in listed["references"]] == [("llm", "api_key", "vaultwarden://OpenAI")]
    assert "sk-from-the-vault" not in str(listed) and fv.PASSWORD not in str(listed)
    # a read-only token can list, not point
    tok = client.post("/api/v1/tokens", json={"name": "ro", "scope": "read"}, headers=h).json()["token"]
    ro = {"Authorization": f"Bearer {tok}"}
    assert tool(client, ro, "list_secrets")["references"]
    assert "read-only" in tool_error(client, ro, "set_secret_reference", section="llm", key="api_key", ref="vaultwarden://Brave")
    # the environment wins over a reference, as over any saved value
    assert ("llm", "api_key") in settings.ENV_OVERRIDES


def test_references_are_parsed_and_written_the_same_way():
    r = secret_providers.parse("vaultwarden://My item/Endpoint token")
    assert (r.provider, r.item, r.field) == ("vaultwarden", "My item", "Endpoint token")
    assert secret_providers.parse("VAULTWARDEN://x").provider == "vaultwarden"
    assert secret_providers.reference("vaultwarden", "a/b", "c%d") == "vaultwarden://a%2Fb/c%25d"
    assert secret_providers.parse(secret_providers.reference("vaultwarden", "a/b", "c%d")).item == "a/b"
    assert secret_providers.parse(secret_providers.reference("vaultwarden", "a/b", "c%d")).field == "c%d"
    for plain in ("sk-abc", "http://not-a-provider/x", "nothing://x", None, 3, ""):
        assert secret_providers.parse(plain) is None
    assert secret_providers.parse("vaultwarden://OpenAI").field is None
