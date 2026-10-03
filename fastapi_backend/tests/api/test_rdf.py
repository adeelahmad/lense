"""The archive as RDF: Dublin Core per recording, linked-data URIs with content negotiation, whole-namespace graphs."""

from __future__ import annotations

import json

import pytest
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import DCMITYPE, DCTERMS, FOAF, RDF, SKOS

from app.domain import metadata, rdf
from tests.api.test_iiif import BASE, Env
from tests.helpers import login


@pytest.fixture
def env(app, db, cfg, folder):
    return Env(app, db, cfg, folder)


def parse(r, fmt="turtle"):
    g = Graph()
    g.parse(data=r.text, format=fmt)
    return g


def test_negotiate():
    assert rdf.negotiate("text/turtle") == "turtle"
    assert rdf.negotiate("text/html,application/xhtml+xml,*/*;q=0.8") is None  # a browser
    assert rdf.negotiate("application/ld+json;q=0.9, text/turtle;q=0.5") == "json-ld"
    assert rdf.negotiate("text/html;q=0.1, application/n-triples") == "nt"
    assert rdf.negotiate(None, "ttl") == "turtle"
    assert rdf.negotiate("text/html", "jsonld") == "json-ld"  # ?format= wins
    with pytest.raises(ValueError):
        rdf.negotiate(None, "csv")


def test_recording_uri_public_and_members(env):
    rid = env.clip
    metadata.save(
        env.db,
        env.cfg,
        rid,
        {
            "creators": [{"name": "Lab media team", "uri": "https://example.org/team"}],
            "subjects": [{"label": "Capsid", "uri": "http://www.wikidata.org/entity/Q190764"}],
            "terms": {"spatial": ["Berlin"], "source": ["https://example.org/original-tape"]},
            "language": ["en"],
        },
    )
    anon = env.client()
    s = URIRef(f"{BASE}/id/recording/{rid}")

    r = anon.get(f"/id/recording/{rid}", headers={"Accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"{BASE}/explore/recordings/{rid}"

    r = anon.get(f"/id/recording/{rid}", headers={"Accept": "text/turtle"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/turtle")
    assert "Accept" in r.headers["vary"]
    g = parse(r)
    assert (s, RDF.type, DCMITYPE.Sound) in g
    assert (s, DCTERMS.license, URIRef("http://creativecommons.org/licenses/by/4.0/")) in g
    assert (s, DCTERMS.subject, URIRef("http://www.wikidata.org/entity/Q190764")) in g
    assert (s, DCTERMS.spatial, Literal("Berlin")) in g
    assert (s, DCTERMS.source, URIRef("https://example.org/original-tape")) in g
    assert (s, DCTERMS.creator, URIRef("https://example.org/team")) in g
    assert (URIRef("https://example.org/team"), FOAF.name, Literal("Lab media team")) in g
    assert g.value(s, DCTERMS.isPartOf) is not None
    assert not list(g.objects(s, DCTERMS.references))  # the entities it mentions are for members

    j = anon.get(f"/id/recording/{rid}", params={"format": "jsonld"})
    assert j.headers["content-type"].startswith("application/ld+json")
    assert (s, DCTERMS.spatial, Literal("Berlin")) in parse(j, "json-ld")
    assert "dcterms" in json.dumps(j.json().get("@context"))
    assert (s, RDF.type, DCMITYPE.Sound) in parse(anon.get(f"/id/recording/{rid}", params={"format": "xml"}), "xml")
    assert (s, RDF.type, DCMITYPE.Sound) in parse(anon.get(f"/id/recording/{rid}", params={"format": "nt"}), "nt")
    assert anon.get(f"/id/recording/{rid}", params={"format": "csv"}).status_code == 400

    # private recordings look absent; members see them, with the entities they mention
    assert anon.get(f"/id/recording/{env.locked}", headers={"Accept": "text/turtle"}).status_code == 404
    c, h = env.admin()
    r = c.get(f"/id/recording/{env.pub}", headers={**h, "Accept": "text/turtle"})
    g = parse(r)
    refs = list(g.objects(URIRef(f"{BASE}/id/recording/{env.pub}"), DCTERMS.references))
    assert refs and all((e, RDF.type, SKOS.Concept) in g for e in refs)
    assert any(str(g.value(e, SKOS.prefLabel)) == "Dyno Therapeutics" for e in refs)
    r = c.get(f"/id/recording/{env.pub}", headers={**h, "Accept": "text/html"}, follow_redirects=False)
    assert r.headers["location"] == f"{BASE}/recordings/{env.pub}"


def test_namespace_graph_and_other_uris(env):
    c, h = env.admin()
    r = c.get("/api/v1/namespaces/pods/rdf", headers=h)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/turtle")
    g = parse(r)
    ns = URIRef(f"{BASE}/id/namespace/pods")
    assert (ns, RDF.type, DCMITYPE.Collection) in g
    items = set(g.subjects(RDF.type, URIRef(f"{BASE}/ns#Item")))
    assert {URIRef(f"{BASE}/id/recording/{i}") for i in (env.pub, env.locked, env.clip)} <= items
    people = set(g.subjects(RDF.type, FOAF.Person))
    assert any(str(g.value(p, FOAF.name)) == "Alice" for p in people)
    dl = c.get("/api/v1/namespaces/pods/rdf", headers=h, params={"format": "json-ld", "download": True})
    assert dl.headers["content-disposition"] == 'attachment; filename="pods.jsonld"'

    eid = next(int(str(e).rsplit("/", 1)[1]) for e in g.subjects(RDF.type, SKOS.Concept))
    assert c.get(f"/id/entity/{eid}", headers={**h, "Accept": "text/turtle"}).status_code == 200
    r = c.get(f"/id/entity/{eid}", headers={**h, "Accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith(f"/entities/{eid}")
    spk = next(int(str(p).rsplit("/", 1)[1]) for p in people if "/id/speaker/" in str(p))
    assert (URIRef(f"{BASE}/id/speaker/{spk}"), RDF.type, FOAF.Person) in parse(
        c.get(f"/id/speaker/{spk}", headers=h, params={"format": "ttl"})
    )
    assert c.get("/id/namespace/pods", headers=h, params={"format": "ttl"}).status_code == 200

    # members only: a viewer of pods sees pods, not calls; anonymous sees neither
    vh = login(c, "vi@x.io", "viewer password 1")
    assert c.get("/api/v1/namespaces/pods/rdf", headers=vh).status_code == 200
    assert c.get("/api/v1/namespaces/calls/rdf", headers=vh).status_code == 404
    anon = env.client()
    assert anon.get(f"/id/entity/{eid}", headers={"Accept": "text/turtle"}).status_code == 404
    assert anon.get("/id/namespace/pods", headers={"Accept": "text/turtle"}).status_code == 404
    assert anon.get(f"/id/speaker/{spk}", headers={"Accept": "text/turtle"}).status_code == 404

    r = c.get(f"/api/v1/recordings/{env.pub}/rdf", headers=h, params={"format": "json-ld"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/ld+json")
    assert c.get(f"/api/v1/recordings/{env.call}/rdf", headers=vh).status_code == 404

    v = parse(env.client().get("/ns"))
    assert (URIRef(f"{BASE}/ns#Item"), RDF.type, URIRef("http://www.w3.org/2002/07/owl#Class")) in v


def test_terms_field(env):
    c, h = env.admin()
    url = f"/api/v1/recordings/{env.pub}/metadata"
    bad = c.put(url, headers=h, json={"set": {"terms": {"colour": ["blue"]}}})
    assert bad.status_code == 400 and "colour" in bad.text
    m = c.put(url, headers=h, json={"set": {"terms": {"alternative": "Episode one", "temporal": ["2026", " ", "2026"]}}}).json()
    assert m["meta"]["terms"] == {"alternative": ["Episode one"], "temporal": ["2026"]}
    dc = c.get(f"/iiif/{env.pub}/dc.xml").text
    assert "<dc:title>Episode one</dc:title>" in dc and "<dc:coverage>2026</dc:coverage>" in dc


def test_import_round_trip_and_dublin_core(env):
    c, h = env.admin()
    rid = env.pub
    metadata.save(env.db, env.cfg, rid, {"identifiers": [{"type": "DOI", "value": "10.1234/ep1"}], "terms": {"spatial": ["Berlin"]}})
    exported = c.get(f"/api/v1/recordings/{rid}/rdf", headers=h).text
    url = "/api/v1/namespaces/pods/rdf/import"
    same = c.post(url, headers=h, json={"data": exported}).json()
    assert same["matched"] == 1 and same["changed"] == 0, same  # what Lens says of itself changes nothing

    ttl = f"""
@prefix dcterms: <http://purl.org/dc/terms/> .
@prefix dc: <http://purl.org/dc/elements/1.1/> .
@prefix ex: <https://example.org/vocab#> .
<{BASE}/id/recording/{rid}> dcterms:title "Capsid episode"@en ;
    dcterms:temporal "2026" ;
    dcterms:license <https://creativecommons.org/licenses/by/4.0/> ;
    ex:rating "5" .
<https://elsewhere.example/item/9> dc:identifier "10.1234/ep1" ; dc:title "Episode one, catalogued" ; dc:coverage "Europe" .
<https://elsewhere.example/item/10> dcterms:title "Nothing here" .
"""
    dry = c.post(url, headers=h, json={"data": ttl}).json()
    assert dry["dry_run"] and dry["matched"] == 2 and [u["title"] for u in dry["unmatched"]] == ["Nothing here"]
    first = next(i for i in dry["items"] if i["subject"].endswith(f"/id/recording/{rid}"))
    assert first["fields"] == ["label", "rights", "statements", "terms"] and first["statements"] == 1
    assert c.get(f"/api/v1/recordings/{rid}/metadata", headers=h).json()["meta"]["label"] != {"en": ["Capsid episode"]}

    done = c.post(url, headers=h, json={"data": ttl, "dry_run": False}).json()
    assert done["changed"] == 2
    meta = c.get(f"/api/v1/recordings/{rid}/metadata", headers=h).json()["meta"]
    assert meta["label"] == {"none": ["Episode one, catalogued"]}  # described twice: terms add up, the one read last wins
    assert "more than once" in done["items"][1]["notes"][0]
    assert meta["terms"] == {"spatial": ["Berlin"], "temporal": ["2026"], "coverage": ["Europe"]}  # the ones it didn't mention stay
    assert meta["statements"] == [{"p": "https://example.org/vocab#rating", "o": "5"}]
    g = parse(c.get(f"/api/v1/recordings/{rid}/rdf", headers=h))
    assert (URIRef(f"{BASE}/id/recording/{rid}"), URIRef("https://example.org/vocab#rating"), Literal("5")) in g  # said again
    assert "metadata.rdf_import" in [a["action"] for a in c.get("/api/v1/audit", headers=h).json()]
    hist = c.get(f"/api/v1/recordings/{rid}/metadata/history", headers=h).json()
    assert c.post(f"/api/v1/metadata/edits/{hist[0]['id']}/revert", headers=h).status_code == 200  # an import is revertable

    jsonld = {
        "@context": {"dcterms": "http://purl.org/dc/terms/"},
        "@id": f"{BASE}/id/recording/{rid}",
        "dcterms:audience": "Researchers",
    }
    r = c.post(url, headers=h, json={"data": json.dumps(jsonld), "dry_run": False}).json()
    assert r["items"][0]["fields"] == ["terms"]
    remote = {**jsonld, "@context": "https://schema.org/"}
    bad = c.post(url, headers=h, json={"data": json.dumps(remote)})
    assert bad.status_code == 400 and "@context" in bad.text
    assert c.post(url, headers=h, json={"data": "<rdf:RDF/>", "format": "xml"}).status_code == 400
    assert c.post(url, headers=h, json={"data": "this is not turtle ."}).status_code == 400

    # a recording of another namespace isn't touched from here; viewers can't import
    other = f'<{BASE}/id/recording/{env.call}> <http://purl.org/dc/terms/title> "x" .'
    assert c.post(url, headers=h, json={"data": other}).json()["matched"] == 0
    vh = login(c, "vi@x.io", "viewer password 1")
    assert c.post(url, headers=vh, json={"data": ttl}).status_code == 403


def test_sparql(env):
    c, h = env.admin()
    url = "/api/v1/namespaces/pods/sparql"
    q = "SELECT ?name WHERE { ?e a skos:Concept ; skos:prefLabel ?name } ORDER BY ?name"
    r = c.get(url, headers=h, params={"query": q})
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/sparql-results+json")
    names = [b["name"]["value"] for b in r.json()["results"]["bindings"]]
    assert "Dyno Therapeutics" in names and r.json()["head"]["vars"] == ["name"]

    ask = c.post(url, headers=h, json={"query": 'ASK { ?r dcterms:title "ep1" }'}).json()
    assert ask["boolean"] is True
    built = c.post(url, headers=h, json={"query": "CONSTRUCT { ?r dcterms:title ?t } WHERE { ?r a lens:Item ; dcterms:title ?t }"})
    assert built.headers["content-type"].startswith("text/turtle")
    assert len(list(parse(built).triples((None, DCTERMS.title, None)))) == 3

    for bad, why in (
        ("SELECT * WHERE { SERVICE <https://dbpedia.org/sparql> { ?s ?p ?o } }", "SERVICE"),
        ("SELECT * FROM <https://example.org/data.ttl> WHERE { ?s ?p ?o }", "FROM"),
        ("SELECT * FROM NAMED <https://example.org/data.ttl> WHERE { GRAPH ?g { ?s ?p ?o } }", "FROM"),
        ("INSERT DATA { <a:b> <a:c> <a:d> }", "SELECT"),
        ("LOAD <https://example.org/data.ttl>", "SELECT"),
        ("SELECT nonsense", "SELECT"),
    ):
        r = c.post(url, headers=h, json={"query": bad})
        assert r.status_code == 400 and why in r.text, (bad, r.text)

    vh = login(c, "vi@x.io", "viewer password 1")
    assert c.get(url, headers=vh, params={"query": q}).status_code == 200
    assert c.get("/api/v1/namespaces/calls/sparql", headers=vh, params={"query": q}).status_code == 404
    assert c.get(url, params={"query": q}).status_code == 401
