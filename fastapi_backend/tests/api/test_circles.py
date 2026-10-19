"""Circles of trust: who someone may put in a circle, sharing an activity or a resource with a circle, what each person
then sees in their own timeline, giving the item itself, undo, vaults, and agents doing the same over MCP and the
assistant."""

from __future__ import annotations

import json
import time

import pytest

from app.domain import access, ai_tools, auth, circles, events, store
from tests.api.test_mcp import rpc, tool, tool_error
from tests.helpers import login, make_user, seed

R = store.R
URL = "/api/v1/circles"
FEED = "/api/v1/activities"


@pytest.fixture
def env(client, db, cfg, folder):
    a, b, c = seed(db, cfg, folder)
    ids = {
        "adeel": make_user(db, "adeel@x.io", "adeel password 1", roles={"pods": "owner", "calls": "owner"}),
        "sam": make_user(db, "sam@x.io", "sam password 1", roles={"calls": "viewer"}),
        "sara": make_user(db, "sara@x.io", "sara password 1", roles={"calls": "viewer"}),
        "ed": make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"}),
        "stranger": make_user(db, "stranger@x.io", "stranger password 1"),
    }
    auth.rename_account(db, ids["adeel"], "Adeel")
    auth.rename_account(db, ids["sam"], "Sam")
    store.ns_id(db, "elsewhere")
    auth.set_role(db, ids["stranger"], store.ns_id(db, "elsewhere"), "viewer")
    yield {
        "rec": (a, b, c),
        "ids": ids,
        **{
            k: login(client, f"{k}@x.io", p)
            for k, p in (
                ("adeel", "adeel password 1"),
                ("sam", "sam password 1"),
                ("sara", "sara password 1"),
                ("ed", "editor password 1"),
                ("stranger", "stranger password 1"),
            )
        },
    }


def _call(client, h, name, args):
    """An MCP tool whose arguments include `name`, which tool() takes for the tool's own."""
    res = rpc(client, h, "tools/call", {"name": name, "arguments": args})["result"]
    assert res["isError"] is False, res
    return res["structuredContent"]


def _feed(client, h, kinds="share.received,share.sent"):
    return client.get(FEED, params={"kinds": kinds}, headers=h).json()["items"]


def _family(client, env):
    r = client.post(URL, headers=env["adeel"], json={"name": "Immediate family", "members": ["sam@x.io", env["ids"]["sara"]]})
    assert r.status_code == 201, r.text
    return r.json()


def test_circles_are_your_own(client, db, env):
    ids = env["ids"]
    got = client.get(URL, headers=env["adeel"]).json()
    assert got["circles"] == [] and got["suggested"] == list(circles.SUGGESTED)
    # people you can add: those you share a namespace with, never yourself; strangers aren't listed or addable
    people = {p["email"] for p in client.get(f"{URL}/people", headers=env["adeel"]).json()}
    assert people == {"sam@x.io", "sara@x.io", "ed@x.io"}
    r = client.post(URL, headers=env["adeel"], json={"name": "Friends", "members": ["stranger@x.io"]})
    assert r.status_code == 400 and "isn't someone you can add" in r.json()["detail"]
    assert client.post(URL, headers=env["adeel"], json={"name": "Friends", "members": ["nobody@x.io"]}).status_code == 400
    fam = _family(client, env)
    assert [m["name"] for m in fam["members"]] == ["Sam", "sara"]
    assert client.post(URL, headers=env["adeel"], json={"name": "immediate  family"}).status_code == 400
    listed = client.get(URL, headers=env["adeel"]).json()
    assert [c["id"] for c in listed["circles"]] == [fam["id"]] and "Immediate family" not in listed["suggested"]
    # nobody else sees or changes it
    assert client.get(URL, headers=env["sam"]).json()["circles"] == []
    assert client.get(f"{URL}/{fam['id']}", headers=env["sam"]).status_code == 404
    assert client.patch(f"{URL}/{fam['id']}", headers=env["sam"], json={"name": "Mine"}).status_code == 404
    assert client.delete(f"{URL}/{fam['id']}", headers=env["sam"]).status_code == 404
    # someone who has you in a circle can add you back
    assert "adeel@x.io" in {p["email"] for p in client.get(f"{URL}/people", headers=env["sam"]).json()}
    assert "adeel@x.io" not in {p["email"] for p in client.get(f"{URL}/people", headers=env["stranger"]).json()}
    # rename, add and take out
    r = client.patch(f"{URL}/{fam['id']}", headers=env["adeel"], json={"name": "Family", "add": [ids["ed"]], "remove": ["sara@x.io"]})
    assert r.status_code == 200 and r.json()["name"] == "Family"
    assert [m["id"] for m in r.json()["members"]] == [ids["sam"], ids["ed"]]
    assert client.delete(f"{URL}/{fam['id']}", headers=env["adeel"]).json() == {"ok": True}
    assert client.get(URL, headers=env["adeel"]).json()["circles"] == []


def test_sharing_with_a_circle(client, db, cfg, env):
    a, b, c = env["rec"]
    ids = env["ids"]
    fam = _family(client, env)
    shares = f"{URL}/shares"
    # an activity from the timeline, with a note: each member sees it in their own timeline, the sharer in theirs
    added = next(
        x
        for x in client.get(FEED, params={"kinds": "asset.added"}, headers=env["adeel"]).json()["items"]
        if x["object"]["id"] == f"lens:recording/{a}"
    )
    r = client.post(shares, headers=env["adeel"], json={"activity": added["id"], "circles": [fam["id"]], "note": "Trip on Friday"})
    assert r.status_code == 201, r.text
    first = r.json()
    assert first["circles"] == ["Immediate family"] and {p["email"] for p in first["people"]} == {"sam@x.io", "sara@x.io"}
    assert first["object"]["about"] == added["id"] and first["object"]["summary"] == added["summary"] and first["granted"] == 0
    got = _feed(client, env["sam"])
    assert len(got) == 1 and got[0]["kind"] == "share.received" and got[0]["type"] == "Announce"
    item = got[0]
    assert item["actor"] == {"type": "Person", "id": f"lens:account/{ids['adeel']}", "name": "Adeel"}
    assert item["summary"] == f"Adeel shared “{added['object']['name']}” with you"
    assert item["object"]["content"] == "Trip on Friday" and "url" not in item["object"]  # Sam can't open it
    assert item["lens"]["audience"] == "owner" and set(item["lens"]["untrusted"]) == set(circles.UNTRUSTED) and item["space"] is None
    assert client.get(f"{FEED}/{item['id']}", headers=env["sam"]).json()["id"] == item["id"]
    assert [x["kind"] for x in _feed(client, env["sara"])] == ["share.received"]
    sent = _feed(client, env["adeel"])
    assert [x["kind"] for x in sent] == ["share.sent"] and sent[0]["summary"].endswith("with Immediate family")
    assert sent[0]["object"]["people"] == ["Sam", "sara"]
    # nobody else sees either, admins included
    assert _feed(client, env["ed"]) == [] and client.get(f"{FEED}/{item['id']}", headers=env["adeel"]).status_code == 404
    listed = client.get(shares, headers=env["sam"]).json()
    assert [s["id"] for s in listed] == [first["id"]] and listed[0]["by"]["email"] == "adeel@x.io" and listed[0]["activity"] == item["id"]
    assert [s["id"] for s in client.get(shares, params={"direction": "sent"}, headers=env["adeel"]).json()] == [first["id"]]

    # with the item: those who can't open the resource are given permission on it, and undo takes it back
    r = client.post(shares, headers=env["adeel"], json={"recording": b, "people": ["sam@x.io"], "with_item": True})
    assert r.status_code == 201, r.text
    second = r.json()
    assert second["granted"] == 1 and access.has_permission(db, b, ids["sam"])
    newest = _feed(client, env["sam"])[0]
    assert newest["object"]["url"] == f"/explore/recordings/{b}" and newest["object"]["with_item"] is True
    assert client.get(f"/api/v1/public/recordings/{b}", headers=env["sam"]).status_code == 200
    assert client.delete(f"{shares}/{second['id']}", headers=env["sam"]).status_code == 404  # only the sharer undoes
    assert client.delete(f"{shares}/{second['id']}", headers=env["adeel"]).json() == {"undone": second["id"], "taken_back": 1}
    assert not access.has_permission(db, b, ids["sam"]) and len(_feed(client, env["sam"])) == 1
    assert [x["kind"] for x in _feed(client, env["adeel"])] == ["share.sent"]

    # what can't be shared: more than one thing, nobody, someone not in a circle, what you can't see, a vault's
    def no(h, body, status=400):
        r = client.post(shares, headers=h, json=body)
        assert r.status_code == status, r.text
        return r.json()["detail"]

    assert "one thing" in no(env["adeel"], {"recording": a, "activity": added["id"], "circles": [fam["id"]]})
    assert "isn't in any of your circles" in no(env["adeel"], {"recording": a, "people": ["ed@x.io"]})
    assert no(env["adeel"], {"recording": a, "circles": [999]}, 404)
    assert no(env["sam"], {"recording": a, "circles": [fam["id"]]}, 404)  # not Sam's circle, and not his to see
    empty = client.post(URL, headers=env["adeel"], json={"name": "Friends"}).json()
    assert "nobody to share with" in no(env["adeel"], {"recording": a, "circles": [empty["id"]]})
    ed = client.post(URL, headers=env["ed"], json={"name": "Team", "members": ["adeel@x.io"]}).json()
    assert "publish permission" in no(env["ed"], {"recording": a, "circles": [ed["id"]], "with_item": True}, 403)
    db.q("UPSERT $r SET vault = true", r=R("data_key", store.ns_id(db, "calls")))
    assert "vault" in no(env["adeel"], {"recording": c, "circles": [fam["id"]]})
    # a share stays shared when its circle is deleted
    client.delete(f"{URL}/{fam['id']}", headers=env["adeel"])
    assert len(_feed(client, env["sam"])) == 1


def _start(db, cfg, consumer):
    """A follower's first look marks where it starts: what's shared in that second isn't seen, so wait for the next."""
    assert events.follow(db, cfg, consumer)[0] == []
    t = store.now()
    while store.now() == t:
        time.sleep(0.05)


def test_automations_follow_shares(db, cfg, env):
    a, _, _ = env["rec"]
    ids = env["ids"]
    me = {"id": ids["adeel"], "email": "adeel@x.io", "name": "Adeel", "admin": False}
    cid = circles.create(db, me, "Immediate family", [ids["sam"]])
    _start(db, cfg, "test-circles")
    circles.share(db, cfg, me, recording=a, circles_=[cid], note="Look")
    acts, commit = events.follow(db, cfg, "test-circles", kinds=["share.received", "share.sent"])
    commit()
    assert sorted(x["kind"] for x in acts) == ["share.received", "share.sent"]
    got = next(x for x in acts if x["kind"] == "share.received")
    assert events.visible(got, events.viewer(ids["sam"], set())) and not events.visible(got, events.viewer(ids["adeel"], None, admin=True))
    # a follower of some namespaces doesn't read shares, which belong to none
    _start(db, cfg, "test-circles-ns")
    circles.share(db, cfg, me, recording=a, circles_=[cid])
    acts, _ = events.follow(db, cfg, "test-circles-ns", kinds=["share.received"], spaces={store.ns_id(db, "pods")})
    assert acts == []
    # someone removed for good loses their circles and what was shared with them
    auth.remove_account(db, ids["sam"])
    assert circles.get(db, cid, ids["adeel"])["members"] == []
    assert db.values("SELECT VALUE id FROM circle_share_to WHERE account = $a", a=ids["sam"]) == []


def test_agents_share_too(client, db, cfg, env):
    a, b, _ = env["rec"]
    ids = env["ids"]
    # MCP: make a circle, share, list, take it back
    made = _call(client, env["adeel"], "save_circle", {"name": "Immediate family", "members": ["sam@x.io"]})["circle"]
    assert [m["email"] for m in made["members"]] == ["sam@x.io"]
    out = tool(client, env["adeel"], "share_with_circle", recording_id=a, circles=[made["id"]], note="For you")
    assert out["people"][0]["email"] == "sam@x.io"
    listed = tool(client, env["sam"], "list_circles", shares="received")
    assert listed["circles"] == [] and [s["id"] for s in listed["shares"]] == [out["id"]]
    assert "isn't in any of your circles" in tool_error(client, env["adeel"], "share_with_circle", recording_id=a, people=["ed@x.io"])
    assert tool(client, env["adeel"], "undo_share", share_id=out["id"]) == {"undone": out["id"], "taken_back": 0}
    assert _call(client, env["adeel"], "save_circle", {"circle_id": made["id"], "name": "Family"})["circle"]["name"] == "Family"
    # the assistant asks first: nothing is shared until the person says yes
    me = {"id": ids["adeel"], "email": "adeel@x.io", "name": "Adeel"}
    pods = store.ns_id(db, "pods")
    box = ai_tools.Toolbox(db, cfg, me, {pods}, {pods}, None, None)
    seen = json.loads(box.call("circles", {})[0])
    assert [c["name"] for c in seen["circles"]] == ["Family"]
    asked = json.loads(box.call("share_with_circle", {"recording_id": b, "circles": [made["id"]], "note": "Dinner plans"})[0])
    assert asked["summary"].startswith("Share “") and asked["summary"].endswith("with Family")
    assert circles.shares(db, {"id": ids["sam"]}) == []
    assert box.call("share_with_circle", {"recording_id": b, "people": ["ed@x.io"]})[1].endswith("add them to one first")
    done = ai_tools.approve(db, cfg, asked["approval"], me, {pods})
    assert done["status"] == "done"
    got = circles.shares(db, {"id": ids["sam"]})
    assert len(got) == 1 and got[0]["note"] == "Dinner plans" and got[0]["by"]["name"] == "Adeel"
    assert db.one("SELECT via FROM $r", r=R("circle_share", got[0]["id"]))["via"] == "assistant"


def test_rules_share_on_their_own_only_when_let(client, db, cfg, env):
    from app.domain import ingest

    _family(client, env)
    graph = {
        "nodes": [
            {"id": "i", "type": "input", "config": {}},
            {
                "id": "s",
                "type": "action",
                "config": {
                    "action": "share_with_circle",
                    "params": {"circles": ["immediate family"], "note": "New from school: {{ event.object.name }}"},
                },
            },
        ],
        "edges": [{"source": "i", "target": "s"}],
    }
    rule = {"name": "School news to the family", "triggers": [{"as": "new", "on": "event", "kinds": ["asset.added"]}], "graph": graph}
    r = client.post("/api/v1/automations", headers=env["adeel"], json=rule)
    assert r.status_code == 200, r.text
    aid = r.json()["id"]
    rec = ingest.import_text(db, cfg, "pods", "Sam: The trip is on Friday.", title="Trip on Friday")
    one = f"create-recording-{rec}"
    # it reaches other people, so it asks first
    run = client.post(f"/api/v1/automations/{aid}/run", headers=env["adeel"], json={"activity": one}).json()
    assert run["status"] == "waiting", run
    assert _feed(client, env["sam"]) == []
    # until the person lets it run on its own
    assert client.post(f"/api/v1/automations/{aid}/policy", headers=env["adeel"], json={"node": "s", "value": "auto"}).status_code == 200
    run = client.post(f"/api/v1/automations/{aid}/run", headers=env["adeel"], json={"activity": one}).json()
    assert run["status"] == "done", run
    [got] = _feed(client, env["sam"], "share.received")
    assert got["object"]["content"] == "New from school: Trip on Friday" and got["object"]["name"] == "Trip on Friday"
    assert got["actor"]["name"] == "Adeel"
    assert db.one("SELECT via FROM circle_share")["via"] == "automation"
    assert db.one("SELECT email FROM audit_log WHERE action = 'circle.share'")["email"] == "automation: School news to the family"
    # a circle that isn't theirs fails the node, saying why
    from app.domain import actions

    intent = {
        "key": "1-s",
        "action": "share_with_circle",
        "params": {"circles": ["Friends"], "activity": one},
        "effect": "out",
        "risk": "routine",
        "untrusted": False,
        "spaces": [],
        "run_as": env["ids"]["adeel"],
        "node": "s",
        "automation": aid,
        "run": 1,
    }
    with pytest.raises(ValueError, match="Adeel has no circle Friends"):
        actions.perform(db, cfg, intent, None)
    # what the person can't see isn't shared
    with pytest.raises(ValueError, match="can't see"):
        actions.perform(
            db, cfg, {**intent, "params": {"circles": ["Immediate family"], "activity": one}, "run_as": env["ids"]["sam"]}, None
        )
    assert actions.perform(db, cfg, {**intent, "params": {"circles": ["Immediate family"], "activity": one}}, None, dry=True)["would"] == (
        "share “Trip on Friday” with Immediate family"
    )


def test_when_people_are_free(client, db, cfg, env):
    from app.domain import ingest

    ids = env["ids"]
    fam = _family(client, env)
    own = store.ns_id(db, "adeel-own")
    auth.set_role(db, ids["adeel"], own, "owner")

    def event(ns, start, end, organizer=None, attendees=None):
        rid = ingest.import_text(db, cfg, ns, f"Event at {start}.", title="Dentist")
        db.q(
            "UPDATE $r SET event = $e",
            r=R("recording", rid),
            e={"startTime": start, "endTime": end, "organizer": organizer, "attendees": attendees or []},
        )

    event("pods", "2026-10-20T10:00:00+00:00", "2026-10-20T11:00:00+00:00", organizer="adeel@x.io")
    event("pods", "2026-10-20T12:00:00+00:00", "2026-10-20T13:00:00+00:00", organizer="ed@x.io")  # not his
    event("adeel-own", "2026-10-20T14:00:00", "2026-10-20T15:00:00")  # his own calendar, a floating time
    ask = {"people": ["adeel@x.io"], "start": "2026-10-20T09:00", "end": "2026-10-20T18:00", "minutes": 60}
    # nothing until he lets the circle see
    got = client.post(f"{URL}/when-free", headers=env["sam"], json=ask).json()
    assert [{k: v for k, v in p.items() if v is not None} for p in got["people"]] == [
        {"id": ids["sam"], "name": "Sam", "busy": []},
        {"person": "adeel@x.io", "refused": "adeel@x.io hasn't let you see when they're free"},
    ]
    r = client.patch(f"{URL}/{fam['id']}", headers=env["adeel"], json={"lets": ["free_busy"]})
    assert r.json()["lets"] == ["free_busy"]
    got = client.post(f"{URL}/when-free", headers=env["sam"], json=ask).json()
    adeel = next({k: v for k, v in p.items() if v is not None} for p in got["people"] if p.get("id") == ids["adeel"])
    assert adeel == {
        "id": ids["adeel"],
        "name": "Adeel",
        "busy": [
            {"start": "2026-10-20T10:00+00:00", "end": "2026-10-20T11:00+00:00"},
            {"start": "2026-10-20T14:00+00:00", "end": "2026-10-20T15:00+00:00"},
        ],
    }
    assert got["free"] == [
        {"start": "2026-10-20T09:00+00:00", "end": "2026-10-20T10:00+00:00"},
        {"start": "2026-10-20T11:00+00:00", "end": "2026-10-20T14:00+00:00"},
        {"start": "2026-10-20T15:00+00:00", "end": "2026-10-20T18:00+00:00"},
    ]
    # in another time zone, the same times
    got = client.post(
        f"{URL}/when-free", headers=env["sam"], json={**ask, "tz": "Europe/London", "start": "2026-10-20T11:00", "end": "2026-10-20T12:00"}
    ).json()
    assert next(p for p in got["people"] if p.get("id") == ids["adeel"])["busy"] == [
        {"start": "2026-10-20T11:00+01:00", "end": "2026-10-20T12:00+01:00"}
    ]
    # he sees each ask in his own timeline; nobody else does
    asked = _feed(client, env["adeel"], "agent.asked")
    assert [a["summary"] for a in asked] == ["Sam asked when you're free, Tue 20 Oct – Tue 20 Oct"] * 2
    assert _feed(client, env["sam"], "agent.asked") == []
    # a stranger, someone he hasn't let see, and an agent over MCP
    assert client.post(f"{URL}/when-free", headers=env["stranger"], json=ask).json()["people"][1]["refused"]
    assert client.post(f"{URL}/when-free", headers=env["ed"], json=ask).json()["people"][1]["refused"]
    out = tool(client, env["sara"], "when_free", people=["adeel@x.io"], start="2026-10-20T09:00", end="2026-10-20T12:00")
    assert out["free"] == [
        {"start": "2026-10-20T09:00+00:00", "end": "2026-10-20T10:00+00:00"},
        {"start": "2026-10-20T11:00+00:00", "end": "2026-10-20T12:00+00:00"},
    ]
    assert _feed(client, env["adeel"], "agent.asked")[0]["summary"].startswith("sara's agent asked when you're free")
    assert client.post(f"{URL}/when-free", headers=env["sam"], json={**ask, "end": "2026-12-20T09:00"}).status_code == 400
    assert client.post(f"{URL}/when-free", headers=env["sam"], json={**ask, "tz": "Mars/Base"}).status_code == 400
    # the assistant asks too, without an approval: it only reads when
    box = ai_tools.Toolbox(db, cfg, {"id": ids["sam"], "email": "sam@x.io", "name": "Sam"}, set(), set(), None, None)
    res = json.loads(
        box.call("when_free", {"people": ["adeel@x.io"], "start": "2026-10-20T09:00", "end": "2026-10-20T18:00", "minutes": 60})[0]
    )
    assert len(res["free"]) == 3 and _feed(client, env["adeel"], "agent.asked")[0]["summary"].startswith("Sam's agent asked")
