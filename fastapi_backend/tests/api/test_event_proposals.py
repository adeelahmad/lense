"""Dates said become calendar proposals (event_proposals.py; docs/processing.md#dates, Propose an event): a date in a
recording's line is proposed as an event, prefilled from the line, that waits in Approvals; approved, the calendar adds
it (a fake Google Calendar here), declined, nothing happens. Only admins propose, and only with a calendar connection
that may add events; agents propose over MCP but can't approve."""

from __future__ import annotations

import json

import pytest

from app.domain import event_proposals, ingest, jobs, sources, store
from tests import fake_google
from tests.api.test_connection_tools import calendar  # noqa: F401 - the fake Google Calendar
from tests.api.test_google_calendar_source import TEAM
from tests.api.test_mcp import tool
from tests.api.test_oauth import _bearer, _grant
from tests.helpers import drain, login, make_user

R = store.R
CALL = """Ann|N|Thanks for calling about the budget.
Ann|N|Let's meet next Tuesday at 3 with tom@example.org to go over it.
Ben|N|And the whole of next weekend is the regatta."""


def _connect(db, cfg, cal, write=True):
    key, public = fake_google.service_account_key()
    cal.service_accounts[key["client_email"]] = public
    cal.calendars[TEAM]["readers"].add(key["client_email"])
    cal.calendars[TEAM]["writers"] = {key["client_email"]}
    params = {"calendars": TEAM, **({"write": "yes"} if write else {})}
    return sources.create(db, cfg, "Team calendar", "gcal", params, {"service_account_credentials": json.dumps(key)})


def _call(db, cfg):
    cfg["analysis"]["dates"] = True
    rid = ingest.import_text(db, cfg, "pods", CALL, "Call with Tom", "auto")
    db.q("UPDATE $r SET recorded_at = '2026-10-14T10:00:00'", r=R("recording", rid))  # a Wednesday
    jobs.enqueue(db, rid, ["analyze"])
    drain(db, cfg)
    return rid


@pytest.fixture
def root(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    return login(client, "root@x.io", "root password 1")


def _dates(client, h, rid):
    r = client.get("/api/v1/dates", headers=h, params={"recording": rid})
    assert r.status_code == 200, r.text
    return r.json()


def test_a_date_said_is_proposed_and_added_only_once_approved(client, db, cfg, calendar, root):  # noqa: F811
    _connect(db, cfg, calendar)
    rid = _call(db, cfg)
    got = _dates(client, root, rid)
    assert got["proposable"] is True
    tue = next(d for d in got["items"] if d["said"] == "next Tuesday")

    r = client.post("/api/v1/dates/propose", headers=root, json={"recording_id": rid, "segment": tue["segment"]})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["status"] == "waiting" and p["url"].endswith(f"/approvals?id={p['approval']}")
    assert p["summary"] == "Add “Let's meet next Tuesday at 3 with tom@example.org to go over it.” on 2026-10-20 15:00 to Harbour team"
    assert "Starts: 2026-10-20T15:00:00 (Europe/Berlin)" in p["preview"] and "Ends: 2026-10-20T16:00:00" in p["preview"]
    assert "Invites: tom@example.org (not emailed)" in p["preview"] and "Said in “Call with Tom” at" in p["preview"]
    assert not calendar.added  # nothing is sent before the yes
    again = client.post("/api/v1/dates/propose", headers=root, json={"recording_id": rid, "segment": tue["segment"]}).json()
    assert again["approval"] == p["approval"] and again["again"] is True  # the same one, still waiting

    waiting = client.get("/api/v1/approvals", headers=root, params={"status": "pending"}).json()
    assert [(a["id"], a["tool"], a["can"]["approve"]) for a in waiting] == [(p["approval"], "add_event", True)]
    r = client.post(f"/api/v1/approvals/{p['approval']}", headers=root, json={"decision": "approve"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "added"
    [(cal, event, q)] = calendar.added
    assert cal == TEAM and q["sendUpdates"] == "none"
    assert event["start"] == {"dateTime": "2026-10-20T15:00:00", "timeZone": "Europe/Berlin"}
    assert event["summary"].startswith("Let's meet next Tuesday at 3") and event["attendees"] == [{"email": "tom@example.org"}]

    # a weekend is all of its days; what the caller gives replaces what was guessed
    wk = next(d for d in got["items"] if d["said"] == "next weekend")
    body = {"recording_id": rid, "segment": wk["segment"], "title": "Regatta", "attendees": []}
    p2 = client.post("/api/v1/dates/propose", headers=root, json=body).json()
    assert "Starts: 2026-10-24\nEnds: 2026-10-26" in p2["preview"] and "Invites" not in p2["preview"]


def test_a_declined_proposal_adds_nothing(client, db, cfg, calendar, root):  # noqa: F811
    _connect(db, cfg, calendar)
    rid = _call(db, cfg)
    tue = next(d for d in _dates(client, root, rid)["items"] if d["said"] == "next Tuesday")
    p = client.post("/api/v1/dates/propose", headers=root, json={"recording_id": rid, "segment": tue["segment"]}).json()
    r = client.post(f"/api/v1/approvals/{p['approval']}", headers=root, json={"decision": "decline"})
    assert r.status_code == 200 and r.json()["status"] == "declined"
    assert not calendar.added
    assert db.one("SELECT status FROM $r", r=R("approval", p["approval"]))["status"] == "declined"


def test_offered_only_to_admins_with_a_calendar_that_adds_events(client, db, cfg, calendar, root):  # noqa: F811
    _connect(db, cfg, calendar, write=False)  # reads only
    rid = _call(db, cfg)
    got = _dates(client, root, rid)
    assert got["proposable"] is False
    seg = got["items"][0]["segment"]
    r = client.post("/api/v1/dates/propose", headers=root, json={"recording_id": rid, "segment": seg})
    assert r.status_code == 400 and "no calendar connection may add events" in r.json()["detail"]
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    ed = login(client, "ed@x.io", "editor password 1")
    assert _dates(client, ed, rid)["proposable"] is False
    assert client.post("/api/v1/dates/propose", headers=ed, json={"recording_id": rid, "segment": seg}).status_code == 403
    first = db.one("SELECT record::id(id) AS id FROM segment WHERE recording = $r AND idx = 0", r=rid)["id"]  # no date there
    assert client.post("/api/v1/dates/propose", headers=root, json={"recording_id": rid, "segment": first}).status_code == 404
    assert not db.rows("SELECT * FROM approval")


def test_agents_propose_over_mcp_but_cannot_approve(client, db, cfg, calendar, root):  # noqa: F811
    _connect(db, cfg, calendar)
    rid = _call(db, cfg)
    _, tokens = _grant(client, root, scope="write")
    listed = tool(client, _bearer(tokens), "list_dates", recording_id=rid)
    assert listed["proposable"] is True
    tue = next(d for d in listed["dates"] if d["said"] == "next Tuesday")
    out = tool(client, _bearer(tokens), "propose_event", recording_id=rid, line=tue["line"], start="2026-10-20T09:30")
    assert out["status"] == "waiting" and "on 2026-10-20 09:30" in out["summary"]
    # an app's token can't approve it (only the person, signed in), but can decline it
    shown = client.get(f"/api/v1/approvals/{out['approval']}", headers=_bearer(tokens)).json()
    assert shown["can"] == {"approve": False, "decline": True, "edit": False, "always": False}
    r = client.post(f"/api/v1/approvals/{out['approval']}", headers=_bearer(tokens), json={"decision": "approve"})
    assert r.status_code == 403 and "an app can only decline it" in r.json()["detail"]
    assert not calendar.added
    r = client.post(f"/api/v1/approvals/{out['approval']}", headers=_bearer(tokens), json={"decision": "decline"})
    assert r.status_code == 200 and r.json()["status"] == "declined" and not calendar.added


@pytest.mark.parametrize(
    ("line", "near", "at"),
    [
        ("Let's meet next Tuesday at 3", "next Tuesday", "15:00"),
        ("next Tuesday at 10", "next Tuesday", "10:00"),
        ("call me tomorrow, 3pm works", "tomorrow", "15:00"),
        ("tomorrow at 9:45 a.m.", "tomorrow", "09:45"),
        ("on 3 March 2027 from 15:30", "3 March 2027", "15:30"),
        ("lunch at noon tomorrow", "tomorrow", "12:00"),
        ("at 8 on Monday, and next Friday at 4 o'clock", "next Friday", "16:00"),
        ("see you on 3 March 2027", "3 March 2027", None),
        ("next Tuesday, 20 people", "next Tuesday", None),
        ("next Tuesday, from 3 March on", "next Tuesday", None),
        ("next Tuesday, by 5 people", "next Tuesday", None),
    ],
)
def test_the_time_of_day_is_read_from_the_line(line, near, at):
    assert event_proposals.time_of_day(line, near) == at
