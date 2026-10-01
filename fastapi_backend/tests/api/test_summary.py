"""Summaries: key points and action items say where in the recording they come from; older summaries' plain strings
still read everywhere."""

from __future__ import annotations

import pytest

from app.domain import analyze, render, store, templates
from tests import fake_llm
from tests.helpers import login, make_user, seed

R = store.R


@pytest.fixture
def llm(cfg):
    srv, url = fake_llm.start()
    cfg["llm"].update(base_url=url, model="fake")
    yield fake_llm.Handler
    srv.shutdown()


def test_summary_items_have_times(client, db, cfg, folder, llm):
    a, _b, _c = seed(db, cfg, folder)
    segs = db.rows("SELECT idx, t0 FROM segment WHERE recording = $r ORDER BY idx", r=a)
    first, last = segs[0]["t0"], segs[-1]["t0"]
    out = analyze.summarize_recording(db, cfg, a)
    # the model cites line times; each becomes the start of the line shown with that time
    assert out["key_points"] == [{"text": "The capsid model beat the benchmark", "t0": first}, {"text": "No time given"}]
    assert out["action_items"] == [{"text": "Send the capsid samples", "who": "Alice", "t0": last}, {"text": "Plain follow-up"}]
    assert (out["sentiment"], out["importance"]) == ("Happy", 5)
    asked = llm.seen[-1]
    assert "[0:00]" in asked["messages"][-1]["content"] and "key_points" in asked["response_format"]["json_schema"]["schema"]["required"]
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    got = client.get(f"/api/v1/recordings/{a}", headers=login(client, "vi@x.io", "viewer password 1")).json()["summary"]
    assert got["action_items"][0]["t0"] == last

    # the report page lists them with their times
    page = render.report_recording(db, cfg, a, folder).read_text()
    assert "<h2>Key points</h2>" in page and f"{store.tc(last)}</span>" in page and "Send the capsid samples (Alice)" in page
    # templates written for plain strings still print the text; new ones can use the fields
    ctx = templates.context(db, cfg, a)
    body = "{% for x in summary.action_items %}[{{ x }}|{{ x.who }}|{{ x.t0 if x.t0 is not none else '' }}]{% endfor %}"
    assert templates.render_body(body, ctx, "export") == f"[Send the capsid samples|Alice|{last}][Plain follow-up||]"


def test_older_summaries_still_read(db, cfg, folder):
    a, _b, _c = seed(db, cfg, folder)
    old = {"summary": "Old.", "topics": ["capsid"], "action_items": ["Call Bob"], "people": [], "sentiment": "Neutral", "importance": 2}
    db.q("UPDATE $r SET summary = $s", r=R("recording", a), s=old)
    page = render.report_recording(db, cfg, a, folder).read_text()
    assert "<h2>Follow-ups</h2><ul><li>Call Bob</li></ul>" in page and "<h2>Key points</h2>" not in page
    assert templates.render_body("{{ summary.action_items|join(', ') }}", templates.context(db, cfg, a), "export") == "Call Bob"
    assert analyze.summary_view(None) == {} and analyze.summary_view("text") == "text"


def test_cited_times():
    starts = {"0:05": 5300, "1:02:03": 3723400}
    assert analyze._cited("0:05", starts, 10_000) == 5300
    assert analyze._cited("[1:02:03]", starts, 4_000_000) == 3723400
    assert analyze._cited("00:05", starts, 10_000) == 5300  # padded the same line
    assert analyze._cited("0:07", starts, 10_000) == 7000  # no line starts then: the time itself
    assert analyze._cited("9:00", starts, 10_000) is None  # past the end
    assert analyze._cited("soon", starts, 10_000) is None and analyze._cited(None, starts, 10_000) is None
    assert analyze._timed([{"text": " "}, 3, "x", {"text": "y", "who": " ", "at": "0:05"}], starts, 10_000) == [
        {"text": "x"},
        {"text": "y", "t0": 5300},
    ]
    assert analyze._timed(None, starts, 1) == []
