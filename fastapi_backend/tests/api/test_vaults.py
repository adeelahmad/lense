"""Vaults: namespaces only their people's passkeys open (app/domain/vaults.py)."""

from __future__ import annotations

import time

from app.domain import jobs, keyring, store
from tests.api.test_passkeys import ORIGIN, WEB, _session, _setup
from tests.fake_authenticator import Authenticator
from tests.helpers import write_wav

R = store.R


def _upload(client, h, data, name="talk.wav"):
    r = client.post("/api/v1/uploads", headers=h, json={"namespace": "pods", "filename": name, "size": len(data)})
    assert r.status_code == 201, r.text
    return client.put(f"/api/v1/uploads/{r.json()['id']}?offset=0", headers={**h, "Content-Type": "application/octet-stream"}, content=data)


def _ask(client, h, device, kind, path, cred_id=None, prf=True):
    """A vault step done with a passkey: options, the device's answer, and the PRF secret sent beside it."""
    o = client.post("/api/v1/namespaces/pods/vault/options", json={"kind": kind}, headers={**h, **WEB})
    assert o.status_code == 200, o.text
    o = o.json()
    cred = device.get(o["options"], ORIGIN, cred_id)
    secret = cred.pop("clientExtensionResults").get("prf", {}).get("results", {}).get("first", "") if prf else ""
    return client.post(path, json={"flow": o["flow"], "credential": cred, "prf": secret}, headers=h)


def test_a_vault_opens_only_with_its_passkeys(app, client, db, folder):
    laptop, phone = Authenticator(), Authenticator()
    h = _session(client, _setup(app, client, laptop))
    wav = folder / "talk.wav"
    write_wav(wav, seconds=1.0)
    data = wav.read_bytes()
    rid = _upload(client, h, data).json()["recording"]
    sid = store.ns_id(db, "pods")
    assert client.get("/api/v1/namespaces/pods/vault", headers=h).json() == {
        "vault": False,
        "unlocked": False,
        "unlocked_until": None,
        "passkeys": [],
    }

    # sealed with the laptop's passkey: the server's own key no longer opens it
    o = client.post("/api/v1/namespaces/pods/vault/options", json={"kind": "seal"}, headers={**h, **WEB}).json()
    assert o["options"]["extensions"]["prf"]["eval"]["first"]
    r = _ask(client, h, laptop, "seal", "/api/v1/namespaces/pods/vault")
    assert r.status_code == 200, r.text
    st = r.json()
    assert st["vault"] and st["unlocked"] and st["unlocked_until"] > time.time() and len(st["passkeys"]) == 1
    row = db.one("SELECT * FROM $r", r=R("data_key", sid))
    assert row["vault"] and "server" not in row["keys"]["1"]["wrapped"]
    assert client.get(f"/api/v1/recordings/{rid}/audio", headers=h).content == data  # open for now

    # locked: its files don't open, and its work waits
    assert client.post("/api/v1/namespaces/pods/vault/lock", headers=h).json()["unlocked"] is False
    assert client.get(f"/api/v1/recordings/{rid}/audio", headers=h).status_code == 423
    db._data_keys.clear()  # nor in a fresh process: nothing the server keeps opens it
    assert client.get(f"/api/v1/recordings/{rid}/audio", headers=h).status_code == 423
    assert db.values("SELECT VALUE id FROM job WHERE status = 'queued' AND space = $s", s=sid)  # the upload's pipeline
    assert sid in keyring.locked_vaults(db) and jobs.claim(db, "w", set(jobs.STEPS)) is None

    # unlocked with its passkey: it plays, and the work goes on
    r = _ask(client, h, laptop, "unlock", "/api/v1/namespaces/pods/vault/unlock")
    assert r.status_code == 200 and r.json()["unlocked"], r.text
    assert client.get(f"/api/v1/recordings/{rid}/audio", headers=h).content == data
    assert jobs.claim(db, "w", set(jobs.STEPS)) is not None

    # a second passkey opens it too; then the first can be let go
    o = client.post("/api/v1/auth/passkeys/options", headers={**h, **WEB}).json()
    client.post("/api/v1/auth/passkeys", json={"flow": o["flow"], "credential": phone.create(o["options"], ORIGIN), "name": "Phone"}, headers=h)
    phone_id = next(iter(phone.keys))
    r = _ask(client, h, phone, "add", "/api/v1/namespaces/pods/vault/passkeys", cred_id=phone_id)
    assert r.status_code == 200 and len(r.json()["passkeys"]) == 2, r.text
    keyring.lock(db, sid)
    assert _ask(client, h, phone, "unlock", "/api/v1/namespaces/pods/vault/unlock", cred_id=phone_id).status_code == 200

    # the only passkey that opens a vault can't be removed, from the vault or from the account
    mine = {k["name"]: k["id"] for k in client.get("/api/v1/auth/passkeys", headers=h).json()}
    assert client.delete(f"/api/v1/namespaces/pods/vault/passkeys/{mine['Laptop']}", headers=h).status_code == 200
    last = client.delete(f"/api/v1/namespaces/pods/vault/passkeys/{mine['Phone']}", headers=h)
    assert last.status_code == 400 and "only passkey" in last.json()["detail"]
    gone = client.delete(f"/api/v1/auth/passkeys/{mine['Phone']}", headers={**h, **WEB})
    assert gone.status_code == 400 and "pods" in gone.json()["detail"]
    keyring.lock(db, sid)
    laptop_id = next(iter(laptop.keys))
    assert client.post("/api/v1/namespaces/pods/vault/options", json={"kind": "unlock"}, headers={**h, **WEB}).json()["options"][
        "allowCredentials"
    ] == [{"id": phone_id, "type": "public-key", "transports": ["internal", "hybrid"]}]  # only the passkeys that open it
    assert laptop_id not in str(keyring.status(db, sid)["wrappers"])

    # an ordinary namespace again: the server opens it whenever
    assert client.delete("/api/v1/namespaces/pods/vault", headers=h).status_code == 423  # it must be open for that
    _ask(client, h, phone, "unlock", "/api/v1/namespaces/pods/vault/unlock", cred_id=phone_id)
    r = client.delete("/api/v1/namespaces/pods/vault", headers=h)
    assert r.status_code == 200 and r.json()["vault"] is False and r.json()["passkeys"] == []
    db._data_keys.clear()
    assert client.get(f"/api/v1/recordings/{rid}/audio", headers=h).content == data
    log = db.values("SELECT VALUE action FROM audit_log")
    assert {"vault.seal", "vault.unlock", "vault.add", "vault.remove", "vault.lock", "vault.unseal"} <= set(log)


def test_a_vault_closes_once_its_time_is_up(app, client, db):
    laptop = Authenticator()
    h = _session(client, _setup(app, client, laptop))
    assert _ask(client, h, laptop, "seal", "/api/v1/namespaces/pods/vault").status_code == 200
    sid = store.ns_id(db, "pods")
    db._vault_until[sid] = time.time() - 1
    assert client.get("/api/v1/namespaces/pods/vault", headers=h).json()["unlocked"] is False
    assert sid in keyring.locked_vaults(db)


def test_a_passkey_without_prf_or_the_wrong_answer_is_refused(app, client, db):
    laptop = Authenticator()
    h = _session(client, _setup(app, client, laptop))
    r = _ask(client, h, laptop, "seal", "/api/v1/namespaces/pods/vault", prf=False)
    assert r.status_code == 400 and "PRF" in r.json()["detail"]
    assert client.get("/api/v1/namespaces/pods/vault", headers=h).json()["vault"] is False
    assert _ask(client, h, laptop, "seal", "/api/v1/namespaces/pods/vault").status_code == 200
    client.post("/api/v1/namespaces/pods/vault/lock", headers=h)
    o = client.post("/api/v1/namespaces/pods/vault/options", json={"kind": "unlock"}, headers={**h, **WEB}).json()
    cred = laptop.get(o["options"], ORIGIN)
    cred.pop("clientExtensionResults")
    wrong = client.post(
        "/api/v1/namespaces/pods/vault/unlock", json={"flow": o["flow"], "credential": cred, "prf": "A" * 43}, headers=h
    )
    assert wrong.status_code == 423
    # an answer for unlocking can't seal, and API tokens can't do either
    raw = client.post("/api/v1/tokens", json={"name": "t", "scope": "write"}, headers=h).json()["token"]
    t = {"Authorization": f"Bearer {raw}"}
    assert client.post("/api/v1/namespaces/pods/vault/options", json={"kind": "unlock"}, headers={**t, **WEB}).status_code == 403


def test_a_vault_encrypts_its_files_even_with_encryption_off(app, client, db, cfg, folder):
    from app.domain import settings, vaults

    settings.save(db, cfg, "encryption", {"files": False})
    laptop = Authenticator()
    h = _session(client, _setup(app, client, laptop))
    wav = folder / "talk.wav"
    write_wav(wav, seconds=1.0)
    data = wav.read_bytes()
    rid = _upload(client, h, data).json()["recording"]
    old = db.one("SELECT path FROM $r", r=R("recording", rid))["path"]
    assert not keyring.is_encrypted(old)
    assert _ask(client, h, laptop, "seal", "/api/v1/namespaces/pods/vault").status_code == 200
    vaults.encrypting[store.ns_id(db, "pods")].join(10)
    assert keyring.is_encrypted(old)  # what it held already
    write_wav(wav, seconds=2.0)
    new = _upload(client, h, wav.read_bytes(), "new.wav").json()["recording"]
    assert keyring.is_encrypted(db.one("SELECT path FROM $r", r=R("recording", new))["path"])  # and what comes in
    assert client.get(f"/api/v1/recordings/{rid}/audio", headers=h).content == data
