"""Review marks on the timeline (docs/timeline.md): each person marks activities reviewed, important or hidden for
themselves alone, one at a time or many at once, filters the timeline by them, and agents do the same over MCP and in
the assistant."""

from __future__ import annotations

import json

from app.domain import ai_tools, auth, event_marks, events, store
from tests.helpers import login, make_user, seed

URL = "/api/v1/activities"
ASSETS = {"kinds": "asset.added"}  # analysis changes the graph too (entity.changed); these tests mark the assets


def _env(client, db, cfg, folder):
    ids = seed(db, cfg, folder)
    root = make_user(db, "root@x.io", "root password 1", admin=True)
    vi = make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    return {
        "ids": [f"create-recording-{i}" for i in ids],
        "root": root,
        "vi": vi,
        "hr": login(client, "root@x.io", "root password 1"),
        "hv": login(client, "vi@x.io", "viewer password 1"),
    }


def _ids(page):
    return [a["id"] for a in page["items"]]


def test_marks_are_set_cleared_and_filter_the_timeline(client, db, cfg, folder):
    env = _env(client, db, cfg, folder)
    pods1, pods2, call = env["ids"]
    h = env["hr"]
    assert client.get(URL, headers=h).json()["marks"] == {}
    got = client.post(f"{URL}/{pods2}/mark", json={"reviewed": True}, headers=h).json()
    assert got["reviewed"] is True and got["important"] is False and got["hidden"] is False and got["at"]
    page = client.get(URL, headers=h).json()
    assert list(page["marks"]) == [pods2] and page["marks"][pods2]["reviewed"] is True
    # filters, joined to the page: reviewed, unreviewed, important, hidden
    assert _ids(client.get(URL, params={**ASSETS, "reviewed": "true"}, headers=h).json()) == [pods2]
    assert _ids(client.get(URL, params={**ASSETS, "reviewed": "false"}, headers=h).json()) == [call, pods1]
    # a filtered page reads on past what the filter leaves out, so a page of one isn't empty
    one = client.get(URL, params={**ASSETS, "reviewed": "false", "limit": 1}, headers=h).json()
    two = client.get(URL, params={**ASSETS, "reviewed": "false", "limit": 1, "before": one["next"]}, headers=h).json()
    assert _ids(one) + _ids(two) == [call, pods1]
    # marks left out stay as they are
    assert client.post(f"{URL}/{pods2}/mark", json={"important": True}, headers=h).json()["reviewed"] is True
    assert _ids(client.get(URL, params={**ASSETS, "important": "true", "reviewed": "true"}, headers=h).json()) == [pods2]
    assert client.post(f"{URL}/{call}/mark", json={"hidden": True}, headers=h).status_code == 200
    assert _ids(client.get(URL, params={**ASSETS, "hidden": "false"}, headers=h).json()) == [pods2, pods1]
    assert _ids(client.get(URL, params={**ASSETS, "hidden": "true"}, headers=h).json()) == [call]
    # cleared: a mark with nothing set is gone
    assert client.post(f"{URL}/{call}/mark", json={"hidden": False}, headers=h).json() == {
        "reviewed": False,
        "important": False,
        "hidden": False,
        "at": None,
    }
    assert db.one("SELECT count() AS n FROM event_mark GROUP ALL")["n"] == 1
    client.post(f"{URL}/{pods2}/mark", json={"reviewed": False, "important": False}, headers=h)
    assert db.rows("SELECT * FROM event_mark") == [] and client.get(URL, headers=h).json()["marks"] == {}
    # bad marks
    assert client.post(f"{URL}/{pods2}/mark", json={}, headers=h).status_code == 400
    assert client.post(f"{URL}/{pods2}/mark", json={"starred": True}, headers=h).status_code == 422
    assert client.post(f"{URL}/create-recording-999/mark", json={"reviewed": True}, headers=h).status_code == 404
    assert client.post(f"{URL}/{pods2}/mark", json={"reviewed": True}).status_code == 401


def test_many_at_once_and_only_what_you_can_see(client, db, cfg, folder):
    env = _env(client, db, cfg, folder)
    pods1, pods2, call = env["ids"]
    r = client.post(f"{URL}/mark", json={"ids": [pods1, pods2, call, "log-77", pods1], "reviewed": True}, headers=env["hv"])
    assert r.status_code == 200
    out = r.json()
    # the calls asset isn't the viewer's to see: it is unknown to them, like one there is none of
    assert sorted(out["marks"]) == [pods1, pods2] and out["unknown"] == [call, "log-77"]
    assert all(m["reviewed"] for m in out["marks"].values())
    assert _ids(client.get(URL, params={**ASSETS, "reviewed": "false"}, headers=env["hv"]).json()) == []
    assert client.post(f"{URL}/{call}/mark", json={"reviewed": True}, headers=env["hv"]).status_code == 404
    # marks are each person's own: the admin sees none of the viewer's, and their own don't touch the viewer's
    assert client.get(URL, headers=env["hr"]).json()["marks"] == {}
    assert _ids(client.get(URL, params={**ASSETS, "reviewed": "false"}, headers=env["hr"]).json()) == [call, pods2, pods1]
    client.post(f"{URL}/mark", json={"ids": [pods2], "reviewed": False, "important": True}, headers=env["hr"])
    assert client.get(URL, headers=env["hv"]).json()["marks"][pods2] == {**out["marks"][pods2]}
    assert client.get(URL, headers=env["hr"]).json()["marks"][pods2]["important"] is True
    # limits and read-only tokens
    assert client.post(f"{URL}/mark", json={"ids": [], "reviewed": True}, headers=env["hv"]).status_code == 422
    assert client.post(f"{URL}/mark", json={"ids": [pods1] * 501, "reviewed": True}, headers=env["hv"]).status_code == 422
    tok = client.post("/api/v1/tokens", json={"name": "ro", "scope": "read"}, headers=env["hv"]).json()["token"]
    ro = {"Authorization": f"Bearer {tok}"}
    assert client.get(URL, headers=ro).json()["marks"][pods1]["reviewed"] is True  # reads them
    assert client.post(f"{URL}/mark", json={"ids": [pods1], "reviewed": False}, headers=ro).status_code == 403
    # a person removed takes their marks with them
    auth.remove_account(db, env["vi"])
    assert db.values("SELECT VALUE account FROM event_mark") == [env["root"]]


def test_one_look_reads_many(db, cfg, folder, monkeypatch):
    monkeypatch.setattr(events, "_KINDS", dict(events._KINDS))  # the test kind is gone after the test
    events.kind("test_thing.happened", label="Thing happened", source="log", as_type="Announce", object_type="Note")
    ids = [f"create-recording-{i}" for i in seed(db, cfg, folder)]
    lid = events.emit(db, "test_thing.happened", actor=events.LENS, obj={"type": "Note", "name": "x"}, space=None, summary="Lens noted x")
    admin = events.viewer(1, None, admin=True)
    got = events.get_many(db, cfg, [*ids, lid, "nope-1", "Bad Id"], admin)
    assert sorted(got) == sorted([*ids, lid])
    assert all(got[i] == events.get(db, cfg, i, admin) for i in got)
    pods = events.viewer(2, {store.ns_id(db, "pods")})
    assert sorted(events.get_many(db, cfg, [*ids, lid], pods)) == sorted(ids[:2])


def test_a_tidy_drops_marks_and_reactions_left_on_activities_that_are_gone(db, cfg, folder, monkeypatch):
    from app.domain import event_reactions

    monkeypatch.setattr(events, "_KINDS", dict(events._KINDS))
    monkeypatch.setitem(event_marks._tidied, "after", "")
    events.kind("test_thing.happened", label="Thing happened", source="log", as_type="Announce", object_type="Note")
    pods1, pods2, call = (f"create-recording-{i}" for i in seed(db, cfg, folder))
    lid = events.emit(db, "test_thing.happened", actor=events.LENS, obj={"type": "Note", "name": "x"}, space=None, summary="Lens noted x")
    event_marks.mark(db, 1, [pods1, pods2, call, lid], {"reviewed": True})
    for i in (pods1, lid):
        event_reactions.react(db, 1, i, "👍")
    db.q("UPDATE event_mark SET at = '2026-01-01T00:00:00+00:00'")
    db.q("UPDATE event_reaction SET at = '2026-01-01T00:00:00+00:00'")
    # the asset deleted and the log entry past events.keep_days; a mark made today on what was never there waits a day
    db.q("DELETE $r", r=store.R("recording", int(pods1.rsplit("-", 1)[1])))
    db.q("DELETE $r", r=store.R("event_log", int(lid.rsplit("-", 1)[1])))
    event_marks.mark(db, 1, ["create-recording-99999"], {"important": True})
    # two at a time: the first look stops at the second id and the next carries on from there
    monkeypatch.setattr(event_marks, "TIDY_BATCH", 2)
    assert event_marks.tidy(db, cfg, now=store.now()) == 1  # create-recording-<pods1>, then create-recording-<pods2>
    assert event_marks._tidied["after"] == sorted([pods1, pods2, call, lid])[1]
    while event_marks._tidied["after"]:
        event_marks.tidy(db, cfg, now=store.now())
    event_marks.tidy(db, cfg, now=store.now())  # past the end: from the start again, nothing more to drop
    assert sorted(db.values("SELECT VALUE activity FROM event_mark")) == sorted([pods2, call, "create-recording-99999"])
    assert db.values("SELECT VALUE activity FROM event_reaction") == []


def test_agents_mark_and_read_what_they_have_not_reviewed(client, db, cfg, folder):
    from tests.api.test_mcp import tool, tool_error

    env = _env(client, db, cfg, folder)
    pods1, pods2, call = env["ids"]
    got = tool(client, env["hv"], "mark_activities", ids=[pods1, call], reviewed=True)
    assert list(got["marks"]) == [pods1] and got["unknown"] == [call]
    assert [a["id"] for a in tool(client, env["hv"], "timeline", unreviewed=True, kinds=["asset.added"])["items"]] == [pods2]
    assert tool(client, env["hv"], "timeline")["marks"][pods1]["reviewed"] is True
    assert "say which marks" in tool_error(client, env["hv"], "mark_activities", ids=[pods1])
    assert "ids is required" in tool_error(client, env["hv"], "mark_activities", reviewed=True)
    # the assistant: the person's own marks, at once
    pods = {store.ns_id(db, "pods")}
    box = ai_tools.Toolbox(db, cfg, {"id": env["vi"], "email": "vi@x.io"}, pods, set(), {}, None)
    assert {"timeline", "mark_activities"} <= {s["function"]["name"] for s in box.specs()}
    out, summary = box.call("mark_activities", {"ids": [pods2], "reviewed": True})
    assert summary == "Marked 1 activity reviewed" and json.loads(out)["marks"][pods2]["reviewed"] is True
    assert not box.outside  # what it gives back is Lens's own word
    box.call("mark_activities", {"ids": [pods2], "reviewed": False})
    out, summary = box.call("timeline", {"unreviewed": True, "kinds": ["asset.added"]})
    assert [a["id"] for a in json.loads(out)["activities"]] == [pods2]
    # the timeline's titles are text from outside Lens: marks that bring things to the person's sight act at once...
    assert box.wary()
    out, summary = box.call("mark_activities", {"ids": [pods2], "important": True})
    assert summary == "Marked 1 activity important" and json.loads(out)["marks"][pods2]["important"] is True
    listed = json.loads(box.call("timeline", {"kinds": ["asset.added"]})[0])["activities"]
    assert {a["id"]: a.get("marked") for a in listed} == {pods2: ["important"], pods1: ["reviewed"]}
    # ...and those that take things out of it ask first
    out, summary = box.call("mark_activities", {"ids": [pods2], "hidden": True})
    assert json.loads(out)["status"] == "waiting for the person's approval" and summary.startswith(
        "Asked for approval: Mark 1 activity hidden"
    )
    assert event_marks.of(db, env["vi"], [pods2])[pods2]["hidden"] is False
    aid = box.approvals[-1]["id"]
    assert ai_tools.approve(db, cfg, aid, {"id": env["vi"], "email": "vi@x.io"}, set()) == {"status": "done", "marked": 1}
    assert event_marks.of(db, env["vi"], [pods2])[pods2] | {"at": None} == {
        "reviewed": False,
        "important": True,
        "hidden": True,
        "at": None,
    }
    assert "give the ids" in json.loads(box.call("mark_activities", {"ids": [], "reviewed": True})[0])["error"]
