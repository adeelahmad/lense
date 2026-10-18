"""The assistant's tools for the mail, calendars and files connected to Lens (connection_tools.py; docs/assistant.md,
"Mail, calendars and files"): admins only, the tools for the kinds connected, reading as each connection reads (nothing
changes in the account), messages and events numbered to cite with their links, and bringing things in only once the
person approves. Everything runs against fakes: tests/fake_gmail.py, fake_google.py, fake_imap.py and fake_rclone.py."""

from __future__ import annotations

import io
import json
import pathlib
import zipfile

import pytest

from app.domain import ai_tools, connection_tools, convert, gcal, gmail, google_auth, sources, store
from tests import fake_google
from tests.api.test_gmail_source import MARA, _key
from tests.api.test_google_calendar_source import TEAM, _team_events
from tests.api.test_mail_calendar_sources import BUOYS, REPORT, _email, _mailboxes
from tests.fake_gmail import Google as Gmail
from tests.helpers import drain, make_user, text_pdf

R = store.R
FAKE_RCLONE = pathlib.Path(__file__).parent.parent / "fake_rclone.py"


def _me(db, email="root@x.io", admin=True):
    uid = make_user(db, email, "a password 1", admin=admin, roles={"pods": "editor"})
    return {"id": uid, "email": email}


def _box(db, cfg, me, admin=True, chat=None, **kw):
    pods = store.ns_id(db, "pods")
    return ai_tools.Toolbox(db, cfg, me, {pods}, {pods}, {}, chat, admin=admin, **kw)


def _names(box):
    return {s["function"]["name"] for s in box.specs()}


def _call(box, tool, **args):
    out, summary = box.call(tool, args)
    return json.loads(out), summary


@pytest.fixture
def as_text(monkeypatch):
    monkeypatch.setattr(convert, "soffice", lambda cfg: None)  # no converters: emails are read as text
    monkeypatch.setattr(convert, "chromium", lambda cfg: None)


@pytest.fixture
def mail(monkeypatch):
    g = Gmail(address=MARA)
    monkeypatch.setattr(google_auth, "TOKEN_URL", g.token_uri)
    monkeypatch.setattr(gmail, "API", g.api)
    google_auth._TOKENS.clear()
    gmail._META.clear()
    yield g
    g.close()


@pytest.fixture
def calendar(monkeypatch):
    g = fake_google.Google()
    monkeypatch.setattr(google_auth, "TOKEN_URL", g.base + "/token")
    monkeypatch.setattr(gcal, "API", g.base + "/calendar/v3")
    google_auth._TOKENS.clear()
    gcal._CALENDARS.clear()
    g.calendars[TEAM] = {"summary": "Harbour team", "timeZone": "Europe/Berlin", "events": _team_events(), "readers": set()}
    yield g
    g.close()


@pytest.fixture
def bucket(folder, monkeypatch):
    state = folder / "throttled"
    state.write_text("")
    monkeypatch.setenv("RCLONE_BINARY", str(FAKE_RCLONE))
    monkeypatch.setenv("FAKE_RCLONE_ROOT", str(folder / "remote"))
    monkeypatch.setenv("FAKE_RCLONE_STATE", str(state))
    return folder / "remote"


def _gmail_source(db, cfg, google):
    return sources.create(db, cfg, "Mara's mail", "gmail", {"user": MARA}, {"service_account_credentials": _key(google)})


def _docx(*paras):
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paras)
    xml = f'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


def test_only_admins_get_them_and_only_for_what_is_connected(db, cfg, mail):
    me = _me(db)
    assert not _names(_box(db, cfg, me)) & connection_tools.NAMES  # nothing connected yet
    _gmail_source(db, cfg, mail)
    names = _names(_box(db, cfg, me)) & connection_tools.NAMES
    assert names == {"connections", "search_mail", "read_mail", "list_files", "import_from_connection"}
    assert "search and read mail" in _box(db, cfg, me).system_note()
    ed = _me(db, "ed@x.io", admin=False)
    assert not _names(_box(db, cfg, ed, admin=False)) & connection_tools.NAMES
    # an admin's switch still wins
    cfg["ai"]["disabled_tools"] = ["read_mail"]
    assert "read_mail" not in _names(_box(db, cfg, me))


def test_the_catalog_says_why_they_are_missing(db, cfg, mail):
    me, ed = _me(db), _me(db, "ed@x.io", admin=False)
    groups = {g["name"]: g for g in ai_tools.catalog(_box(db, cfg, me))}
    assert not groups["connected"]["available"] and "nothing is connected yet" in groups["connected"]["why"]
    _gmail_source(db, cfg, mail)
    groups = {g["name"]: g for g in ai_tools.catalog(_box(db, cfg, me))}
    tools = {t["name"]: t for t in groups["connected"]["tools"]}
    assert groups["connected"]["available"] and groups["connected"]["asks"] and not groups["connected"]["out"]
    assert tools["search_mail"]["effect"] == "read" and tools["import_from_connection"]["effect"] == "asks"
    assert not tools["calendar_events"]["available"]  # no calendar is connected
    groups = {g["name"]: g for g in ai_tools.catalog(_box(db, cfg, ed, admin=False))}
    assert (groups["connected"]["available"], groups["connected"]["why"]) == (False, "for admins")


def test_searching_and_reading_gmail(db, cfg, mail, as_text):
    report = mail.add(REPORT, ["INBOX", "UNREAD", "Label_7"], at=1790000000000)
    mail.add(BUOYS, ["INBOX"], at=1790000100000)
    sid = _gmail_source(db, cfg, mail)
    assert sources.test(db, cfg, sid)["ok"]
    me = _me(db)
    box = _box(db, cfg, me, chat=None)

    got, summary = _call(box, "connections")
    assert got["connections"] == [
        {
            "id": sid,
            "name": "Mara's mail",
            "kind": "mail",
            "type": "Gmail or Google Workspace",
            "account": MARA,
            "works": True,
            "tools": ["import_from_connection", "list_files", "read_mail", "search_mail"],
        }
    ]
    assert not box.outside  # the connections are Lens's own word

    mail.requests.clear()
    got, summary = _call(box, "search_mail", query="from:mara harbour", limit=5)
    assert summary == 'Searched Mara\'s mail for "from:mara harbour": 2 messages'
    q = [q for m, p, q in mail.requests if p.endswith("/users/me/messages")][0]
    assert q["q"] == ["from:mara harbour"] and q["maxResults"] == ["5"]
    first = got["messages"][1]
    assert (first["id"], first["subject"], first["from"]) == (report, "Harbour report", "Mara Keane <mara@example.org>")
    assert first["labels"] == ["Inbox", "Harbour"] and first["unread"] and first["path"] == f"{report}.eml"
    assert first["url"] == f"https://mail.google.com/mail/u/{MARA}/#all/{report}"
    ref = box.refs[first["ref"] - 1]
    assert (ref["source"], ref["site"], ref["url"], ref["title"]) == ("mail", "Mara's mail", first["url"], "Harbour report")
    assert box.outside == ["tool search_mail"]  # mail is someone else's words: what sends data out now asks first

    got, summary = _call(box, "read_mail", message=report)
    assert summary == "Read “Harbour report” in Mara's mail"
    assert got["text"] == "The ships arrived at dawn. The lighthouse keeper wrote twice."
    assert got["to"] == "tom@example.org" and got["ref"] == first["ref"]  # the same message keeps its number
    # Gmail is only read: nothing was changed, and the message stays unread
    assert {m for m, _, _ in mail.requests} == {"GET"} and "UNREAD" in mail.messages[report]["labels"]
    # the model reads it marked as data
    out, _ = box.call("read_mail", {"message": report}, model=True)
    assert out.startswith("<untrusted-") and "lighthouse keeper" in out

    got, _ = _call(box, "read_mail", message="../../etc")
    assert "isn't a Gmail message id" in got["error"]
    got, _ = _call(box, "read_mail", message="ffff0000")
    assert "isn't in the mailbox any more" in got["error"]


def test_searching_an_imap_mailbox(db, cfg, as_text):
    from tests import fake_imap

    srv = fake_imap.start(_mailboxes(), flags={"Trash": ["\\Trash"]})
    try:
        params = {"host": "127.0.0.1", "port": str(srv.port), "security": "none", "user": "lens"}
        sources.create(db, cfg, "Work mail", "imap", params, {"pass": "mail password 1"})
        box = _box(db, cfg, _me(db))
        got, _ = _call(box, "search_mail", query="buoys")
        assert [m["subject"] for m in got["messages"]] == ["Buoys"]
        assert got["searched"].startswith("the subjects and senders of the newest") and got["searched"].endswith("in INBOX")
        assert "ref" not in got["messages"][0]  # no page to link to
        got, _ = _call(box, "read_mail", message=got["messages"][0]["path"])
        assert got["text"] == "The new buoys are in." and "url" not in got
    finally:
        srv.shutdown()


def test_calendar_events_between_two_times(db, cfg, calendar):
    key, public = fake_google.service_account_key()
    calendar.service_accounts[key["client_email"]] = public
    calendar.calendars[TEAM]["readers"].add(key["client_email"])
    sources.create(db, cfg, "Team calendar", "gcal", {"calendars": TEAM}, {"service_account_credentials": json.dumps(key)})
    box = _box(db, cfg, _me(db))
    assert {"calendar_events", "list_files"} <= _names(box) and "search_mail" not in _names(box)

    got, summary = _call(box, "calendar_events", **{"from": "2026-10-01", "to": "2026-10-31"})
    assert summary == "Looked at Team calendar from 2026-10-01 to 2026-10-31: 3 events"
    assert (got["from"], got["to"], got["calendars"]) == ("2026-10-01T00:00:00+00:00", "2026-10-31T00:00:00+00:00", ["Harbour team"])
    standup, moved, regatta = got["events"]  # working-location entries aren't events
    assert (moved["title"], moved["start"]) == ("Harbour stand-up (moved)", "2026-10-09T11:30:00+02:00")
    assert (standup["title"], standup["start"], standup["where"]) == (
        "Harbour stand-up",
        "2026-10-02T10:00:00+02:00",
        "Pier 4, meeting room",
    )
    assert standup["attendees"] == ["Tom Byrne <tom@example.org> (accepted)", "ann@example.org (needsAction)"]
    assert standup["organizer"] == "Mara Keane <mara@example.org>" and standup["video"].startswith("https://meet.google.com/")
    assert standup["about"].startswith("Agenda: the night crossing")
    assert (regatta["title"], regatta["all_day"]) == ("Autumn regatta", True)
    ref = box.refs[regatta["ref"] - 1]
    assert (ref["source"], ref["url"]) == ("event", "https://www.google.com/calendar/event?eid=regatta")
    asked = [q for path, q in calendar.api_calls("/events")][-1]
    assert asked["singleEvents"] == "true" and asked["orderBy"] == "startTime"

    got, _ = _call(box, "calendar_events", query="regatta", **{"from": "2026-10-01T00:00:00+02:00", "to": "2026-11-01"})
    assert [e["title"] for e in got["events"]] == ["Autumn regatta"]
    got, _ = _call(box, "calendar_events", **{"from": "next week"})
    assert "isn't an ISO 8601 date or time" in got["error"]
    got, _ = _call(box, "calendar_events", **{"from": "2026-10-10", "to": "2026-10-01"})
    assert got["error"] == "to is before from"


def test_finding_and_reading_files_in_storage(db, cfg, bucket):
    (bucket / "reports" / "2026").mkdir(parents=True)
    (bucket / "reports" / "2026" / "Harbour report.md").write_text("# Harbour\nThe ships arrived at dawn.\n")
    (bucket / "reports" / "budget.csv").write_text("item,cost\nbuoys,1200\n")
    (bucket / "reports" / "keeper.pdf").write_bytes(text_pdf([["The keeper wrote twice."]]))
    (bucket / "reports" / "letter.docx").write_bytes(_docx("Dear harbour master,", "The buoys are in."))
    (bucket / "photo.jpg").write_bytes(b"\xff\xd8\xff\xe0\0\0JFIF\0" + b"\0" * 64)
    sid = sources.create(db, cfg, "Bucket", "s3", {"provider": "AWS"}, {"secret_access_key": "s3cret"})
    box = _box(db, cfg, _me(db))
    assert {"find_files", "read_file", "list_files"} <= _names(box) and "search_mail" not in _names(box)

    got, summary = _call(box, "list_files", connection="Bucket")
    assert [(e["path"], e.get("folder")) for e in got["entries"]] == [("reports", True), ("photo.jpg", None)]
    got, summary = _call(box, "find_files", connection=sid, name="HARBOUR report")
    assert [f["path"] for f in got["files"]] == ["reports/2026/Harbour report.md"]
    assert summary == 'Looked for "HARBOUR report" in Bucket: 1 found'

    got, _ = _call(box, "read_file", path="reports/2026/Harbour report.md")
    assert got["text"] == "# Harbour\nThe ships arrived at dawn.\n"
    got, _ = _call(box, "read_file", path="reports/budget.csv", max_chars=12)
    assert got["text"] == "item,cost\nbu" and got["cut_short"] == "the text goes on for 9 more characters"
    got, _ = _call(box, "read_file", path="reports/keeper.pdf")
    assert "The keeper wrote twice." in got["text"]
    got, _ = _call(box, "read_file", path="reports/letter.docx")
    assert got["text"] == "Dear harbour master,\nThe buoys are in."
    got, _ = _call(box, "read_file", path="photo.jpg")
    assert "isn't text, a PDF or a Word file" in got["error"]
    got, _ = _call(box, "read_file", path="reports/gone.txt")
    assert "there's no file reports/gone.txt" in got["error"]
    got, _ = _call(box, "read_file", path="../secrets.txt")
    assert "paths can't contain .." in got["error"]


def test_bringing_things_in_waits_for_the_persons_yes(db, cfg, bucket, mail, as_text):
    (bucket / "calls").mkdir(parents=True)
    (bucket / "calls" / "call1.txt").write_text("Ann: the buoys are in.\nBen: great.")
    files = sources.create(db, cfg, "Bucket", "s3", {"provider": "AWS"}, {"secret_access_key": "s3cret"})
    report = mail.add(_email("Tide tables", "High water at six.", msgid="tide@example.org"), ["INBOX"])
    inbox = _gmail_source(db, cfg, mail)
    me = _me(db)
    box = _box(db, cfg, me, chat=1)

    got, summary = _call(box, "import_from_connection", connection="Bucket", paths=["calls/call1.txt"], namespace="pods")
    assert got["status"] == "waiting for the person's approval" and summary == "Asked for approval: Bring call1.txt from Bucket into pods"
    assert not db.rows("SELECT id FROM recording WHERE remote.source = $s", s=files)  # nothing yet
    done = ai_tools.approve(db, cfg, got["approval"], me, {store.ns_id(db, "pods")}, admin=True)
    assert [r["status"] for r in done["results"]] == ["queued"] and done["namespace"] == "pods"
    # a message's id from search_mail is its path; a new namespace is made
    got, _ = _call(box, "import_from_connection", connection=inbox, paths=[report], namespace="mail")
    done = ai_tools.approve(db, cfg, got["approval"], me, set(), admin=True)
    assert [r["status"] for r in done["results"]] == ["queued"] and "mail" in store.space_names(db).values()
    drain(db, cfg)
    assert {r["title"] for r in db.rows("SELECT title FROM recording WHERE remote != NONE")} == {"call1", "Tide tables"}
    # only admins carry it out
    got, _ = _call(box, "import_from_connection", connection="Bucket", paths=["calls/call1.txt"], namespace="pods")
    with pytest.raises(PermissionError):
        ai_tools.approve(db, cfg, got["approval"], me, {store.ns_id(db, "pods")}, admin=False)
