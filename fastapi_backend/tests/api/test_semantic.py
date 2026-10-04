"""Search by meaning: passages embedded by an OpenAI-compatible server, searched through SurrealDB's vector index, and
fused with the keyword hits; kept current as transcripts change, and backfilled by a routine."""

from __future__ import annotations

import pytest

from app.domain import chat, jobs, routines, search, semantic, store
from tests import fake_llm
from tests.helpers import drain, login, make_user, quiet, seed

R = store.R
MONEY = """Erin|N|I have to be honest, we can't afford the rent this month.
Frank|Sad|Cash is tight for everyone, the bills keep coming.
Erin|N|Maybe we should ask about a smaller budget for the studio.
Frank|N|Anyway, did you see the match last night?"""


@pytest.fixture
def llm(cfg):
    srv, url = fake_llm.start()
    cfg["llm"].update(base_url=url, model="fake")
    fake_llm.Handler.embeddings, fake_llm.Handler.embed_fail = True, False
    fake_llm.Handler.embedded.clear()
    semantic._QUERIES.clear()
    semantic._DOWN.clear()
    yield fake_llm.Handler
    fake_llm.Handler.embeddings = False
    srv.shutdown()


def _archive(db, cfg, folder):
    from app.domain import analyze, ingest

    ids = seed(db, cfg, folder)
    p = folder / "money.txt"
    p.write_text(MONEY)
    ids.append(ingest.import_transcript(db, cfg, "pods", p, log=quiet))
    analyze.analyze_pending(db, cfg, log=quiet)
    return ids


def test_search_by_meaning_finds_other_words(client, db, cfg, folder, llm):
    ep1, ep2, call, money = _archive(db, cfg, folder)
    assert not semantic.available(db, cfg)
    assert semantic.index_pending(db, cfg, log=quiet) == 4
    st = semantic.state(db)
    assert st["model"] == "nomic-embed-text" and st["dimension"] == len(fake_llm.TOPICS) + fake_llm.EXTRA
    assert semantic.available(db, cfg)
    # passages are runs of lines, prefixed the way the model wants documents
    assert llm.embedded[0]["input"][0].startswith("search_document: ")
    rows = db.rows("SELECT idx0, idx1, kind, speakers, recording FROM passage WHERE recording = $r", r=money)
    assert [(r["idx0"], r["idx1"], r["kind"]) for r in rows] == [(0, 3, "said")]

    # no word of the query is said, but the money talk is about it
    res = search.search(db, "worried about our finances", cfg=cfg, mode="semantic", spaces=set(store.space_names(db)))
    assert res["mode"] == "semantic" and res["total"] >= 1
    top = res["hits"][0]
    assert top["recording_id"] == money and top["match"] == "meaning" and top["similarity"] >= 0.5
    assert "afford the rent" in top["snippet"] and "<mark>" not in top["snippet"]
    # by keyword alone there's nothing
    assert search.search(db, "worried about our finances", cfg=cfg, mode="keyword")["total"] == 0
    # auto: both, since it's set up and the query has no "phrases" or OR
    assert search.search(db, "worried about our finances", cfg=cfg, mode="auto")["mode"] == "hybrid"
    assert search.search(db, '"our finances"', cfg=cfg, mode="auto")["mode"] == "keyword"
    assert search.search(db, "rent OR bills", cfg=cfg, mode="auto")["mode"] == "keyword"
    assert search.search(db, "worried about our finances", mode="auto")["mode"] == "keyword"  # no cfg: words only

    # hybrid: a passage that holds a keyword hit adds to it rather than standing alone, so no moment shows twice
    hy = search.search(db, "capsid samples", cfg=cfg, mode="hybrid", limit=200)
    keys = [(h["recording_id"], h["source"], h["idx"]) for h in hy["hits"]]
    assert len(keys) == len(set(keys))
    assert {h["match"] for h in hy["hits"]} >= {"both"}
    assert all(h["match"] != "meaning" or h["similarity"] >= 0.52 for h in hy["hits"])
    kw = search.search(db, "capsid samples", cfg=cfg, mode="keyword", limit=200)
    assert hy["total"] >= kw["total"]

    # filters apply to passages too: a speaker who isn't in the money talk finds none of it
    erin = next(s["id"] for s in db.rows("SELECT record::id(id) AS id, name FROM speaker") if s["name"] == "Erin")
    assert search.search(db, "finances", cfg=cfg, mode="semantic", speaker=erin)["hits"][0]["speaker"] == "Erin"
    alice = next(
        s["id"]
        for s in db.rows("SELECT record::id(id) AS id, name FROM speaker WHERE space = $s", s=store.ns_id(db, "pods"))
        if s["name"] == "Alice"
    )
    assert all(h["recording_id"] != money for h in search.search(db, "finances", cfg=cfg, mode="semantic", speaker=alice)["hits"])
    assert search.search(db, "finances", cfg=cfg, mode="semantic", recording=ep1)["total"] == 0


def test_search_api_by_meaning_keeps_to_readable(client, db, cfg, folder, llm):
    _ep1, _ep2, call, money = _archive(db, cfg, folder)
    semantic.index_pending(db, cfg, log=quiet)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    make_user(db, "root@x.io", "root password 1", admin=True)
    hv, hr = login(client, "vi@x.io", "viewer password 1"), login(client, "root@x.io", "root password 1")
    q = {"q": "a courier delivery", "mode": "semantic"}
    assert all(h["recording_id"] != call for h in client.get("/api/v1/search", params=q, headers=hv).json()["hits"])
    mine = client.get("/api/v1/search", params=q, headers=hr).json()
    assert mine["mode"] == "semantic" and mine["hits"][0]["recording_id"] == call and mine["hits"][0]["match"] == "meaning"

    res = client.get("/api/v1/search", params={"q": "money worries", "facets": True}, headers=hv).json()
    assert res["mode"] == "hybrid" and res["hits"][0]["recording_id"] == money
    # moments found by meaning alone are counted too
    assert res["facets"]["moments"] >= 1 and {r["id"] for r in res["facets"]["recordings"]} >= {money}
    assert client.get("/api/v1/search", params={"q": "x", "mode": "nope"}, headers=hv).status_code == 422


def test_edits_reembed_only_what_changed(client, db, cfg, folder, llm):
    *_, money = _archive(db, cfg, folder)
    semantic.index_pending(db, cfg, log=quiet)
    n = len(llm.embedded)
    assert semantic.index_pending(db, cfg, log=quiet) == 0  # all indexed
    assert semantic.index_pending(db, cfg, force=True, log=quiet) == 4 and len(llm.embedded) == n  # unchanged: nothing sent

    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    h = login(client, "ed@x.io", "editor password 1")
    r = client.patch(f"/api/v1/recordings/{money}/segments/3", json={"text": "Anyway, the flight to the airport leaves at six."}, headers=h)
    assert r.status_code == 200
    assert jobs.get(db, r.json()["job"])["steps"] == [{"type": "analyze"}, {"type": "embed"}, {"type": "report"}]
    drain(db, cfg)
    assert len(llm.embedded) == n + 1 and len(llm.embedded[-1]["input"]) == 1  # the one passage
    assert "flight" in db.one("SELECT text FROM passage WHERE recording = $r", r=money)["text"]


def test_speaker_changes_reach_passages_without_reembedding(db, cfg, folder, llm):
    from app.domain import speakers

    *_, money = _archive(db, cfg, folder)
    semantic.index_pending(db, cfg, log=quiet)
    who = {s["name"]: s["id"] for s in db.rows("SELECT record::id(id) AS id, name FROM speaker WHERE name IN ['Erin', 'Frank']")}
    speakers.merge(db, who["Frank"], who["Erin"])
    assert money in semantic.unindexed(db, cfg, sorted(store.space_names(db)), 100)  # its passage names Frank
    # a stale passage never shows a line by someone else: Frank's lines are Erin's now, but the passage says Frank
    hits = search.search(db, "the bills keep coming", cfg=cfg, mode="semantic", speaker=who["Frank"])["hits"]
    assert not [h for h in hits if h["recording_id"] == money]
    n = len(llm.embedded)
    assert semantic.index_pending(db, cfg, log=quiet) == 1 and len(llm.embedded) == n  # same text: no new vectors
    assert db.one("SELECT speakers FROM passage WHERE recording = $r", r=money)["speakers"] == [who["Erin"]]
    hits = search.search(db, "worried about our finances", cfg=cfg, mode="semantic", speaker=who["Erin"])["hits"]
    assert hits and hits[0]["recording_id"] == money


def test_failing_server_is_left_be(db, cfg, folder, llm):
    ep1, ep2, call, money = _archive(db, cfg, folder)
    llm.embed_fail = True
    for rid in (ep1, ep2, call):
        jobs.enqueue(db, rid, ["embed"])
    drain(db, cfg)
    assert len(llm.embedded) == 1  # the first job failed; the others skipped without asking again
    assert semantic.failing(db, cfg)
    routine = {"actions": [{"type": "pipeline", "steps": ["embed"], "recordings": "unindexed"}], "namespaces": []}
    assert routines._idle(db, cfg, routine)
    llm.embed_fail = False
    assert semantic.index_pending(db, cfg, log=quiet) == 4  # asked for now: tried again
    assert not semantic.failing(db, cfg)


def test_model_change_drops_old_vectors(db, cfg, folder, llm):
    _archive(db, cfg, folder)
    semantic.index_pending(db, cfg, log=quiet)
    passages = db.values("SELECT VALUE count() FROM passage GROUP ALL")
    cfg["embeddings"]["model"] = "all-minilm"
    assert not semantic.available(db, cfg)  # the stored vectors are another model's
    assert search.search(db, "finances", cfg=cfg, mode="auto")["mode"] == "keyword"
    note = search.search(db, "finances", cfg=cfg, mode="semantic")
    assert note["mode"] == "keyword" and "nothing is indexed" in note["meaning"]
    assert len(semantic.unindexed(db, cfg, sorted(store.space_names(db)), 100)) == 4
    assert semantic.index_pending(db, cfg, log=quiet) == 4
    st = semantic.state(db)
    assert st["model"] == "all-minilm" and semantic.available(db, cfg)
    assert db.values("SELECT VALUE count() FROM passage GROUP ALL") == passages
    assert not llm.embedded[-1]["input"][0].startswith("search_document: ")  # minilm takes no prefix
    assert semantic.status(db, cfg)["indexed"] == 4


def test_server_down_falls_back_to_words(client, db, cfg, folder, llm):
    *_, money = _archive(db, cfg, folder)
    semantic.index_pending(db, cfg, log=quiet)
    llm.embed_fail = True
    res = search.search(db, "capsid", cfg=cfg, mode="semantic")
    assert res["mode"] == "keyword" and "unavailable" in res["meaning"] and res["total"] >= 3
    hy = search.search(db, "capsid", cfg=cfg, mode="hybrid")
    assert hy["mode"] == "keyword" and hy["total"] == res["total"] and "unavailable" in hy["meaning"]
    # the embed step skips instead of failing the job, and the recording waits for the routine
    db.q("UPDATE $r SET embedded = NONE", r=R("recording", money))
    db.q("DELETE passage WHERE recording = $r", r=money)
    jid = jobs.enqueue(db, money, ["embed", "report"])
    drain(db, cfg)
    j = jobs.get(db, jid)
    assert j["status"] == "succeeded"
    assert money in semantic.unindexed(db, cfg, sorted(store.space_names(db)), 100)
    llm.embed_fail = False
    semantic._DOWN.clear()
    assert semantic.index_pending(db, cfg, log=quiet) == 1
    assert search.search(db, "finances", cfg=cfg, mode="semantic")["hits"][0]["recording_id"] == money


def test_off_or_unconfigured(db, cfg, folder, llm):
    *_, money = _archive(db, cfg, folder)
    cfg["embeddings"]["enabled"] = False
    jid = jobs.enqueue(db, money, ["embed"])
    drain(db, cfg)
    assert jobs.get(db, jid)["status"] == "succeeded" and not db.rows("SELECT id FROM passage")
    assert not llm.embedded
    res = search.search(db, "finances", cfg=cfg, mode="hybrid")
    assert res["mode"] == "keyword" and "off" in res["meaning"]


def test_routine_backfills(client, db, cfg, folder, llm):
    _archive(db, cfg, folder)
    routines.seed(db)
    r = next(x for x in routines.list_routines(db) if x["name"] == routines.INDEX_NAME)
    assert r["enabled"] and r["actions"] == [{"type": "pipeline", "steps": ["embed"], "recordings": "unindexed", "limit": 500}]
    run = routines.get_run(db, routines.run(db, cfg, r["id"]))
    assert run["results"][0]["result"]["queued"] == 4
    drain(db, cfg)
    assert semantic.status(db, cfg)["indexed"] == 4
    run = routines.get_run(db, routines.run(db, cfg, r["id"]))
    assert run["results"][0]["result"]["recordings"] == 0
    cfg["embeddings"]["enabled"] = False
    run = routines.get_run(db, routines.run(db, cfg, r["id"]))
    assert run["results"][0]["result"] == {"recordings": 0, "queued": 0, "errors": 0}


def test_chat_retrieval_by_meaning(db, cfg, folder, llm):
    *_, money = _archive(db, cfg, folder)
    spaces = set(store.space_names(db))
    assert all(p["recording_id"] != money for p in chat.retrieve(db, "how are their finances", spaces))
    semantic.index_pending(db, cfg, log=quiet)
    got = chat.retrieve(db, "how are their finances", spaces, cfg=cfg)
    assert got and got[0]["recording_id"] == money
    # a question of only common words still finds what it's about
    assert chat.retrieve(db, "money", spaces, cfg=cfg)[0]["recording_id"] == money


def test_delete_and_move_keep_passages_in_step(client, db, cfg, folder, llm):
    ep1, _ep2, call, money = _archive(db, cfg, folder)
    semantic.index_pending(db, cfg, log=quiet)
    make_user(db, "root@x.io", "root password 1", admin=True)
    h = login(client, "root@x.io", "root password 1")
    assert client.post(f"/api/v1/recordings/{money}/move", json={"namespace": "calls"}, headers=h).status_code == 200
    assert set(db.values("SELECT VALUE space FROM passage WHERE recording = $r", r=money)) == {store.ns_id(db, "calls")}
    assert client.delete(f"/api/v1/recordings/{ep1}", headers=h).status_code == 200
    assert not db.rows("SELECT id FROM passage WHERE recording = $r", r=ep1)


def test_admin_status_test_and_index(client, db, cfg, folder, llm):
    _archive(db, cfg, folder)
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    h, hv = login(client, "root@x.io", "root password 1"), login(client, "vi@x.io", "viewer password 1")
    assert client.get("/api/v1/admin/semantic", headers=hv).status_code == 403
    st = client.get("/api/v1/admin/semantic", headers=h).json()
    assert st["configured"] and not st["current"] and st["indexed"] == 0 and st["recordings"] == 4 and st["min_similarity"] == 0.52
    t = client.post("/api/v1/settings/embeddings/test", headers=h).json()
    assert t["ok"] and t["dimension"] == len(fake_llm.TOPICS) + fake_llm.EXTRA and t["model"] == "nomic-embed-text"
    q = client.post("/api/v1/admin/semantic/index", params={"limit": 3}, headers=h).json()
    assert q == {"recordings": 3, "remaining": True}
    drain(db, cfg)
    st = client.get("/api/v1/admin/semantic", headers=h).json()
    assert st["current"] and st["indexed"] == 3 and st["passages"] >= 3 and st["dimension"] == t["dimension"]

    # settings are checked
    bad = client.put("/api/v1/settings/embeddings", json={"min_similarity": 2}, headers=h)
    assert bad.status_code == 400 and "0 to 1" in bad.json()["detail"]
    assert client.put("/api/v1/settings/embeddings", json={"base_url": "ftp://x"}, headers=h).status_code == 400
    assert client.put("/api/v1/settings/embeddings", json={"model": ""}, headers=h).status_code == 400
    ok = client.put("/api/v1/settings/embeddings", json={"min_similarity": 0.4, "api_key": "sk-embed", "passage_chars": 600}, headers=h)
    assert ok.status_code == 200
    view = client.get("/api/v1/settings", headers=h).json()["embeddings"]["values"]
    assert view["min_similarity"] == 0.4 and view["api_key"] == {"secret": True, "set": True}

    llm.embed_fail = True
    assert "404" in client.post("/api/v1/settings/embeddings/test", headers=h).json()["error"]
    client.put("/api/v1/settings/embeddings", json={"enabled": False}, headers=h)
    assert client.post("/api/v1/admin/semantic/index", headers=h).status_code == 400


def test_embeddings_server_of_its_own(cfg, monkeypatch):
    cfg["llm"].update(base_url="http://llm.local/v1", api_key="sk-llm")
    assert semantic.endpoint(cfg) == ("http://llm.local/v1", "sk-llm", "nomic-embed-text")
    cfg["embeddings"].update(base_url="http://embed.local:11434/v1/", model="mxbai-embed-large")
    assert semantic.endpoint(cfg) == ("http://embed.local:11434/v1", None, "mxbai-embed-large")  # not the LLM's key
    monkeypatch.setenv("EMBED_KEY", "sk-e")
    cfg["embeddings"]["api_key_env"] = "EMBED_KEY"
    assert semantic.endpoint(cfg)[1] == "sk-e"
    assert semantic.prefixes(cfg) == ("Represent this sentence for searching relevant passages: ", "")
    cfg["embeddings"].update(query_prefix="", document_prefix="doc: ", min_similarity=0.2)
    assert semantic.prefixes(cfg) == ("", "doc: ") and semantic.floor(cfg) == 0.2
    assert semantic.query_text('"capsid model" OR exploit') == "capsid model exploit"
