"""A namespace's own chat completions (/api/v1/namespaces/<namespace>/chat/completions, docs/api.md): OpenAI's API,
answered by the configured model from that namespace alone, citing it; off until an owner turns it on."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.domain import budgets, store
from tests import fake_llm
from tests.api._assist import start_llm
from tests.helpers import login, make_user, seed

Q = "When does the Dyno Therapeutics shipment leave?"


@pytest.fixture
def llm(cfg):
    srv = start_llm(cfg)
    yield fake_llm.Handler
    srv.shutdown()
    fake_llm.Handler.usage = None


@pytest.fixture
def app(cfg, db, llm):
    from app.main import create_app

    return create_app(cfg, db, background=False)


@pytest.fixture
def env(client, db, cfg, folder):
    ids = seed(db, cfg, folder)
    make_user(db, "own@x.io", "owner password 1", roles={"pods": "owner", "calls": "viewer"})
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    make_user(db, "root@x.io", "admin password 1", admin=True)
    return {
        "ids": ids,
        "pods": store.ns_id(db, "pods"),
        "own": login(client, "own@x.io", "owner password 1"),
        "vi": login(client, "vi@x.io", "viewer password 1"),
        "root": login(client, "root@x.io", "admin password 1"),
    }


def turn_on(client, h, ns="pods", **body: Any) -> dict[str, Any]:
    r = client.patch(f"/api/v1/namespaces/{ns}/integrations", headers=h, json={"chat": True, **body})
    assert r.status_code == 200, r.text
    return r.json()


def ask(client, h, content=Q, ns="pods", status=200, **body: Any) -> dict[str, Any]:
    msgs = body.pop("messages", None) or [{"role": "user", "content": content}]
    r = client.post(f"/api/v1/namespaces/{ns}/chat/completions", headers=h, json={"model": "lens", "messages": msgs, **body})
    assert r.status_code == status, r.text
    return r.json()


def chunks(text: str) -> list[Any]:
    out = []
    for block in text.strip().split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                out.append(line[6:] if line[6:] == "[DONE]" else json.loads(line[6:]))
    return out


def _system(llm) -> str:
    return next(m["content"] for m in llm.seen[-1]["messages"] if m["role"] == "system")


def test_off_until_an_owner_turns_it_on(client, env, llm):
    got = client.get("/api/v1/namespaces/pods/integrations", headers=env["vi"]).json()
    assert (got["chat"], got["tools"], got["chat_url"]) == (False, [], "http://localhost:3000/api/v1/namespaces/pods")
    assert "search_transcripts" in [t["name"] for t in got["tool_choices"]]
    assert not {"remember", "entity_setup", "propose_entity_change"} & {t["name"] for t in got["tool_choices"]}  # reads only
    err = ask(client, env["own"], status=403)["error"]
    assert err["code"] == "endpoint_off" and "Connect apps" in err["message"]  # OpenAI's error shape
    r = client.post("/api/v1/namespaces/pods/chat/completions", json={"messages": [{"role": "user", "content": Q}]})
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_api_key" and r.headers["www-authenticate"] == "Bearer"
    # owners turn it on, and only with tools that read
    assert client.patch("/api/v1/namespaces/pods/integrations", headers=env["vi"], json={"chat": True}).status_code == 403
    r = client.patch("/api/v1/namespaces/pods/integrations", headers=env["own"], json={"chat": True, "tools": ["remember"]})
    assert r.status_code == 400 and "remember" in r.json()["detail"]
    got = turn_on(client, env["own"], tools=["search_transcripts", "list_recordings", "search_transcripts"])
    assert (got["chat"], got["mcp"], got["tools"], got["updated_by"]) == (
        True,
        False,
        ["search_transcripts", "list_recordings"],
        "own@x.io",
    )
    assert turn_on(client, env["own"], mcp=True)["tools"] == ["search_transcripts", "list_recordings"]  # the rest stays
    # someone who can't read a namespace doesn't learn it's there
    assert ask(client, env["vi"], ns="calls", status=404)["error"]["code"] == "not_found"
    assert ask(client, env["vi"], ns="nowhere", status=404)["error"]["code"] == "not_found"
    assert ask(client, env["own"], ns="calls", status=403)["error"]["code"] == "endpoint_off"
    audit = [a["action"] for a in client.get("/api/v1/audit", headers=env["root"]).json()]
    assert "namespace.integrations" in audit


def test_answers_from_the_namespace_alone_with_citations(client, env, cfg, llm):
    ep1, ep2, call_id = env["ids"]
    turn_on(client, env["own"])
    msgs = [
        {"role": "system", "content": "Answer in one line."},
        {"role": "user", "content": "Who makes capsids?"},
        {"role": "assistant", "content": "Dyno Therapeutics [1]."},
        {"role": "user", "content": [{"type": "text", "text": Q}, {"type": "image_url", "image_url": {"url": "x"}}]},
    ]
    got = ask(client, env["own"], messages=msgs)  # reads pods and calls; here, only pods
    assert (got["object"], got["model"], got["choices"][0]["finish_reason"]) == ("chat.completion", "fake", "stop")
    answer, sources = got["choices"][0]["message"]["content"].split("\n\nSources:\n")
    assert answer == "The shipment leaves on Friday [1]."
    found = got["lens"]["sources"]
    assert found and {s["recording_id"] for s in found} <= {ep1, ep2} and found[0]["cited"]
    assert got["citations"] == [s["url"] for s in found] and got["citations"][0].startswith(
        f"http://localhost:3000/resources/{found[0]['recording_id']}"
    )
    assert sources.startswith("[1] [") and got["citations"][0] in sources and got["lens"]["namespace"] == "pods"
    sent = llm.seen[-1]["messages"]
    assert "shipment leaves on Friday" not in json.dumps(sent)  # the calls namespace's line never reached the model
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "user"] and sent[1]["content"] == "Who makes capsids?"
    system = _system(llm)
    assert "from the pods namespace alone" in system and system.endswith("Answer in one line.")
    assert "Lens's rules above still hold" in system  # the app's instructions come after Lens's
    # an app asking for a format gets the answer alone
    got = ask(client, env["own"], response_format={"type": "text"})
    assert got["choices"][0]["message"]["content"] == "The shipment leaves on Friday [1]." and got["citations"]
    # the namespace's own assistant answers when it's on
    client.patch(
        "/api/v1/namespaces/pods/assistant", headers=env["own"], json={"enabled": True, "name": "Pod pal", "instructions": "Be brief."}
    )
    ask(client, env["vi"])
    assert "You are Pod pal, the assistant of the pods namespace" in _system(llm) and "Be brief." in _system(llm)
    # nothing to answer
    for bad in ([], [{"role": "assistant", "content": "hi"}], [{"role": "user", "content": "  "}], [{"role": "robot", "content": "x"}]):
        r = client.post("/api/v1/namespaces/pods/chat/completions", headers=env["own"], json={"messages": bad})
        assert r.status_code in (400, 422), bad
        if r.status_code == 400:
            assert r.json()["error"]["code"] == "invalid_messages"


def test_streams_as_openai_does(client, env, cfg, llm):
    turn_on(client, env["own"])
    r = client.post(
        "/api/v1/namespaces/pods/chat/completions",
        headers=env["vi"],
        json={"messages": [{"role": "user", "content": Q}], "stream": True, "stream_options": {"include_usage": True}},
    )
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    got = chunks(r.text)
    assert got[-1] == "[DONE]"
    first, *rest, last, done = got
    assert first["choices"][0]["delta"] == {"role": "assistant", "content": ""} and first["object"] == "chat.completion.chunk"
    text = "".join(c["choices"][0]["delta"].get("content") or "" for c in rest)
    assert text.startswith("The shipment leaves on Friday [1].\n\nSources:\n[1] [")
    assert last["choices"][0]["finish_reason"] == "stop" and last["citations"] and last["lens"]["sources"][0]["cited"]
    assert "usage" in last and len({c["id"] for c in got[:-1]}) == 1


def test_tools_the_owners_offer(client, env, cfg, llm):
    ep1, ep2, call_id = env["ids"]
    turn_on(client, env["own"], tools=["search_transcripts"])

    def tool(i, name, args):
        return {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}

    llm.tool_script = [
        {"content": "", "tool_calls": [tool(1, "search_transcripts", {"query": "Dyno Therapeutics"}), tool(2, "remember", {"fact": "x"})]},
        {"content": "Dyno Therapeutics comes up on the pods [1]."},
    ]
    got = ask(client, env["own"])
    offered = [t["function"]["name"] for t in llm.seen[0]["tools"]]
    assert offered == ["search_transcripts"]  # what the owners offered, and nothing that changes data
    steps = got["lens"]["steps"]
    assert [s["tool"] for s in steps] == ["search_transcripts", "remember"] and "unknown tool" in steps[1]["summary"], steps
    assert got["choices"][0]["message"]["content"].startswith("Dyno Therapeutics comes up on the pods [1].\n\nSources:")
    found = got["lens"]["sources"]
    assert found and {s["recording_id"] for s in found} <= {ep1, ep2}  # the search never left pods
    record = got["lens"]["record"]  # what produced it, as a chat answer's record (no message keeps it: no tokens)
    assert (record["outcome"], record["used"], record["tools"]) == (
        "answered",
        {"rounds": 1, "tool_calls": 2},
        {"picked": ["search_transcripts"], "offered": ["search_transcripts"]},
    )
    assert record["scope"] == {"namespaces": ["pods"]} and "tokens" not in record
    result = next(m["content"] for m in llm.seen[1]["messages"] if m["role"] == "tool")
    assert "Dyno Therapeutics" in result and "leaves on Friday" not in result  # calls is another namespace
    assert result.startswith("<") and 'source="tool search_transcripts"' in result  # read as data (untrusted.wrap)
    # streamed, each look-up is a comment while the answer is worked out
    llm.tool_script = [{"content": "", "tool_calls": [tool(1, "search_transcripts", {"query": "capsid"})]}, {"content": "Capsids [1]."}]
    r = client.post(
        "/api/v1/namespaces/pods/chat/completions", headers=env["own"], json={"messages": [{"role": "user", "content": Q}], "stream": True}
    )
    assert ": looked up search_transcripts" in r.text and chunks(r.text)[-2]["lens"]["steps"][0]["tool"] == "search_transcripts"
    assert "".join(c["choices"][0]["delta"].get("content") or "" for c in chunks(r.text)[:-1]).startswith("Capsids [1].\n\nSources:")
    # a model that answers without looking gets the search's excerpts instead
    llm.tool_script = [{"content": "I don't know."}]
    got = ask(client, env["own"])
    assert got["choices"][0]["message"]["content"].startswith("The shipment leaves on Friday [1].")
    # one that can't call tools answers from a search, and says so
    llm.reject_tools = True
    got = ask(client, env["own"])
    assert got["lens"]["notice"].startswith("This model can't use tools") and got["choices"][0]["message"]["content"].startswith(
        "The shipment"
    )


def test_models_tokens_and_no_model(client, env, cfg, llm):
    turn_on(client, env["own"])
    got = client.get("/api/v1/namespaces/pods/models", headers=env["vi"]).json()
    assert [m["id"] for m in got["data"]] == ["lens", "fake", "fake-large"] and got["object"] == "list"
    assert client.get("/api/v1/namespaces/calls/models", headers=env["vi"]).status_code == 404
    assert ask(client, env["vi"], model="fake-large")["model"] == "fake-large" and llm.seen[-1]["model"] == "fake-large"
    assert ask(client, env["vi"], model="gpt-4o")["model"] == "fake"  # what an app sends by default: the configured one
    # a read-only API key is enough
    key = client.post("/api/v1/tokens", headers=env["vi"], json={"name": "my app"}).json()["token"]
    assert ask(client, {"Authorization": f"Bearer {key}"})["choices"][0]["message"]["content"].startswith("The shipment")
    assert ask(client, {"Authorization": "Bearer la_nope"}, status=401)["error"]["code"] == "invalid_api_key"
    # no model set up: the best excerpts on their own
    assert client.put("/api/v1/settings/llm", headers=env["root"], json={"base_url": None}).status_code == 200
    got = ask(client, env["vi"])
    assert got["choices"][0]["message"]["content"].startswith("No language model is configured") and got["model"] == "lens"


def test_counts_for_the_namespace_and_its_budget(client, env, db, cfg, llm):
    turn_on(client, env["own"])
    llm.usage = {"prompt_tokens": 40, "completion_tokens": 8, "total_tokens": 48}
    ask(client, env["vi"])
    ref = f"space:{env['pods']}"
    assert budgets.status(db, ref, {"tokens": 1000, "period": "month"})["spent"]["tokens"] >= 48
    budgets.put(db, ref, tokens=10)
    err = ask(client, env["vi"], status=429)["error"]
    assert err["code"] == "insufficient_quota" and "budget" in err["message"]


def test_agents_turn_it_on(client, env):
    from tests.api.test_mcp_namespace import call

    is_err, got = call(
        client, env["own"], "set_namespace_integrations", server="/mcp", namespace="pods", chat=True, tools=["list_recordings"]
    )
    assert not is_err and (got["chat"], got["tools"]) == (True, ["list_recordings"]) and got["chat_url"].endswith("/api/v1/namespaces/pods")
    is_err, got = call(client, env["vi"], "namespace_integrations", server="/mcp", namespace="pods")
    assert not is_err and got["chat"] is True and "list_recordings" in [t["name"] for t in got["tool_choices"]]
