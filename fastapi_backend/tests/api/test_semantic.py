"""Search by meaning (docs/api.md#search): off unless an admin switches it on; the embed step turns lines and
descriptions into vectors; search then also finds passages that mean the same, blended with the words' ranking, in
what the asker may read; corrections, moves and deletes are followed. The model here is a tiny one built for the
test (tests/embed_helpers.py): nothing is downloaded."""

from __future__ import annotations

import hashlib
import io

import pytest

from app.domain import embeddings, ingest, jobs, store
from tests.conftest import make_cfg
from tests.embed_helpers import model_dir
from tests.helpers import drain, login, make_user

R = store.R
SEA = """Ana|N|The ship left the harbour before the tide turned this morning.
Ben|N|We talked about the weather for a while and then went home.
Ana|N|Three vessels were still waiting at the dock when we came back."""
MONEY = """Cy|N|The invoice shows a price far above the budget we agreed.
Di|N|Nobody mentioned the weather at all during that meeting.
Cy|N|We will talk again next week, same time as usual."""


@pytest.fixture
def cfg(folder):
    return make_cfg(folder, search={"semantic_model": str(model_dir(folder))})


@pytest.fixture
def people(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    return {
        "root": login(client, "root@x.io", "root password 1"),
        "viewer": login(client, "vi@x.io", "viewer password 1"),
        "editor": login(client, "ed@x.io", "editor password 1"),
    }


def _on(client, people, **more):
    r = client.put("/api/v1/settings/search", headers=people["root"], json={"semantic": True, **more})
    assert r.status_code == 200, r.text


def _import(client, h, ns, text, title):
    r = client.post("/api/v1/import", headers=h, json={"namespace": ns, "text": text, "title": title})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _run(client, db):
    """Run the queued jobs with the settings as saved in the app."""
    return drain(db, client.app.state.settings.current())


def _search(client, h, q, **params):
    r = client.get("/api/v1/search", headers=h, params={"q": q, **params})
    assert r.status_code == 200, r.text
    return r.json()


def _lines(res):
    return sorted((h["match"], h["snippet"].replace("<mark>", "").replace("</mark>", "")[:24]) for h in res["hits"])


def test_off_by_default_and_admins_switch_it_on(client, people, db, cfg):
    hr, hv = people["root"], people["viewer"]
    assert client.get("/api/v1/search/capabilities", headers=hv).json() == {"semantic": False, "model": None}
    assert client.get("/api/v1/search/capabilities").status_code == 401
    rid = _import(client, hr, "pods", SEA, "At sea")
    _run(client, db)
    job = db.one("SELECT log, steps FROM job WHERE recording = $r", r=rid)
    assert "embed" in [s["type"] for s in job["steps"]]
    assert any("embed skipped: search by meaning is off (search.semantic)" in line for line in job["log"])
    assert not db.values("SELECT VALUE id FROM embedding")
    # asking for meaning where it's off is a plain search
    res = _search(client, hv, "vessels", semantic=True)
    assert res["total"] == 1 and res["semantic"] is None and res["hits"][0].get("match") is None

    put = lambda body, h=hr: client.put("/api/v1/settings/search", headers=h, json=body)  # noqa: E731
    assert put({"semantic": True}, hv).status_code == 403
    assert put({"semantic": "yes"}).status_code == 400
    assert put({"semantic_weight": 1.5}).status_code == 400
    assert put({"semantic_weight": True}).status_code == 400
    assert put({"semantic_model": "/tmp/other"}).status_code == 400  # startup only
    assert put({"semantic": True, "semantic_weight": 0.4}).status_code == 200
    assert client.get("/api/v1/search/capabilities", headers=hv).json() == {"semantic": True, "model": "tiny-topics"}
    boot = client.get("/api/v1/settings", headers=hr).json()["bootstrap"]["semantic_model"]
    assert (boot["enabled"], boot["model"], boot["ready"], boot["reason"]) == (True, "tiny-topics", True, None)


def test_passages_that_mean_the_same_are_found_within_what_you_may_read(client, people, db, cfg):
    hr, hv = people["root"], people["viewer"]
    _on(client, people)
    sea = _import(client, hr, "pods", SEA, "At sea")
    money = _import(client, hr, "calls", MONEY, "The budget call")
    _run(client, db)
    rows = db.rows("SELECT kind, model, recording, space FROM embedding")
    assert len(rows) == 6 and {r["model"] for r in rows} == {"tiny-topics"} and {r["kind"] for r in rows} == {"segment"}
    log = db.one("SELECT log FROM job WHERE recording = $r", r=sea)["log"]
    assert any("3 passage(s) embedded (tiny-topics); 0 already were" in line for line in log)

    # the words alone find the one line that says "vessels"
    plain = _search(client, hv, "vessels")
    assert plain["total"] == 1 and plain["hits"][0]["match"] is None
    # by meaning, the line about the ship and the harbour is found too; the line about the weather isn't
    res = _search(client, hv, "vessels", semantic=True, facets=True)
    assert _lines(res) == [("both", "Three vessels were still"), ("meaning", "The ship left the harbou")]
    assert res["semantic"] == {"model": "tiny-topics", "meaning": 1}
    assert [h["match"] for h in res["hits"]] == ["both", "meaning"]  # the words and the meaning beat the meaning alone
    assert res["hits"][0]["similarity"] > 0.9 and res["hits"][1]["recording_id"] == sea and res["hits"][1]["source"] == "said"
    f = res["facets"]
    assert (f["moments"], f["meaning"]) == (2, 1)
    assert [(s["name"], s["count"]) for s in f["speakers"]] == [("Ana", 2)]
    assert f["recordings"] == [{"id": sea, "title": "At sea", "count": 2}]
    # words that are said nowhere still find what's about them
    res = _search(client, hv, "sailors")
    assert res["total"] == 0
    assert [h["match"] for h in _search(client, hv, "sailors", semantic=True)["hits"]] == ["meaning", "meaning"]
    # filters hold for meaning as for words
    ben = next(
        s["id"] for s in db.rows("SELECT record::id(id) AS id, name, label FROM speaker") if "Ben" in (s.get("name"), s.get("label"))
    )
    assert _search(client, hv, "sailors", semantic=True, speaker=ben)["total"] == 0
    assert _search(client, hv, "sailors", semantic=True, recording=sea)["total"] == 2
    assert _search(client, hv, "sailors", semantic=True, recording=money)["total"] == 0

    # a namespace you have no role in is never found, by words or by meaning
    assert _search(client, hv, "expensive payments", semantic=True)["total"] == 0
    res = _search(client, hr, "expensive payments", semantic=True)
    assert [(h["match"], h["namespace"]) for h in res["hits"]] == [("meaning", "calls")]
    assert client.get("/api/v1/search", headers=hv, params={"q": "payments", "semantic": True, "ns": "calls"}).status_code == 404

    # how much meaning counts is the admins' to say: with none, meaning alone ranks last
    _on(client, people, semantic_weight=0.0)
    res = _search(client, hv, "vessels", semantic=True)
    assert [h["match"] for h in res["hits"]] == ["both", "meaning"]


def test_visitors_search_by_meaning_in_what_is_public(client, new_client, people, db, cfg):
    hr = people["root"]
    _on(client, people)
    sea = _import(client, hr, "pods", SEA, "At sea")
    hidden = _import(client, hr, "pods", SEA.replace("this morning", "last night"), "Not public")
    _run(client, db)
    assert client.put(f"/api/v1/resources/{sea}/access", headers=hr, json={"access": "public"}).status_code == 200
    anon = new_client()
    res = anon.get("/api/v1/public/search", params={"q": "sailors"}).json()
    assert res["semantic"] is True and res["total"] == 0
    res = anon.get("/api/v1/public/search", params={"q": "sailors", "semantic": True}).json()
    assert [i["id"] for i in res["items"]] == [sea] and hidden not in [i["id"] for i in res["items"]]
    assert {h["match"] for h in res["items"][0]["hits"]} == {"meaning"}
    # a public resource whose transcript is closed isn't found by what it says
    assert client.put(f"/api/v1/resources/{sea}/access", headers=hr, json={"access": "public", "open": ["media"]}).status_code == 200
    assert anon.get("/api/v1/public/search", params={"q": "sailors", "semantic": True}).json()["total"] == 0


def test_corrections_moves_and_deletes_are_followed(client, people, db, cfg):
    hr, he = people["root"], people["editor"]
    _on(client, people)
    rid = _import(client, hr, "pods", SEA, "At sea")
    _run(client, db)
    assert _search(client, he, "sailors", semantic=True)["total"] == 2
    # a corrected line isn't found by what it used to say, and is embedded again by the job the correction queues
    r = client.patch(f"/api/v1/resources/{rid}/segments/0", headers=he, json={"text": "The invoice was paid late and nobody noticed."})
    assert r.status_code == 200, r.text
    assert [h["idx"] for h in _search(client, he, "sailors", semantic=True)["hits"]] == [2]
    _run(client, db)
    job = db.one("SELECT log FROM job WHERE id = $j", j=R("job", r.json()["job"]))
    assert any("1 passage(s) embedded (tiny-topics); 2 already were" in line for line in job["log"])
    assert [h["idx"] for h in _search(client, he, "payments", semantic=True)["hits"]] == [0]
    # running the step again embeds nothing twice
    jobs.enqueue(db, rid, ["embed"])
    _run(client, db)
    assert len(db.values("SELECT VALUE id FROM embedding WHERE recording = $r", r=rid)) == 3

    # moved: found in its new namespace only
    assert client.post(f"/api/v1/resources/{rid}/move", headers=hr, json={"namespace": "calls"}).status_code == 200
    _run(client, db)
    assert _search(client, he, "sailors", semantic=True)["total"] == 0  # the editor has no role in calls
    assert _search(client, hr, "sailors", semantic=True)["hits"][0]["namespace"] == "calls"
    # deleted: its vectors go with it
    assert client.delete(f"/api/v1/resources/{rid}", headers=hr).status_code == 200
    assert not db.values("SELECT VALUE id FROM embedding")
    assert _search(client, hr, "sailors", semantic=True)["total"] == 0


def test_the_step_says_why_it_cant(client, people, db, cfg, folder, monkeypatch):
    hr = people["root"]
    _on(client, people)
    # nothing long enough to mean anything
    rid = ingest.import_text(db, cfg, "pods", "Ana|N|Yes.\nBen|N|No.\nAna|N|Fine.", title="Short")
    jobs.enqueue(db, rid, ["embed"])
    _run(client, db)
    assert "embed skipped: there's no text to embed" in "\n".join(db.one("SELECT log FROM job WHERE recording = $r", r=rid)["log"])
    # a model folder without its files
    empty = dict(cfg, search={**cfg["search"], "semantic": True, "semantic_model": str(folder / "nothing-here")})
    assert "no model at" in embeddings.why_not(empty) and embeddings.status(empty)["ready"] is False
    with pytest.raises(RuntimeError, match="no model at"):
        embeddings.embedder(empty)
    assert embeddings.nearest(db, empty, "anything") == []
    # the runtime isn't installed
    monkeypatch.setitem(embeddings._MODELS, "x", None)
    embeddings._MODELS.clear()
    import builtins

    real = builtins.__import__

    def no_onnx(name, *a, **k):
        if name in ("onnxruntime", "tokenizers"):
            raise ImportError(name)
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_onnx)
    on = dict(cfg, search={**cfg["search"], "semantic": True})
    assert "aren't installed" in embeddings.status(on)["reason"]
    rid = ingest.import_text(db, cfg, "pods", SEA, title="At sea")
    jobs.enqueue(db, rid, ["embed"])
    _run(client, db)
    log = "\n".join(db.one("SELECT log FROM job WHERE recording = $r", r=rid)["log"])
    assert 'embed skipped: ONNX Runtime and tokenizers aren\'t installed (pip install "lens[semantic]")' in log
    monkeypatch.undo()
    embeddings._MODELS.clear()
    assert client.get("/api/v1/search", headers=hr, params={"q": "sailors", "semantic": True}).json()["total"] == 0  # nothing embedded


def test_the_default_model_is_fetched_once_and_checked(folder, monkeypatch):
    files = {"onnx/model.onnx": b"a model", "tokenizer.json": b"a tokenizer"}
    monkeypatch.setattr(
        embeddings,
        "FILES",
        {"model.onnx": ("onnx/model.onnx", hashlib.sha256(b"a model").hexdigest()), "tokenizer.json": ("tokenizer.json", "0" * 64)},
    )
    asked, said = [], []

    def opener(url, timeout=None):
        asked.append(url)
        return io.BytesIO(files[url.split(embeddings.REVISION + "/")[1]])

    d = folder / "models" / embeddings.NAME
    with pytest.raises(RuntimeError, match="isn't the file expected"):  # the tokenizer's hash is wrong
        embeddings.fetch(d, said.append, opener)
    assert sorted(p.name for p in d.iterdir()) == ["model.onnx"] and (d / "model.onnx").read_bytes() == b"a model"
    assert said == [f"fetching {embeddings.NAME}/model.onnx", f"fetching {embeddings.NAME}/tokenizer.json"]
    monkeypatch.setitem(embeddings.FILES, "tokenizer.json", ("tokenizer.json", hashlib.sha256(b"a tokenizer").hexdigest()))
    embeddings.fetch(d, opener=opener)
    assert len(asked) == 3 and asked[-1].endswith("/tokenizer.json")  # the model wasn't fetched again
    embeddings.fetch(d, opener=opener)
    assert len(asked) == 3
    # where the server can't reach it, the step says where to put the files
    cfg = {"data_dir": str(folder / "offline"), "search": {"semantic": True, "semantic_model": None}}
    assert embeddings.model_dir(cfg) == (folder / "offline" / "models" / embeddings.NAME, True)
    assert "is fetched the first time" in embeddings.status(cfg)["reason"]

    def offline(folder, say=None):
        raise OSError("no network")

    monkeypatch.setattr(embeddings, "fetch", offline)
    with pytest.raises(RuntimeError, match="couldn't fetch all-MiniLM-L6-v2 from huggingface.co"):
        embeddings.embedder(cfg)
