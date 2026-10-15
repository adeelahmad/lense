"""Topics: the vocabulary (labels, broader, narrower, related), recordings about topics, topics made from entities,
undoing merges and deletes, and topics in the property graph and in RDF."""

from __future__ import annotations

import json

import pytest
from rdflib import Graph, URIRef
from rdflib.namespace import SKOS

from app.domain import ai_tools, analyze, notebook, store, topics
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
    aws = env.eid("AWS")  # a term (Amazon Web Services is an organisation)
    mentioned_in = {m["recording"] for m in db.rows("SELECT recording FROM mentions WHERE entity = $e", e=aws)}
    assert c.post(f"/api/v1/entities/{aws}/topic", headers=vi).status_code == 403
    dyno = env.eid("Dyno Therapeutics")
    assert c.post(f"/api/v1/entities/{dyno}/topic", headers=ed).status_code == 400  # an organisation, not a topic
    t = c.post(f"/api/v1/entities/{aws}/topic", headers=ed).json()
    assert t["label"] == "AWS" and t["from_entity"] == aws
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


def test_undo_a_merge(env):
    c, ed, vi, db = env.c, env.h["editor"], env.h["viewer"], env.db
    t = make(c, ed, "Funding")
    u = make(c, ed, "Grants", alt=["bursaries"], definition="Money given.")
    v = make(c, ed, "Money", broader=[u["id"]], related=[u["id"]])
    c.post(f"/api/v1/topics/{t['id']}/recordings", json={"recordings": [1, 2]}, headers=ed)
    c.post(f"/api/v1/topics/{u['id']}/recordings", json={"recordings": [2, 4]}, headers=ed)
    page = notebook.create(db, store.ns_id(db, "pods"), "ed@x.io", "Grant notes", about_thing=f"topic:{u['id']}")
    m = c.post("/api/v1/topics/merge", json={"keep": t["id"], "others": [u["id"]]}, headers=ed).json()
    assert m["alt"] == ["Grants", "bursaries"] and [a["recording"] for a in m["about"]] == [1, 2, 4]
    assert c.get(f"/api/v1/topics/{v['id']}", headers=vi).json()["broader"] == [{"id": t["id"], "label": "Funding"}]
    changes = c.get("/api/v1/namespaces/pods/topics/changes", headers=vi).json()
    assert [(x["id"], x["kind"], x["keep"], x["labels"], x["undone"]) for x in changes] == [
        (m["undo"], "merge", t["id"], ["Grants"], False)
    ]
    assert c.post(f"/api/v1/topics/changes/{m['undo']}/undo", headers=vi).status_code == 403
    assert c.post(f"/api/v1/topics/changes/{m['undo']}/undo", headers=ed).status_code == 200
    # both topics as they were: labels, recordings, broader and related, and the page
    back = c.get(f"/api/v1/topics/{u['id']}", headers=vi).json()
    assert (back["label"], back["alt"], back["definition"]) == ("Grants", ["bursaries"], "Money given.")
    assert [a["recording"] for a in back["about"]] == [2, 4]
    assert back["related"] == [{"id": v["id"], "label": "Money"}]
    kept = c.get(f"/api/v1/topics/{t['id']}", headers=vi).json()
    assert kept["alt"] == [] and kept["related"] == [] and [a["recording"] for a in kept["about"]] == [1, 2]
    money = c.get(f"/api/v1/topics/{v['id']}", headers=vi).json()
    assert money["broader"] == money["related"] == [{"id": u["id"], "label": "Grants"}]
    assert db.one("SELECT about FROM $r", r=R("note_page", page))["about"] == f"topic:{u['id']}"
    # once only
    again = c.post(f"/api/v1/topics/changes/{m['undo']}/undo", headers=ed)
    assert again.status_code == 400 and "Nothing to undo" in again.text
    assert c.get("/api/v1/namespaces/pods/topics/changes", headers=vi).json()[0]["undone"] is True
    assert c.post("/api/v1/topics/changes/999/undo", headers=ed).status_code == 404


def test_undo_a_delete(env):
    c, ed, vi = env.c, env.h["editor"], env.h["viewer"]
    bio = make(c, ed, "Biology")
    gene = make(c, ed, "Gene therapy", alt=["GT"], broader=[bio["id"]])
    vec = make(c, ed, "Viral vectors", broader=[gene["id"]])
    c.post(f"/api/v1/topics/{gene['id']}/recordings", json={"recordings": [1]}, headers=ed)
    d = c.delete(f"/api/v1/topics/{gene['id']}", headers=ed).json()
    assert c.get(f"/api/v1/topics/{vec['id']}", headers=vi).json()["broader"] == [{"id": bio["id"], "label": "Biology"}]
    assert c.post(f"/api/v1/topics/changes/{d['undo']}/undo", headers=ed).status_code == 200
    g = c.get(f"/api/v1/topics/{gene['id']}", headers=vi).json()
    assert (g["label"], g["alt"], g["broader"]) == ("Gene therapy", ["GT"], [{"id": bio["id"], "label": "Biology"}])
    assert [a["recording"] for a in g["about"]] == [1]
    assert c.get(f"/api/v1/topics/{vec['id']}", headers=vi).json()["broader"] == [{"id": gene["id"], "label": "Gene therapy"}]
    # a label someone has taken since keeps the topic away
    d = c.delete(f"/api/v1/topics/{gene['id']}", headers=ed).json()
    make(c, ed, "gt")
    clash = c.post(f"/api/v1/topics/changes/{d['undo']}/undo", headers=ed)
    assert clash.status_code == 400 and "Another topic is called Gene therapy" in clash.text
    assert c.get(f"/api/v1/topics/{gene['id']}", headers=vi).status_code == 404


def test_undo_deleting_a_topic_made_from_an_entity(env):
    c, ed, vi = env.c, env.h["editor"], env.h["viewer"]
    aws = env.eid("AWS")  # a term (Amazon Web Services is an organisation)
    t = c.post(f"/api/v1/entities/{aws}/topic", headers=ed).json()
    d = c.delete(f"/api/v1/topics/{t['id']}", headers=ed).json()
    assert c.get(f"/api/v1/entities/{aws}", headers=vi).json()["hidden"] is False
    assert c.post(f"/api/v1/topics/changes/{d['undo']}/undo", headers=ed).status_code == 200
    back = c.get(f"/api/v1/topics/{t['id']}", headers=vi).json()
    assert back["from_entity"] == aws and {a["source"] for a in back["about"]} == {"entity"}
    assert c.get(f"/api/v1/entities/{aws}", headers=vi).json()["hidden"] is True
    hist = c.get("/api/v1/graph/history", headers=vi, params={"entity": aws}).json()["versions"]
    assert (hist[0]["op"], hist[0]["why"]) == ("entity.hide", f"topic {t['id']} is back")


def test_the_assistant_proposes_topic_changes(env):
    db, cfg = env.db, env.cfg
    pods, calls = store.ns_id(db, "pods"), store.ns_id(db, "calls")
    keep = topics.create(db, pods, "Funding")
    grants = topics.create(db, pods, "Grants")
    elsewhere = topics.create(db, calls, "Budgets")
    me = {"id": 1, "email": "ed@x.io"}
    box = ai_tools.Toolbox(db, cfg, me, {pods, calls}, {calls}, None, None)
    assert box.call("propose_topic_change", {"action": "delete", "topic_id": grants})[1].endswith("needs editor access to the namespace")
    box = ai_tools.Toolbox(db, cfg, me, {pods}, {pods}, None, None)
    assert box.call("propose_topic_change", {"action": "rename", "topic_id": keep})[1] == "propose_topic_change: rename needs new_label"
    assert box.call("propose_topic_change", {"action": "delete", "topic_id": elsewhere})[1].endswith(f"no topic {elsewhere} in scope")
    merge = {"action": "merge", "topic_id": keep, "merge_ids": [elsewhere]}
    assert box.call("propose_topic_change", merge)[1].endswith("other topics of the same namespace")
    # nothing changes until the person says yes; a merge says how to undo it
    out = json.loads(box.call("propose_topic_change", {**merge, "merge_ids": [grants]})[0])
    assert out["summary"] == "Merge Grants into the topic Funding" and db.one("SELECT id FROM $r", r=R("topic", grants))
    done = ai_tools.approve(db, cfg, out["approval"], me, {pods})
    assert done["status"] == "done" and not db.one("SELECT id FROM $r", r=R("topic", grants))
    topics.undo(db, done["undo"])
    assert topics.detail(db, grants, {pods})["label"] == "Grants"
    out = json.loads(box.call("propose_topic_change", {"action": "describe", "topic_id": keep, "definition": "Money for work."})[0])
    ai_tools.approve(db, cfg, out["approval"], me, {pods})
    assert topics.detail(db, keep, {pods})["definition"] == "Money for work."
    out = json.loads(box.call("propose_topic_change", {"action": "rename", "topic_id": keep, "new_label": "Funds"})[0])
    assert out["summary"] == "Rename the topic Funding to Funds"
    assert ai_tools.approve(db, cfg, out["approval"], me, {calls}, decision="decline") == {"status": "declined"}
    assert topics.detail(db, keep, {pods})["label"] == "Funding"


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
