"""Routing rules on a watched email account: a shared inbox's messages sent to different namespaces, or skipped, by
who sent them, who they were for, their subject, list or mailbox. Runs against the fake IMAP server."""

from __future__ import annotations

from email.message import EmailMessage

import pytest

from app.domain import convert, mail_routes, sources, store
from tests import fake_imap
from tests.helpers import login, make_user

R = store.R


def _mail(subject, sender, to, uid, *, cc=None, delivered=None, listed=None):
    m = EmailMessage()
    m["Subject"], m["From"], m["To"], m["Date"] = subject, sender, to, "Tue, 29 Sep 2026 09:30:00 +0100"
    m["Message-ID"] = f"<{uid}@example.org>"
    if cc:
        m["Cc"] = cc
    if delivered:
        m["Delivered-To"] = delivered
    if listed:
        m["List-Id"] = listed
    m.set_content(f"Message {uid}.")
    return (uid, m.as_bytes(), f"{uid:02d}-Sep-2026 09:31:02 +0100")


def _inbox():
    return {
        "INBOX": [
            _mail("Invoice 2231", "Billing <billing@acme.com>", "info@harbour.org", 1),
            _mail("Ticket: lamp broken", "Ana <ana@gmail.com>", "Help Desk <support@harbour.org>", 2),
            _mail("Lunch?", "Tom <tom@harbour.org>", "info@harbour.org", 3, cc="Mara <mara@harbour.org>"),
            _mail("Weekly digest", "news@lists.example", "info@harbour.org", 4, listed="Harbour news <news.lists.example>"),
            _mail("Forwarded", "Ana <ana@gmail.com>", "someone@else.org", 5, delivered="support@harbour.org"),
        ],
        "Archive": [_mail("Old invoice", "billing@acme.com", "info@harbour.org", 6)],
    }


@pytest.fixture
def imap():
    srv = fake_imap.start(_inbox())
    yield srv
    srv.shutdown()


@pytest.fixture
def admin(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    return login(client, "root@x.io", "root password 1")


@pytest.fixture
def mail(client, db, admin, imap, monkeypatch):
    monkeypatch.setattr(convert, "soffice", lambda cfg: None)  # no converters: emails are read as text
    monkeypatch.setattr(convert, "chromium", lambda cfg: None)
    params = {"host": "127.0.0.1", "port": str(imap.port), "security": "none", "user": "lens"}
    r = client.post(
        "/api/v1/sources", headers=admin, json={"name": "shared", "type": "imap", "params": params, "secrets": {"pass": "mail password 1"}}
    )
    assert r.status_code == 200, r.text
    for ns in ("finance", "support", "family"):
        store.ns_id(db, ns)
    return r.json()["id"]


RULES = [
    {"name": "Bills", "match": {"from": "*@acme.com"}, "namespace": "finance"},
    {"match": {"to": "support@"}, "namespace": "support"},
    {"match": {"list": "news.lists.example"}, "skip": True},
]


def _where(db):
    names = store.space_names(db)
    return {r["title"]: names[r["space"]] for r in db.rows("SELECT title, space FROM recording")}


def test_a_shared_inbox_routed_to_namespaces(client, db, cfg, admin, mail, imap):
    h = admin
    # before saving: the latest messages, and where each would go
    r = client.post(
        "/api/v1/watches/routes/preview", headers=h, json={"source": mail, "path": "INBOX", "namespace": "family", "routes": RULES}
    )
    assert r.status_code == 200, r.text
    shown = {m["title"]: (m["namespace"], m["skipped"], m["rule"]) for m in r.json()}
    assert shown == {
        "Invoice 2231": ("finance", False, 1),
        "Ticket: lamp broken": ("support", False, 2),
        "Lunch?": ("family", False, None),  # no rule: the watch's namespace
        "Weekly digest": (None, True, 3),
        "Forwarded": ("support", False, 2),  # received at the shared alias
    }
    lunch = next(m for m in r.json() if m["title"] == "Lunch?")
    assert lunch["from"] == ["tom@harbour.org"] and lunch["to"] == ["info@harbour.org", "mara@harbour.org"]

    r = client.post(
        "/api/v1/watches",
        headers=h,
        json={"source": mail, "path": "INBOX", "namespace": "family", "stable_seconds": 0, "backfill": True, "routes": RULES},
    )
    assert r.status_code == 200, r.text
    wid = r.json()["id"]
    watch = next(w for w in client.get("/api/v1/watches", headers=h).json() if w["id"] == wid)
    assert watch["routes"][0] == {"name": "Bills", "match": {"from": ["*@acme.com"]}, "namespace": "finance", "skip": False}
    assert watch["routes"][2]["skip"] and watch["routes"][2]["namespace"] is None

    assert sources.poll_watch(db, cfg, wid) == {"seen": 5, "new": 4, "waiting": 0, "skipped": 1, "errors": 0, "routed": 3}
    assert _where(db) == {"Invoice 2231": "finance", "Ticket: lamp broken": "support", "Lunch?": "family", "Forwarded": "support"}
    skipped = db.one("SELECT status, rule FROM remote_file WHERE path = 'INBOX/7-4.eml'")
    assert skipped == {"status": "skipped", "rule": 3}

    # rules apply to mail that comes after a change; a namespace deleted since sends its mail home
    r = client.patch(
        f"/api/v1/watches/{wid}", headers=h, json={"routes": [{"match": {"subject": ["door", "window"]}, "namespace": "finance"}]}
    )
    assert r.status_code == 200, r.text
    imap.mailboxes["INBOX"].append(_mail("Ticket: door", "x@y.org", "info@harbour.org", 7))
    imap.mailboxes["INBOX"].append(_mail("Invoice 9", "billing@acme.com", "info@harbour.org", 8))
    assert sources.poll_watch(db, cfg, wid)["routed"] == 1
    assert _where(db)["Ticket: door"] == "finance" and _where(db)["Invoice 9"] == "family"


def test_rules_are_checked(client, admin, mail):
    h = admin
    base = {"source": mail, "path": "INBOX", "namespace": "family"}
    for routes, said in [
        ([{"match": {}, "namespace": "finance"}], "at least one condition"),
        ([{"match": {"from": "a"}}], "needs a namespace, or skip"),
        ([{"match": {"from": "a"}, "namespace": "nowhere"}], "no namespace nowhere"),  # rules never create namespaces
        ([{"match": {"from": "a"}, "namespace": "finance", "skip": True}], "not both"),
        ([{"match": {"from": [" "]}, "namespace": "finance"}], "between 1 and"),
    ]:
        r = client.post("/api/v1/watches", headers=h, json={**base, "routes": routes})
        assert r.status_code == 400 and said in r.json()["detail"], (routes, r.text)
    r = client.post("/api/v1/watches", headers=h, json={**base, "routes": [{"match": {"sender": "a"}, "namespace": "finance"}]})
    assert r.status_code == 422  # not a field rules look at


def test_rules_only_on_email_watches(db):
    store.ns_id(db, "finance")
    with pytest.raises(ValueError, match="email account"):
        sources._routes(db, {"type": "local"}, {"routes": [{"match": {"from": "a"}, "namespace": "finance"}]})
    assert sources._routes(db, {"type": "local"}, {"routes": []}) == {"routes": []}  # none is fine anywhere


def test_matching():
    rule = {"match": {"from": ["*@acme.com", "boss@"], "subject": ["invoice"]}}
    mail = {"from": ["billing@ACME.com"], "to": [], "subject": "Your Invoice", "list": "", "mailbox": "INBOX"}
    assert mail_routes.matches(rule, mail)
    assert not mail_routes.matches(rule, {**mail, "subject": "Hello"})  # every condition must match
    assert mail_routes.matches(rule, {**mail, "from": ["boss@harbour.org"]})  # any pattern of one may
    assert not mail_routes.matches(rule, {**mail, "from": ["billing@acme.com.evil.io"]})  # a pattern with * matches whole
    assert mail_routes.route([], mail, 5, {5}) == (5, None)
    assert mail_routes.route([{**rule, "space": 9}], mail, 5, {5}) == (5, 1)  # namespace deleted: home
    assert mail_routes.route([{**rule, "space": 9}], None, 5, {5, 9}) == (5, None)  # not an email: home
