"""Email accounts (IMAP) and calendar feeds (iCal) as sources: their messages and events shown as files, browsed,
watched and imported like a storage source's. Everything runs against local fixtures: a calendar served over HTTP
from this process, and a fake IMAP server (tests/fake_imap.py)."""

from __future__ import annotations

import base64
import datetime as dt
import http.server
import threading
from email.message import EmailMessage

import pytest

from app.domain import calendars, convert, feeds, ingest, sources, store
from tests import fake_imap
from tests.api.test_documents import HARBOUR
from tests.helpers import chromium_binary, drain, login, make_user, text_pdf

R = store.R

CALENDAR = """BEGIN:VCALENDAR\r
VERSION:2.0\r
PRODID:-//Example//Team calendar//EN\r
X-WR-CALNAME:Harbour team\r
BEGIN:VTIMEZONE\r
TZID:Europe/Berlin\r
BEGIN:STANDARD\r
DTSTART:19701025T030000\r
TZOFFSETFROM:+0200\r
TZOFFSETTO:+0100\r
END:STANDARD\r
END:VTIMEZONE\r
BEGIN:VEVENT\r
UID:standup-1@example.org\r
DTSTAMP:{stamp}\r
LAST-MODIFIED:{changed}\r
DTSTART;TZID=Europe/Berlin:20261002T100000\r
DTEND;TZID=Europe/Berlin:20261002T103000\r
SUMMARY:Harbour stand-up\r
LOCATION:Pier 4\\, meeting room\r
ORGANIZER;CN=Mara Keane:mailto:mara@example.org\r
ATTENDEE;CN=Tom Byrne;PARTSTAT=ACCEPTED:mailto:tom@example.org\r
ATTENDEE:mailto:ann@example.org\r
RRULE:FREQ=WEEKLY;BYDAY=FR\r
DESCRIPTION:{description}\r
BEGIN:VALARM\r
ACTION:DISPLAY\r
TRIGGER:-PT10M\r
END:VALARM\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:standup-1@example.org\r
RECURRENCE-ID;TZID=Europe/Berlin:20261009T100000\r
DTSTAMP:{stamp}\r
DTSTART;TZID=Europe/Berlin:20261009T113000\r
DTEND;TZID=Europe/Berlin:20261009T120000\r
SUMMARY:Harbour stand-up (moved)\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:regatta@example.org\r
DTSTAMP:{stamp}\r
CREATED:20260901T080000Z\r
DTSTART;VALUE=DATE:20261017\r
DTEND;VALUE=DATE:20261019\r
SUMMARY:Autumn regatta\r
DESCRIPTION:Two days on the water. Bring the lighthouse logbook.\r
END:VEVENT\r
END:VCALENDAR\r
"""
LONG = (
    "Agenda: the night crossing\\, the new buoys and the keeper's report. The lighthouse keeper wrote twice about the"
    "\r\n  fog.\\nAnything else goes at the end."
)


def _calendar(description=LONG, changed="20260925T120000Z", stamp=None):
    stamp = stamp or dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")  # a new one every time, as servers do
    return CALENDAR.format(stamp=stamp, changed=changed, description=description)


def test_reading_a_calendar():
    cal = calendars.parse(_calendar())
    assert cal["name"] == "Harbour team" and list(cal["zones"]) == ["Europe/Berlin"]
    standup, moved, regatta = cal["events"]
    assert calendars.start(standup) == dt.datetime(2026, 10, 2, 10, 0, tzinfo=calendars._zone("Europe/Berlin"))
    assert calendars.iso(calendars.start(standup)) == "2026-10-02T10:00:00+02:00"
    assert calendars.iso(calendars.start(regatta)) == "2026-10-17T00:00:00+00:00"
    # an occurrence that was moved is an event of its own; the same event reads the same whatever its DTSTAMP
    assert len({calendars.file_id(e) for e in cal["events"]}) == 3
    again = calendars.parse(_calendar(stamp="20300101T000000Z"))["events"][0]
    assert calendars.file_id(again) == calendars.file_id(standup)
    assert calendars.event_ics(cal, again) == calendars.event_ics(cal, standup)
    one = calendars.event_ics(cal, standup)
    assert "DTSTAMP" not in one and "BEGIN:VTIMEZONE" in one and "BEGIN:VALARM" in one
    assert calendars.parse(one)["events"][0]["props"] == [p for p in standup["props"] if p[0] != "DTSTAMP"]

    text = calendars.event_text(standup)
    assert text.startswith("# Harbour stand-up\n")
    assert "When — Friday 2 October 2026, 10:00 to 10:30 (Europe/Berlin)" in text
    assert "Where — Pier 4, meeting room" in text
    assert "Organizer — Mara Keane (mara@example.org)" in text
    assert "Attendees — Tom Byrne (tom@example.org), ann@example.org" in text
    assert "Repeats — FREQ=WEEKLY;BYDAY=FR" in text
    assert "The lighthouse keeper wrote twice about the fog.\nAnything else goes at the end." in text
    assert "When — Saturday 17 October 2026 to Sunday 18 October 2026 (all day)" in calendars.event_text(regatta)
    # read as a transcript, the details aren't taken for speakers, and the title is the event's
    t = ingest.read_text_transcript(text, "markdown")
    assert t["title"] == "Harbour stand-up" and not any(s.get("speaker") for s in t["segments"])


class _Feed(http.server.BaseHTTPRequestHandler):
    calendar = ""
    auth = "Basic " + base64.b64encode(b"lens:feed password 1").decode()
    hits = 0

    def do_GET(self):
        type(self).hits += 1
        if self.headers.get("Authorization") != self.auth:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="cal"')
            self.end_headers()
            return
        body = _Feed.calendar.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/calendar; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def feed():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Feed)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Feed.calendar, _Feed.hits = _calendar(), 0
    feeds._FEEDS.clear()
    yield f"http://127.0.0.1:{srv.server_address[1]}/team.ics"
    srv.shutdown()


@pytest.fixture
def admin(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    return login(client, "root@x.io", "root password 1")


def _segments(db, rid):
    return " ".join(s["text"] for s in db.rows("SELECT idx, text FROM segment WHERE recording = $r ORDER BY idx", r=rid))


def test_a_calendar_feed_as_a_source(client, db, cfg, admin, feed):
    h = admin
    assert set(client.get("/api/v1/sources/backends", headers=h).json()) >= {"imap", "ical"}
    r = client.post("/api/v1/sources", headers=h, json={"name": "team", "type": "ical", "params": {"url": feed, "user": "lens"}})
    assert r.status_code == 200 and r.json()["health"] == {**r.json()["health"], "ok": False}
    assert "401" in r.json()["health"]["error"]
    sid = r.json()["id"]
    r = client.patch(f"/api/v1/sources/{sid}", headers=h, json={"secrets": {"pass": "feed password 1"}})
    assert r.json()["health"]["ok"], r.text
    src = client.get("/api/v1/sources", headers=h).json()[0]
    assert (src["type"], src["label"], src["secrets"]) == ("ical", "Calendar feed (iCal)", {"pass": {"secret": True, "set": True}})

    entries = client.get(f"/api/v1/sources/{sid}/browse", headers=h).json()
    assert [e["name"] for e in entries] == [
        "2026-10-17 Autumn regatta.ics",
        "2026-10-09 Harbour stand-up (moved).ics",
        "2026-10-02 Harbour stand-up.ics",
    ]
    assert all(not e["dir"] and e["path"].endswith(".ics") for e in entries)
    standup = entries[2]
    assert (standup["title"], standup["when"], standup["modified"]) == (
        "Harbour stand-up",
        "2026-10-02T10:00:00+02:00",
        "2026-09-25T12:00:00+00:00",
    )
    assert client.get(f"/api/v1/sources/{sid}/browse", params={"path": "x"}, headers=h).status_code == 400

    # watched with backfill: every event becomes a text resource, titled and dated by the event
    r = client.post("/api/v1/watches", headers=h, json={"source": sid, "namespace": "pods", "stable_seconds": 0, "backfill": True})
    assert r.status_code == 200, r.text
    wid = r.json()["id"]
    assert client.post("/api/v1/watches/preview", headers=h, json={"source": sid}).json()["transcripts"] == 3
    assert sources.poll_watch(db, cfg, wid)["new"] == 3
    drain(db, cfg)
    recs = {r["title"]: r for r in db.rows("SELECT record::id(id) AS id, title, source, recorded_at, status, remote, path FROM recording")}
    assert set(recs) == {"Harbour stand-up", "Harbour stand-up (moved)", "Autumn regatta"}
    one = recs["Harbour stand-up"]
    assert (one["source"], one["recorded_at"], one["status"]) == ("transcript", "2026-10-02T10:00:00+02:00", "analyzed")
    assert one["remote"] == {"source": sid, "path": standup["path"]} and one["path"] == f"team:{standup['path']}"
    assert "Pier 4, meeting room" in _segments(db, one["id"]) and "keeper wrote twice about the fog" in _segments(db, one["id"])

    # fetched again (a new DTSTAMP): nothing new
    _Feed.calendar = _calendar(stamp="20300101T000000Z")
    feeds._FEEDS.clear()
    assert sources.poll_watch(db, cfg, wid)["new"] == 0
    # the event changed: read again into the same resource
    _Feed.calendar = _calendar("Agenda: the harbour master joins.", changed="20260930T090000Z")
    feeds._FEEDS.clear()
    assert sources.poll_watch(db, cfg, wid)["new"] == 1
    drain(db, cfg)
    assert len(db.rows("SELECT id FROM recording")) == 3
    assert "harbour master joins" in _segments(db, one["id"]) and "fog" not in _segments(db, one["id"])

    # chosen on purpose, from the listing: already there
    r = client.post("/api/v1/import/source", headers=h, json={"source": sid, "paths": [standup["path"]], "namespace": "pods"})
    assert r.json()["results"] == [{"path": standup["path"], "status": "already", "recording": one["id"], "job": None, "detail": None}]
    # the event's own .ics can be fetched (what a remote resource's file is)
    assert b"SUMMARY:Harbour stand-up" in b"".join(sources.stream(db, cfg, sid, standup["path"]))


def test_calendar_addresses():
    assert feeds._url({"params": {"url": "webcal://cal.example.org/team.ics"}}) == "https://cal.example.org/team.ics"
    for bad in ("", "ftp://x/y.ics", "/etc/passwd", "file:///etc/passwd"):
        with pytest.raises(RuntimeError):
            feeds._url({"params": {"url": bad}})


def test_a_calendar_file_imported_as_a_transcript(client, admin):
    data = base64.b64encode(_calendar().encode()).decode()
    r = client.post("/api/v1/import", headers=admin, json={"namespace": "pods", "filename": "team.ics", "data": data})
    assert r.status_code == 200, r.text


def _email(subject, body, attachment=False, date="Tue, 29 Sep 2026 09:30:00 +0100"):
    m = EmailMessage()
    m["Subject"], m["From"], m["To"], m["Date"] = subject, "Mara Keane <mara@example.org>", "tom@example.org", date
    m.set_content(body)
    if attachment:
        m.add_attachment(text_pdf(HARBOUR), maintype="application", subtype="pdf", filename="harbour.pdf")
    return m.as_bytes()


def _mailboxes():
    return {
        "INBOX": [
            (11, _email("Harbour report", "The ships arrived at dawn. The lighthouse keeper wrote twice."), "29-Sep-2026 09:31:02 +0100"),
            (12, _email("Buoys", "The new buoys are in."), "30-Sep-2026 14:00:00 +0000"),
        ],
        "Projects/Lighthouse": [(3, _email("Keeper's log", "Fog all night.", attachment=True), " 1-Oct-2026 07:15:00 +0000")],
        "Trash": [(1, _email("Old news", "Gone."), "01-Jan-2026 00:00:00 +0000")],
        "&AMk-quipe": [],  # "Équipe" in IMAP's modified UTF-7
    }


@pytest.fixture
def imap():
    srv = fake_imap.start(_mailboxes(), flags={"Trash": ["\\Trash"]})
    yield srv
    srv.shutdown()


def _imap_source(client, h, srv, password="mail password 1"):
    params = {"host": "127.0.0.1", "port": str(srv.port), "security": "none", "user": "lens"}
    r = client.post("/api/v1/sources", headers=h, json={"name": "mail", "type": "imap", "params": params, "secrets": {"pass": password}})
    assert r.status_code == 200, r.text
    return r.json()


def test_an_imap_account_as_a_source(client, db, cfg, admin, imap, monkeypatch):
    h = admin
    monkeypatch.setattr(convert, "soffice", lambda cfg: None)  # no converters: emails are read as text
    monkeypatch.setattr(convert, "chromium", lambda cfg: None)
    bad = _imap_source(client, h, imap, "wrong")
    assert not bad["health"]["ok"] and "refused to sign in" in bad["health"]["error"]
    sid = _imap_source(client, h, imap)["id"]
    assert client.post(f"/api/v1/sources/{sid}/test", headers=h).json()["ok"]

    # mailboxes are folders; messages are .eml files, named by their subjects
    boxes = client.get(f"/api/v1/sources/{sid}/browse", headers=h).json()
    assert [(b["path"], b["name"], b["dir"]) for b in boxes] == [
        ("INBOX", "INBOX", True),
        ("Projects/Lighthouse", "Projects/Lighthouse", True),
        ("Trash", "Trash", True),
        ("&AMk-quipe", "Équipe", True),
    ]
    inbox = client.get(f"/api/v1/sources/{sid}/browse", params={"path": "INBOX"}, headers=h).json()
    assert [(e["path"], e["name"], e["title"]) for e in inbox] == [
        ("INBOX/12.eml", "Buoys.eml", "Buoys"),
        ("INBOX/11.eml", "Harbour report.eml", "Harbour report"),
    ]
    assert inbox[1]["modified"] == "2026-09-29T09:31:02+01:00" and inbox[1]["when"] == "2026-09-29T09:30:00+01:00"
    assert client.get(f"/api/v1/sources/{sid}/browse", params={"path": "Nope"}, headers=h).status_code == 400
    assert client.get(f"/api/v1/sources/{sid}/browse", params={"path": "&AMk-quipe"}, headers=h).json() == []

    # the whole account watched: every mailbox but the bin
    r = client.post("/api/v1/watches", headers=h, json={"source": sid, "namespace": "pods", "stable_seconds": 0, "backfill": True})
    wid = r.json()["id"]
    assert client.post("/api/v1/watches/preview", headers=h, json={"source": sid}).json()["transcripts"] == 3
    assert sources.poll_watch(db, cfg, wid) == {"seen": 3, "new": 3, "waiting": 0, "skipped": 0, "errors": 0}
    drain(db, cfg)
    recs = {r["title"]: r for r in db.rows("SELECT record::id(id) AS id, title, source, recorded_at, status, remote FROM recording")}
    assert set(recs) == {"Harbour report", "Buoys", "Keeper's log"}
    report = recs["Harbour report"]
    assert (report["source"], report["recorded_at"], report["status"]) == ("transcript", "2026-09-29T09:30:00+01:00", "analyzed")
    assert report["remote"] == {"source": sid, "path": "INBOX/11.eml"}
    said = _segments(db, report["id"])
    assert "lighthouse keeper wrote twice" in said and "From — Mara Keane (mara@example.org)" in said
    assert "Attachments — harbour.pdf" in _segments(db, recs["Keeper's log"]["id"])
    assert sources.poll_watch(db, cfg, wid)["new"] == 0

    # nothing on the server was changed: mailboxes opened read-only, messages fetched without marking them read
    sent = " ".join(imap.commands).upper()
    assert "EXAMINE" in sent and not any(w in sent for w in (" SELECT ", "STORE", "EXPUNGE", "COPY", "MOVE", "BODY[]"))

    # a message chosen on purpose from another mailbox
    r = client.post("/api/v1/import/source", headers=h, json={"source": sid, "paths": ["Trash/1.eml", "Trash/9.eml"], "namespace": "calls"})
    assert [x["status"] for x in r.json()["results"]] == ["queued", "error"]


@pytest.mark.skipif(not chromium_binary(), reason="needs Chromium")
def test_an_imap_message_is_an_email_document_with_its_attachments(client, db, cfg, admin, imap):
    cfg["documents"]["chromium"] = chromium_binary()
    cfg["documents"]["convert_seconds"] = 120
    sid = _imap_source(client, admin, imap)["id"]
    r = client.post(
        "/api/v1/import/source", headers=admin, json={"source": sid, "paths": ["Projects/Lighthouse/3.eml"], "namespace": "pods"}
    )
    assert r.json()["results"][0]["status"] == "queued", r.text
    rid = r.json()["results"][0]["recording"]
    drain(db, cfg)
    rec = db.one("SELECT title, source, status, recorded_at, error FROM $r", r=R("recording", rid))
    assert (rec["title"], rec["source"], rec["status"]) == ("Keeper's log", "document", "analyzed"), rec
    made = db.rows("SELECT source, attached_to, title FROM recording WHERE attached_to.resource = $r", r=rid)
    assert [(m["source"], m["title"]) for m in made] == [("document", "harbour")]
