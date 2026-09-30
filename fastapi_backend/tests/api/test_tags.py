"""Tags on recordings (docs/api.md): editors set them one recording at a time or on many at once; the list filters by
them and counts them."""

from __future__ import annotations

import pytest

from tests.helpers import login, make_user, seed


@pytest.fixture
def env(app, db, cfg, folder, client):
    a, b, call = seed(db, cfg, folder)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor", "calls": "viewer"})
    make_user(db, "view@x.io", "viewer password 1", roles={"pods": "viewer"})
    return {
        "a": a,
        "b": b,
        "call": call,
        "he": login(client, "ed@x.io", "editor password 1"),
        "hv": login(client, "view@x.io", "viewer password 1"),
    }


def test_tagging_one_recording(client, env):
    a, he, hv = env["a"], env["he"], env["hv"]
    url = f"/api/v1/recordings/{a}"
    assert client.patch(url, headers=hv, json={"tags": ["x"]}).status_code == 403  # viewers read
    r = client.patch(url, headers=he, json={"tags": [" Board ", "board", "Q3   review", ""]})
    assert r.status_code == 200, r.text
    assert r.json()["tags"] == ["Board", "Q3 review"]  # tidied, no repeats (ignoring case), sorted
    assert client.get(url, headers=hv).json()["tags"] == ["Board", "Q3 review"]
    assert client.patch(url, headers=he, json={"tags": ["x" * 41]}).status_code == 400
    assert client.patch(url, headers=he, json={"tags": [f"t{i}" for i in range(21)]}).status_code == 422
    assert client.patch(url, headers=he, json={}).status_code == 400
    # a title and tags together
    r = client.patch(url, headers=he, json={"title": "Episode one", "tags": ["board"]})
    assert (r.json()["title"], r.json()["tags"]) == ("Episode one", ["board"])
    assert client.patch(url, headers=he, json={"tags": []}).json()["tags"] == []


def test_tags_in_the_list(client, env):
    a, b, call, he, hv = env["a"], env["b"], env["call"], env["he"], env["hv"]
    client.patch(f"/api/v1/recordings/{a}", headers=he, json={"tags": ["Board", "urgent"]})
    client.patch(f"/api/v1/recordings/{b}", headers=he, json={"tags": ["board"]})

    def ids(**params):
        r = client.get("/api/v1/recordings", headers=he, params=params)
        return sorted(x["id"] for x in r.json()), r.headers["X-Total-Count"]

    assert ids(tag="BOARD") == (sorted([a, b]), "2")  # ignoring case
    assert ids(tag="urgent") == ([a], "1")
    assert ids(tag=["urgent", "nope"]) == ([a], "1")  # any of them
    assert ids(tag="nope") == ([], "0")
    rows = {x["id"]: x["tags"] for x in client.get("/api/v1/recordings", headers=he).json()}
    assert (rows[a], rows[b], rows[call]) == (["Board", "urgent"], ["board"], [])

    # bulk: add and remove on several; only where you edit
    body = {"recordings": [a, b], "add": ["Q3"], "remove": ["BOARD"]}
    assert client.post("/api/v1/recordings/tags", headers=hv, json=body).status_code == 403
    assert client.post("/api/v1/recordings/tags", headers=he, json={**body, "recordings": [a, call]}).status_code == 403
    assert client.post("/api/v1/recordings/tags", headers=he, json=body).json()["changed"] == 2
    rows = {x["id"]: x["tags"] for x in client.get("/api/v1/recordings", headers=he).json()}
    assert (rows[a], rows[b]) == (["Q3", "urgent"], ["Q3"])
    assert client.post("/api/v1/recordings/tags", headers=he, json=body).json()["changed"] == 0  # nothing left to do

    # the tags in use, most used first; only in namespaces you can read
    client.patch(f"/api/v1/recordings/{a}", headers=he, json={"tags": ["Q3", "urgent"]})
    assert client.get("/api/v1/recordings/tags", headers=he).json() == [
        {"tag": "Q3", "recordings": 2},
        {"tag": "urgent", "recordings": 1},
    ]
    assert client.get("/api/v1/recordings/tags", headers=he, params={"ns": "calls"}).json() == []
    assert client.get("/api/v1/recordings/tags", headers=hv, params={"ns": "calls"}).status_code == 404
