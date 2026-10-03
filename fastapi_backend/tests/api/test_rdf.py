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
