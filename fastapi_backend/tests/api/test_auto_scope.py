"""A conversation over everything gets the namespace its first question is about, when the choice is sure; a scope the
person chose, and later questions, are left alone."""

from __future__ import annotations

import json

import pytest

from tests import fake_jev, fake_llm
from tests.api._assist import Assist, sse, start_llm
from tests.helpers import login, make_user

Q = "When does the Dyno Therapeutics shipment leave?"


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
def app(cfg, db, llm):
    from app.main import create_app

    cfg["ai"]["tools"] = False
    return create_app(cfg, db, background=False)


def ask(c, h, cid, q=Q):
    return sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": q}).text)


def test_a_chat_over_everything_is_narrowed_to_the_namespace_it_is_about(app, db, cfg, folder, new_client, jev):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["admin"]
    jev.answer = {"type": "choice", "choice": "calls", "confidence": 0.93, "probabilities": {"calls": 0.93, "pods": 0.07}}
    cid = c.post("/api/v1/chats", headers=h, json={}).json()["id"]
    ev = ask(c, h, cid)
    assert ev["scoped"] == [{"namespaces": ["calls"], "confidence": 0.93, "by": "jev"}]
    assert ev["step"][0]["tool"] == "choose_namespace"
    assert {p["namespace"] for p in ev["passages"][0]} == {"calls"}
    state = json.loads(jev.seen[-1][1]["state"])
    assert state["question"] == Q and state["matching_excerpts_per_namespace"]["calls"] >= 1
    convo = c.get(f"/api/v1/chats/{cid}", headers=h).json()
    assert convo["scope"] == {"namespaces": ["calls"]}
    assert convo["messages"][1]["steps"][0]["args"] == {"namespace": "calls"}
    # widened again by the person: later questions stay over everything
    assert c.patch(f"/api/v1/chats/{cid}", headers=h, json={"scope": {}}).status_code == 200
    n = len(jev.seen)
    assert "scoped" not in ask(c, h, cid, "And the podcast?") and len(jev.seen) == n


def test_unsure_or_already_scoped_chats_are_left_alone(app, db, cfg, folder, new_client, jev):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["admin"]
    jev.answer = {"type": "choice", "choice": "calls", "confidence": 0.55, "probabilities": {"calls": 0.55, "pods": 0.45}}
    cid = c.post("/api/v1/chats", headers=h, json={}).json()["id"]
    ev = ask(c, h, cid)
    assert "scoped" not in ev  # unsure: the likeliest to tap, nothing set
    assert ev["suggested"] == [{"namespaces": [{"name": "calls", "new": False}, {"name": "pods", "new": False}]}]
    assert not c.get(f"/api/v1/chats/{cid}", headers=h).json().get("scope")
    n = len(jev.seen)
    cid = c.post("/api/v1/chats", headers=h, json={"scope": {"namespaces": ["pods"]}}).json()["id"]
    assert "scoped" not in ask(c, h, cid) and len(jev.seen) == n
    # one namespace to read: nothing to choose
    v, hv = s.cl["viewer"]
    vid = v.post("/api/v1/chats", headers=hv, json={}).json()["id"]
    assert "scoped" not in ask(v, hv, vid) and len(jev.seen) == n


def test_when_nothing_fits_admins_get_new_namespaces_to_pick(app, db, cfg, folder, new_client, jev, llm):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["admin"]
    jev.answer = {"type": "choice", "choice": "none of these", "confidence": 0.9, "probabilities": {"none of these": 0.9}}
    cid = c.post("/api/v1/chats", headers=h, json={}).json()["id"]
    ev = ask(c, h, cid, "Where should we go on holiday this summer?")
    # the model's names, made valid, without ones that exist; nothing is created until one is picked
    assert ev["suggested"] == [{"namespaces": [{"name": "travel", "new": True}, {"name": "family-trips", "new": True}]}]
    assert "travel" not in [n["name"] for n in c.get("/api/v1/namespaces", headers=h).json()]
    assert "scoped" not in ev and not c.get(f"/api/v1/chats/{cid}", headers=h).json().get("scope")
    # someone who can't create namespaces isn't offered new ones
    make_user(db, "two@x.io", "two password 1", roles={"pods": "viewer", "calls": "viewer"})
    t = new_client()
    ht = login(t, "two@x.io", "two password 1")
    tid = t.post("/api/v1/chats", headers=ht, json={}).json()["id"]
    assert "suggested" not in ask(t, ht, tid, "Where should we go on holiday this summer?")
