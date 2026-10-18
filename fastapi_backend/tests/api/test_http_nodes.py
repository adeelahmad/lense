"""The HTTP call node of asset and graph workflows: out through the web tools' guard, with the host written out, the
answer passed on as outside text, credentials sealed to their address, and a try on the canvas that only reads."""

from __future__ import annotations

import http.server
import json
import threading

import pytest

from app.domain import activity, analyze, http_nodes, ingest, mcp_servers, netguard, workflows
from tests.helpers import login, make_user


class _Api(http.server.BaseHTTPRequestHandler):
    got: list = []

    def _answer(self, status, body, ctype="application/json"):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _take(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode() if n else None
        _Api.got.append({"method": self.command, "path": self.path, "headers": dict(self.headers), "body": body})

    def do_GET(self):
        self._take()
        if self.path.startswith("/away"):  # to the same server under another name: another host to urllib
            self.send_response(302)
            self.send_header("Location", f"http://localhost:{self.server.server_address[1]}/lookup")
            self.end_headers()
        elif self.path.startswith("/missing"):
            self._answer(404, '{"error": "no such thing"}')
        else:
            self._answer(200, json.dumps({"path": self.path, "key": self.headers.get("X-Api-Key")}))

    def do_POST(self):
        self._take()
        self._answer(201, "made", "text/plain")

    def log_message(self, *a):
        pass


@pytest.fixture
def api(cfg, monkeypatch):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Api)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Api.got = []
    monkeypatch.setattr(netguard, "PORTS", netguard.PORTS | {srv.server_address[1]})  # the test server's port, as if 443
    cfg["documents"]["web_networks"] = ["127.0.0.0/8"]  # a network an admin allowed: the test server's
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture
def rec(db, cfg):
    rid = ingest.import_text(db, cfg, "pods", "Alice: Ship the capsid samples Friday.\nDave: Thanks.", title="Courier call")
    analyze.analyze_recording(db, cfg, rid)
    return rid


def node(nid, t, **config):
    return {"id": nid, "type": t, "config": config}


def edge(a, b):
    return {"source": a, "target": b}


def call_graph(http):
    return {
        "nodes": [node("in", "input"), node("call", "http", **http), node("o", "output", key="answer")],
        "edges": [edge("in", "call"), edge("call", "o")],
    }


def answer(db, rid):
    return db.one("SELECT VALUE value FROM output WHERE recording = $r AND key = 'answer'", r=rid)


def test_it_calls_the_address_and_passes_the_answer_on(api, db, cfg, rec):
    graph = call_graph(
        {
            "url": api + "/lookup?title={{ recording.title | urlencode }}",
            "headers": {"X-Api-Key": "key-1", "X-Asset": "{{ recording.id }}"},
        }
    )
    wid = workflows.create(db, "lookup", graph)
    kept = workflows.get(db, wid)["graph"]["nodes"][1]["config"]
    assert mcp_servers.is_sealed(kept["headers"]["X-Api-Key"]) and "key-1" not in json.dumps(kept)
    activity.bind(db)  # as the server and the worker do
    workflows.run(db, cfg, rec, wid, say=lambda m: None)
    out = answer(db, rec)
    assert out["status"] == 200 and out["ok"] and out["untrusted"]
    assert out["json"] == {"path": "/lookup?title=Courier%20call", "key": "key-1"}  # opened only to send
    assert _Api.got[-1]["headers"]["X-Asset"] == str(rec)
    row = db.one("SELECT * OMIT id FROM activity WHERE action = 'workflow.http' ORDER BY at DESC LIMIT 1")
    assert row["detail"] == {"host": "127.0.0.1", "method": "GET"} and f"recording:{rec}" in row["resources"]

    # a 4xx answer is passed on, to branch on; a sealed key moved to another host has to be entered again
    workflows.save_version(db, wid, call_graph({**kept, "url": api + "/missing"}))
    workflows.run(db, cfg, rec, wid, say=lambda m: None)
    assert answer(db, rec)["status"] == 404 and answer(db, rec)["ok"] is False
    with pytest.raises(ValueError, match="enter the X-Api-Key header again"):
        workflows.save_version(db, wid, call_graph({**kept, "url": "https://elsewhere.example/"}))


def test_where_it_goes_is_written_out_and_guarded(api, db, cfg, rec):
    def problem(http):
        with pytest.raises(ValueError) as e:
            workflows.validate_graph(db, call_graph(http))
        return str(e.value)

    assert "host written out" in problem({"url": "https://{{ input.host }}/x"})
    assert "host written out" in problem({"url": "https://api.example.com{{ input }}"})
    assert "sends no body" in problem({"url": "https://api.example.com/", "body": "{{ input }}"})
    assert "method is one of" in problem({"url": "https://api.example.com/", "method": "TRACE"})
    assert "isn't a header it can set" in problem({"url": "https://api.example.com/", "headers": {"Host": "x"}})
    assert "1 to 60 seconds" in problem({"url": "https://api.example.com/", "seconds": 0})
    assert "http" not in workflows.SCOPES["event"] and "http" not in workflows.SCOPES["tool"]  # automations ask first

    # what flows through can't move it, and the guard keeps it to public addresses (or networks an admin allowed)
    wid = workflows.create(db, "away", call_graph({"url": api + "/{{ recording.title }}"}))
    with pytest.raises(ValueError, match="can't change where"):
        workflows.run(db, cfg, rec, wid, say=lambda m: None)
    cfg["documents"]["web_networks"] = []
    workflows.save_version(db, wid, call_graph({"url": api + "/lookup"}))
    with pytest.raises(ValueError, match="public address"):
        workflows.run(db, cfg, rec, wid, say=lambda m: None)
    cfg["documents"]["web_networks"] = ["127.0.0.0/8"]

    # a credential isn't taken to another server by a redirect
    workflows.save_version(db, wid, call_graph({"url": api + "/away", "headers": {"Authorization": "Bearer t"}}))
    with pytest.raises(ValueError, match="redirects to another server"):
        workflows.run(db, cfg, rec, wid, say=lambda m: None)
    assert [g["path"] for g in _Api.got] == ["/away"]


def test_a_try_only_reads(api, client, db, cfg, rec):
    make_user(db, "root@x.io", "root password 1", admin=True)
    h = login(client, "root@x.io", "root password 1")
    post = call_graph({"method": "POST", "url": api + "/notes", "body": '{"title": {{ recording.title | json }}}'})
    t = client.post("/api/v1/workflows/test", headers=h, json={"graph": post, "recording": rec}).json()
    assert t["error"] is None and '"would": "POST' in t["trace"]["call"]["value"] and _Api.got == []
    get = call_graph({"url": api + "/lookup"})
    t = client.post("/api/v1/workflows/test", headers=h, json={"graph": get, "recording": rec}).json()
    assert t["error"] is None and [g["method"] for g in _Api.got] == ["GET"]

    # run for real, a POST sends its body as JSON
    wid = workflows.create(db, "post", post)
    workflows.run(db, cfg, rec, wid, say=lambda m: None)
    sent = _Api.got[-1]
    assert sent["method"] == "POST" and json.loads(sent["body"]) == {"title": "Courier call"}
    assert sent["headers"]["Content-Type"] == "application/json" and answer(db, rec)["body"] == "made"
    types = {n["type"]: n for n in client.get("/api/v1/workflows", headers=h).json()["node_types"]}
    assert types["http"]["scopes"] == ["recording", "graph"]
    assert http_nodes.shown({"Authorization": "Bearer t", "Accept": "x"}) == {"Authorization": "••••", "Accept": "x"}
