"""Podcast routes and MCP tools: who may make an episode about what, where it goes, and what they see of it."""

from __future__ import annotations

import pytest

from app.domain import store
from tests import fake_llm
from tests.api.test_mcp import tool, tool_error
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
    make_user(db, "ca@x.io", "calls password 1", roles={"calls": "editor"})
    make_user(db, "root@x.io", "root password 1", admin=True)
    yield {
        "ids": (a, b, c),
        "ed": login(client, "ed@x.io", "editor password 1"),
        "vi": login(client, "vi@x.io", "viewer password 1"),
        "ca": login(client, "ca@x.io", "calls password 1"),
        "root": login(client, "root@x.io", "root password 1"),
    }
    srv.shutdown()


def test_making_and_reading_an_episode(client, db, cfg, env):
    a, b, c = env["ids"]
    body = {
        "selection": {"recordings": [a], "excerpts": [{"recording": b, "idx": 1}]},
        "prompt": "for a beginner",
        "length": 5,
        "style": "beginner",
    }
    # a viewer can't add an episode anywhere
    assert client.post("/api/v1/podcasts", headers=env["vi"], json=body).status_code == 400
    # nobody may pick what they can't read
    assert client.post("/api/v1/podcasts", headers=env["ed"], json={"selection": {"recordings": [c]}}).status_code == 404
    assert client.post("/api/v1/podcasts", headers=env["ed"], json={"selection": {}}).status_code == 400
    assert client.post("/api/v1/podcasts", headers=env["ed"], json={**body, "style": "opera"}).status_code == 422

    # an editor who can't add to the podcasts namespace gets the episode in the sources' namespace
    r = client.post("/api/v1/podcasts", headers=env["ed"], json=body)
    assert r.status_code == 202, r.text
    out = r.json()
    assert (out["namespace"], out["placed"]) == ("pods", "sources")
    rid = out["episode"]
    got = client.get(f"/api/v1/podcasts/{rid}", headers=env["vi"]).json()
    assert got["status"] == "queued" and got["job"] == out["job"] and got["namespace"] == "pods"
    assert got["request"]["style"] == "beginner" and got["request"]["prompt"] == "for a beginner"

    drain(db, cfg)
    got = client.get(f"/api/v1/podcasts/{rid}", headers=env["vi"]).json()
    assert got["status"] == "script_only" and got["job"] is None and got["title"] == "Capsids and shipments"
    assert got["lines"][2]["sources"][0]["recording"] == a and "t0" in got["lines"][2]["sources"][0]
    assert got["checks"] and got["sources"][0]["title"] == "ep1" and got["audio"] is None
    assert [e["id"] for e in client.get("/api/v1/podcasts", headers=env["vi"]).json()] == [rid]
    assert client.get("/api/v1/podcasts", headers=env["ca"]).json() == []
    assert client.get(f"/api/v1/podcasts/{rid}", headers=env["ca"]).status_code == 404
    # it's a recording too: its transcript reads like any other
    rec = client.get(f"/api/v1/recordings/{rid}", headers=env["vi"]).json()
    assert rec["title"] == "Capsids and shipments"
    assert client.get(f"/api/v1/podcasts/{a}", headers=env["vi"]).status_code == 404  # not an episode

    # editors remake and delete it; viewers can't
    assert client.post(f"/api/v1/podcasts/{rid}/regenerate", headers=env["vi"]).status_code == 403
    r = client.post(f"/api/v1/podcasts/{rid}/regenerate", headers=env["ed"])
    assert r.status_code == 202 and client.get(f"/api/v1/podcasts/{rid}", headers=env["ed"]).json()["job"] == r.json()["job"]
    assert client.post(f"/api/v1/podcasts/{rid}/regenerate", headers=env["ed"]).json()["job"] == r.json()["job"]  # one at a time
    drain(db, cfg)
    assert client.delete(f"/api/v1/podcasts/{rid}", headers=env["vi"]).status_code == 403
    assert client.delete(f"/api/v1/podcasts/{rid}", headers=env["ed"]).json() == {"ok": True}
    assert client.get(f"/api/v1/podcasts/{rid}", headers=env["ed"]).status_code == 404
    assert not db.one("SELECT id FROM $r", r=R("recording", rid))


def test_admins_fill_the_podcasts_namespace(client, db, cfg, env):
    a, _b, c = env["ids"]
    out = client.post("/api/v1/podcasts", headers=env["root"], json={"selection": {"recordings": [a, c]}}).json()
    assert (out["namespace"], out["placed"]) == ("podcasts", "podcasts")
    # its readers are only admins, so a pods viewer doesn't see it
    assert client.get(f"/api/v1/podcasts/{out['episode']}", headers=env["vi"]).status_code == 404


def test_agents_make_episodes(client, db, cfg, env):
    a, b, c = env["ids"]
    he = env["ed"]
    assert "give recording_ids or lines" in tool_error(client, he, "create_podcast")
    assert 'lines are "<recording_id>:<line>"' in tool_error(client, he, "create_podcast", lines=["nope"])
    assert "not found" in tool_error(client, he, "create_podcast", recording_ids=[c])
    out = tool(client, he, "create_podcast", lines=[f"{a}:2", f"{b}:0"], prompt="compare them", style="debate", length=3)
    assert out["namespace"] == "pods" and out["url"].endswith(f"/resources/{out['episode']}")
    assert tool(client, he, "get_podcast", episode_id=out["episode"])["status"] == "queued"
    drain(db, cfg)
    got = tool(client, env["vi"], "get_podcast", episode_id=out["episode"])
    assert got["status"] == "script_only" and got["script"][0]["speaker"] == "Alex"
    assert any("/resources/" in u for line in got["script"] for u in line.get("cites") or [])
    assert got["changed_by_fact_check"] and got["sources"][0]["n"] == 1
    assert "not found" in tool_error(client, env["ca"], "get_podcast", episode_id=out["episode"])
    assert "not found" in tool_error(client, env["vi"], "get_podcast", episode_id=a)
