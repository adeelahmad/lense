"""Classifying a resource or a text (domain/classify.py): the decision model picks a label, the language model when
there's none, sure answers are acted on only when asked, and agents classify through MCP and the assistant."""

from __future__ import annotations

import json

import pytest

from app.domain import activity, ai_tools, content_types, store, topics
from tests import fake_jev, fake_llm
from tests.api._assist import start_llm
from tests.api.test_mcp import rpc
from tests.api.test_oauth import _bearer, _grant
from tests.helpers import login, make_user, seed

R = store.R


@pytest.fixture
def llm(cfg):
    srv = start_llm(cfg)
    yield fake_llm.Handler
    srv.shutdown()


@pytest.fixture
def jev(cfg):
    srv = fake_jev.start(cfg)
    yield fake_jev.Handler
    srv.shutdown()


@pytest.fixture
def app(cfg, db, llm, jev):
    """Built after both models are set up: the app reads its settings once, until they change."""
    from app.main import create_app

    return create_app(cfg, db, background=False)


@pytest.fixture
def env(client, db, cfg, folder):
    ep1, ep2, call = seed(db, cfg, folder)
    pods = store.ns_id(db, "pods")
    gene = topics.create(db, pods, "Gene therapy", definition="Treating disease by changing genes")
    security = topics.create(db, pods, "Security", alt=["exploits"])
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor", "calls": "viewer"})
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    return {
        "ep1": ep1,
        "ep2": ep2,
        "call": call,
        "pods": pods,
        "gene": gene,
        "security": security,
        "he": login(client, "ed@x.io", "editor password 1"),
        "hv": login(client, "vi@x.io", "viewer password 1"),
    }


def post(client, h, status=200, **body):
    r = client.post("/api/v1/classify", json=body, headers=h)
    assert r.status_code == status, r.text
    return r.json()


def test_labels_given_and_what_the_model_reads(client, db, env, jev, llm):
    jev.answer = {"type": "choice", "choice": "science", "confidence": 0.93, "probabilities": {"science": 0.93, "sport": 0.07}}
    got = post(client, env["hv"], recording=env["ep1"], labels={"science": "research, biology, medicine", "sport": "games and teams"})
    assert (got["choice"], got["confidence"], got["sure"], got["by"]) == ("science", 0.93, True, "jev")
    assert [x["label"] for x in got["ranked"]] == ["science", "sport"] and got["applied"] is None
    _, body = jev.seen[-1]
    q = body["questions"]["q"]
    assert q["instructions"] == "Which label fits this resource best?"
    assert q["criteria"] == {"science": "research, biology, medicine", "sport": "games and teams"}
    state = json.loads(body["state"])
    assert state["kind"] == "transcript" and state["file"] == "ep1.txt"
    assert "Dyno Therapeutics" in state["resource"] and state["resource"].startswith("<untrusted-")  # marked as data
    # counted in the activity ledger for the resource
    assert any(r["kind"] == "out" for r in activity.history(db, f"recording:{env['ep1']}", 50))
    # a list of labels, some with what they mean; a text; a question of your own
    got = post(client, env["hv"], text="The striker scored twice.", labels=["science", "sport: games and teams"], question="Which desk?")
    _, body = jev.seen[-1]
    assert body["questions"]["q"]["criteria"] == {"science": "science", "sport": "games and teams"}
    assert body["questions"]["q"]["instructions"] == "Which desk?"
    assert "The striker scored twice." in json.loads(body["state"])["text"]
    # without a decision model, the language model answers
    jev.status = 500
    llm.decision = {"choice": "sport", "confidence": 0.9}
    got = post(client, env["hv"], text="The striker scored twice.", labels=["science", "sport"])
    assert (got["choice"], got["by"]) == ("sport", "llm")


def test_vocabularies_and_applying_a_sure_answer(client, db, env, jev):
    ep1, he, hv = env["ep1"], env["he"], env["hv"]
    # content types of the resource's kind, by label; the key comes back
    jev.answer = {"type": "choice", "choice": "Email", "confidence": 0.95, "probabilities": {"Email": 0.95}}
    got = post(client, hv, recording=ep1, vocabulary="content_type")
    assert got["key"] == "email" and got["question"] == "What kind of text is this resource?"
    labels = set(jev.seen[-1][1]["questions"]["q"]["criteria"])
    assert "Email" in labels and "Podcast" not in labels  # only text types for a transcript
    assert content_types.of_recording(db, ep1)[1] is False  # nothing changed without apply
    # apply needs an editor; a sure answer is acted on, and audited
    post(client, hv, 403, recording=ep1, vocabulary="content_type", apply=True)
    got = post(client, he, recording=ep1, vocabulary="content_type", apply=True)
    assert got["applied"] == "content_type"
    assert content_types.of_recording(db, ep1)[0]["key"] == "email"
    assert db.values("SELECT VALUE action FROM audit_log WHERE action = 'recording.classify'") == ["recording.classify"]
    # an answer that isn't sure changes nothing
    before = topics.of_recording(db, env["ep2"])
    jev.answer = {"type": "choice", "choice": "Gene therapy", "confidence": 0.55, "probabilities": {"Gene therapy": 0.55, "Security": 0.45}}
    got = post(client, he, recording=env["ep2"], vocabulary="topic", apply=True)
    assert (got["choice"], got["id"], got["sure"], got["applied"]) == ("Gene therapy", env["gene"], False, None)
    assert topics.of_recording(db, env["ep2"]) == before
    criteria = jev.seen[-1][1]["questions"]["q"]["criteria"]
    assert criteria == {"Security": "exploits", "Gene therapy": "Treating disease by changing genes"}  # the most used first
    # a sure topic is suggested, for someone to accept
    jev.answer = {"type": "choice", "choice": "Gene therapy", "confidence": 0.9, "probabilities": {"Gene therapy": 0.9}}
    got = post(client, he, recording=env["ep2"], vocabulary="topic", apply=True)
    assert got["applied"] == "topic_suggested"
    mine = [t for t in topics.of_recording(db, env["ep2"]) if t["source"] == "classify"]
    assert [(t["label"], t["status"]) for t in mine] == [("Gene therapy", "suggested")]
    # one it already has stays as it is
    assert post(client, he, recording=env["ep2"], vocabulary="topic", apply=True)["applied"] is None
    # labels given are added as a tag
    jev.answer = None  # the first option, sure of it
    got = post(client, he, recording=env["ep2"], labels=["Benchmarks", "Recipes"], apply=True)
    assert got["applied"] == "tag"
    assert db.one("SELECT tags FROM $r", r=R("recording", env["ep2"]))["tags"] == ["Benchmarks"]
    # a text's topics are a namespace's
    got = post(client, hv, text="a new exploit", vocabulary="topic", namespace="pods")
    assert got["choice"] in ("Gene therapy", "Security")


def test_bad_questions(client, db, env, jev):
    hv = env["hv"]
    assert "give one" in post(client, hv, 400, labels=["a", "b"])["detail"]
    assert "give one" in post(client, hv, 400, recording=env["ep1"], text="x", labels=["a", "b"])["detail"]
    assert "one" in post(client, hv, 400, recording=env["ep1"])["detail"]
    assert "at least two" in post(client, hv, 400, recording=env["ep1"], labels=["only"])["detail"]
    assert "namespace" in post(client, hv, 400, text="x", vocabulary="topic")["detail"]
    assert "only a resource" in post(client, hv, 400, text="x", labels=["a", "b"], apply=True)["detail"]
    post(client, hv, 404, recording=env["call"], labels=["a", "b"])  # not theirs
    post(client, hv, 422, recording=env["ep1"], vocabulary="colour")


def test_nothing_set_up_to_answer(client, db, cfg, env, llm):
    cfg["decisions"] = {"engine": "off"}
    cfg["llm"]["base_url"] = "http://127.0.0.1:9/v1"
    db.next_id("settings")  # the app reads its settings again
    r = client.post("/api/v1/classify", json={"text": "x", "labels": ["a", "b"]}, headers=env["hv"])
    assert r.status_code == 409 and "nothing could answer" in r.json()["detail"]


def test_agents_classify(client, db, cfg, env, jev):
    # MCP: read only, never applies
    _, tokens = _grant(client, env["hv"], scope="read")
    jev.answer = {"type": "choice", "choice": "Gene therapy", "confidence": 0.88, "probabilities": {"Gene therapy": 0.88, "Security": 0.12}}
    before = topics.of_recording(db, env["ep2"])
    res = rpc(client, _bearer(tokens), "tools/call", {"name": "classify", "arguments": {"recording_id": env["ep2"], "vocabulary": "topic"}})
    out = res["result"]["structuredContent"]
    assert (out["choice"], out["id"], out["sure"], out["by"]) == ("Gene therapy", env["gene"], True, "jev")
    assert topics.of_recording(db, env["ep2"]) == before
    bad = rpc(client, _bearer(tokens), "tools/call", {"name": "classify", "arguments": {"recording_id": env["call"], "labels": ["a", "b"]}})
    assert bad["result"]["isError"] is True
    # the assistant
    me = {"id": 1, "email": "ed@x.io"}
    box = ai_tools.Toolbox(db, cfg, me, {env["pods"]}, {env["pods"]}, None, None)
    assert "classify" in {t["function"]["name"] for t in box.specs()}
    jev.answer = {"type": "choice", "choice": "science", "confidence": 0.7, "probabilities": {"science": 0.7, "sport": 0.3}}
    got, summary = box.t_classify(recording_id=env["ep1"], labels=["science: biology and medicine", "sport"])
    assert summary == "Classified as science (70% sure)" and got["sure"] is False
    with pytest.raises(ValueError, match="isn't available"):
        box.t_classify(recording_id=env["call"], labels=["a", "b"])


def test_a_workflow_classifies(client, db, cfg, env, jev):
    from app.domain import workflows

    ep2 = env["ep2"]

    def node(nid, t, **config):
        return {"id": nid, "type": t, "config": config}

    def graph(*nodes):
        return {"nodes": [node("in", "input"), *nodes], "edges": [{"source": a["id"], "target": b["id"]} for a, b in zip(
            [node("in", "input"), *nodes], nodes, strict=False
        )]}  # fmt: skip

    # what it passes on goes on to the next node; one that acts on a sure answer keeps something by itself
    desk = node("c", "classify", labels=["security: exploits and attacks", "health"], question="Which desk?")
    wid = workflows.create(db, "Desk", graph(desk, node("o", "output", key="desk")))
    jev.answer = {"type": "choice", "choice": "security", "confidence": 0.9, "probabilities": {"security": 0.9, "health": 0.1}}
    said = []
    workflows.run(db, cfg, ep2, wid, say=said.append)
    out = db.one("SELECT * FROM $o", o=R("output", f"{ep2}-desk"))["value"]
    assert (out["choice"], out["sure"], out["by"]) == ("security", True, "jev") and [x["label"] for x in out["ranked"]] == [
        "security",
        "health",
    ]
    assert "Which desk?" == jev.seen[-1][1]["questions"]["q"]["instructions"] and any("security (90% sure)" in s for s in said)
    acts = node("c", "classify", vocabulary="topic", apply=True)
    wid = workflows.create(db, "Topics", graph(acts))
    jev.answer = {"type": "choice", "choice": "Gene therapy", "confidence": 0.9, "probabilities": {"Gene therapy": 0.9}}
    # a try keeps nothing
    tried = workflows.try_graph(db, cfg, graph(acts), rid=ep2)
    assert tried["error"] is None and any("would act on it" in s for s in tried["log"])
    assert not [t for t in topics.of_recording(db, ep2) if t["source"] == "classify"]
    workflows.run(db, cfg, ep2, wid)
    assert [(t["label"], t["status"]) for t in topics.of_recording(db, ep2) if t["source"] == "classify"] == [("Gene therapy", "suggested")]
    # what came in, as text
    text = node("c", "classify", labels=["question", "statement"], of="input")
    tried = workflows.try_graph(
        db, cfg, graph(node("t", "template", template="Is it on Friday?"), text, node("o", "output", key="k")), rid=ep2
    )
    assert tried["error"] is None and "Is it on" in json.loads(jev.seen[-1][1]["state"])["text"]
    # checked when saved
    for bad, why in (
        (node("c", "classify"), "give labels"),
        (node("c", "classify", labels=["one"]), "at least two"),
        (node("c", "classify", vocabulary="colour"), "content_type or topic"),
        (node("c", "classify", vocabulary="topic", of="input", apply=True), "only the asset"),
    ):
        with pytest.raises(ValueError, match=why):
            workflows.create(db, "Bad", graph(bad, node("o", "output", key="k")))
    with pytest.raises(ValueError, match="keeps nothing"):
        workflows.create(db, "Keeps nothing", graph(node("c", "classify", vocabulary="topic")))
