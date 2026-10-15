"""Gmail and Google Workspace mailboxes as sources (feeds.py, gmail.py, google_auth.py): signed in as a connected Google
account (its refresh token), or as a Workspace service account; read through the Gmail API or over IMAP.
Everything runs against a fake Google (tests/fake_gmail.py) and the fake IMAP server, with no real accounts."""

from __future__ import annotations

import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.domain import convert, feeds, gmail, google_auth, sources, store
from tests import fake_imap
from tests.api.test_mail_calendar_sources import BUOYS, REPORT, _email
from tests.fake_gmail import GMAIL_READ, MAIL, Google
from tests.helpers import drain, login, make_user

R = store.R
MARA = "mara@harbour.example"


@pytest.fixture
def google(monkeypatch):
    g = Google(address=MARA)
    monkeypatch.setattr(google_auth, "TOKEN_URL", g.token_uri)
    monkeypatch.setattr(gmail, "API", g.api)
    google_auth._TOKENS.clear()
    gmail._META.clear()
    yield g
    g.close()


@pytest.fixture
def admin(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    return login(client, "root@x.io", "root password 1")


@pytest.fixture
def as_text(monkeypatch):
    monkeypatch.setattr(convert, "soffice", lambda cfg: None)  # no converters: emails are read as text
    monkeypatch.setattr(convert, "chromium", lambda cfg: None)


def _key(google, scopes=(GMAIL_READ,), domain="harbour.example"):
    """A service account's JSON key, which the fake Google knows and lets use `scopes` for people of `domain`."""
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    who = "lens-reader@harbour-project.iam.gserviceaccount.com"
    google.service_accounts[who] = {"public_key": k.public_key(), "scopes": list(scopes), "domain": domain}
    return json.dumps(
        {"type": "service_account", "client_email": who, "client_id": "1234567890", "private_key_id": "k1", "private_key": pem}
    )


def _service_account_source(client, admin, google, user=MARA, **kw):
    body = {
        "name": "Mara's mail",
        "type": "gmail",
        "params": {"user": user},
        "secrets": {"service_account_credentials": _key(google, **kw)},
    }
    r = client.post("/api/v1/sources", headers=admin, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _gets(google, path_part):
    return [q for m, p, q in google.requests if m == "GET" and path_part in p]


def test_a_workspace_mailbox_through_the_gmail_api(client, db, cfg, admin, google, as_text, monkeypatch):
    report = google.add(REPORT, ["INBOX", "CATEGORY_PERSONAL", "UNREAD"], at=1790000000000)
    buoys = google.add(BUOYS, ["INBOX", "Label_7"], at=1790000100000)
    google.add(_email("Draft", "Not yet."), ["DRAFT"], at=1790000200000)
    google.add(_email("Spam", "Win a boat."), ["SPAM"], at=1790000300000)
    made = _service_account_source(client, admin, google)
    assert made["health"]["ok"], made
    sid = made["id"]
    src = client.get("/api/v1/sources", headers=admin).json()[0]
    assert (src["type"], src["oauth"], src["params"]) == ("gmail", None, {"user": MARA, "via": "api"})
    assert src["secrets"]["service_account_credentials"]["set"] and "PRIVATE KEY" not in json.dumps(src)

    # labels are folders (Gmail's own by the names its web app shows); a label's messages are files
    boxes = client.get(f"/api/v1/sources/{sid}/browse", headers=admin).json()
    assert [(b["path"], b["name"], b["dir"]) for b in boxes] == [
        ("INBOX", "Inbox", True),
        ("SENT", "Sent", True),
        ("CATEGORY_PERSONAL", "Primary", True),
        ("Label_7", "Harbour", True),
    ]
    inbox = client.get(f"/api/v1/sources/{sid}/browse", params={"path": "INBOX"}, headers=admin).json()
    assert [(e["path"], e["name"], e["title"]) for e in inbox] == [
        (f"INBOX/{buoys}.eml", "Buoys.eml", "Buoys"),
        (f"INBOX/{report}.eml", "Harbour report.eml", "Harbour report"),
    ]
    assert inbox[1]["when"] == "2026-09-29T09:30:00+01:00" and inbox[1]["modified"].startswith("2026-09-")
    assert client.get(f"/api/v1/sources/{sid}/browse", params={"path": "Nope"}, headers=admin).status_code == 400
    # counting what a watch would take reads no message's headers
    google.requests.clear()
    r = client.post("/api/v1/watches/preview", headers=admin, json={"source": sid, "path": "INBOX"})
    assert r.json()["transcripts"] == 2 and not _gets(google, "/messages/")

    # the whole mailbox watched, leaving what's there: nothing old is read, the watch starts at Gmail's history now
    r = client.post("/api/v1/watches", headers=admin, json={"source": sid, "namespace": "pods", "stable_seconds": 600})
    wid = r.json()["id"]
    google.requests.clear()
    assert sources.poll_watch(db, cfg, wid) == {"seen": 0, "new": 0, "waiting": 0, "skipped": 0, "errors": 0}
    assert not _gets(google, "/messages") and [c["uid"] for c in db.one("SELECT cursor FROM $r", r=R("watch_path", wid))["cursor"]] == [
        google.history_id
    ]
    # what arrives after is taken at once (a message is whole when Gmail lists it), from the history alone
    tide = google.add(_email("Tide tables", "High water at six.", msgid="tide@example.org"), ["INBOX"])
    google.add(_email("Another draft", "Still not."), ["DRAFT"])
    google.requests.clear()
    assert sources.poll_watch(db, cfg, wid)["new"] == 1
    assert [q["format"] for q in _gets(google, "/messages/")] == [["metadata"], ["raw"]]  # the new one's headers, then it
    assert not [p for m, p, q in google.requests if p.endswith("/users/me/messages")]
    assert sources.poll_watch(db, cfg, wid)["new"] == 0
    drain(db, cfg)
    rec = db.one("SELECT record::id(id) AS id, title, source, status, remote FROM recording")
    assert (rec["title"], rec["source"], rec["status"]) == ("Tide tables", "transcript", "analyzed")
    assert rec["remote"] == {"source": sid, "path": f"{tide}.eml"}
    said = " ".join(s["text"] for s in db.rows("SELECT text FROM segment WHERE recording = $r", r=rec["id"]))
    assert "High water at six." in said and "From — Mara Keane (mara@example.org)" in said

    # a label watched with what's there, then a label put on an older message: that one comes too
    r = client.post(
        "/api/v1/watches",
        headers=admin,
        json={"source": sid, "path": "Label_7", "namespace": "calls", "stable_seconds": 0, "backfill": True},
    )
    lid = r.json()["id"]
    assert sources.poll_watch(db, cfg, lid)["new"] == 1  # the buoys
    google.label(report, "Label_7")
    assert sources.poll_watch(db, cfg, lid)["new"] == 1  # the report, labelled since

    # routing rules work on Gmail too: the mailbox a message is in is its labels
    rules = [{"match": {"mailbox": ["Harbour"]}, "namespace": "calls"}]
    rows = client.post("/api/v1/watches/routes/preview", headers=admin, json={"source": sid, "routes": rules, "namespace": "pods"}).json()
    assert {r["title"]: r["namespace"] for r in rows} == {"Tide tables": "pods", "Buoys": "calls", "Harbour report": "calls"}

    # Gmail forgot the history the cursor points into: what arrived since an hour before the last scan is read again,
    # and what the watch already has is skipped
    google.kept_from = google.history_id + 1
    google.add(_email("Late news", "The ferry is late."), ["INBOX"])
    assert sources.poll_watch(db, cfg, wid)["new"] == 1
    assert sources.poll_watch(db, cfg, wid)["new"] == 0

    # a message chosen by hand, though browsing a label shows only its newest
    monkeypatch.setattr(gmail, "BROWSE", 1)
    r = client.post(
        "/api/v1/import/source",
        headers=admin,
        json={"source": sid, "paths": [f"INBOX/{report}.eml", "INBOX/ffff.eml"], "namespace": "pods"},
    )
    assert [x["status"] for x in r.json()["results"]] == ["queued", "error"]
    drain(db, cfg)
    assert {r["title"] for r in db.rows("SELECT title FROM recording WHERE space = $s", s=store.ns_id(db, "pods", create=False))} == {
        "Tide tables",
        "Late news",
        "Harbour report",
    }

    # nothing in Gmail was changed: only reads, read only
    assert {m for m, p, q in google.requests if p != "/token"} == {"GET"}
    assert all(t["scope"] == GMAIL_READ for t in google.access.values())


def test_what_google_says_when_a_service_account_cant_read(client, admin, google):
    h = _service_account_source(client, admin, google, scopes=())["health"]
    assert (
        not h["ok"]
        and "1234567890" in h["error"]
        and "domain-wide delegation for https://www.googleapis.com/auth/gmail.readonly" in h["error"]
    )
    h = _service_account_source(client, admin, google, user="ann@elsewhere.example")["health"]
    assert not h["ok"] and "can't act as ann@elsewhere.example" in h["error"]
    h = _service_account_source(client, admin, google, user="")["health"]
    assert not h["ok"] and "enter their address" in h["error"]
    r = client.post("/api/v1/sources", headers=admin, json={"type": "gmail", "params": {"via": "pop"}})
    assert r.status_code == 400 and "via is one of" in r.text


def _account(google, refresh, scope=GMAIL_READ):
    """A Google account's authorized user JSON (as gcloud writes it), its refresh token known to the fake Google."""
    google.refresh[refresh] = {"email": MARA, "scope": scope, "client_id": google.client_id}
    return json.dumps(
        {"type": "authorized_user", "client_id": google.client_id, "client_secret": google.client_secret, "refresh_token": refresh}
    )


def test_a_connected_google_account(client, db, cfg, admin, google, monkeypatch):
    r = client.post("/api/v1/sources", headers=admin, json={"type": "gmail", "name": "My mail"})
    sid = r.json()["id"]
    assert "connect a Google account, or upload a service account key" in r.json()["health"]["error"]

    # connected: the refresh token kept sealed with its client, the mailbox read through the Gmail API
    r = client.patch(
        f"/api/v1/sources/{sid}", headers=admin, json={"params": {"user": MARA}, "secrets": {"token": _account(google, "1//read")}}
    )
    assert r.json()["health"]["ok"], r.text
    src = client.get("/api/v1/sources", headers=admin).json()[0]
    assert src["secrets"]["token"]["set"] and "1//read" not in json.dumps(src)
    assert json.loads(google_auth.secret(cfg, sources.get(db, sid), "token"))["refresh_token"] == "1//read"
    google.add(REPORT)
    assert [e["title"] for e in client.get(f"/api/v1/sources/{sid}/browse", params={"path": "INBOX"}, headers=admin).json()] == [
        "Harbour report"
    ]

    # read over IMAP instead: that needs all of Gmail, which this account wasn't given
    r = client.patch(f"/api/v1/sources/{sid}", headers=admin, json={"params": {"via": "imap"}})
    assert r.json()["health"]["error"].endswith("connect the Google account again")
    imap = fake_imap.start(
        {"INBOX": [(11, REPORT, "29-Sep-2026 09:31:02 +0100")]}, user=MARA, token=lambda t: MAIL in google.access[t]["scope"]
    )
    try:
        monkeypatch.setattr(feeds, "GMAIL_IMAP", ("127.0.0.1", str(imap.port), "none"))
        r = client.patch(f"/api/v1/sources/{sid}", headers=admin, json={"secrets": {"token": _account(google, "1//all", MAIL)}})
        assert r.json()["health"]["ok"], r.text
        assert [e["path"] for e in client.get(f"/api/v1/sources/{sid}/browse", params={"path": "INBOX"}, headers=admin).json()] == [
            "INBOX/7-11.eml"
        ]
        assert any("AUTHENTICATE XOAUTH2" in c for c in imap.commands) and not any("LOGIN" in c for c in imap.commands)
    finally:
        imap.shutdown()

    # revoked in the Google account: the source says to connect it again
    google.refused_refresh = True
    google_auth._TOKENS.clear()
    h = client.post(f"/api/v1/sources/{sid}/test", headers=admin).json()
    assert not h["ok"] and "Connect the Google account again" in h["error"]


def test_an_agent_gives_a_token_it_has(client, admin, google):
    google.refresh["1//agent"] = {"email": MARA, "scope": GMAIL_READ, "client_id": google.client_id}
    token = {"refresh_token": "1//agent", "client_id": google.client_id, "client_secret": google.client_secret}
    body = {"type": "gmail", "params": {"user": MARA}, "secrets": {"token": json.dumps(token)}}
    r = client.post("/api/v1/sources", headers=admin, json=body)
    assert r.status_code == 200 and r.json()["health"]["ok"], r.text
    bad = client.post("/api/v1/sources", headers=admin, json={**body, "secrets": {"token": '{"access_token": "x"}'}})
    assert bad.status_code == 400 and "client_id, client_secret and refresh_token" in bad.text
