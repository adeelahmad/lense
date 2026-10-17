"""An automation's workflow (scope event) over the API (docs/automations.md, The event workflow scope): saved with its
own checks and the leaf rule, kept out of the canvas's list, never run by a pipeline or a routine, and tried on an
activity or a kind's sample, with what each action would do."""

from __future__ import annotations

from app.domain import ingest
from tests.helpers import login, make_user, seed, without

URL = "/api/v1/workflows"


def node(nid, t, **config):
    return {"id": nid, "type": t, "config": config}


def edge(a, b, branch=None):
    return {"source": a, "target": b, **({"branch": branch} if branch else {})}


LOG = {
    "nodes": [node("i", "input"), node("a", "action", action="log_line", params={"text": "new: {{ event.object.name }}"})],
    "edges": [edge("i", "a")],
}


def _root(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    return login(client, "root@x.io", "root password 1")


def test_event_workflows_are_checked_and_kept_apart(client, db, cfg, monkeypatch):
    without(monkeypatch, "match")  # deciding comes with its own module
    h = _root(client, db)
    rec = ingest.import_text(db, cfg, "pods", "Alice: Hello.", title="Hello")
    cat = client.get(URL, headers=h).json()
    types = {n["type"]: n for n in cat["node_types"]}
    assert types["action"]["scopes"] == ["event"] and types["action"]["keeps"] and types["action"]["outputs"] == ["out"]
    assert types["decide"]["dynamic"] == "outputs" and "event" in cat["scopes"]

    def problem(graph):
        r = client.post(URL, headers=h, json={"name": "x", "scope": "event", "graph": graph})
        assert r.status_code == 400, r.text
        return r.json()["detail"]

    assert "needs an action node" in problem({"nodes": [node("i", "input")], "edges": []})
    assert "choose an action" in problem({"nodes": [node("i", "input"), node("a", "action", action="nope")], "edges": [edge("i", "a")]})
    assert "text" in problem({"nodes": [node("i", "input"), node("a", "action", action="log_line", params={})], "edges": [edge("i", "a")]})
    assert "coming soon" in problem(
        {"nodes": [node("i", "input"), node("d", "decide", question="Urgent?", options=["yes", "no"])], "edges": [edge("i", "d")]}
    )
    wid = client.post(URL, headers=h, json={"name": "Say it", "scope": "event", "graph": LOG}).json()["id"]
    # the canvas's list leaves it out; asked for by scope, it's there
    assert all(w["id"] != wid for w in client.get(URL, headers=h).json()["workflows"])
    assert [w["id"] for w in client.get(URL, params={"scope": "event"}, headers=h).json()["workflows"]] == [wid]
    assert client.get(f"{URL}/{wid}", headers=h).json()["scope"] == "event"
    # its automation runs it: not on an asset, nor as a pipeline step or a routine's action
    for path, body in (
        ("/api/v1/pipelines", {"name": "p", "steps": [{"type": "workflow", "workflow": wid}]}),
        ("/api/v1/routines", {"name": "r", "actions": [{"type": "workflow", "workflow": wid}]}),
    ):
        r = client.post(path, headers=h, json=body)
        assert r.status_code == 400 and "its automation runs it" in r.json()["detail"], r.text
    r = client.post(f"{URL}/{wid}/run", headers=h, json={"recording": rec})
    assert r.status_code == 400 and "its automation runs it" in r.json()["detail"]


def test_an_event_workflow_is_tried_on_an_activity_or_a_sample(client, db, cfg, folder):
    ids = seed(db, cfg, folder)
    h = _root(client, db)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    t = client.post(f"{URL}/test", headers=h, json={"scope": "event", "kind": "asset.added", "graph": LOG}).json()
    assert t["error"] is None and t["trace"]["a"]["status"] == "done", t
    [it] = t["intents"]
    assert (
        it["action"] == "log_line"
        and it["status"] == "done"
        and it["effect"] == "read"
        and it["result"]["line"] == "new: Call with the school"
    )
    assert it["untrusted"] is True and it["params"] == {"text": "new: Call with the school", "level": "info"}
    assert any("would write" in line for line in t["log"]), t["log"]
    # on a real activity, by its id
    one = f"create-recording-{ids[0]}"
    t = client.post(f"{URL}/test", headers=h, json={"scope": "event", "activity": one, "graph": LOG}).json()
    title = client.get(f"/api/v1/activities/{one}", headers=h).json()["object"]["name"]
    assert t["intents"][0]["result"]["line"] == f"new: {title}"
    # what it can't be tried on
    for body, code, words in (
        ({"activity": "create-recording-999"}, 404, "no such activity"),
        ({"kind": "email.nope"}, 400, "no kind email.nope"),
        ({}, 400, "give an activity"),
    ):
        r = client.post(f"{URL}/test", headers=h, json={"scope": "event", "graph": LOG, **body})
        assert r.status_code == code and words in r.json()["detail"], r.text
    # only an admin tries workflows
    hv = login(client, "vi@x.io", "viewer password 1")
    assert client.post(f"{URL}/test", headers=hv, json={"scope": "event", "kind": "asset.added", "graph": LOG}).status_code == 403
