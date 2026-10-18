"""Quiet processing (quiet.py): heavy steps only in their hours, and only so many at once."""

from __future__ import annotations

import datetime as dt

import pytest

from app.domain import ingest, jobs, quiet, settings, store
from tests import helpers
from tests.helpers import drain


def at(h, m=0):
    return dt.datetime(2026, 10, 18, h, m, tzinfo=dt.UTC)


def test_the_window_and_its_checks():
    assert quiet.in_window("01:00-06:00", now=at(3)) and not quiet.in_window("01:00-06:00", now=at(6))
    assert quiet.in_window("23:00-06:00", now=at(23, 30)) and quiet.in_window("23:00-06:00", now=at(2))
    assert not quiet.in_window("23:00-06:00", now=at(12))
    assert quiet.in_window("01:00-06:00", "Europe/London", now=at(1, 30))  # 02:30 there in summer time
    assert not quiet.in_window("01:00-06:00", "Europe/London", now=at(5, 30))
    for bad in ("1:00-6:00", "01:00-01:00", "25:00-06:00", "night"):
        with pytest.raises(ValueError):
            settings._check("workers", "heavy_hours", bad, None)
    assert settings._check("workers", "heavy_hours", "  ", None) is None  # empty: any time
    with pytest.raises(ValueError):
        settings._check("workers", "heavy_at_once", 0, None)
    with pytest.raises(ValueError):
        settings._check("workers", "heavy_steps", ["transcribe", "nap"], None)
    with pytest.raises(ValueError):
        settings._check("workers", "timezone", "Mars/Olympus", None)


def test_heavy_steps_wait_for_their_hours_and_their_turn(cfg, db, folder):
    tr = folder / "call.txt"
    tr.write_text("[00:00] Mara: Here is the plan.")
    rid = ingest.import_transcript(db, cfg, "pods", tr, log=helpers.quiet)
    jid = jobs.enqueue(db, rid, ["transcribe"], by="test")  # it says why there is nothing to do
    assert quiet.held(db, cfg) == set()  # off by default: nothing waits

    now = dt.datetime.now(dt.UTC)
    later = now + dt.timedelta(hours=2)
    cfg["workers"]["heavy_hours"] = f"{later:%H:%M}-{later + dt.timedelta(hours=1):%H:%M}"  # not now
    assert "transcribe" in quiet.held(db, cfg) and "analyze" not in quiet.held(db, cfg)
    drain(db, cfg)
    job = db.one("SELECT status, next_step FROM $j", j=store.R("job", jid))
    assert (job["status"], job["next_step"]) == ("queued", "transcribe")  # still waiting for its hours

    cfg["workers"]["heavy_hours"] = f"{now - dt.timedelta(hours=1):%H:%M}-{now + dt.timedelta(hours=1):%H:%M}"
    cfg["workers"]["heavy_at_once"] = 1
    db.q("CREATE job:9000 SET status = 'running', next_step = 'ocr', recording = $r, steps = ['ocr']", r=rid)
    assert quiet.held(db, cfg) == set(quiet.HEAVY)  # one heavy step running already: the limit
    assert quiet.held(db, cfg, job=9000) == set()  # not counting the job asking
    drain(db, cfg)
    assert db.one("SELECT status FROM $j", j=store.R("job", jid))["status"] == "queued"

    db.q("UPDATE job:9000 SET status = 'succeeded', next_step = NONE")
    drain(db, cfg)
    assert db.one("SELECT status FROM $j", j=store.R("job", jid))["status"] == "succeeded"
