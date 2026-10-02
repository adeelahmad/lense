"""Notifications (docs/notifications.md): a namespace's owners add webhooks and Matterbridge gateways; the notifier
reads what happened and sends it, signed, once, with retries. Every target here is a server on this machine, which
the tests allow through notifications.networks."""

from __future__ import annotations

import base64
import hashlib
import hmac
import http.server
import json
import threading
import time

import pytest

from app.domain import notify, store
from tests.helpers import login, make_user, seed

R = store.R
URL = "/api/v1/namespaces/pods/notifications"


class Inbox:
    """A local HTTP server that keeps what it's sent and answers with `status`."""

    def __init__(self):
        self.got, self.status = [], 200
        inbox = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 - the stdlib's name
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                inbox.got.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
                self.send_response(inbox.status)
                self.end_headers()
                self.wfile.write(b"nope" if inbox.status >= 400 else b"ok")

            def log_message(self, *a):
                pass

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def json(self, i=-1):
        return json.loads(self.got[i]["body"])


@pytest.fixture
def inbox():
    i = Inbox()
    yield i
    i.srv.shutdown()


@pytest.fixture
def env(app, db, cfg, folder, client):
    ids = seed(db, cfg, folder)
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "own@x.io", "owner password 1", roles={"pods": "owner"})
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    hr = login(client, "root@x.io", "root password 1")
    assert client.put("/api/v1/settings/notifications", json={"networks": ["127.0.0.0/8"]}, headers=hr).status_code == 200
    return {
        "ids": ids,
        "pods": store.ns_id(db, "pods"),
        "cfg": app.state.settings.current(),
        "hr": hr,
        "ho": login(client, "own@x.io", "owner password 1"),
        "he": login(client, "ed@x.io", "editor password 1"),
    }


def verify(secret, headers, body):
    """What a receiver does: the Standard Webhooks check, written out from the spec."""
    key = base64.b64decode(secret.split("_", 1)[1])
    signed = f"{headers['webhook-id']}.{headers['webhook-timestamp']}.".encode() + body
    want = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    return any(s.split(",", 1)[1] == want for s in headers["webhook-signature"].split())


def begin(db, cfg):
    """The notifier's first look, then the next second: what happens in the second it started in isn't sent."""
    notify.scan(db, cfg)
    start = store.now()
    while store.now() == start:
        time.sleep(0.05)


def finish_job(db, rid, status="succeeded", batch=None, error=None):
    jid = db.next_id("job")
    t = store.now()
    rec = db.one("SELECT space FROM $r", r=R("recording", rid))
    db.q(
        "CREATE $r CONTENT $d",
        r=R("job", jid),
        d=store.clean(
            {
                "recording": rid,
                "space": rec["space"],
                "steps": [{"type": "transcribe"}, {"type": "analyze"}],
                "step_index": 1 if status == "failed" else 2,
                "status": status,
                "error": error,
                "batch": batch,
                "created_at": t,
                "updated_at": t,
                "finished_at": t,
            }
        ),
    )
    return jid


def test_addresses_and_settings(client, env):
    assert notify.check_url("http://mb:4242", "matterbridge") == "http://mb:4242/api/message"
    assert notify.check_url("http://mb:4242/api/message", "matterbridge") == "http://mb:4242/api/message"
    for bad in ("", "ftp://x.io/", "http://u:p@x.io/", "https://x.io/#a", "x.io/hook", "http://x.io:99999/"):
        with pytest.raises(ValueError):
            notify.check_url(bad, "webhook")
    assert notify._hint("https://hooks.slack.com/services/T0/B0/secret") == "https://hooks.slack.com/…"
    h = env["hr"]
    for bad in ({"networks": ["nonsense"]}, {"poll_seconds": 0}, {"app_url": "ftp://x"}, {"enabled": "yes"}):
        assert client.put("/api/v1/settings/notifications", json=bad, headers=h).status_code == 400, bad
    assert client.put("/api/v1/settings/notifications", json={"app_url": "https://lens.example/"}, headers=h).status_code == 200
    assert notify.app_url(client.app.state.settings.current()) == "https://lens.example"


def test_owners_manage_targets_and_a_test_is_signed(client, db, env, inbox):
    body = {"name": "Hooks", "kind": "webhook", "url": inbox.url + "/hook?k=v", "events": ["job.failed"]}
    assert client.post(URL, json=body, headers=env["he"]).status_code == 403  # editors don't
    r = client.post(URL, json=body, headers=env["ho"])
    assert r.status_code == 200, r.text
    secret, t = r.json()["secret"], r.json()["target"]
    assert secret.startswith("whsec_") and t["secret_set"] and t["url"] == inbox.url + "/…"
    assert client.post(URL, json={**body, "name": "hooks"}, headers=env["ho"]).status_code == 400  # names are unique
    listed = client.get(URL, headers=env["ho"]).json()
    assert [x["name"] for x in listed["targets"]] == ["Hooks"] and listed["enabled"]
    assert "k=v" not in json.dumps(listed) and "whsec" not in json.dumps(listed)  # the address and secret stay sealed

    r = client.post(f"{URL}/{t['id']}/test", headers=env["ho"])
    assert r.json() == {"ok": True, "code": 200, "error": None}
    got = inbox.got[-1]
    assert got["path"] == "/hook?k=v" and verify(secret, got["headers"], got["body"])
    assert inbox.json()["type"] == "test" and inbox.json()["namespace"] == "pods"

    new = client.post(f"{URL}/{t['id']}/secret", headers=env["ho"]).json()["secret"]
    client.post(f"{URL}/{t['id']}/test", headers=env["ho"])
    assert verify(new, inbox.got[-1]["headers"], inbox.got[-1]["body"]) and not verify(
        secret, inbox.got[-1]["headers"], inbox.got[-1]["body"]
    )

    r = client.patch(f"{URL}/{t['id']}", json={"events": ["job.succeeded", "batch.finished"], "enabled": False}, headers=env["ho"])
    assert r.json()["events"] == ["job.succeeded", "batch.finished"] and r.json()["enabled"] is False
    assert client.patch(f"{URL}/{t['id']}", json={"events": ["nope"]}, headers=env["ho"]).status_code == 422
    log = client.get(f"{URL}/{t['id']}/deliveries", headers=env["ho"]).json()
    assert [d["event"] for d in log] == ["test", "test"] and log[0]["status"] == "sent"
    assert client.delete(f"{URL}/{t['id']}", headers=env["ho"]).status_code == 200
    assert client.get(URL, headers=env["ho"]).json()["targets"] == []
    acts = {a["action"] for a in db.rows("SELECT action FROM audit_log")}
    assert {"namespace.notification.create", "namespace.notification.secret", "namespace.notification.delete"} <= acts


def test_private_addresses_need_an_admin(client, env, inbox):
    client.put("/api/v1/settings/notifications", json={"networks": []}, headers=env["hr"])
    t = client.post(URL, json={"name": "Local", "kind": "slack", "url": inbox.url}, headers=env["ho"]).json()["target"]
    r = client.post(f"{URL}/{t['id']}/test", headers=env["ho"]).json()
    assert not r["ok"] and "isn't a public address" in r["error"] and not inbox.got


def test_runs_that_end_are_sent_once_to_matterbridge(client, db, env, inbox):
    body = {
        "name": "Chat",
        "kind": "matterbridge",
        "url": inbox.url,
        "gateway": "lens",
        "token": "tok",
        "events": ["job.failed", "job.succeeded"],
    }
    assert client.post(URL, json={**body, "gateway": ""}, headers=env["ho"]).status_code == 400
    client.post(URL, json=body, headers=env["ho"])
    cfg = env["cfg"]
    begin(db, cfg)  # the first look only marks where to start
    a, b, call = env["ids"]
    finish_job(db, a, "failed", error="RuntimeError: no model")
    finish_job(db, b)
    finish_job(db, call, "failed")  # calls has no targets
    assert notify.scan(db, cfg) == 2
    assert notify.scan(db, cfg) == 0  # the lookback sees them again, but they were claimed
    notify._CLAIMED.clear()  # another process, with nothing claimed in memory
    assert notify.scan(db, cfg) == 0
    assert notify.deliver_due(db, cfg) == 2 and notify.deliver_due(db, cfg) == 0
    msgs = sorted((inbox.json(i) for i in range(2)), key=lambda m: m["text"])
    assert all(g["path"] == "/api/message" and g["headers"]["authorization"] == "Bearer tok" for g in inbox.got)
    assert msgs[0]["gateway"] == "lens" and msgs[0]["username"] == "Lens"
    assert "failed at analyze in pods: RuntimeError: no model" in " ".join(m["text"] for m in msgs)
    assert "finished processing in pods" in " ".join(m["text"] for m in msgs)


def test_failures_are_retried_and_refusals_are_not(client, db, env, inbox):
    t = client.post(URL, json={"name": "Hook", "kind": "webhook", "url": inbox.url, "events": ["job.failed"]}, headers=env["ho"]).json()
    cfg = env["cfg"]
    begin(db, cfg)
    finish_job(db, env["ids"][0], "failed")
    notify.scan(db, cfg)
    inbox.status = 503
    notify.deliver_due(db, cfg)
    d = db.rows("SELECT status, attempts, error, next_at FROM notify_delivery")[0]
    assert d["status"] == "pending" and d["attempts"] == 1 and d["error"].startswith("HTTP 503") and d["next_at"] > store.now()
    assert notify.deliver_due(db, cfg) == 0  # not due yet
    db.q("UPDATE notify_delivery SET next_at = $t", t=store.now())
    inbox.status = 200
    notify.deliver_due(db, cfg)
    d = db.rows("SELECT status, attempts, key FROM notify_delivery")[0]
    assert d["status"] == "sent" and d["attempts"] == 2
    assert len({g["headers"]["webhook-id"] for g in inbox.got}) == 1  # a retry is the same message to the receiver
    last = client.get(URL, headers=env["ho"]).json()["targets"][0]["last"]
    assert last["ok"] and last["code"] == 200

    finish_job(db, env["ids"][1], "failed")
    notify.scan(db, cfg)
    inbox.status = 410
    notify.deliver_due(db, cfg)
    log = client.get(f"{URL}/{t['target']['id']}/deliveries", headers=env["ho"]).json()
    gone = next(d for d in log if d["code"] == 410)  # both came in the same second: either may be listed first
    assert gone["status"] == "failed" and gone["attempts"] == 1 and len(log) == 2


def test_batch_runs_and_additions_are_told_as_one(client, db, env, inbox, folder, cfg):
    from app.domain import ingest

    client.post(URL, json={"name": "All", "kind": "discord", "url": inbox.url, "events": list(notify.EVENTS)}, headers=env["ho"])
    c = env["cfg"]
    begin(db, c)
    a, b, _ = env["ids"]
    bid = db.next_id("batch")
    db.q("CREATE $r CONTENT $d", r=R("batch", bid), d={"label": "Summaries", "status": "running", "started": [a, b], "recordings": [a, b]})
    finish_job(db, a, batch=bid)
    finish_job(db, b, "failed", batch=bid)
    for i in range(7):
        p = folder / f"new{i}.txt"
        p.write_text(f"Alice|N|Item {i} is here.")
        ingest.import_transcript(db, cfg, "pods", p, title=f"Item {i}", log=lambda *_: None)
    notify.scan(db, c)
    notify.deliver_due(db, c)
    texts = sorted(inbox.json(i)["content"] for i in range(len(inbox.got)))
    assert len(texts) == 2, texts  # no message per job of the batch run, nor per item added
    assert texts[0].startswith("7 items were added to pods, among them “Item 0”")
    assert texts[1].startswith("Batch run “Summaries” finished in pods: 1 done, 1 failed.")


def test_a_batch_run_is_counted_per_namespace(client, db, env, inbox):
    client.post(URL, json={"name": "Batches", "kind": "slack", "url": inbox.url, "events": ["batch.finished"]}, headers=env["ho"])
    c = env["cfg"]
    begin(db, c)
    a, b, call = env["ids"]
    bid = db.next_id("batch")
    db.q(
        "CREATE $r CONTENT $d",
        r=R("batch", bid),
        d={"label": "All", "status": "running", "started": [a, b, call], "recordings": [a, b, call]},
    )
    finish_job(db, a, batch=bid)
    finish_job(db, call, "failed", batch=bid)  # in calls, which pods' owners don't see
    finish_job(db, b, batch=bid)
    notify.scan(db, c)
    notify.deliver_due(db, c)
    assert len(inbox.got) == 1 and inbox.json()["text"].startswith("Batch run “All” finished in pods: 2 done.")


def test_a_busy_minute_is_read_page_by_page(client, db, env, inbox, monkeypatch):
    monkeypatch.setattr(notify, "PAGE", 3)
    client.post(URL, json={"name": "Hook", "kind": "slack", "url": inbox.url, "events": ["job.failed"]}, headers=env["ho"])
    c = env["cfg"]
    begin(db, c)
    a, b, _ = env["ids"]
    for _ in range(8):  # more than a page in the same second
        finish_job(db, a, "failed")
    assert notify.scan(db, c) == 3 and notify.scan(db, c) == 3 and notify.scan(db, c) == 2
    finish_job(db, b, "failed")  # and what comes after them still goes
    assert notify.scan(db, c) == 1 and notify.scan(db, c) == 0
    assert db.one("SELECT count() AS n FROM notify_delivery GROUP ALL")["n"] == 9


def test_an_event_isnt_lost_when_queueing_it_fails(client, db, env, inbox, monkeypatch):
    client.post(URL, json={"name": "Hook", "kind": "slack", "url": inbox.url, "events": ["job.failed"]}, headers=env["ho"])
    c = env["cfg"]
    begin(db, c)
    finish_job(db, env["ids"][0], "failed")
    real = db.run

    def broken(*a, **k):
        raise RuntimeError("the database went away")

    monkeypatch.setattr(db, "run", broken)
    with pytest.raises(RuntimeError):
        notify.scan(db, c)
    assert not db.rows("SELECT id FROM notify_event") and not db.rows("SELECT id FROM notify_delivery")  # nothing half done
    monkeypatch.setattr(db, "run", real)
    assert notify.scan(db, c) == 1 and notify.scan(db, c) == 0


def test_a_slow_target_is_cut_off(env):
    import socket

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()

    def trickle():
        conn, _ = srv.accept()
        conn.recv(65536)
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 1000\r\n\r\n")
        for _ in range(40):  # a byte every quarter second: never quiet long enough for the read timeout
            try:
                conn.sendall(b"x")
            except OSError:
                break
            time.sleep(0.25)
        conn.close()

    threading.Thread(target=trickle, daemon=True).start()
    t0 = time.monotonic()
    with pytest.raises(TimeoutError):
        notify.post(env["cfg"], f"http://127.0.0.1:{srv.getsockname()[1]}/", b"{}", {}, timeout=1, deadline=2)
    assert time.monotonic() - t0 < 5
    srv.close()
