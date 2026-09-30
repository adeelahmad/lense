"""Entities, search, the graph and its scopes, word clouds and reports."""

from __future__ import annotations

from app.domain import analyze, graph, render, search, speakers, store
from tests.helpers import quiet, seed


def test_entities_search_graph_scopes(db, cfg, folder):
    seed(db, cfg, folder)
    assert "Dyno Therapeutics" in db.values("SELECT VALUE name FROM entity")
    res = search.search(db, "capsid", ns="pods")
    assert res["total"] >= 3
    assert "<mark>" in res["hits"][0]["snippet"]
    res = search.search(db, "exploits")  # stemming: exploits finds exploit
    assert res["total"] >= 3
    assert all(h["namespace"] == "pods" for h in res["hits"])
    res = search.search(db, '"next story"')
    assert res["total"] == 1
    assert "&lt;!--&lt;script&gt;" in res["hits"][0]["snippet"]  # transcript text is escaped
    assert search.search(db, "frightening OR shipment")["total"] == 2
    g = graph.build(db, cfg, "global")
    assert g["namespaces"] == ["pods"]  # calls is isolated
    assert {n["label"] for n in g["nodes"] if n["kind"] == "speaker"} == {"Alice", "Bob", "Carol"}
    db.q("UPDATE space SET graph = 'shared' WHERE name = 'calls'")
    g = graph.build(db, cfg, "global")
    dyno = [n for n in g["nodes"] if n["label"] == "Dyno Therapeutics"]
    assert (len(dyno), dyno[0]["ns"]) == (1, ["calls", "pods"])
    ids = {(r["space"], r["name"]): r["id"] for r in db.rows("SELECT record::id(id) AS id, space, name FROM speaker")}
    pods, calls = store.ns_id(db, "pods"), store.ns_id(db, "calls")
    speakers.link(db, ids[(pods, "Alice")], ids[(calls, "Alice")])
    g = graph.build(db, cfg, "global")
    assert any(e["kind"] == "same person" for e in g["edges"])
    assert {n["label"] for n in graph.build(db, cfg, "ns:calls")["nodes"] if n["kind"] == "speaker"} == {"Alice", "Dave"}
    via_edges = db.values("SELECT VALUE ->mentions->entity.name FROM segment WHERE recording = 3")
    assert "Dyno Therapeutics" in [n for x in via_edges for n in x]  # the mentions are real graph edges


def test_word_cloud_and_reports(db, cfg, folder):
    ids = seed(db, cfg, folder)
    assert "capsid" in [w for w, _ in analyze.keywords(db, ids[0], 20)]
    svg = render.wordcloud_svg([("alpha", 5), ("beta", 4), ("gamma", 3), ("delta", 2)])
    assert svg.count("<text") == 4
    files = render.build_reports(db, cfg, log=quiet)
    assert "index.html" in {p.name for p in files}
    page = [p for p in files if p.name.startswith("ep1")][0].read_text()
    assert "ArchivePlayer.mount" in page
    assert "<!--<script>" not in page
