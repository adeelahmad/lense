"""The archive as a property graph (graph_model.py) and read-only Cypher over it (cypher.py)."""

from __future__ import annotations

import pytest

from app.domain import analyze, cypher, graph_model, ingest
from tests.helpers import quiet, seed

EXTRA = (
    "Alice|N|We met the Northwind Labs team and the North Wind Labs lawyers.\n"
    "Bob|N|Amazon Web Services hosts it; AWS bills monthly.\nAlice|N|Northwind Labs again, with Dyno Therapeutics."
)


@pytest.fixture
def g(db, cfg, folder):
    seed(db, cfg, folder)
    p = folder / "extra.txt"
    p.write_text(EXTRA)
    ingest.import_transcript(db, cfg, "pods", p, log=quiet)
    analyze.analyze_pending(db, cfg, log=quiet)
    return graph_model.build(db, "ns:pods")


def rows(g, src, **params):
    return cypher.run(g, src, params)["rows"]


def test_model(g):
    s = graph_model.schema(g)
    labels = {x["label"]: x["count"] for x in s["labels"]}
    assert labels["Namespace"] == 1 and labels["Recording"] == 3 and labels["Collection"] >= 1
    assert labels["Speaker"] == 3 and labels["Entity"] >= 4
    assert {"CONTAINS", "HAS_SPEAKER", "MENTIONS", "SAID", "MENTIONED_WITH", "SPOKE_WITH"} <= set(s["stats"]["types"])
    assert "Organisation" in s["entity_types"]
    assert g.namespaces == ["pods"]


def test_match_where_order(g):
    got = rows(g, "MATCH (e:Organisation) WHERE e.mentions >= 2 RETURN e.name AS name ORDER BY name")
    assert ["Dyno Therapeutics"] in got and ["Northwind Labs"] in got
    # single quotes, lowercase functions and aggregates, as models write them
    got = rows(
        g,
        "match (s:Speaker)-[r:SAID]->(e:Entity) where toLower(e.name) contains 'dyno' "
        "return s.name as speaker, sum(r.count) as n order by n desc, speaker",
    )
    assert got[0][0] in ("Alice", "Bob") and all(n > 0 for _, n in got)
    assert rows(g, "MATCH (n:Namespace) RETURN count(*)") == [[1]]


def test_directions_and_hops(g):
    dyno = rows(g, 'MATCH (e:Entity {name: "Dyno Therapeutics"}) RETURN id(e)')[0][0]
    # recordings point at entities; the same pattern read backwards, and either way
    a = rows(g, "MATCH (r:Recording)-[:MENTIONS]->(e {id: $id}) RETURN r.name ORDER BY r.name", id=dyno)
    b = rows(g, "MATCH (e {id: $id})<-[:MENTIONS]-(r) RETURN r.name ORDER BY r.name", id=dyno)
    c = rows(g, "MATCH (e {id: $id})-[:MENTIONS]-(r) RETURN r.name ORDER BY r.name", id=dyno)
    assert a == b == c and len(a) == 3
    # ancestors: namespace down to the entity through collections and recordings
    up = rows(g, "MATCH (n:Namespace)-[:CONTAINS|MENTIONS*1..4]->(e {id: $id}) RETURN DISTINCT n.name", id=dyno)
    assert up == [["pods"]]
    assert rows(g, "MATCH (e:Entity)-[*0]-(x) WHERE e.name = 'AWS' RETURN x.name") in ([["AWS"]], [])


def test_paths(g):
    got = rows(
        g,
        "MATCH p = shortestPath((a:Speaker {name: 'Carol'})-[*..6]-(b:Entity {name: 'Northwind Labs'})) "
        "RETURN length(p), [n IN nodes(p) | n.name]",
    )
    assert got and got[0][0] >= 2 and got[0][1][0] == "Carol" and got[0][1][-1] == "Northwind Labs"
    out = cypher.run(g, "MATCH p = (s:Speaker)-[:SAID]->(e) RETURN p LIMIT 3")
    assert out["nodes"] and out["edges"] and all(e["type"] == "SAID" for e in out["edges"])


def test_with_unwind_union_case(g):
    got = rows(
        g,
        "MATCH (r:Recording)-[m:MENTIONS]->(e) WITH r, count(e) AS n WHERE n > 1 "
        "RETURN r.name AS rec, n, CASE WHEN n > 2 THEN 'many' ELSE 'few' END AS size ORDER BY n DESC",
    )
    assert got and all(x[1] > 1 for x in got)
    assert rows(g, "UNWIND [3, 1, 2] AS x RETURN x ORDER BY x") == [[1], [2], [3]]
    assert rows(g, "MATCH (s:Speaker) RETURN s.name AS n UNION MATCH (s:Speaker) RETURN s.name AS n").__len__() == 3
    assert rows(g, "RETURN 1 + 2 * 3 AS x, 'a' + 'b', [1,2,3][1..], size('abc'), 7 / 2, 7.0 / 2") == [[7, "ab", [2, 3], 3, 3, 3.5]]
    assert rows(g, "MATCH (e:Entity) WHERE e.name =~ 'North.*' RETURN collect(DISTINCT e.name)")[0][0][0].startswith("North")


def test_optional_and_nulls(g):
    got = rows(g, "MATCH (n:Namespace) OPTIONAL MATCH (n)-[:SAME_AS]->(x) RETURN n.name, x, x.name IS NULL")
    assert got == [["pods", None, True]]


@pytest.mark.parametrize(
    ("src", "says"),
    [
        ("MATCH (n) DETACH DELETE n", "read-only"),
        ("CREATE (n:Person {name: 'x'}) RETURN n", "read-only"),
        ("MATCH (n) SET n.name = 'x' RETURN n", "read-only"),
        ("MATCH (n RETURN n", "expected"),
        ("MATCH (n) RETURN nope(n)", "unknown function"),
        ("MATCH (n) WHERE m.x = 1 RETURN n", "isn't defined"),
        ("RETURN $who", "parameter"),
        ("MATCH (a)-[*1..20]-(b) RETURN a", "8 hops"),
        ("", "empty"),
    ],
)
def test_errors_say_what_to_fix(g, src, says):
    with pytest.raises(cypher.CypherError, match=says):
        cypher.run(g, src)


def test_budget(g):
    with pytest.raises(cypher.TooBig):
        cypher.Runner(g, max_steps=50).run(cypher.parse("MATCH (a)-[*1..6]-(b) RETURN count(*)")[0][0])
    out = cypher.run(g, "MATCH (a) RETURN a", max_rows=2)
    assert out["truncated"] and len(out["rows"]) == 2


def test_related_and_paths(g):
    dyno = rows(g, 'MATCH (e:Entity {name: "Dyno Therapeutics"}) RETURN id(e)')[0][0]
    up = graph_model.related(g, dyno, "ancestors", depth=5)
    labels = {n["labels"][0] for n in up["nodes"]}
    assert {"Recording", "Collection", "Namespace", "Speaker"} <= labels
    assert all(e["type"] in graph_model.HIERARCHY for e in up["edges"])
    parents = graph_model.related(g, dyno, "parents")
    assert {n["labels"][0] for n in parents["nodes"] if n["depth"] == 1} == {"Recording", "Speaker"}
    ns = [n["id"] for n in up["nodes"] if n["labels"][0] == "Namespace"][0]
    kids = graph_model.related(g, ns, "children")
    assert {n["labels"][0] for n in kids["nodes"] if n["depth"] == 1} <= {"Collection", "Recording"}
    down = graph_model.related(g, ns, "descendants", depth=6, limit=5)
    assert down["truncated"] and len(down["nodes"]) <= 6
    carol = rows(g, "MATCH (s:Speaker {name: 'Carol'}) RETURN id(s)")[0][0]
    p = graph_model.paths(g, carol, dyno, max_depth=3, limit=5)
    assert p["paths"] and p["paths"][0]["nodes"][0] == carol and p["paths"][0]["nodes"][-1] == dyno
    assert [x["length"] for x in p["paths"]] == sorted(x["length"] for x in p["paths"])
    assert len({tuple(x["nodes"]) for x in p["paths"]}) == len(p["paths"])
    short = graph_model.paths(g, carol, dyno, max_depth=3, shortest=True)
    assert {x["length"] for x in short["paths"]} == {p["paths"][0]["length"]}
    with pytest.raises(KeyError):
        graph_model.related(g, "e999999", "children")


def test_global_scope_merges_names(db, cfg, folder):
    seed(db, cfg, folder)
    from app.domain import store

    pods = store.ns_id(db, "pods")
    g = graph_model.build(db, "global", {pods})
    assert g.namespaces == ["pods"]  # calls is isolated and not readable here
    both = graph_model.build(db, "global", None)
    assert both.namespaces == ["pods"]  # isolated namespaces stay out of the global graph
    calls = graph_model.build(db, "ns:calls", None)
    ids = rows(calls, "MATCH (e:Entity {name: 'Dyno Therapeutics'}) RETURN e.ids")[0][0]
    assert calls.resolve(f"e{ids[0]}") == f"e{ids[0]}"
    with pytest.raises(KeyError):
        graph_model.build(db, "ns:calls", {pods})


def test_where_prunes_early(g):
    # the WHERE on a is checked as soon as a is bound, so a wide pattern stays cheap
    out = cypher.run(g, "MATCH (a)-[*1..4]-(b) WHERE a.name = 'AWS' RETURN count(DISTINCT b)")
    full = cypher.run(g, "MATCH (a)-[*1..4]-(b) RETURN count(DISTINCT b)")
    assert out["steps"] < full["steps"] / 3
    assert out["rows"][0][0] > 0
