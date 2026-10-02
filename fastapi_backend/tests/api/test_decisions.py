"""Typed decisions (docs/configuration.md#decisions): admins set up a System One server; searches are then reranked by
whether each hit answers the query; without one, or when it fails, everything works as before."""

from __future__ import annotations

import pytest

from app.domain import decide, ingest
from tests import fake_decide
from tests.helpers import login, make_user

BOATS = """Ana|N|The invoice for the harbour office was paid late again.
Ben|N|Our ship left the harbour before the tide turned this morning.
Ana|N|Somebody painted the harbour wall bright yellow last week."""


@pytest.fixture
def jev():
    srv, url = fake_decide.start()
    decide.recovered()
    yield fake_decide.Handler, url
    srv.shutdown()
    decide.recovered()


@pytest.fixture
def people(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    return {"root": login(client, "root@x.io", "root password 1"), "viewer": login(client, "vi@x.io", "viewer password 1")}


def _put(client, h, body):
    return client.put("/api/v1/settings/decisions", headers=h, json=body)


def _search(client, h, q, **params):
    r = client.get("/api/v1/search", headers=h, params={"q": q, "mode": "keyword", **params})
    assert r.status_code == 200, r.text
    return r.json()


def test_admins_set_up_the_decision_model(client, people, jev):
    handler, url = jev
    hr, hv = people["root"], people["viewer"]
    status = client.get("/api/v1/admin/decisions", headers=hr).json()
    assert (status["enabled"], status["configured"], status["hosted"], status["model"]) == (False, False, True, "jev-latest")
    assert status["base_url"] == "https://api.typesafe.ai"
    assert client.get("/api/v1/admin/decisions", headers=hv).status_code == 403
    assert client.post("/api/v1/settings/decisions/test", headers=hr).json()["error"] == "switch decisions on, with a model, first"
    # the settings are checked
    for bad in (
        {"enabled": "yes"},
        {"base_url": "ftp://x"},
        {"model": ""},
        {"timeout": 0},
        {"rerank_top": 2},
        {"apply_above": 0.2},
        {"flag_above": 1.5},
        {"rerank": 1},
        {"nonsense": True},
    ):
        assert _put(client, hr, bad).status_code == 400, bad
    assert _put(client, hv, {"enabled": True}).status_code == 403
    assert (
        _put(client, hr, {"enabled": True, "base_url": url + "/", "model": "fake-jev", "api_key": "ts-secret", "rerank_top": 8}).status_code
        == 200
    )
    # the key is kept encrypted and never shown
    shown = client.get("/api/v1/settings", headers=hr).json()["decisions"]["values"]
    assert shown["api_key"] == {"secret": True, "set": True} and shown["base_url"] == url and shown["rerank_top"] == 8
    status = client.get("/api/v1/admin/decisions", headers=hr).json()
    assert (status["configured"], status["hosted"], status["model"], status["key"]) == (True, False, "fake-jev", True)
    test = client.post("/api/v1/settings/decisions/test", headers=hr).json()
    assert test["ok"] and test["model"] == "fake-jev" and test["yes"] > 0.5 and test["choice"] == "sea" and test["ms"] >= 0
    assert handler.keys[-1] == "Bearer ts-secret"
    assert client.post("/api/v1/settings/decisions/test", headers=hv).status_code == 403
    # a server that refuses says why
    handler.fail = (401, {"detail": {"message": "Invalid API key"}})
    test = client.post("/api/v1/settings/decisions/test", headers=hr).json()
    assert not test["ok"] and "401" in test["error"]
    # an empty address goes back to the hosted one
    assert _put(client, hr, {"base_url": ""}).status_code == 200
    assert client.get("/api/v1/admin/decisions", headers=hr).json()["hosted"] is True


def test_search_is_reranked_by_what_answers_the_query(client, people, db, cfg, jev):
    handler, url = jev
    hr, hv = people["root"], people["viewer"]
    rid = ingest.import_text(db, cfg, "pods", BOATS, title="Harbour notes")
    # without a decision model: the order of the words, and nothing says otherwise
    plain = _search(client, hv, "harbour")
    assert plain["total"] == 3 and plain["rerank"] is False and plain["reranked"] is None
    assert all(h["relevance"] is None for h in plain["hits"])
    assert _put(client, hr, {"enabled": True, "base_url": url, "model": "fake-jev"}).status_code == 200

    # "ship leaving the harbour": every line says harbour; the one about the ship is judged to answer it
    res = _search(client, hv, "harbour", rerank=True)
    assert (res["rerank"], res["reranked"]) == (True, 3) and {h["recording_id"] for h in res["hits"]} == {rid}
    asked = handler.seen[-1]
    assert asked["state"]["search_query"] == "harbour" and len(asked["questions"]) == 3
    # the passages are data; a question only names the one it's about
    assert asked["questions"]["h0"]["type"] == "noul" and "passage h0" in asked["questions"]["h0"]["instructions"]
    assert "harbour" in asked["state"]["passages"]["h0"] and set(asked["state"]["passages"]) == {"h0", "h1", "h2"}
    handler.script = {"painted": {"noul": 0.05}, "ship": {"noul": 0.97}, "invoice": {"noul": 0.4}}
    res = _search(client, hv, "harbour")  # reranking is the default where it's set up
    assert [h["idx"] for h in res["hits"]] == [1, 0, 2] and [h["relevance"] for h in res["hits"]] == [0.97, 0.4, 0.05]
    # asked not to, the order is the words'
    assert [h["idx"] for h in _search(client, hv, "harbour", rerank=False)["hits"]] == [h["idx"] for h in plain["hits"]]
    # pages are cut from the same judged order: none repeats a hit, none skips one
    pages = [_search(client, hv, "harbour", limit=1, offset=n)["hits"][0]["idx"] for n in range(3)]
    assert pages == [1, 0, 2]
    # only the best are judged
    assert _put(client, hr, {"rerank_top": 4}).status_code == 200
    assert _search(client, hv, "harbour")["reranked"] == 3
    # nothing found, nothing asked
    before = len(handler.seen)
    assert _search(client, hv, "zeppelin")["total"] == 0 and len(handler.seen) == before

    # switched off for search, or the server failing: the search still answers, in the words' order
    assert _put(client, hr, {"rerank": False}).status_code == 200
    off = _search(client, hv, "harbour")
    assert (off["rerank"], off["reranked"]) == (False, None)
    assert _put(client, hr, {"rerank": True}).status_code == 200
    handler.fail = (529, {"detail": "overloaded"})
    decide.recovered()
    down = _search(client, hv, "harbour")
    assert down["total"] == 3 and down["reranked"] is None and down["rerank"] is True
    assert [h["idx"] for h in down["hits"]] == [h["idx"] for h in plain["hits"]]


def test_the_key_stays_with_its_server(client, people, jev):
    """A saved key was for the server it was saved with: pointing decisions at another host drops it, and an address
    can't carry a login of its own."""
    hr, url = people["root"], jev[1]
    put = lambda body: _put(client, hr, body)  # noqa: E731
    assert put({"enabled": True, "base_url": url, "model": "fake-jev", "api_key": "ts-secret"}).status_code == 200
    assert client.get("/api/v1/admin/decisions", headers=hr).json()["key"] is True
    assert put({"timeout": 12}).status_code == 200 and client.get("/api/v1/admin/decisions", headers=hr).json()["key"] is True
    assert put({"base_url": "http://127.0.0.1:9"}).status_code == 200
    assert client.get("/api/v1/admin/decisions", headers=hr).json()["key"] is False
    assert put({"base_url": "http://user:pass@127.0.0.1:9"}).status_code == 400
    # the test says what went wrong to the admin, with the address
    r = client.post("/api/v1/settings/decisions/test", headers=hr).json()
    assert r["ok"] is False and "127.0.0.1:9" in r["error"]
