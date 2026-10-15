"""Google Calendar as a source: signing in as a service account (alone, or acting as a Workspace user) or as a
connected Google account, its calendars as folders and their events as files, browsed, watched and imported like an
iCal feed's. Everything runs against tests/fake_google.py, never Google."""

from __future__ import annotations

import json

import pytest

from app.domain import calendars, gcal, google_auth, sources
from tests import fake_google
from tests.helpers import drain, login, make_user

TEAM = "harbour@group.calendar.google.com"
HOLIDAYS = "en.irish#holiday@group.v.calendar.google.com"
MARA = "mara@harbour.example"
MEET = "https://meet.google.com/abc-defg-hij"


def _at(when, zone="Europe/Berlin"):
    return {"date": when} if len(when) == 10 else {"dateTime": when, "timeZone": zone}


def _event(eid, summary, start, end, updated="2026-09-25T12:00:00.000Z", **more):
    return {
        "id": eid,
        "iCalUID": f"{eid.split('_')[0]}@google.com",
        "status": "confirmed",
        "summary": summary,
        "start": _at(start),
        "end": _at(end),
        "created": "2026-09-01T08:00:00.000Z",
        "updated": updated,
        "htmlLink": f"https://www.google.com/calendar/event?eid={eid}",
        **more,
    }


def _team_events(updated="2026-09-25T12:00:00.000Z", description=None):
    return [
        _event(
            "standup",
            "Harbour stand-up",
            "2026-10-02T10:00:00+02:00",
            "2026-10-02T10:30:00+02:00",
            updated,
            location="Pier 4, meeting room",
            description=description
            or 'Agenda: the night crossing<br>the new buoys and <a href="https://example.org/report">the keeper\'s report</a>',
            organizer={"email": "mara@example.org", "displayName": "Mara Keane"},
            attendees=[
                {"email": "tom@example.org", "displayName": "Tom Byrne", "responseStatus": "accepted"},
                {"email": "ann@example.org", "responseStatus": "needsAction", "optional": True},
            ],
            recurrence=["RRULE:FREQ=WEEKLY;BYDAY=FR"],
            hangoutLink=MEET,
        ),
        _event(
            "standup_20261009T080000Z",
            "Harbour stand-up (moved)",
            "2026-10-09T11:30:00+02:00",
            "2026-10-09T12:00:00+02:00",
            recurringEventId="standup",
            originalStartTime=_at("2026-10-09T10:00:00+02:00"),
        ),
        {
            "id": "standup_20261016T080000Z",
            "status": "cancelled",
            "recurringEventId": "standup",
            "originalStartTime": _at("2026-10-16T10:00:00+02:00"),
        },
        _event("regatta", "Autumn regatta", "2026-10-17", "2026-10-19", description="Two days on the water."),
        _event("office", "Office", "2026-10-05", "2026-10-06", eventType="workingLocation"),
        _event("old", "Launch of the old boat", "2020-03-01T09:00:00Z", "2020-03-01T10:00:00Z"),
    ]


@pytest.fixture
def google(monkeypatch):
    g = fake_google.Google()
    monkeypatch.setattr(google_auth, "TOKEN_URL", g.base + "/token")
    monkeypatch.setattr(gcal, "API", g.base + "/calendar/v3")
    google_auth._TOKENS.clear()
    gcal._LISTED.clear()
    gcal._CALENDARS.clear()
    g.calendars[TEAM] = {"summary": "Harbour team", "timeZone": "Europe/Berlin", "events": _team_events(), "readers": set()}
    g.calendars[HOLIDAYS] = {"summary": "Holidays in Ireland", "timeZone": "Europe/Dublin", "events": [], "readers": {MARA}}
    g.calendars[MARA] = {
        "summary": MARA,
        "timeZone": "Europe/Dublin",
        "events": [  # without a time zone of its own: the calendar's
            {
                **_event("dentist", "Dentist", "2026-10-20T15:00:00+01:00", "2026-10-20T15:30:00+01:00", visibility="private"),
                "start": {"dateTime": "2026-10-20T15:00:00+01:00"},
                "end": {"dateTime": "2026-10-20T15:30:00+01:00"},
            }
        ],
        "readers": {MARA},
    }
    g.lists[MARA] = [MARA, TEAM, HOLIDAYS]
    yield g
    g.close()


@pytest.fixture
def admin(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    return login(client, "root@x.io", "root password 1")


def _service_account(google, readers=(TEAM,)):
    key, public = fake_google.service_account_key()
    google.service_accounts[key["client_email"]] = public
    for c in readers:
        google.calendars[c]["readers"].add(key["client_email"])
    return key


def _segments(db, rid):
    return " ".join(s["text"] for s in db.rows("SELECT idx, text FROM segment WHERE recording = $r ORDER BY idx", r=rid))


def test_a_shared_calendar_read_as_a_service_account(google, client, db, cfg, admin):
    h = admin
    key = _service_account(google)
    assert client.get("/api/v1/sources/backends", headers=h).json()["gcal"] == {
        "label": "Google Calendar",
        "fields": {"calendars": "", "impersonate": "", "days_back": "365"},
        "secrets": ["token", "service_account_credentials"],
        "oauth": None,
        "signin": None,
    }
    # on its own, a service account has no calendars: it's told what to do
    body = {"name": "team", "type": "gcal", "secrets": {"service_account_credentials": json.dumps(key)}}
    r = client.post("/api/v1/sources", headers=h, json=body)
    assert r.status_code == 200, r.text
    sid, health = r.json()["id"], r.json()["health"]
    assert not health["ok"] and f"{key['client_email']} has no calendars of its own: share a calendar" in health["error"]
    r = client.patch(f"/api/v1/sources/{sid}", headers=h, json={"params": {"calendars": TEAM}})
    assert r.json()["health"]["ok"], r.text
    # the key is never shown again; its token_uri is never followed
    src = client.get("/api/v1/sources", headers=h).json()[0]
    assert src["secrets"] == {"token": {"secret": True, "set": False}, "service_account_credentials": {"secret": True, "set": True}}
    assert "PRIVATE KEY" not in json.dumps(src)
    assert [c for c in google.calls if c[0] == "token"]  # at Google's own token endpoint (here, the fake)

    top = client.get(f"/api/v1/sources/{sid}/browse", headers=h).json()
    assert [(e["path"], e["name"], e["dir"]) for e in top] == [(TEAM, "Harbour team", True)]
    entries = client.get(f"/api/v1/sources/{sid}/browse", headers=h, params={"path": TEAM}).json()
    # the cancelled occurrence, the working location and the event from 2020 aren't files
    assert [e["name"] for e in entries] == [
        "2026-10-17 Autumn regatta.ics",
        "2026-10-09 Harbour stand-up (moved).ics",
        "2026-10-02 Harbour stand-up.ics",
    ]
    standup = entries[2]
    assert (standup["path"], standup["title"], standup["when"], standup["modified"]) == (
        f"{TEAM}/standup.ics",
        "Harbour stand-up",
        "2026-10-02T10:00:00+02:00",
        "2026-09-25T12:00:00+00:00",
    )
    assert client.get(f"/api/v1/sources/{sid}/browse", headers=h, params={"path": "nobody@example.org"}).status_code == 400

    # watched with backfill: every event becomes a text resource, titled and dated by the event
    r = client.post(
        "/api/v1/watches", headers=h, json={"source": sid, "path": TEAM, "namespace": "pods", "stable_seconds": 0, "backfill": True}
    )
    assert r.status_code == 200, r.text
    wid = r.json()["id"]
    assert client.post("/api/v1/watches/preview", headers=h, json={"source": sid, "path": TEAM}).json()["transcripts"] == 3
    assert sources.poll_watch(db, cfg, wid)["new"] == 3
    drain(db, cfg)
    recs = {r["title"]: r for r in db.rows("SELECT record::id(id) AS id, title, source, recorded_at, status, remote FROM recording")}
    assert set(recs) == {"Harbour stand-up", "Harbour stand-up (moved)", "Autumn regatta"}
    one = recs["Harbour stand-up"]
    assert (one["source"], one["recorded_at"], one["status"]) == ("transcript", "2026-10-02T10:00:00+02:00", "analyzed")
    assert one["remote"] == {"source": sid, "path": standup["path"]}
    text = _segments(db, one["id"])
    for said in (
        "Pier 4, meeting room",
        "Mara Keane (mara@example.org)",
        "Tom Byrne (tom@example.org), ann@example.org",
        "FREQ=WEEKLY;BYDAY=FR",
        "Friday 16 October 2026, 10:00 (Europe/Berlin)",  # the cancelled occurrence
        MEET,
        "the new buoys and the keeper's report (https://example.org/report)",
    ):
        assert said in text, said
    assert "<br>" not in text and "<a " not in text
    assert "When — Saturday 17 October 2026 to Sunday 18 October 2026 (all day)" in _segments(db, recs["Autumn regatta"]["id"])

    # listed again with nothing changed: nothing new. Changed in Google: read again into the same resource
    gcal.forget({"id": sid})
    assert sources.poll_watch(db, cfg, wid)["new"] == 0
    google.calendars[TEAM]["events"] = _team_events("2026-09-30T09:00:00.000Z", "Agenda: the harbour master joins.")
    gcal.forget({"id": sid})
    assert sources.poll_watch(db, cfg, wid)["new"] == 1
    drain(db, cfg)
    assert len(db.rows("SELECT id FROM recording")) == 3
    assert "harbour master joins" in _segments(db, one["id"]) and "buoys" not in _segments(db, one["id"])
    # the event's own .ics can be fetched (what a remote resource's file is)
    assert b"SUMMARY:Harbour stand-up" in b"".join(sources.stream(db, cfg, sid, standup["path"]))
    # every call to Google is in the activity ledger
    actions = {r["action"] for r in db.rows("SELECT action FROM activity")}
    assert {"google.token", "google.calendar"} <= actions


def test_acting_as_a_workspace_user(google, client, db, cfg, admin):
    key = _service_account(google, readers=())
    body = {"type": "gcal", "params": {"impersonate": MARA}, "secrets": {"service_account_credentials": json.dumps(key)}}
    r = client.post("/api/v1/sources", headers=admin, json=body)
    sid, health = r.json()["id"], r.json()["health"]
    assert not health["ok"] and f"act as {MARA}" in health["error"] and "domain-wide delegation" in health["error"]
    google.delegated[key["client_email"]] = {MARA}
    assert sources.test(db, cfg, sid)["ok"]
    google.calendars[TEAM]["readers"].add(MARA)
    gcal.forget({"id": sid})
    # the user's calendar list, their own calendar first; watching all of them leaves Google's holidays out
    assert [e["name"] for e in sources.browse(db, cfg, sid)] == [MARA, "Harbour team", "Holidays in Ireland"]
    files = sources.list_files(db, cfg, sources.get(db, sid), "")
    assert sorted(f["rel"] for f in files) == sorted(
        [f"{MARA}/dentist.ics", f"{TEAM}/standup.ics", f"{TEAM}/standup_20261009T080000Z.ics", f"{TEAM}/regatta.ics"]
    )
    dentist = gcal.fetch(cfg, sources.get(db, sid), f"{MARA}/dentist.ics").decode()
    assert "CLASS:PRIVATE" in dentist and "DTSTART;TZID=Europe/Dublin:20261020T150000" in dentist


def test_a_connected_google_account(google, client, db, cfg, admin):
    google.users["refresh-1"] = {"client_id": "lens-client", "client_secret": "client secret 1", "account": MARA}
    user = {"type": "authorized_user", "client_id": "lens-client", "client_secret": "client secret 1", "refresh_token": "refresh-1"}
    bad = client.post("/api/v1/sources", headers=admin, json={"type": "gcal", "secrets": {"token": "{not json"}})
    assert bad.status_code == 400 and "isn't JSON" in bad.json()["detail"]
    bad = client.post("/api/v1/sources", headers=admin, json={"type": "gcal", "secrets": {"token": json.dumps({"refresh_token": "x"})}})
    assert bad.status_code == 400 and "client_id, client_secret and refresh_token" in bad.json()["detail"]
    r = client.post("/api/v1/sources", headers=admin, json={"type": "gcal", "secrets": {"token": json.dumps(user)}})
    sid = r.json()["id"]
    assert r.json()["health"]["ok"], r.text
    assert [e["name"] for e in sources.browse(db, cfg, sid)] == [MARA, "Harbour team", "Holidays in Ireland"]
    # a token that ends early is swapped for a new one, once
    google.expire_next = True
    gcal.forget({"id": sid})
    assert [e["name"] for e in sources.browse(db, cfg, sid, MARA)] == ["2026-10-20 Dentist.ics"]
    # Google throttling the account: it works, slowly
    google.throttle = True
    health = sources.test(db, cfg, sid)
    assert health["ok"] and "rate limiting" in health["limited"]
    google.throttle = False
    # access revoked: connect again
    del google.users["refresh-1"]
    google_auth._TOKENS.clear()
    health = sources.test(db, cfg, sid)
    assert not health["ok"] and "Connect the Google account again" in health["error"]
    # without a way to sign in, it says so before calling Google
    r = client.post("/api/v1/sources", headers=admin, json={"type": "gcal"})
    assert "connect a Google account, or upload a service account key" in r.json()["health"]["error"]


def test_an_event_as_icalendar():
    cal = {"id": TEAM, "name": "Harbour team", "zone": "Europe/Berlin"}
    ev = _event(
        "x1",
        "Fog drill\nATTENDEE:mailto:intruder@example.org",  # outside text stays one property
        "2026-10-02T08:00:00Z",
        "2026-10-02T09:00:00Z",
        description="Line one\nLine two; three, four " + "long " * 40,
        attendees=[{"email": "a@example.org", "displayName": 'Ann "Skipper"; Byrne\r\nX-EVIL:1'}],
    )
    ics = gcal.event_ics(ev, cal)
    parsed = calendars.parse(ics)
    e = parsed["events"][0]
    assert parsed["name"] == "Harbour team"
    assert [k for k, _, _ in e["props"]].count("ATTENDEE") == 1 and "X-EVIL" not in [k for k, _, _ in e["props"]]
    assert calendars.summary(e) == "Fog drill\nATTENDEE:mailto:intruder@example.org"
    # no time zone of its own: the calendar's
    assert calendars.iso(calendars.start(e)) == "2026-10-02T10:00:00+02:00"
    assert all(len(l) <= 75 for l in ics.split("\r\n"))
    assert calendars.unescape(calendars.first(e, "DESCRIPTION")[1]).startswith("Line one\nLine two; three, four long")
    # a zone this machine doesn't know: kept in UTC
    utc = gcal.event_ics(_event("x2", "Call", "2026-10-02T08:00:00Z", "2026-10-02T09:00:00Z"), {**cal, "zone": "Nowhere/Atlantis"})
    assert "DTSTART;TZID=Europe/Berlin" in utc and "20261002T100000" in utc  # the event's own zone (from _at) wins
    bare = {**_event("x3", "Call", "2026-10-02T08:00:00Z", "2026-10-02T09:00:00Z"), "start": {"dateTime": "2026-10-02T08:00:00Z"}}
    assert "DTSTART:20261002T080000Z" in gcal.event_ics(bare, {**cal, "zone": "Nowhere/Atlantis"})
    assert gcal._text("plain <not html> text") == "plain <not html> text"
