"""Content as activities (docs/automations.md, Kinds): every new asset is an activity of the most specific kind its
row tells (an email received, a calendar event, a file from storage, an upload, a page saved from the web, an import),
with the right actor and the kind's own fields, written the way the real code paths write them."""

from __future__ import annotations

import datetime as dt
import time
from email.message import EmailMessage

import pytest

from app.domain import (
    convert,
    deletion,
    events,
    events_content,
    feeds,
    gmail,
    import_hooks,
    ingest,
    jobs,
    sources,
    store,
    uploads,
    webcapture,
)
from tests.helpers import PODS1, drain, make_user, write_wav

R = store.R
ADMIN = events.viewer(1, None, admin=True)
PODS = "\n".join(f"[00:{i:02d}] {line.split('|')[0]}: {line.split('|')[2]}" for i, line in enumerate(PODS1.splitlines()))
ICS = """BEGIN:VCALENDAR\r
VERSION:2.0\r
BEGIN:VEVENT\r
UID:evening-1@stmarys.org\r
DTSTART:20261020T170000Z\r
DTEND:20261020T183000Z\r
SUMMARY:Parents' evening\r
LOCATION:School hall\\, St Mary's\r
ORGANIZER;CN=School office:mailto:Office@StMarys.org\r
ATTENDEE;CN=Sam:mailto:sam@example.org\r
ATTENDEE:mailto:alex@example.org\r
END:VEVENT\r
END:VCALENDAR\r
"""


@pytest.fixture(autouse=True)
def fresh():
    """Names are looked up again in every test (each has its own database), and nothing handled carries over."""
    events_content.forget_names()
    events._seen.clear()
    events._claimed.clear()
    yield
    events_content.forget_names()


def source(db, name, typ, **params):
    sid = db.next_id("storage_source")
    db.q("CREATE $r CONTENT $d", r=R("storage_source", sid), d={"name": name, "type": typ, "params": params, "created_at": store.now()})
    return sources.get(db, sid)


def watch(db, sid, space):
    wid = db.next_id("watch_path")
    db.q("CREATE $r CONTENT $d", r=R("watch_path", wid), d={"source": sid, "path": "", "space": space})
    return wid


def start(db, cfg, consumer="test.content"):
    """A follower's first look marks where it starts: what's made in that second isn't seen, so wait for the next."""
    assert events.follow(db, cfg, consumer)[0] == []
    t = store.now()
    while store.now() == t:
        time.sleep(0.05)


def new(db, cfg, consumer="test.content", kinds=None):
    """What a follower sees next."""
    got, commit = events.follow(db, cfg, consumer, kinds=kinds)
    commit()
    return got


def one(db, cfg, rid):
    a = events.get(db, cfg, f"create-recording-{rid}", ADMIN)
    assert a is not None and events.check_activity(a)
    return a


def test_an_email_from_a_mailbox_is_email_received(db, cfg):
    pods = store.ns_id(db, "pods")
    src = source(db, "Work mail", "imap", host="imap.example.org")
    wid = watch(db, src["id"], pods)
    start(db, cfg)
    f = {
        "path": "INBOX/7-12.eml",
        "title": "School trip on Friday",
        "size": 2048,
        "modified": "2026-10-15T16:21:58+00:00",
        "when": "2026-10-15T16:20:41+00:00",
        "key": "message-id:trip@stmarys.org",
        "mail": {
            "from": ["office@stmarys.org"],
            "to": ["sam@example.org", "alex@example.org"],
            "cc": ["alex@example.org"],
            "subject": "School trip on Friday",
            "list": "",
            "mailbox": "INBOX",
        },
    }
    rid, job = sources._ingest(db, cfg, src, f, "document", pods, f"watch:{wid}")
    assert job and db.one("SELECT mail FROM $r", r=R("recording", rid))["mail"]["from"] == ["office@stmarys.org"]
    got = new(db, cfg)
    assert [a["id"] for a in got] == [f"create-recording-{rid}"]
    a = got[0]
    assert a["kind"] == "email.received" and a["type"] == "Create" and a["space"] == pods
    assert a["actor"] == {"type": "Service", "id": f"lens:source/{src['id']}", "name": "Work mail"}
    o = a["object"]
    assert o["id"] == f"lens:recording/{rid}" and o["url"] == f"/resources/{rid}" and o["content_type"] == "email"
    assert o["from"] == ["office@stmarys.org"] and o["to"] == ["sam@example.org", "alex@example.org"] and o["cc"] == ["alex@example.org"]
    assert o["subject"] == "School trip on Friday" and o["mailbox"] == "INBOX" and o["date"] == "2026-10-15T16:20:41+00:00"
    assert "list" not in o and "snippet" not in o and "attachments" not in o  # not known until it's been read
    assert a["summary"] == "Work mail received “School trip on Friday” from office@stmarys.org"
    assert set(a["lens"]["untrusted"]) >= {"object.name", "object.subject", "object.from", "object.snippet"}
    # once it's read (documents.py keeps convert.email_info as `email`), its text's start and attachments show
    db.q(
        "UPDATE $r SET email = $e",
        r=R("recording", rid),
        e={"subject": "School trip on Friday", "from": "Office <office@stmarys.org>", "attachments": 1, "snippet": "Dear parents"},
    )
    a = one(db, cfg, rid)
    assert a["object"]["snippet"] == "Dear parents" and a["object"]["attachments"] == 1 and a["id"] == got[0]["id"]
    # a trigger on the parent matches it, and the catalog has its fields
    assert events.follow(db, cfg, "test.parent", kinds=["asset.added"])[0] == []
    assert events.field_at("email.received", "object.from.0")["title"] == "Address"
    assert events.kind_info("email.received")["parent"] == "asset.added"


def test_what_reading_an_email_keeps_and_what_a_mailbox_says(tmp_path):
    msg = tmp_path / "trip.eml"
    msg.write_bytes(
        b"From: School office <office@stmarys.org>\r\nTo: sam@example.org\r\nCc: alex@example.org\r\nSubject: School trip\r\n"
        b"Date: Thu, 15 Oct 2026 16:20:41 +0000\r\nMIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary=b\r\n\r\n"
        b"--b\r\nContent-Type: text/plain\r\n\r\nDear parents,\r\n\r\n  the trip leaves at 9.\r\n"
        b"--b\r\nContent-Type: application/pdf\r\nContent-Disposition: attachment; filename=form.pdf\r\n\r\n%PDF-1.4\r\n--b--\r\n"
    )
    info = convert.email_info(convert.read_eml(msg))
    assert info["subject"] == "School trip" and info["from"] == "School office <office@stmarys.org>" and info["cc"] == "alex@example.org"
    assert info["attachments"] == 1 and info["snippet"] == "Dear parents, the trip leaves at 9."
    # Gmail lists a message with a snippet; Cc is kept apart from To (which routing rules read, Cc included)
    entry = gmail._entry(
        {
            "id": "18c",
            "internalDate": "1792080041000",
            "sizeEstimate": 900,
            "snippet": "Dear parents,   the trip",
            "payload": {
                "headers": [
                    {"name": "Subject", "value": "School trip"},
                    {"name": "From", "value": "Office <office@stmarys.org>"},
                    {"name": "To", "value": "sam@example.org"},
                    {"name": "Cc", "value": "Alex <alex@example.org>"},
                ]
            },
        },
        "INBOX",
        {"INBOX": "Inbox"},
        None,
    )
    assert entry["mail"]["snippet"] == "Dear parents, the trip" and entry["mail"]["cc"] == ["alex@example.org"]
    assert entry["mail"]["to"] == ["sam@example.org", "alex@example.org"]
    assert sources._mail_of(entry) == {
        "from": ["office@stmarys.org"],
        "to": ["sam@example.org", "alex@example.org"],
        "cc": ["alex@example.org"],
        "subject": "School trip",
        "mailbox": "Inbox",
        "date": entry["when"],
        "snippet": "Dear parents, the trip",
    }
    assert sources._mail_of({"path": "a.pdf"}) is None and feeds._addressing(EmailMessage(), "", "INBOX")["cc"] == []


def test_a_calendar_event_is_calendar_event_added_with_its_times(db, cfg, folder):
    pods = store.ns_id(db, "pods")
    inbox = folder / "inbox"
    inbox.mkdir()
    (inbox / "evening.ics").write_text(ICS)
    src = source(db, "Family calendar", "local")
    start(db, cfg)
    f = {"path": str(inbox / "evening.ics"), "title": "Parents' evening", "size": len(ICS), "modified": store.now(), "when": None}
    rid, _ = sources._ingest(db, cfg, src, f, "transcript", pods, "sam@example.org")
    ev = db.one("SELECT event FROM $r", r=R("recording", rid))["event"]
    assert ev["uid"] == "evening-1@stmarys.org|" and ev["calendar"] == "Family calendar"
    got = new(db, cfg)
    a = got[0]
    assert [x["id"] for x in got] == [f"create-recording-{rid}"] and a["kind"] == "calendar_event.added"
    assert a["actor"]["id"] == f"lens:source/{src['id']}" and a["actor"]["name"] == "Family calendar"
    o = a["object"]
    assert o["name"] == "Parents' evening" and o["content_type"] == "calendar_event" and o["calendar"] == "Family calendar"
    assert o["startTime"] == "2026-10-20T17:00:00+00:00" and o["endTime"] == "2026-10-20T18:30:00+00:00"
    assert o["location"] == "School hall, St Mary's" and o["organizer"] == "office@stmarys.org"
    assert o["attendees"] == ["sam@example.org", "alex@example.org"]
    assert a["summary"] == "Family calendar added the event “Parents' evening” on 2026-10-20"
    assert {"object.location", "object.organizer"} <= set(a["lens"]["untrusted"])


def test_a_file_from_storage_is_file_added(db, cfg):
    calls = store.ns_id(db, "calls")
    src = source(db, "Office share", "s3")
    wid = watch(db, src["id"], calls)
    f = {"path": "board/minutes.pdf", "size": 48213, "modified": "2026-10-15T09:00:00+00:00"}
    rid, _ = sources._ingest(db, cfg, src, f, "document", calls, f"watch:{wid}")
    a = one(db, cfg, rid)
    assert a["kind"] == "file.added" and a["actor"] == {"type": "Service", "id": f"lens:source/{src['id']}", "name": "Office share"}
    o = a["object"]
    assert o["name"] == "minutes" and o["path"] == "board/minutes.pdf" and o["mediaType"] == "application/pdf" and o["size"] == 48213
    assert o["type"] == "Document" and o["media"] == "document" and "duration" not in o
    assert a["summary"] == "Office share added “minutes”" and set(a["lens"]["untrusted"]) == {"object.name", "object.path"}


def test_an_upload_is_upload_added_by_whoever_uploaded_it(db, cfg, folder):
    sam = make_user(db, "sam@example.org", "sam password 1", roles={"pods": "editor"})
    db.q("UPDATE $r SET name = 'Sam'", r=R("account", sam))
    wav = folder / "call.wav"
    write_wav(wav, seconds=1.5)
    up = uploads.take(db, cfg, "pods", wav, "Call with the school.wav", {"id": sam, "email": "sam@example.org"})
    rid = up["recording"]
    a = one(db, cfg, rid)
    assert a["kind"] == "upload.added" and a["actor"] == {"type": "Person", "id": f"lens:account/{sam}", "name": "Sam"}
    o = a["object"]
    assert o["path"] == "Call with the school.wav" and o["mediaType"] in ("audio/x-wav", "audio/wav") and o["duration"] == 1.5
    assert o["size"] > 0 and o["type"] == "Audio" and a["summary"] == "Sam uploaded “Call with the school”"
    # finished uploads are forgotten after a day; who uploaded it is still known from its first run's queuer
    db.q("DELETE upload")
    events_content.forget_names()
    assert one(db, cfg, rid)["actor"] == a["actor"] and one(db, cfg, rid)["kind"] == "upload.added"


def test_a_page_saved_from_the_web_is_web_page_saved(db, cfg):
    pods = store.ns_id(db, "pods")
    sam = make_user(db, "sam@example.org", "sam password 1")
    rid = webcapture.create(db, pods, "https://museum.example.org/visit?lang=en", "Museum opening hours", by="sam@example.org")
    a = one(db, cfg, rid)
    assert a["kind"] == "web_page.saved" and a["actor"] == {"type": "Person", "id": f"lens:account/{sam}", "name": "sam"}
    assert a["object"]["href"] == "https://museum.example.org/visit?lang=en" and a["object"]["site"] == "museum.example.org"
    assert a["object"]["url"] == f"/resources/{rid}" and a["summary"] == "sam saved “Museum opening hours” from museum.example.org"
    # someone who isn't an account (any more) is still named, by their address
    gone = webcapture.create(db, pods, "https://example.org/", "Example", by="old@example.org")
    assert one(db, cfg, gone)["actor"] == {"type": "Person", "id": "lens:email/old@example.org", "name": "old@example.org"}


def test_imports_are_import_received_by_their_hook_or_person(db, cfg):
    pods = store.ns_id(db, "pods")
    hook, _ = import_hooks.create(db, pods, "Phone shortcut")
    got = import_hooks.accept_text(db, cfg, import_hooks.get(db, hook["id"]), "Milk, eggs, bread", "Shopping list")
    a = one(db, cfg, got["recording"])
    assert a["kind"] == "import.received" and a["actor"] == {
        "type": "Service",
        "id": f"lens:import_hook/{hook['id']}",
        "name": "Phone shortcut",
    }
    assert a["object"]["via"] == "hook" and a["summary"] == "Phone shortcut imported “Shopping list”"
    pasted = ingest.import_text(db, cfg, "pods", "[00:00] Sam: Remember the forms.", title="Note to self")
    a = one(db, cfg, pasted)
    assert a["kind"] == "import.received" and a["object"]["via"] == "paste" and a["actor"] == events.LENS


def test_a_hook_upload_is_an_import(db, cfg):
    pods = store.ns_id(db, "pods")
    hook, _ = import_hooks.create(db, pods, "Recorder")
    wav = uploads.incoming(cfg) / "memo-in"
    write_wav(wav, seconds=1)
    got = import_hooks.accept_file(db, cfg, import_hooks.get(db, hook["id"]), wav, "memo.wav")
    a = one(db, cfg, got["recording"])
    assert a["kind"] == "import.received" and a["actor"]["id"] == f"lens:import_hook/{hook['id']}" and a["actor"]["name"] == "Recorder"
    db.q("DELETE upload")  # once its upload is forgotten, its first run's queuer ("import hook: Recorder") names the hook
    events_content.forget_names()
    assert one(db, cfg, got["recording"])["actor"]["id"] == f"lens:import_hook/{hook['id']}"


def test_actors_name_who_did_it_in_a_few_queries(db, cfg):
    pods = store.ns_id(db, "pods")
    ann = make_user(db, "ann@example.org", "ann password 1")
    src = source(db, "Drive", "drive")
    wid = watch(db, src["id"], pods)
    rid = db.next_id("routine")
    db.q("CREATE $r CONTENT $d", r=R("routine", rid), d={"name": "Morning digest"})
    labels = [
        (ann, None),
        ("ANN@example.org", None),
        (f"watch:{wid}", None),
        (f"routine:{rid}", None),
        ("automation:4", None),
        ("assistant", None),
        ("analysis", None),
        (None, None),
        ("watch:999", None),
    ]
    got = events_content.actors(db, labels)
    assert got[(ann, None)] == got[("ANN@example.org", None)] == {"type": "Person", "id": f"lens:account/{ann}", "name": "ann"}
    assert got[(f"watch:{wid}", None)] == {"type": "Service", "id": f"lens:source/{src['id']}", "name": "Drive"}
    assert got[(f"routine:{rid}", None)] == {"type": "Application", "id": f"lens:routine/{rid}", "name": "Morning digest"}
    assert got[("automation:4", None)]["id"] == "lens:automation/4" and got[("assistant", None)]["id"] == "lens:assistant"
    assert got[("analysis", None)] == got[(None, None)] == got[("watch:999", None)] == events.LENS
    # names are kept a little while (looked up again after NAME_SECONDS, or when forgotten)
    db.q("UPDATE $r SET name = 'Shared drive'", r=R("storage_source", src["id"]))
    assert events_content.actors(db, [(f"watch:{wid}", None)])[(f"watch:{wid}", None)]["name"] == "Drive"
    events_content.forget_names(db)
    assert events_content.actors(db, [(f"watch:{wid}", None)])[(f"watch:{wid}", None)]["name"] == "Shared drive"
    # an automation that queued an asset's first run is its actor, whatever else the row says
    rec = db.next_id("recording")
    db.q(
        "CREATE $r CONTENT $d",
        r=R("recording", rec),
        d={"space": pods, "title": "Made by a rule", "source": "document", "web": {"url": "https://x.org/"}, "created_at": store.now()},
    )
    db.q("CREATE job CONTENT $d", d={"recording": rec, "created_by": "automation:9", "created_at": store.now()})
    a = one(db, cfg, rec)
    assert a["actor"]["id"] == "lens:automation/9" and a["kind"] == "web_page.saved"


def test_an_attachment_made_a_resource_is_an_asset_added(db, cfg):
    pods = store.ns_id(db, "pods")
    rid = db.next_id("recording")
    db.q(
        "CREATE $r CONTENT $d",
        r=R("recording", rid),
        d={
            "space": pods,
            "title": "form",
            "source": "document",
            "path": "/data/uploads/pods/attachment-3-1/form.eml",
            "attached_to": {"resource": 3, "file": "1"},
            "created_at": store.now(),
        },
    )
    assert one(db, cfg, rid)["kind"] == "asset.added"


def _rows(db, n, space, when):
    for _ in range(n):
        rid = db.next_id("recording")
        db.q(
            "CREATE $r CONTENT $d", r=R("recording", rid), d={"space": space, "title": f"Scan {rid}", "source": "image", "created_at": when}
        )


def test_paging_six_hundred_assets_of_one_second(db, cfg):
    pods, calls = store.ns_id(db, "pods"), store.ns_id(db, "calls")
    start(db, cfg, "test.many")
    when = (dt.datetime.now(dt.UTC) + dt.timedelta(seconds=2)).isoformat(timespec="seconds")
    _rows(db, 600, pods, when)
    _rows(db, 5, calls, when)
    ids = sorted(db.values("SELECT VALUE record::id(id) FROM recording WHERE space = $s", s=pods), reverse=True)
    seen, before = [], None
    member = events.viewer(7, {pods})
    while True:
        p = events.page(db, cfg, member, before, {"kinds": ["asset.added"]}, 50)
        seen += [a["id"] for a in p["items"]]
        if not p["next"]:
            break
        before = p["next"]
    assert seen == [f"create-recording-{i}" for i in ids]  # each once, newest first, the id breaking the tie
    assert all(events.get(db, cfg, a, member)["space"] == pods for a in seen[:3])
    # following reads them a page at a time, oldest first, each once
    first, commit = events.follow(db, cfg, "test.many", limit=500)
    commit()
    rest, commit = events.follow(db, cfg, "test.many", limit=500)
    commit()
    got = [a["id"] for a in first + rest]
    assert len(first) == 500 and len(got) == 605 and len(set(got)) == 605


# ---------- runs, deletions, calendar moves ----------
def test_finished_runs_are_processed_or_failed_with_the_assets_entities(db, cfg, monkeypatch):
    sam = make_user(db, "sam@example.org", "sam password 1")
    start(db, cfg, "test.runs")
    rid = ingest.import_text(db, cfg, "pods", PODS, title="Capsid episode")
    jid = jobs.enqueue(db, rid, ["analyze"], by="sam@example.org")
    drain(db, cfg)
    got = [a for a in new(db, cfg, "test.runs") if a["kind"] == "asset.processed"]
    assert len(got) == 1
    a = got[0]
    finished = db.one("SELECT finished_at FROM $r", r=R("job", jid))["finished_at"]
    assert a["id"] == f"processed-job-{jid}-{int(events._parse(finished).timestamp())}" and a["type"] == "Update"
    assert a["actor"] == {"type": "Person", "id": f"lens:account/{sam}", "name": "sam"} and a["space"] == store.ns_id(db, "pods")
    assert a["object"]["id"] == f"lens:recording/{rid}" and a["object"]["job"] == f"lens:job/{jid}" and a["object"]["steps"] == ["analyze"]
    assert a["summary"] == "“Capsid episode” finished processing (analyze)"
    names = [t["name"] for t in a["tag"]]
    assert "Dyno Therapeutics" in names and len(names) <= 10 and all(t["type"] == "Mention" for t in a["tag"])
    assert names[0] == "Dyno Therapeutics"  # the most mentioned first
    assert events.get(db, cfg, a["id"], ADMIN) == a
    # a run that fails says where and why; tried again, it finishes again as another activity
    monkeypatch.setitem(jobs.STEPS, "report", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("the report model isn't reachable")))
    bad = jobs.enqueue(db, rid, ["report"], by="automation:4")
    drain(db, cfg)
    fail = [x for x in new(db, cfg, "test.runs") if x["kind"] == "asset.failed"]
    assert len(fail) == 1 and fail[0]["actor"]["id"] == "lens:automation/4" and fail[0]["type"] == "Announce"
    assert fail[0]["object"]["step"] == "report" and "isn't reachable" in fail[0]["object"]["error"]
    assert fail[0]["summary"] == "Processing “Capsid episode” failed at report" and "object.error" in fail[0]["lens"]["untrusted"]
    first = fail[0]["id"]
    time.sleep(1.05)  # a second later, so the second try finishes at another time
    jobs.retry(db, bad)
    drain(db, cfg)
    again = [x for x in new(db, cfg, "test.runs") if x["kind"] == "asset.failed"]
    assert len(again) == 1 and again[0]["id"] != first and events.get(db, cfg, first, ADMIN) is None
    assert events.get_many(db, cfg, [first, again[0]["id"], a["id"]], ADMIN) == {again[0]["id"]: again[0], a["id"]: a}
    # the timeline narrows by kind in its query; a viewer of another namespace sees none of it
    ids = [x["id"] for x in events.page(db, cfg, ADMIN, flt={"kinds": ["asset.failed"]})["items"]]
    assert ids == [again[0]["id"]]
    assert events.page(db, cfg, events.viewer(9, {store.ns_id(db, "calls")}), flt={"kinds": ["asset.processed"]})["items"] == []


def test_a_deletion_is_logged_once_as_asset_deleted(db, cfg):
    sam = make_user(db, "sam@example.org", "sam password 1")
    pods = store.ns_id(db, "pods")
    rid = ingest.import_text(db, cfg, "pods", "[00:00] Sam: Old minutes.", title="Old minutes")
    start(db, cfg, "test.gone")
    deletion.delete(db, cfg, rid, "sam@example.org")
    got = new(db, cfg, "test.gone", kinds=["asset.deleted"])
    assert len(got) == 1
    a = got[0]
    assert a["kind"] == "asset.deleted" and a["type"] == "Delete" and a["space"] == pods and a["id"].startswith("log-")
    assert a["actor"] == {"type": "Person", "id": f"lens:account/{sam}", "name": "sam"}
    assert a["object"] == {"type": "Document", "id": f"lens:recording/{rid}", "name": "Old minutes", "media": "transcript"}
    assert a["summary"] == "sam deleted “Old minutes”"
    # logged once per asset, whatever retries
    assert events_content.deleted(db, rid, {"title": "Old minutes", "space": pods, "source": "transcript"}, "sam@example.org") == a["id"]
    assert len(db.values("SELECT VALUE id FROM event_log WHERE kind = 'asset.deleted'")) == 1


def test_an_event_that_moves_is_calendar_event_changed(db, cfg, monkeypatch):
    pods = store.ns_id(db, "pods")
    src = source(db, "Family calendar", "ical", user="")
    body = {"now": ICS.encode()}
    monkeypatch.setattr(feeds, "fetch", lambda cfg, src, path: body["now"])
    f = {"path": "evening.ics", "title": "Parents' evening", "size": len(ICS), "modified": store.now(), "when": None}
    rid, _ = sources._ingest(db, cfg, src, f, "transcript", pods, "watch:1")
    start(db, cfg, "test.moves")
    # the same event, read again, starts a day later
    body["now"] = ICS.replace("20261020T17", "20261021T17").replace("20261020T18", "20261021T18").encode()
    again, _ = sources._ingest(db, cfg, src, {**f, "modified": "2026-10-16T08:00:00+00:00"}, "transcript", pods, "watch:1")
    assert again == rid and db.one("SELECT event FROM $r", r=R("recording", rid))["event"]["startTime"] == "2026-10-21T17:00:00+00:00"
    got = new(db, cfg, "test.moves", kinds=["calendar_event.changed"])
    assert len(got) == 1
    a = got[0]
    assert a["actor"] == {"type": "Service", "id": f"lens:source/{src['id']}", "name": "Family calendar"} and a["type"] == "Update"
    assert a["object"]["startTime"] == "2026-10-21T17:00:00+00:00" and a["object"]["endTime"] == "2026-10-21T18:30:00+00:00"
    assert a["object"]["before"] == {"startTime": "2026-10-20T17:00:00+00:00", "endTime": "2026-10-20T18:30:00+00:00"}
    assert a["summary"] == "Family calendar moved “Parents' evening” to 2026-10-21 17:00" and a["object"]["id"] == f"lens:recording/{rid}"
    # read again unchanged, or the same move noticed twice: nothing more
    sources._ingest(db, cfg, src, {**f, "modified": "2026-10-16T09:00:00+00:00"}, "transcript", pods, "watch:1")
    old = {"startTime": "2026-10-20T17:00:00+00:00"}
    new_ = db.one("SELECT event FROM $r", r=R("recording", rid))["event"]
    assert events_content.calendar_moved(db, rid, pods, src, "Parents' evening", old, new_) == a["id"]
    assert len(db.values("SELECT VALUE id FROM event_log WHERE kind = 'calendar_event.changed'")) == 1
