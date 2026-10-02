"""The MCP server's judging tools (docs/mcp.md): `ask` puts a yes/no question to passages the caller may read, `check`
judges a statement against the passages it cites. Both rest on a decision model and aren't offered without one."""

from __future__ import annotations

import pytest

from app.domain import decide, ingest
from tests import fake_decide
from tests.api.test_mcp import rpc, tool, tool_error
from tests.helpers import login, make_user

SHIP = """Ana|N|The shipment for the harbour office leaves on Friday morning.
Ben|N|We talked about the weather for a while and then went home.
Ana|N|The budget was not approved by the board this quarter."""
CALL = """Cy|N|The shipment to the island leaves on Monday instead.
Di|N|Nobody mentioned the harbour office at all.
Cy|N|We will talk again next week at the usual time."""


@pytest.fixture
def env(client, db, cfg):
    srv, url = fake_decide.start()
    decide.recovered()
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    hr, hv = login(client, "root@x.io", "root password 1"), login(client, "vi@x.io", "viewer password 1")
    pods = ingest.import_text(db, cfg, "pods", SHIP, title="Shipping notes")
    calls = ingest.import_text(db, cfg, "calls", CALL, title="Island call")
    yield {"hr": hr, "hv": hv, "pods": pods, "calls": calls, "url": url, "jev": fake_decide.Handler}
    srv.shutdown()
    decide.recovered()


def _on(client, env, **more):
    r = client.put(
        "/api/v1/settings/decisions", headers=env["hr"], json={"enabled": True, "base_url": env["url"], "model": "fake-jev", **more}
    )
    assert r.status_code == 200, r.text


def _names(client, h):
    return {t["name"] for t in rpc(client, h, "tools/list")["result"]["tools"]}


def test_the_tools_are_offered_where_a_decision_model_is_set_up(client, env):
    hv = env["hv"]
    assert not {"ask", "check"} & _names(client, hv)
    assert "needs a decision model" in tool_error(client, hv, "ask", question="Does the shipment leave on Friday?")
    _on(client, env)
    assert {"ask", "check"} <= _names(client, hv)
    listed = {t["name"]: t for t in rpc(client, hv, "tools/list")["result"]["tools"]}
    assert listed["ask"]["inputSchema"]["required"] == ["question"] and listed["check"]["annotations"]["readOnlyHint"] is True
    # an admin can keep them from assistants while decisions stay on for the rest
    _on(client, env, mcp=False)
    assert not {"ask", "check"} & _names(client, hv)
    assert "needs a decision model" in tool_error(client, hv, "check", statement="The shipment leaves on Friday.")


def test_ask_judges_the_passages_you_may_read(client, env):
    hv, hr = env["hv"], env["hr"]
    _on(client, env)
    out = tool(client, hv, "ask", question="Does the shipment leave on Friday?")
    assert out["answer"] == "yes" and out["passages"][0]["recording_id"] == env["pods"]
    best = out["passages"][0]
    assert (
        "leaves on Friday" in best["text"]
        and best["says_yes"] >= 0.75
        and best["line"] == 0
        and best["url"].endswith(f"/resources/{env['pods']}")
    )
    assert {p["recording_id"] for p in out["passages"]} == {env["pods"]}  # never the namespace the viewer has no role in
    assert [p["says_yes"] for p in out["passages"]] == sorted((p["says_yes"] for p in out["passages"]), reverse=True)
    asked = env["jev"].seen[-1]["questions"]["p0"]
    assert asked["type"] == "noul" and asked["instructions"]["question"] == "Does the shipment leave on Friday?"
    # an admin's assistant reads both namespaces
    assert {p["recording_id"] for p in tool(client, hr, "ask", question="Does the shipment leave?")["passages"]} == {
        env["pods"],
        env["calls"],
    }
    # within one recording; one the caller can't read isn't there
    assert tool(client, hv, "ask", question="shipment harbour?", recording_id=env["pods"])["passages"]
    assert tool_error(client, hv, "ask", question="shipment?", recording_id=env["calls"]) == "not found, or not something you can read"
    # nothing about it: unclear, and the model isn't asked
    before = len(env["jev"].seen)
    nothing = tool(client, hv, "ask", question="zeppelin")
    assert (nothing["answer"], nothing["passages"]) == ("unclear", []) and len(env["jev"].seen) == before
    # the passages found don't say so
    env["jev"].script = {"p0": {"noul": 0.1}, "p1": {"noul": 0.05}, "p2": {"noul": 0.02}}
    assert tool(client, hv, "ask", question="shipment harbour budget")["answer"] == "no"
    env["jev"].script = {"p0": {"noul": 0.5}}
    assert tool(client, hv, "ask", question="shipment harbour budget")["answer"] == "unclear"
    assert "is required" in tool_error(client, hv, "ask")
    # the model failing is said, not guessed around
    env["jev"].script, env["jev"].fail = {}, (529, {"detail": "overloaded"})
    decide.recovered()
    assert "the decision model couldn't answer" in tool_error(client, hv, "ask", question="shipment?")


def test_check_judges_a_statement_against_what_it_cites(client, env):
    hv = env["hv"]
    _on(client, env)
    pods = env["pods"]
    ok = tool(
        client, hv, "check", statement="The shipment for the harbour office leaves on Friday morning.", citations=[f"{pods}:0"], lines=1
    )
    assert ok["verdict"] == "supported" and ok["passages"][0]["judgment"] == "supports"
    p = ok["passages"][0]
    assert p["probabilities"]["supports"] == 0.9 and p["confidence"] == 0.9 and p["line"] == 0 and p["at"] == "0:00"
    # the passage says it isn't so
    no = tool(client, hv, "check", statement="The budget was approved by the board this quarter.", citations=[f"{pods}:2"], lines=1)
    assert no["verdict"] == "contradicted"
    # a citation that says nothing about it doesn't support it
    silent = tool(client, hv, "check", statement="The shipment leaves on Friday morning.", citations=[f"{pods}:1"], lines=1)
    assert silent["verdict"] == "unsupported" and silent["passages"][0]["judgment"] == "silent"
    both = tool(
        client, hv, "check", statement="The budget was approved by the board this quarter.", citations=[f"{pods}:2", f"{pods}:1"], lines=1
    )
    assert both["verdict"] == "contradicted"
    env["jev"].script = {
        "p0": {"choice": "supports", "probabilities": {"supports": 0.9}, "confidence": 0.9},
        "p1": {"choice": "contradicts", "probabilities": {"contradicts": 0.8}, "confidence": 0.8},
    }
    assert tool(client, hv, "check", statement="x", citations=[f"{pods}:0", f"{pods}:2"])["verdict"] == "mixed"
    # an answer the model isn't sure of supports nothing
    env["jev"].script = {
        "p0": {"choice": "supports", "probabilities": {"supports": 0.4, "silent": 0.35, "contradicts": 0.25}, "confidence": 0.4}
    }
    assert tool(client, hv, "check", statement="x", citations=[f"{pods}:0"])["verdict"] == "unsupported"
    env["jev"].script = {}
    # without citations: what the archive has about it, in what the caller may read
    found = tool(client, hv, "check", statement="The shipment leaves on Friday morning.")
    assert found["verdict"] == "supported" and {p["recording_id"] for p in found["passages"]} == {pods}
    assert tool(client, hv, "check", statement="zeppelin")["verdict"] == "unsupported"
    # citations are checked like everything else
    assert "recording_id:line" in tool_error(client, hv, "check", statement="x", citations=["twelve"])
    assert tool_error(client, hv, "check", statement="x", citations=[f"{env['calls']}:0"]) == "not found, or not something you can read"
    assert "has no line 99" in tool_error(client, hv, "check", statement="x", citations=[f"{pods}:99"])
    assert "at most 8" in tool_error(client, hv, "check", statement="x", citations=[f"{pods}:0"] * 9)


def test_search_results_carry_the_judged_relevance(client, env):
    hv = env["hv"]
    assert "relevance" not in tool(client, hv, "search", query="shipment")["results"][0]
    _on(client, env)
    env["jev"].script = {"h0": {"noul": 0.91}}
    assert tool(client, hv, "search", query="shipment")["results"][0]["relevance"] == 0.91
