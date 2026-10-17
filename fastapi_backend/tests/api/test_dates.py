"""Dates said in assets, read as days on the calendar (analysis.dates): the rules, the analyze step keeping them, and
agents finding them by day through the API, MCP and the assistant, only in what the person can read."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from app.domain import ai_tools, dates, ingest, jobs, store
from tests.api.test_mcp import tool
from tests.api.test_oauth import _bearer, _grant
from tests.helpers import drain, login, make_user

R = store.R
WED = dt.date(2026, 10, 14)  # a Wednesday


@pytest.mark.parametrize(
    ("said", "day", "until", "period", "relative"),
    [
        ("next Tuesday", "2026-10-20", None, "day", True),
        ("this Friday", "2026-10-16", None, "day", True),
        ("next Friday", "2026-10-23", None, "day", True),
        ("last Friday", "2026-10-09", None, "day", True),
        ("yesterday", "2026-10-13", None, "day", True),
        ("the day after tomorrow", "2026-10-16", None, "day", True),
        ("in three days", "2026-10-17", None, "day", True),
        ("5 days from now", "2026-10-19", None, "day", True),
        ("last week", "2026-10-05", "2026-10-11", "week", True),
        ("two weeks ago", "2026-09-28", "2026-10-04", "week", True),
        ("next weekend", "2026-10-24", "2026-10-25", "weekend", True),
        ("next month", "2026-11-01", "2026-11-30", "month", True),
        ("last year", "2025-01-01", "2025-12-31", "year", True),
        ("3 March 2024", "2024-03-03", None, "day", False),
        ("3rd of March", "2026-03-03", None, "day", True),
        ("March the 5th, 2025", "2025-03-05", None, "day", False),
        ("March 2024", "2024-03-01", "2024-03-31", "month", False),
        ("2026-10-20", "2026-10-20", None, "day", False),
        ("20/10/2026", "2026-10-20", None, "day", False),
    ],
)
def test_dates_are_read_as_days(said, day, until, period, relative):
    got = dates.find(f"We said {said}, I think.", WED)
    assert [(d["text"], d["day"], d["until"], d["period"], d["relative"]) for d in got] == [(said, day, until or day, period, relative)]


def test_what_could_be_two_dates_is_left_out():
    for line in ("see you on Friday", "on 10/11/2026", "I may 3 times", "about 2000 people", "31 February 2024", "for three years"):
        assert dates.find(line, WED) == [], line
    assert [d["text"] for d in dates.find("in 2024, about 2000 people", WED)] == ["2024"]
    # without the asset's date, only dates that give their year
    assert [d["text"] for d in dates.find("next Tuesday, March 3rd or 5 May 2024", None)] == ["5 May 2024"]
    assert dates.day_bound("2026-02", last=True) == "2026-02-28" and dates.day_bound("2024") == "2024-01-01"
    with pytest.raises(ValueError):
        dates.day_bound("next week")


PLAN = """Ann|N|Let's meet next Tuesday to go over the budget.
Ben|N|The report from 3 March 2024 still holds, and the launch is in March 2027.
Ann|N|Okay, nothing else."""


def _plan(db, cfg):
    rid = ingest.import_text(db, cfg, "pods", PLAN, "Planning", "auto")
    db.q("UPDATE $r SET recorded_at = '2026-10-14T10:00:00'", r=R("recording", rid))
    jid = jobs.enqueue(db, rid, ["analyze"])
    drain(db, cfg)
    return rid, jid


def test_off_by_default_reads_none(db, cfg):
    rid, _ = _plan(db, cfg)
    assert not db.rows("SELECT * FROM date_said WHERE recording = $r", r=rid)


def test_agents_find_what_was_said_about_a_day(client, db, cfg):
    cfg["analysis"]["dates"] = True
    rid, jid = _plan(db, cfg)
    assert any("read 3 date(s) as days on the calendar" in line for line in db.one("SELECT log FROM $j", j=R("job", jid))["log"])
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    make_user(db, "ca@x.io", "calls password 1", roles={"calls": "viewer"})
    vi, ca = login(client, "vi@x.io", "viewer password 1"), login(client, "ca@x.io", "calls password 1")

    r = client.get("/api/v1/dates", headers=vi, params={"start": "2026-10", "end": "2026-10"})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["reading"] is True and got["total"] == 1
    d = got["items"][0]
    assert (d["day"], d["said"], d["relative"], d["recording_id"], d["idx"]) == ("2026-10-20", "next Tuesday", True, rid, 0)
    assert d["text"] == "Let's meet next Tuesday to go over the budget." and d["title"] == "Planning"
    # a range takes a month that overlaps it; everything, the earliest first
    assert [x["said"] for x in client.get("/api/v1/dates", headers=vi, params={"start": "2027-03-15"}).json()["items"]] == ["March 2027"]
    assert [x["day"] for x in client.get("/api/v1/dates", headers=vi).json()["items"]] == ["2024-03-03", "2026-10-20", "2027-03-01"]
    assert client.get("/api/v1/dates", headers=vi, params={"q": "launch"}).json()["total"] == 2  # the line's words count
    assert client.get("/api/v1/dates", headers=vi, params={"start": "soon"}).status_code == 400
    assert client.get("/api/v1/dates", headers=ca).json()["total"] == 0  # not their namespace

    # over MCP, signed in as the viewer
    _, tokens = _grant(client, vi, scope="read")
    out = tool(client, _bearer(tokens), "list_dates", start="2026-10-20", end="2026-10-20")
    assert out["reading"] is True and [x["said"] for x in out["dates"]] == ["next Tuesday"]
    assert out["dates"][0]["url"].endswith(f"/resources/{rid}") and out["dates"][0]["relative"] is True

    # the assistant cites the line
    box = ai_tools.Toolbox(db, cfg, {"id": 1, "email": "vi@x.io"}, {store.ns_id(db, "pods")}, set(), None, None)
    res, note = box.call("find_dates", {"start": "2026-10-19", "end": "2026-10-25"})
    found = json.loads(res)["dates"]
    assert note == "Found 1 date(s)" and found[0]["said"] == "next Tuesday" and found[0]["ref"] == 1
    assert box.refs[0]["text"] == "Let's meet next Tuesday to go over the budget."

    # turned off, analysis takes them away again
    cfg["analysis"]["dates"] = False
    jobs.enqueue(db, rid, ["analyze"])
    drain(db, cfg)
    assert not db.rows("SELECT * FROM date_said WHERE recording = $r", r=rid)
