"""Topics: the vocabulary (labels, broader, narrower, related), recordings about topics, topics made from entities,
and topics in the property graph and in RDF."""

from __future__ import annotations

import pytest
from rdflib import Graph, URIRef
from rdflib.namespace import SKOS

from app.domain import analyze, topics
from app.domain.store import R
from tests.api.test_entities import Env


@pytest.fixture
def env(db, cfg, folder, client):
    return Env(db, cfg, folder, client)


def make(c, h, label, **kw):
    r = c.post("/api/v1/namespaces/pods/topics", json={"label": label, **kw}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()


def test_vocabulary(env):
    c, ed, vi = env.c, env.h["editor"], env.h["viewer"]
    assert c.post("/api/v1/namespaces/pods/topics", json={"label": "Biology"}, headers=vi).status_code == 403
    bio = make(c, ed, "Biology", definition="Living things.")
    gene = make(c, ed, "Gene therapy", alt=["gene therapies", "Gene Therapy", "GT"], broader=[bio["id"]])
    assert gene["alt"] == ["gene therapies", "GT"]  # the label itself isn't another label
    assert gene["broader"] == [{"id": bio["id"], "label": "Biology"}]
    vec = make(c, ed, "Viral vectors", broader=[gene["id"]], related=[bio["id"]])
    # labels and other labels are unique in the namespace
    assert c.post("/api/v1/namespaces/pods/topics", json={"label": "gt"}, headers=ed).status_code == 400
    # a topic can't end up broader than itself
    loop = c.patch(f"/api/v1/topics/{bio['id']}", json={"broader": [vec["id"]]}, headers=ed)
    assert loop.status_code == 400 and "broader than itself" in loop.text
    # related is symmetric
    b = c.get(f"/api/v1/topics/{bio['id']}", headers=vi).json()
    assert b["related"] == [{"id": vec["id"], "label": "Viral vectors"}]
    assert b["narrower"] == [{"id": gene["id"], "label": "Gene therapy"}]
    top = c.get("/api/v1/topics", params={"top": True}, headers=vi).json()
    assert [t["label"] for t in top["items"]] == ["Biology"] and top["items"][0]["narrower"] == 1
    assert [t["label"] for t in c.get("/api/v1/topics", params={"q": "therapies"}, headers=vi).json()["items"]] == ["Gene therapy"]
    # editing: rename, clear the definition
    r = c.patch(f"/api/v1/topics/{bio['id']}", json={"label": "Life sciences", "definition": ""}, headers=ed).json()
    assert (r["label"], r["definition"]) == ("Life sciences", None)
    assert c.patch(f"/api/v1/topics/{bio['id']}", json={"label": "x"}, headers=vi).status_code == 403
    # deleting a topic moves its narrower topics up
    assert c.delete(f"/api/v1/topics/{gene['id']}", headers=ed).status_code == 200
    v = c.get(f"/api/v1/topics/{vec['id']}", headers=vi).json()
    assert v["broader"] == [{"id": bio["id"], "label": "Life sciences"}]


def test_recordings_about_topics(env):
    c, ed, vi = env.c, env.h["editor"], env.h["viewer"]
    t = make(c, ed, "Funding")
    out = c.post(f"/api/v1/topics/{t['id']}/recordings", json={"recordings": [1, 2]}, headers=ed).json()
    assert [(a["recording"], a["source"], a["status"]) for a in out["about"]] == [(1, "person", "accepted"), (2, "person", "accepted")]
    assert out["recordings"] == 2
    # only the topic's namespace's recordings (call is in calls)
    assert c.post(f"/api/v1/topics/{t['id']}/recordings", json={"recordings": [3]}, headers=ed).status_code == 404
    assert c.get("/api/v1/recordings/1/topics", headers=vi).json() == [
        {"id": t["id"], "label": "Funding", "source": "person", "weight": None, "status": "accepted"}
    ]
    c.post(f"/api/v1/topics/{t['id']}/recordings", json={"recordings": [2], "remove": True}, headers=ed)
    assert c.get(f"/api/v1/topics/{t['id']}", headers=vi).json()["recordings"] == 1
    # merging: labels become other labels, recordings move over
    u = make(c, ed, "Grants")
    c.post(f"/api/v1/topics/{u['id']}/recordings", json={"recordings": [4]}, headers=ed)
    m = c.post("/api/v1/topics/merge", json={"keep": t["id"], "others": [u["id"]]}, headers=ed).json()
    assert m["alt"] == ["Grants"] and [a["recording"] for a in m["about"]] == [1, 4]
    assert c.get(f"/api/v1/topics/{u['id']}", headers=vi).status_code == 404


def test_topic_from_entity(env):
    c, ed, vi, db = env.c, env.h["editor"], env.h["viewer"], env.db
    aws = env.eid("Amazon Web Services")
    mentioned_in = {m["recording"] for m in db.rows("SELECT recording FROM mentions WHERE entity = $e", e=aws)}
    assert c.post(f"/api/v1/entities/{aws}/topic", headers=vi).status_code == 403
    dyno = env.eid("Dyno Therapeutics")
    assert c.post(f"/api/v1/entities/{dyno}/topic", headers=ed).status_code == 400  # an organisation, not a topic
    t = c.post(f"/api/v1/entities/{aws}/topic", headers=ed).json()
    assert t["label"] == "Amazon Web Services" and t["from_entity"] == aws
    assert {a["recording"] for a in t["about"]} == mentioned_in and {a["source"] for a in t["about"]} == {"entity"}
    assert c.get(f"/api/v1/entities/{aws}", headers=vi).json()["hidden"] is True
    # deleting the topic shows the entity again
    c.delete(f"/api/v1/topics/{t['id']}", headers=ed)
    assert c.get(f"/api/v1/entities/{aws}", headers=vi).json()["hidden"] is False
    # both are versions in the graph's history, with why
    hist = c.get("/api/v1/graph/history", headers=vi, params={"entity": aws}).json()["versions"]
    assert [(v["op"], v["why"]) for v in hist[:2]] == [
        ("entity.show", f"topic {t['id']} was deleted"),
        ("entity.hide", f"became topic {t['id']}"),
    ]


def test_topics_in_the_graph_and_rdf(env):
    c, ed, vi = env.c, env.h["editor"], env.h["viewer"]
    bio = make(c, ed, "Biology")
    gene = make(c, ed, "Gene therapy", broader=[bio["id"]], alt=["GT"])
    c.post(f"/api/v1/topics/{gene['id']}/recordings", json={"recordings": [1]}, headers=ed)
    q = {
        "query": "MATCH (t:Topic {name: 'Biology'})-[:NARROWER*0..4]->(n:Topic)<-[a:ABOUT]-(r:Recording) RETURN r.name, n.name, a.source",
        "scope": "ns:pods",
    }
    out = c.post("/api/v1/graph/query", json=q, headers=vi).json()
    assert out["rows"] == [["ep1", "Gene therapy", "person"]]
    rel = c.get("/api/v1/graph/related", params={"node": f"t{gene['id']}", "relation": "parents", "scope": "ns:pods"}, headers=vi).json()
    assert {n["id"] for n in rel["nodes"]} == {f"t{gene['id']}", f"t{bio['id']}", "r1"}
    schema = c.get("/api/v1/graph/schema", params={"scope": "ns:pods"}, headers=vi).json()
    assert {"label": "Topic", "count": 2} in [{k: x[k] for k in ("label", "count")} for x in schema["labels"]]
    # RDF: a SKOS concept in the namespace's vocabulary
    r = c.get(f"/id/topic/{gene['id']}", headers={**vi, "Accept": "text/turtle"})
    assert r.status_code == 200
    g = Graph().parse(data=r.text, format="turtle")
    me = next(s for s in g.subjects(SKOS.prefLabel, None) if str(s).endswith(f"/id/topic/{gene['id']}"))
    assert str(next(g.objects(me, SKOS.altLabel))) == "GT"
    assert str(next(g.objects(me, SKOS.broader))).endswith(f"/id/topic/{bio['id']}")
    assert str(next(g.objects(me, SKOS.inScheme))).endswith("/id/namespace/pods#topics")
    rec = c.get("/id/recording/1", headers={**vi, "Accept": "text/turtle"})
    rg = Graph().parse(data=rec.text, format="turtle")
    subjects = {str(o) for o in rg.objects(None, URIRef("http://purl.org/dc/terms/subject"))}
    assert any(s.endswith(f"/id/topic/{gene['id']}") for s in subjects)
    # a stranger to the namespace gets nothing
    assert c.get(f"/id/topic/{gene['id']}", headers={"Accept": "text/turtle"}).status_code == 404


def test_analysis_suggests_topics(env):
    c, ed, vi, db = env.c, env.h["editor"], env.h["viewer"], env.db
    # a transcript that says a label (or another label) twice or more is suggested; once isn't enough
    cap = make(c, ed, "Capsid design", alt=["capsid"])
    bench = make(c, ed, "Benchmarks")
    about = {(a["recording"], a["source"], a["status"]) for a in c.get(f"/api/v1/topics/{cap['id']}", headers=vi).json()["about"]}
    assert about == {(env.a, "analysis", "suggested")}
    b = c.get(f"/api/v1/topics/{bench['id']}", headers=vi).json()
    assert [(a["recording"], a["status"]) for a in b["about"]] == [(env.b, "suggested")] and b["recordings"] == 0
    # accepting keeps where it came from; dismissing is remembered, so analysis doesn't suggest it again
    c.post(f"/api/v1/topics/{cap['id']}/recordings", json={"recordings": [env.a]}, headers=ed)
    c.post(f"/api/v1/topics/{bench['id']}/recordings", json={"recordings": [env.b], "remove": True}, headers=ed)
    assert [(t["label"], t["source"], t["status"]) for t in c.get(f"/api/v1/recordings/{env.a}/topics", headers=vi).json()] == [
        ("Capsid design", "analysis", "accepted")
    ]
    assert c.get(f"/api/v1/recordings/{env.b}/topics", headers=vi).json() == []
    c.patch(f"/api/v1/topics/{bench['id']}", json={"alt": ["benchmark results"]}, headers=ed)  # matches again
    assert c.get(f"/api/v1/topics/{bench['id']}", headers=vi).json()["about"] == []
    analyze.analyze_recording(db, env.cfg, env.b)  # re-analysis keeps people's choices
    assert c.get(f"/api/v1/recordings/{env.b}/topics", headers=vi).json() == []
    # summaries: a topic they name is suggested; what no topic covers is offered as a new one, until skipped
    db.q("UPDATE $r SET summary = $s", r=R("recording", env.x), s={"topics": ["Machine learning", "Lab safety", "capsid"]})
    db.q("UPDATE $r SET summary = $s", r=R("recording", env.b), s={"topics": ["lab safety"]})
    ml = make(c, ed, "Machine learning")
    assert [(a["recording"], a["weight"]) for a in ml["about"]] == [(env.x, topics.SUMMARY_WEIGHT)]
    cands = c.get("/api/v1/namespaces/pods/topics/candidates", headers=vi).json()
    assert cands == [{"label": "lab safety", "recordings": 2}]  # the first spelling when none is more common
    assert c.post("/api/v1/namespaces/pods/topics/candidates/skip", json={"label": "lab  Safety"}, headers=vi).status_code == 403
    assert c.post("/api/v1/namespaces/pods/topics/candidates/skip", json={"label": "lab  Safety"}, headers=ed).status_code == 200
    assert c.get("/api/v1/namespaces/pods/topics/candidates", headers=vi).json() == []
    # only accepted links are in the graph
    q = {"scope": "ns:pods", "query": "MATCH (r:Recording)-[:ABOUT]->(t:Topic) RETURN r.id, t.name"}
    rows = c.post("/api/v1/graph/query", json=q, headers=vi).json()["rows"]
    assert rows == [[f"r{env.a}", "Capsid design"]]
