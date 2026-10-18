"""Graph workflow nodes that publish: a routine picks new resources or timeline activities and adds them to a feed, or
makes a podcast episode about them; Try on the canvas says what they would do, and bad settings are refused."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from app.domain import events, routines, store
from tests import fake_llm
from tests.helpers import drain, login, make_user, seed

R = store.R


@pytest.fixture
def env(client, db, cfg, folder):
    srv, url = fake_llm.start()
    cfg["llm"].update(base_url=url, model="fake")
    fake_llm.Handler.podcast_script = None
    a, b, c = seed(db, cfg, folder)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    make_user(db, "ca@x.io", "calls password 1", roles={"calls": "editor"})  # so calls has a reader pods' readers aren't
    make_user(db, "root@x.io", "root password 1", admin=True)
    yield {
        "ids": (a, b, c),
        "ed": login(client, "ed@x.io", "editor password 1"),
        "vi": login(client, "vi@x.io", "viewer password 1"),
        "root": login(client, "root@x.io", "root password 1"),
    }
    srv.shutdown()


def _graph(*nodes):
    """A straight line of nodes after the input node."""
    ns = [{"id": "in", "type": "input", "config": {}}] + [{"id": f"n{i}", **n} for i, n in enumerate(nodes)]
    return {"nodes": ns, "edges": [{"source": ns[i]["id"], "target": ns[i + 1]["id"]} for i in range(len(ns) - 1)]}


def _workflow(client, h, name, graph):
    r = client.post("/api/v1/workflows", headers=h, json={"name": name, "scope": "graph", "graph": graph})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _routine(client, db, cfg, h, wid, namespaces):
    r = client.post(
        "/api/v1/routines",
        headers=h,
        json={
            "name": f"Publish {wid}",
            "namespaces": [store.ns_id(db, n, create=False) for n in namespaces],
            "actions": [{"type": "workflow", "workflow": wid}],
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _run(client, db, cfg, h, rid):
    run_id = routines.run(db, cfg, rid, by="test")
    run = routines.get_run(db, run_id)
    assert run["status"] == "done", run["results"]
    return run["results"][0]["result"], run["log"]


def _feed(client, h, kind="rss", title="Pods"):
    r = client.post("/api/v1/feeds", headers=h, json={"namespace": "pods", "title": title, "kind": kind})
    assert r.status_code == 201, r.text
    return r.json()


def _items(client, path):
    r = client.get(path)
    assert r.status_code == 200, r.text
    return ET.fromstring(r.content).find("channel").findall("item")


def test_a_routine_publishes_new_resources_to_a_feed(client, db, cfg, env):
    a, b, c = env["ids"]
    h = env["root"]
    feed = _feed(client, env["ed"])
    wid = _workflow(
        client,
        h,
        "New to the feed",
        _graph(
            {"type": "select_assets", "config": {"since": "all", "limit": 10}}, {"type": "publish_feed", "config": {"feed": feed["id"]}}
        ),
    )
    types = {n["type"]: n for n in client.get("/api/v1/workflows", headers=h).json()["node_types"]}
    assert types["publish_feed"]["scopes"] == ["graph"] and types["publish_feed"]["outputs"] == []
    assert types["publish_feed"]["keeps"] and types["podcast_episode"]["keeps"] and not types["select_assets"]["keeps"]
    assert types["select_assets"]["outputs"] == ["out"] and "since" in types["select_assets"]["settings"]

    # over pods: both resources there; a second run adds nothing new
    rid = _routine(client, db, cfg, h, wid, ["pods"])
    res, log = _run(client, db, cfg, h, rid)
    assert res["published"] == 2 and res["nodes"] == 3, res
    assert any("2 resource(s)" in line for line in log) and any("2 added to 'Pods'" in line for line in log)
    res, log = _run(client, db, cfg, h, rid)
    assert res["published"] == 0 and any("2 there already" in line for line in log)
    listed = client.get(f"/api/v1/feeds/{feed['id']}/items", headers=env["vi"]).json()
    assert {i["recording"] for i in listed} == {a, b}
    assert listed[0]["origin"]["routine"] == rid and listed[0]["origin"]["node"] == "n1" and listed[0]["by"] == f"routine:{rid}"
    assert {i.findtext("title") for i in _items(client, feed["path"])} == {"ep1", "ep2"}

    # what the feed's readers couldn't read is refused, said in the log, and nothing else is published
    wid2 = _workflow(
        client,
        h,
        "Calls to pods",
        _graph(
            {"type": "select_assets", "config": {"since": "all", "namespaces": ["calls"]}},
            {"type": "publish_feed", "config": {"feed": feed["id"]}},
        ),
    )
    rid2 = _routine(client, db, cfg, h, wid2, ["pods", "calls"])
    res, log = _run(client, db, cfg, h, rid2)
    assert res["published"] == 0 and any("not everyone who reads the feed can read" in line for line in log), log
    assert len(client.get(f"/api/v1/feeds/{feed['id']}/items", headers=env["vi"]).json()) == 2

    # a routine that doesn't cover the feed's namespace can't publish into it
    rid3 = _routine(client, db, cfg, h, wid, ["calls"])
    run = routines.get_run(db, routines.run(db, cfg, rid3, by="test"))
    assert run["status"] == "error" and "doesn't cover" in str(run["results"][0]), run

    # since last_run: what came in since the routine last ran
    wid3 = _workflow(
        client, h, "Fresh", _graph({"type": "select_assets", "config": {}}, {"type": "publish_feed", "config": {"feed": feed["id"]}})
    )
    rid4 = _routine(client, db, cfg, h, wid3, ["pods"])
    res, log = _run(client, db, cfg, h, rid4)  # the first run takes the past week, which holds a and b (there already)
    assert res["published"] == 0 and any("2 there already" in line for line in log), log
    db.q("UPDATE $r SET created_at = $t", r=R("recording", a), t="2020-01-01T00:00:00+00:00")
    db.q("DELETE feed_item")
    res, log = _run(client, db, cfg, h, rid4)
    assert res["published"] == 0 and any("0 resource(s)" in line for line in log), log  # nothing since the run just before
    db.q(
        "UPDATE $r SET created_at = $t", r=R("recording", b), t=events.ago(-60)
    )  # a minute ahead: after the run before, whatever the second
    res, _ = _run(client, db, cfg, h, rid4)
    assert res["published"] == 1 and [i["recording"] for i in client.get(f"/api/v1/feeds/{feed['id']}/items", headers=h).json()] == [b]


def test_a_routine_publishes_the_timeline_and_text(client, db, cfg, env):
    a, b, _c = env["ids"]
    h = env["root"]
    feed = _feed(client, env["ed"], title="Pods timeline")
    wid = _workflow(
        client,
        h,
        "Timeline to the feed",
        _graph(
            {"type": "select_activities", "config": {"kinds": ["asset.added"], "since": "all"}},
            {"type": "publish_feed", "config": {"feed": feed["id"]}},
        ),
    )
    rid = _routine(client, db, cfg, h, wid, ["pods"])
    res, log = _run(client, db, cfg, h, rid)
    assert res["published"] == 2 and any("2 activities" in line for line in log), (res, log)
    listed = client.get(f"/api/v1/feeds/{feed['id']}/items", headers=env["vi"]).json()
    assert {i["kind"] for i in listed} == {"activity"} and {i["activity"]["kind"] for i in listed} == {"import.received"}
    assert all(i["activity"]["object"] and i["categories"] == ["import.received"] for i in listed)
    assert _run(client, db, cfg, h, rid)[0]["published"] == 0

    # text a template node wrote becomes a text item, titled by the node; the same text isn't added twice
    wid2 = _workflow(
        client,
        h,
        "Digest",
        _graph(
            {"type": "select_assets", "config": {"since": "all"}},
            {"type": "template", "config": {"template": "{{ input | length }} new this week"}},
            {"type": "publish_feed", "config": {"feed": feed["id"], "title": "Weekly digest"}},
        ),
    )
    rid2 = _routine(client, db, cfg, h, wid2, ["pods"])
    assert _run(client, db, cfg, h, rid2)[0]["published"] == 1
    assert _run(client, db, cfg, h, rid2)[0]["published"] == 0
    text = [i for i in client.get(f"/api/v1/feeds/{feed['id']}/items", headers=h).json() if i["kind"] == "text"]
    assert len(text) == 1 and text[0]["title"] == "Weekly digest" and text[0]["summary"] == "2 new this week"
    assert {i.findtext("title") for i in _items(client, feed["path"])} >= {"Weekly digest"}


def test_a_routine_makes_an_episode_and_the_feed_lists_it_once_made(client, db, cfg, env):
    a, b, c = env["ids"]
    h = env["root"]
    feed = _feed(client, env["ed"], title="Pods weekly")
    wid = _workflow(
        client,
        h,
        "Weekly episode",
        _graph(
            {"type": "select_assets", "config": {"since": "all"}},
            {
                "type": "podcast_episode",
                "config": {"feed": feed["id"], "length": 5, "style": "recap", "title": "This week in pods", "max_sources": 2},
            },
        ),
    )
    rid = _routine(client, db, cfg, h, wid, ["pods", "calls"])
    res, log = _run(client, db, cfg, h, rid)
    assert res["episodes"] == 1 and res["published"] == 1, (res, log)
    ep = db.one("SELECT recording, space, status, request FROM podcast LIMIT 1")
    assert ep["status"] == "queued" and ep["space"] == feed["space"]
    assert (
        sorted(ep["request"]["selection"]["recordings"]) == [a, b] and ep["request"]["length"] == 5
    )  # c is in calls: the feed's readers can't read it
    rec = db.one("SELECT title, created_by FROM $r", r=R("recording", ep["recording"]))
    assert rec["title"] == "This week in pods" and rec["created_by"] == f"routine:{rid}"
    listed = client.get(f"/api/v1/feeds/{feed['id']}/items", headers=env["vi"]).json()
    assert [(i["kind"], i["recording"]) for i in listed] == [("episode", ep["recording"])]
    assert _items(client, feed["path"]) == []  # not until it's made
    drain(db, cfg)
    assert client.get(f"/api/v1/podcasts/{ep['recording']}", headers=env["vi"]).json()["status"] == "script_only"
    items = _items(client, feed["path"])
    # the outline's title replaces the one asked for, as it does for any episode
    assert len(items) == 1 and items[0].findtext("title") == "Capsids and shipments" and "Sources:" in items[0].findtext("description")

    # an episode about the new resources only: nothing new, no episode; and the episode isn't a source for the next one
    wid2 = _workflow(
        client,
        h,
        "Fresh episode",
        _graph({"type": "select_assets", "config": {}}, {"type": "podcast_episode", "config": {"min_sources": 2}}),
    )
    rid2 = _routine(client, db, cfg, h, wid2, ["pods"])
    db.q("UPDATE recording SET created_at = $t WHERE id IN $ids", ids=[R("recording", a), R("recording", b)], t="2020-01-01T00:00:00+00:00")
    res, log = _run(client, db, cfg, h, rid2)
    assert res.get("episodes", 0) == 0 and any("no episode" in line for line in log), log
    assert len(db.rows("SELECT id FROM podcast")) == 1
    # placed where the person asked, with sources its readers couldn't read left out
    wid3 = _workflow(
        client,
        h,
        "Into calls",
        _graph(
            {"type": "select_assets", "config": {"since": "all", "namespaces": ["pods"]}},
            {"type": "podcast_episode", "config": {"namespace": "calls"}},
        ),
    )
    rid3 = _routine(client, db, cfg, h, wid3, ["pods", "calls"])
    res, log = _run(client, db, cfg, h, rid3)
    assert res.get("episodes", 0) == 0 and sum("couldn't read; left out" in line for line in log) == 2, log


def test_try_says_what_it_would_do_and_bad_settings_are_refused(client, db, cfg, env):
    h = env["root"]
    feed = _feed(client, env["ed"])
    graph = _graph(
        {"type": "select_assets", "config": {"since": "all"}},
        {"type": "podcast_episode", "config": {"feed": feed["id"], "length": 7}},
    )
    r = client.post("/api/v1/workflows/test", headers=h, json={"scope": "graph", "graph": graph, "namespaces": ["pods"]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["error"] is None and any(
        "would make a 7-minute episode about 2 resource(s) in the feed's namespace and add it to 'Pods'" in line for line in out["log"]
    ), out["log"]
    assert not db.rows("SELECT id FROM podcast") and not db.rows("SELECT id FROM feed_item")
    graph = _graph(
        {"type": "select_activities", "config": {"since": "all", "kinds": ["asset.added"]}},
        {"type": "publish_feed", "config": {"feed": feed["id"]}},
    )
    out = client.post("/api/v1/workflows/test", headers=h, json={"scope": "graph", "graph": graph, "namespaces": ["pods"]}).json()
    assert out["error"] is None and any("would add 2 item(s) to 'Pods'" in line for line in out["log"]), out["log"]

    def bad(graph, want):
        r = client.post("/api/v1/workflows", headers=h, json={"name": "x", "scope": "graph", "graph": graph})
        assert r.status_code == 400 and want in r.json()["detail"], r.text

    bad(_graph({"type": "select_assets", "config": {}}), "needs an apply changes, publish to feed or podcast episode node")
    bad(
        _graph({"type": "select_assets", "config": {"since": "yesterday"}}, {"type": "publish_feed", "config": {"feed": feed["id"]}}),
        "since is",
    )
    bad(
        _graph({"type": "select_assets", "config": {"namespaces": ["nope"]}}, {"type": "publish_feed", "config": {"feed": feed["id"]}}),
        "no namespace nope",
    )
    bad(
        _graph({"type": "select_assets", "config": {"limit": 0}}, {"type": "publish_feed", "config": {"feed": feed["id"]}}),
        "limit is 1 to 500",
    )
    bad(
        _graph(
            {"type": "select_activities", "config": {"kinds": ["email.nope"]}}, {"type": "publish_feed", "config": {"feed": feed["id"]}}
        ),
        "no kind email.nope",
    )
    bad(_graph({"type": "publish_feed", "config": {}}), "choose a feed")
    bad(_graph({"type": "publish_feed", "config": {"feed": 999}}), "feed 999 doesn't exist")
    bad(_graph({"type": "podcast_episode", "config": {"style": "opera"}}), "style is one of")
    bad(_graph({"type": "podcast_episode", "config": {"max_sources": 2, "min_sources": 3}}), "min_sources is 1 to max_sources")
    # the nodes are for graph workflows only
    r = client.post(
        "/api/v1/workflows", headers=h, json={"name": "x", "graph": _graph({"type": "publish_feed", "config": {"feed": feed["id"]}})}
    )
    assert r.status_code == 400 and "node types are" in r.json()["detail"]
