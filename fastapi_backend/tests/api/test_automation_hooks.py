"""An automation's webhook (docs/automations.md#webhook-trigger): a secret address per trigger, made when the automation
is saved; a call to it is a `webhook.received` activity, capped in size and rate, signed when the caller signs, and
logged once however often it is retried."""

from __future__ import annotations

import time

import pytest

from app.domain import automation_hooks, events, notify, store

R = store.R
ADMIN = events.viewer(1, None, admin=True)


@pytest.fixture(autouse=True)
def fresh():
    automation_hooks._calls.clear()
    events._seen.clear()
    events._claimed.clear()
    yield
    automation_hooks._calls.clear()


def _call(client, made, **kw):
    return client.post(made["path"], **kw)


def _start(db, cfg, consumer):
    """A follower's first look marks where it starts: what's logged in that second isn't seen, so wait for the next."""
    assert events.follow(db, cfg, consumer, kinds=["webhook.received"])[0] == []
    t = store.now()
    while store.now() == t:
        time.sleep(0.05)


def _logged(db):
    return db.values("SELECT VALUE record::id(id) FROM event_log WHERE kind = 'webhook.received'")


def test_a_hook_is_made_for_a_trigger_and_only_its_hash_kept(client, db, cfg):
    made = automation_hooks.token_for(db, cfg, 5, "door")
    token = made["path"].rsplit("/", 1)[1]
    assert token.startswith("lah_") and made["url"].endswith(made["path"]) and made["path"].startswith("/api/v1/automations/hooks/")
    assert made["secret"].startswith("whsec_")
    row = db.one("SELECT * FROM $r", r=R("automation_hook", made["hook"]))
    assert row["automation"] == 5 and row["alias"] == "door" and row["signed"] is False
    assert token not in str(row) and made["secret"] not in str(row)  # a hash and a sealed secret
    assert automation_hooks.get(db, made["hook"])["alias"] == "door"
    assert [h["id"] for h in automation_hooks.for_automation(db, 5)] == [made["hook"]]
    with pytest.raises(ValueError):
        automation_hooks.token_for(db, cfg, 5, "Door!")
    with pytest.raises(ValueError):
        automation_hooks.token_for(db, cfg, 0, "door")

    # made again for the same trigger: a new address, and the old one stops
    again = automation_hooks.token_for(db, cfg, 5, "door")
    assert again["hook"] != made["hook"] and again["path"] != made["path"]
    assert _call(client, made, json={}).status_code == 401
    assert _call(client, again, json={}).status_code == 202
    assert [h["id"] for h in automation_hooks.for_automation(db, 5)] == [again["hook"]]

    # revoked: gone at once
    assert automation_hooks.revoke(db, again["hook"]) is True and automation_hooks.revoke(db, again["hook"]) is False
    assert _call(client, again, json={}).status_code == 401
    assert client.post("/api/v1/automations/hooks/not-a-token", json={}).status_code == 401


def test_a_call_is_a_webhook_received_activity(client, db, cfg):
    pods = store.ns_id(db, "pods")
    db.q("CREATE $r CONTENT $d", r=R("automation", 7), d={"name": "Front door", "spaces": [pods], "enabled": True})
    made = automation_hooks.token_for(db, cfg, 7, "door")
    _start(db, cfg, "test.hooks")

    r = _call(client, made, json={"event": "doorbell", "camera": "front"}, params={"source": "ha", "tag": ["a", "b"]})
    assert r.status_code == 202, r.text
    aid = r.json()["id"]
    got, commit = events.follow(db, cfg, "test.hooks", kinds=["webhook.received"])
    commit()
    assert [a["id"] for a in got] == [aid]
    a = events.check_activity(got[0])
    assert a["kind"] == "webhook.received" and a["type"] == "Announce" and a["space"] == pods
    assert a["actor"] == {"type": "Service", "id": f"lens:automation_hook/{made['hook']}", "name": "Webhook for Front door"}
    assert a["object"] == {
        "type": "Note",
        "hook": made["hook"],
        "payload": {"event": "doorbell", "camera": "front"},
        "content_type": "application/json",
        "query": {"source": "ha", "tag": ["a", "b"]},
    }
    assert a["summary"] == "Webhook for Front door was called"
    assert set(a["lens"]["untrusted"]) == {"object.payload", "object.query", "object.content_type"}
    assert "audience" not in a["lens"]
    assert events.get(db, cfg, aid, ADMIN)["object"]["hook"] == made["hook"]
    assert db.one("SELECT calls, last_called_at FROM $r", r=R("automation_hook", made["hook"]))["calls"] == 1

    # an automation not stored yet is named by its id, and its call is for admins
    other = automation_hooks.token_for(db, cfg, 9, "hook")
    b = events.get(db, cfg, _call(client, other, json=[1, 2]).json()["id"], ADMIN)
    assert b["actor"]["name"] == "Webhook for Automation 9" and b["space"] is None and b["lens"]["audience"] == "admins"
    assert b["object"]["payload"] == [1, 2]

    # switched off: refused, so the caller knows
    db.q("UPDATE $r SET enabled = false", r=R("automation", 7))
    assert _call(client, made, json={}).status_code == 403


def test_forms_text_and_what_isnt_taken(client, db, cfg):
    made = automation_hooks.token_for(db, cfg, 5, "hook")

    def payload(**kw):
        r = _call(client, made, **kw)
        assert r.status_code == 202, r.text
        return events.get(db, cfg, r.json()["id"], ADMIN)["object"].get("payload")

    assert payload(data={"item": "milk", "n": ["1", "2"]}) == {"item": "milk", "n": ["1", "2"]}
    assert payload(files={"item": (None, "eggs")}) == {"item": "eggs"}  # a multipart form's text fields
    assert payload(content="groceries", headers={"Content-Type": "text/plain"}) == "groceries"
    assert payload(content=b"") is None
    assert _call(client, made, content=b"{nope", headers={"Content-Type": "application/json"}).status_code == 400
    assert _call(client, made, content=b"\x00\x01", headers={"Content-Type": "application/octet-stream"}).status_code == 415
    assert _call(client, made, files={"f": ("a.txt", b"hi", "text/plain")}).status_code == 400  # no files


def test_size_and_rate_caps(client, db, cfg, monkeypatch):
    made = automation_hooks.token_for(db, cfg, 5, "hook")
    big = b'{"x": "' + b"a" * automation_hooks.MAX_BODY + b'"}'
    assert _call(client, made, content=big, headers={"Content-Type": "application/json"}).status_code == 413

    def chunks():
        for _ in range(80):
            yield b"a" * 1024

    assert _call(client, made, content=chunks(), headers={"Content-Type": "text/plain"}).status_code == 413  # no length given
    assert not _logged(db)

    monkeypatch.setattr(automation_hooks, "PER_MINUTE", 3)
    assert [_call(client, made, json={}).status_code for _ in range(4)] == [202, 202, 202, 429]
    r = _call(client, made, json={})
    assert r.status_code == 429 and r.headers["retry-after"] == "60"
    assert len(_logged(db)) == 3
    # a minute later there's room again; each hook has its own allowance
    t = time.monotonic()
    assert automation_hooks.allow(made["hook"], t + 61) is True
    assert automation_hooks.allow(made["hook"] + 1, t) is True


def test_signed_calls_and_retries(client, db, cfg):
    made = automation_hooks.token_for(db, cfg, 5, "hook", signed=True)
    body = b'{"temp": 31}'

    def signed(msg_id, ts=None, secret=None, payload=body):
        ts = str(int(time.time()) if ts is None else ts)
        sig = notify.sign(secret or made["secret"], msg_id, ts, payload)
        return {
            "webhook-id": msg_id,
            "webhook-timestamp": ts,
            "webhook-signature": f"v1,bm9wZQ== {sig}",
            "Content-Type": "application/json",
        }

    assert _call(client, made, content=body, headers={"Content-Type": "application/json"}).status_code == 401  # unsigned
    assert _call(client, made, content=body, headers=signed("m1", secret=notify.new_secret())).status_code == 401
    assert _call(client, made, content=body, headers=signed("m1", ts=int(time.time()) - 600)).status_code == 401
    assert _call(client, made, content=b'{"temp": 99}', headers=signed("m1")).status_code == 401  # body changed
    first = _call(client, made, content=body, headers=signed("m1"))
    assert first.status_code == 202, first.text
    # the sender tries again: the same activity, logged once
    again = _call(client, made, content=body, headers=signed("m1"))
    assert again.status_code == 202 and again.json()["id"] == first.json()["id"]
    assert len(_logged(db)) == 1

    # an unsigned hook takes unsigned calls, checks signed ones, and counts an Idempotency-Key once
    plain = automation_hooks.token_for(db, cfg, 6, "hook")
    assert _call(client, plain, json={"a": 1}).status_code == 202
    assert _call(client, plain, content=body, headers=signed("m2", secret=made["secret"])).status_code == 401
    ids = {_call(client, plain, json={"a": 2}, headers={"Idempotency-Key": "k-1"}).json()["id"] for _ in range(3)}
    assert len(ids) == 1 and len(_logged(db)) == 3
