"""The archive as RDF: every recording, collection, namespace, entity and speaker is a resource with a URI, described
with Dublin Core (DCMI Metadata Terms), SKOS, FOAF and OWL, plus a small Lens vocabulary for what those don't say.

RDF is generated from what Lens keeps (metadata.py, the entities, the speakers), so it never goes stale and nothing is
migrated. URIs live under `/id/` of the address Lens publishes (`iiif.base_url`, or the request's):

    /id/recording/<id>   a recording: dcterms title, description, creator, contributor, subject, created,
                         language, license, rights, publisher, identifier, isPartOf, format, extent, references
                         (the entities it mentions), and any other DCMI term saved in its `terms`
    /id/collection/<id>  a collection (dcmitype:Collection), inside its parent or its namespace
    /id/namespace/<name> a namespace: a dcmitype:Collection, and the skos:ConceptScheme of its entities
    /id/entity/<id>      a skos:Concept (also foaf:Person, foaf:Organization, dcterms:Location or dcmitype:Event by
                         its type), with owl:sameAs to the same thing in other namespaces
    /id/speaker/<id>     a foaf:Person, with owl:sameAs to the speakers declared the same person
    /id/topic/<id>       a skos:Concept of the namespace's topics (`/id/namespace/<name>#topics`, a skos:ConceptScheme),
                         with its labels, definition and skos:broader, skos:narrower and skos:related topics; recordings
                         about it name it as dcterms:subject
    /id/field/<id>       a custom field (fields.py), used as the property its values are given with

The Lens vocabulary is `<address>/ns#` (served there, as Turtle). Refine later: a permanent vocabulary address shared by
every Lens, so graphs from two archives use the same terms.

What a visitor gets of a public recording is what its schema.org record says (metadata, published custom fields);
members also get the entities it mentions, its access and its internal fields.
"""

from __future__ import annotations

import pathlib
import urllib.parse

from rdflib import BNode, Graph, Literal, Namespace, URIRef
from rdflib.namespace import DCMITYPE, DCTERMS, FOAF, OWL, RDF, RDFS, SKOS, XSD

from . import access as acc
from . import documents
from . import fields as fieldsmod
from . import metadata as md
from . import render, store

R = store.R

# format name -> media type; the first is what a client that asks for RDF without saying which gets
FORMATS = {
    "turtle": "text/turtle",
    "json-ld": "application/ld+json",
    "nt": "application/n-triples",
    "xml": "application/rdf+xml",
}
MEDIA = {v: k for k, v in FORMATS.items()} | {"application/x-turtle": "turtle", "application/json": "json-ld", "text/n3": "turtle"}
SUFFIX = {"ttl": "turtle", "jsonld": "json-ld", "nt": "nt", "rdf": "xml"}
DC_KIND = {"video": DCMITYPE.MovingImage, "document": DCMITYPE.Text, "image": DCMITYPE.StillImage, "transcript": DCMITYPE.Text}
ENTITY_CLASS = {"PERSON": FOAF.Person, "ORG": FOAF.Organization, "PLACE": DCTERMS.Location, "EVENT": DCMITYPE.Event}
QUIET = ("DATE", "NUMBER")


class Uris:
    """The URIs of one Lens, from the address it publishes."""

    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.vocab = Namespace(f"{self.base}/ns#")

    def recording(self, rid):
        return URIRef(f"{self.base}/id/recording/{int(rid)}")

    def collection(self, cid):
        return URIRef(f"{self.base}/id/collection/{int(cid)}")

    def namespace(self, name):
        return URIRef(f"{self.base}/id/namespace/{urllib.parse.quote(str(name), safe='')}")

    def entity(self, eid):
        return URIRef(f"{self.base}/id/entity/{int(eid)}")

    def topic(self, tid):
        return URIRef(f"{self.base}/id/topic/{int(tid)}")

    def topics(self, name):
        """A namespace's vocabulary of topics."""
        return URIRef(f"{self.namespace(name)}#topics")

    def speaker(self, sid):
        return URIRef(f"{self.base}/id/speaker/{int(sid)}")

    def field(self, fid):
        return URIRef(f"{self.base}/id/field/{int(fid)}")

    def page(self, rid, public=False):
        """The recording's page: the one visitors see when it's public, else the workspace's."""
        return URIRef(f"{self.base}/{'explore/' if public else ''}recordings/{int(rid)}")

    def manifest(self, rid):
        return URIRef(f"{self.base}/iiif/{int(rid)}/manifest")


def new_graph(u: Uris) -> Graph:
    g = Graph()
    for prefix, ns in (("dcterms", DCTERMS), ("dcmitype", DCMITYPE), ("foaf", FOAF), ("skos", SKOS), ("owl", OWL)):
        g.bind(prefix, ns)
    g.bind("lens", u.vocab)
    return g


def _text(g, s, p, langmap):
    for lang, vals in (langmap or {}).items():
        for v in vals:
            g.add((s, p, Literal(v, lang=None if lang == "none" else lang)))


def _value(v):
    """Text, or a resource when it is an http(s) address."""
    return URIRef(v) if isinstance(v, str) and v.startswith(("http://", "https://")) and " " not in v else Literal(v)


def _agent(g, u, p):
    """A creator or contributor: their speaker, their own URI, or a blank node with their name. The role they had here
    is said only of a blank node (a shared one is the same person in every recording; refine later: qualified roles)."""
    if p.get("speaker"):
        node = u.speaker(p["speaker"])
        g.add((node, RDF.type, FOAF.Person))
    elif p.get("uri"):
        node = URIRef(p["uri"])
    else:
        node = BNode()
        if p.get("role"):
            g.add((node, u.vocab.role, Literal(p["role"])))
    g.add((node, FOAF.name, Literal(p["name"])))
    return node


def _namespace_name(db, sid):
    return (db.one("SELECT name FROM $s", s=R("space", sid)) or {}).get("name")


def add_recording(g, db, cfg, u: Uris, rid, member=False, entities=None, topics=None):
    """A recording's description. `member`: the requester has a role where it is (entities, topics, access, internal
    fields). `entities` and `topics` collect the entities it references and the topics it is about, for the caller to
    describe (add_entities, add_topics)."""
    rid = int(rid)
    row = db.one("SELECT space, collection, source, media, path, duration_ms, title FROM $r", r=R("recording", rid))
    if not row:
        raise KeyError(rid)
    meta = md.effective(db, cfg, rid)
    s = u.recording(rid)
    kind = render.kind(row)
    g.add((s, RDF.type, u.vocab.Item))
    g.add((s, RDF.type, DC_KIND.get(kind, DCMITYPE.Sound)))
    _text(g, s, DCTERMS.title, meta.get("label"))
    _text(g, s, DCTERMS.description, meta.get("summary"))
    for p in meta.get("creators") or []:
        g.add((s, DCTERMS.creator, _agent(g, u, p)))
    for p in meta.get("contributors") or []:
        g.add((s, DCTERMS.contributor, _agent(g, u, p)))
    for sub in meta.get("subjects") or []:
        if sub.get("entity") and member:
            node = u.entity(sub["entity"])
            if entities is not None:
                entities.add(int(sub["entity"]))
        elif sub.get("uri"):
            node = URIRef(sub["uri"])
            g.add((node, RDFS.label, Literal(sub["label"])))
        else:
            node = Literal(sub["label"])
        g.add((s, DCTERMS.subject, node))
    if meta.get("navDate"):
        g.add((s, DCTERMS.created, Literal(meta["navDate"], datatype=XSD.dateTime)))
    for lang in meta.get("language") or []:
        g.add((s, DCTERMS.language, Literal(lang, datatype=DCTERMS.RFC5646)))
    if meta.get("rights"):
        g.add((s, DCTERMS.license, URIRef(meta["rights"])))
    _text(g, s, DCTERMS.rights, meta.get("attribution"))
    if meta.get("provider"):
        prov = meta["provider"]
        node = URIRef(prov["homepage"]) if prov.get("homepage") else BNode()
        g.add((node, RDF.type, FOAF.Organization))
        g.add((node, FOAF.name, Literal(prov["name"])))
        if prov.get("logo"):
            g.add((node, FOAF.logo, URIRef(prov["logo"])))
        g.add((s, DCTERMS.publisher, node))
    for i in meta.get("identifiers") or []:
        lit = Literal(i["value"])
        if i.get("type"):
            node = BNode()
            g.add((node, RDF.value, lit))
            g.add((node, u.vocab.identifierType, Literal(i["type"])))
            g.add((s, u.vocab.identifier, node))
        g.add((s, DCTERMS.identifier, lit))
    if meta.get("homepage"):
        g.add((s, FOAF.homepage, URIRef(meta["homepage"])))
    for rel in meta.get("related") or []:
        g.add((s, DCTERMS.relation, URIRef(rel["id"])))
        if rel.get("label"):
            g.add((URIRef(rel["id"]), RDFS.label, Literal(rel["label"])))
    for pair in meta.get("metadata") or []:
        node = BNode()
        _text(g, node, RDFS.label, pair["label"])
        _text(g, node, RDF.value, pair["value"])
        g.add((s, u.vocab.property, node))
    for term, vals in (meta.get("terms") or {}).items():
        for v in vals:
            g.add((s, DCTERMS[term], _value(v)))
    for st in meta.get("statements") or []:
        o = (
            URIRef(st["o"])
            if st.get("uri")
            else Literal(st["o"], lang=st.get("lang"), datatype=URIRef(st["datatype"]) if st.get("datatype") else None)
        )
        g.add((s, URIRef(st["p"]), o))
    if row.get("duration_ms") and kind in ("audio", "video"):
        g.add((s, DCTERMS.extent, Literal(md._iso_duration(row["duration_ms"]), datatype=XSD.duration)))
    path = str(row.get("path") or "")
    fmt = documents.content_type(path) if kind in documents.KINDS else render.AUDIO_TYPES.get(pathlib.PurePath(path).suffix.lower())
    if fmt:
        g.add((s, DCTERMS["format"], Literal(fmt, datatype=DCTERMS.IMT)))
    g.add(
        (s, DCTERMS.isPartOf, u.collection(row["collection"]) if row.get("collection") else u.namespace(_namespace_name(db, row["space"])))
    )
    g.add((s, u.vocab.lensId, Literal(rid)))
    a = acc.of(db, rid)
    g.add((s, FOAF.page, u.page(rid, acc.published(a))))
    g.add((s, RDFS.seeAlso, u.manifest(rid)))
    if member:
        g.add((s, u.vocab.access, Literal(a["access"])))
        refs = db.rows("SELECT entity, count() AS n FROM mentions WHERE recording = $r GROUP BY entity", r=rid)
        for m in refs:
            if entities is not None:
                entities.add(int(m["entity"]))
        ids = [int(m["entity"]) for m in refs]
        shown = (
            {
                e["id"]
                for e in db.rows(
                    "SELECT record::id(id) AS id, type, hidden FROM entity WHERE id IN $ids", ids=[R("entity", i) for i in ids]
                )
                if not e.get("hidden") and e.get("type") not in QUIET
            }
            if ids
            else set()
        )
        for i in ids:
            if i in shown:
                g.add((s, DCTERMS.references, u.entity(i)))
        for t in db.values("SELECT VALUE topic FROM topic_about WHERE recording = $r AND status = 'accepted'", r=rid):
            g.add((s, DCTERMS.subject, u.topic(t)))
            if topics is not None:
                topics.add(int(t))
    defs, frow = fieldsmod.resource_fields(db, rid)
    vals = fieldsmod.values_of(frow)
    for f in defs:
        if vals.get(f["id"]) is None or not (member or f.get("published")):
            continue
        p = u.field(f["id"])
        g.add((p, RDF.type, RDF.Property))
        g.add((p, RDFS.label, Literal(f["label"])))
        for v in vals[f["id"]] if isinstance(vals[f["id"]], list) else [vals[f["id"]]]:
            g.add((s, p, _value(v) if f["type"] == "link" else Literal(v)))
    return s


def add_entities(g, db, u: Uris, eids):
    """Entities as SKOS concepts of their namespace, typed by what they are, linked to the same thing elsewhere."""
    eids = sorted({int(e) for e in eids})
    if not eids:
        return
    rows = db.rows(
        "SELECT record::id(id) AS id, space, name, type, description, hidden FROM entity WHERE id IN $ids",
        ids=[R("entity", i) for i in eids],
    )
    names = {}
    aliases = {}
    for a in db.rows("SELECT key, entity FROM entity_alias WHERE entity IN $e", e=eids):
        aliases.setdefault(a["entity"], []).append(a["key"])
    for e in rows:
        if e.get("hidden"):
            continue
        s = u.entity(e["id"])
        if e["space"] not in names:
            names[e["space"]] = _namespace_name(db, e["space"])
        g.add((s, RDF.type, SKOS.Concept))
        g.add((s, RDF.type, u.vocab.Entity))
        if e.get("type") in ENTITY_CLASS:
            g.add((s, RDF.type, ENTITY_CLASS[e["type"]]))
        g.add((s, SKOS.prefLabel, Literal(e["name"])))
        for al in aliases.get(e["id"], []):
            if al != (e["name"] or "").lower():
                g.add((s, SKOS.altLabel, Literal(al)))
        if e.get("description"):
            g.add((s, SKOS.definition, Literal(e["description"])))
        g.add((s, u.vocab.entityType, Literal(e.get("type") or "TERM")))
        g.add((s, SKOS.inScheme, u.namespace(names[e["space"]])))
    for link in db.rows("SELECT a, b FROM entity_link WHERE a IN $e OR b IN $e", e=eids):
        g.add((u.entity(link["a"]), OWL.sameAs, u.entity(link["b"])))


def add_topics(g, db, u: Uris, tids):
    """Topics as SKOS concepts of their namespace's vocabulary, with how they nest and relate."""
    tids = sorted({int(t) for t in tids})
    if not tids:
        return
    rows = db.rows(
        "SELECT record::id(id) AS id, space, label, alt, definition, broader, related FROM topic WHERE id IN $ids",
        ids=[R("topic", i) for i in tids],
    )
    names = {}
    for t in rows:
        if t["space"] not in names:
            names[t["space"]] = _namespace_name(db, t["space"])
        scheme = u.topics(names[t["space"]])
        s = u.topic(t["id"])
        g.add((s, RDF.type, SKOS.Concept))
        g.add((s, RDF.type, u.vocab.Topic))
        g.add((s, SKOS.prefLabel, Literal(t["label"])))
        for a in t.get("alt") or []:
            g.add((s, SKOS.altLabel, Literal(a)))
        if t.get("definition"):
            g.add((s, SKOS.definition, Literal(t["definition"])))
        g.add((s, SKOS.inScheme, scheme))
        g.add((scheme, RDF.type, SKOS.ConceptScheme))
        g.add((scheme, DCTERMS.title, Literal(f"Topics of {names[t['space']]}")))
        if t.get("broader"):
            for b in t["broader"]:
                g.add((s, SKOS.broader, u.topic(b)))
                g.add((u.topic(b), SKOS.narrower, s))
        else:
            g.add((s, SKOS.topConceptOf, scheme))
            g.add((scheme, SKOS.hasTopConcept, s))
        for r in t.get("related") or []:
            g.add((s, SKOS.related, u.topic(r)))
    for n in db.rows("SELECT record::id(id) AS id, broader FROM topic WHERE broader CONTAINSANY $t", t=tids):
        for b in n.get("broader") or []:
            if b in tids:
                g.add((u.topic(b), SKOS.narrower, u.topic(n["id"])))


def add_speakers(g, db, u: Uris, space=None, ids=None):
    """A namespace's speakers (or the ones in `ids`) as people, linked to those declared the same person."""
    if ids is not None:
        rows = db.rows("SELECT record::id(id) AS id, name, label FROM speaker WHERE id IN $ids", ids=[R("speaker", int(i)) for i in ids])
    else:
        rows = db.rows("SELECT record::id(id) AS id, name, label FROM speaker WHERE space = $s", s=space)
    ids = [r["id"] for r in rows]
    for r in rows:
        s = u.speaker(r["id"])
        g.add((s, RDF.type, FOAF.Person))
        g.add((s, FOAF.name, Literal(r.get("name") or r["label"])))
    for x in (
        db.rows(
            "SELECT record::id(in) AS a, record::id(out) AS b FROM same_as WHERE in IN $s OR out IN $s", s=[R("speaker", i) for i in ids]
        )
        if ids
        else []
    ):
        g.add((u.speaker(x["a"]), OWL.sameAs, u.speaker(x["b"])))


def add_namespace(g, db, u: Uris, sid):
    ns = md.namespace(db, sid)
    s = u.namespace(ns["name"])
    g.add((s, RDF.type, DCMITYPE.Collection))
    g.add((s, RDF.type, SKOS.ConceptScheme))
    g.add((s, RDF.type, u.vocab.Namespace))
    g.add((s, DCTERMS.title, Literal(ns["name"])))
    _text(g, s, DCTERMS.description, ns["meta"].get("summary"))
    if ns["meta"].get("rights"):
        g.add((s, DCTERMS.license, URIRef(ns["meta"]["rights"])))
    return s


def add_collection(g, db, u: Uris, c, ns_name):
    s = u.collection(c["id"])
    g.add((s, RDF.type, DCMITYPE.Collection))
    g.add((s, RDF.type, u.vocab.Collection))
    g.add((s, DCTERMS.title, Literal(c["name"])))
    if c.get("description"):
        g.add((s, DCTERMS.description, Literal(c["description"])))
    parent = u.collection(c["parent"]) if c.get("parent") else u.namespace(ns_name)
    g.add((s, DCTERMS.isPartOf, parent))
    g.add((parent, DCTERMS.hasPart, s))
    return s


def recording_graph(db, cfg, base, rid, member=False):
    u = Uris(base)
    g = new_graph(u)
    ents, tops = set(), set()
    add_recording(g, db, cfg, u, rid, member, ents, tops)
    if member:
        add_entities(g, db, u, ents)
        add_topics(g, db, u, tops)
    return g


def namespace_graph(db, cfg, base, sid):
    """Everything a namespace holds, for its members: itself, its collections, recordings, entities and speakers."""
    u = Uris(base)
    g = new_graph(u)
    add_namespace(g, db, u, sid)
    name = _namespace_name(db, sid)
    for c in db.rows("SELECT record::id(id) AS id, name, parent, description FROM collection WHERE space = $s", s=sid):
        add_collection(g, db, u, c, name)
    ents = set()
    for rid in [r["id"] for r in db.rows("SELECT record::id(id) AS id FROM recording WHERE space = $s ORDER BY id", s=sid)]:
        add_recording(g, db, cfg, u, rid, True, ents)
    ents |= set(db.values("SELECT VALUE record::id(id) FROM entity WHERE space = $s", s=sid))
    add_entities(g, db, u, ents)
    add_topics(g, db, u, db.values("SELECT VALUE record::id(id) FROM topic WHERE space = $s", s=sid))
    add_speakers(g, db, u, space=sid)
    return g


def speaker_graph(db, base, sid):
    u = Uris(base)
    g = new_graph(u)
    add_speakers(g, db, u, ids=[sid])
    return g


def entity_graph(db, base, eid):
    u = Uris(base)
    g = new_graph(u)
    add_entities(g, db, u, [eid])
    return g


def topic_graph(db, base, tid):
    u = Uris(base)
    g = new_graph(u)
    add_topics(g, db, u, [tid])
    return g


def collection_graph(db, base, cid):
    u = Uris(base)
    g = new_graph(u)
    c = db.one("SELECT record::id(id) AS id, space, name, parent, description FROM $r", r=R("collection", int(cid)))
    if not c:
        raise KeyError(cid)
    s = add_collection(g, db, u, c, _namespace_name(db, c["space"]))
    for rid in [r["id"] for r in db.rows("SELECT record::id(id) AS id FROM recording WHERE collection = $c ORDER BY id", c=c["id"])]:
        g.add((s, DCTERMS.hasPart, u.recording(rid)))
    for child in db.values("SELECT VALUE record::id(id) FROM collection WHERE parent = $c", c=c["id"]):
        g.add((s, DCTERMS.hasPart, u.collection(child)))
    return g


def vocabulary(base):
    """The Lens vocabulary itself: its classes and properties, with what each means."""
    u = Uris(base)
    g = new_graph(u)
    v = u.vocab
    g.add((URIRef(str(v)[:-1]), RDF.type, OWL.Ontology))
    g.add((URIRef(str(v)[:-1]), DCTERMS.title, Literal("Lens vocabulary", lang="en")))
    for cls, label in (
        (v.Item, "A resource in the archive: a recording, a video, a document, an image or a transcript."),
        (v.Collection, "A collection of items inside a namespace."),
        (v.Namespace, "A namespace: a separate archive, with its own collections, people, entities and settings."),
        (v.Entity, "A named thing found in, or defined for, a namespace."),
        (v.Topic, "A topic of a namespace's controlled vocabulary: what its items are about."),
    ):
        g.add((cls, RDF.type, OWL.Class))
        g.add((cls, RDFS.comment, Literal(label, lang="en")))
    for prop, label in (
        (v.lensId, "The item's number in this Lens."),
        (v.access, "Who sees the item: public, restricted or private."),
        (v.role, "What an agent did: speaker, host, author, …"),
        (v.entityType, "The entity's type: PERSON, ORG, PLACE, PRODUCT, EVENT, WORK, TERM or one of the namespace's own."),
        (v.property, "A label and value shown with the item (rdfs:label, rdf:value)."),
        (v.identifier, "An identifier with its type (rdf:value, lens:identifierType)."),
        (v.identifierType, "What kind of identifier it is: DOI, ISBN, a catalogue number, …"),
    ):
        g.add((prop, RDF.type, RDF.Property))
        g.add((prop, RDFS.comment, Literal(label, lang="en")))
    return g


CONTEXT = {
    "dcterms": str(DCTERMS),
    "dcmitype": str(DCMITYPE),
    "foaf": str(FOAF),
    "skos": str(SKOS),
    "owl": str(OWL),
    "rdfs": str(RDFS),
    "xsd": str(XSD),
}


def serialize(g: Graph, fmt: str) -> bytes:
    if fmt == "json-ld":
        ctx = {**CONTEXT, **{p: str(n) for p, n in g.namespaces() if p == "lens"}}
        return g.serialize(format="json-ld", context=ctx, indent=2).encode()
    return g.serialize(format=fmt).encode()


def negotiate(accept: str | None, fmt: str | None = None) -> str | None:
    """The RDF format a request asks for: `format` (a name or a suffix), else its Accept header (by q). None when it
    prefers HTML (a browser) or doesn't ask for RDF at all."""
    if fmt:
        fmt = SUFFIX.get(fmt, fmt)
        if fmt not in FORMATS:
            raise ValueError(f"format is one of {', '.join(FORMATS)}")
        return fmt
    best, best_q = None, 0.0
    for i, part in enumerate((accept or "").split(",")):
        bits = [b.strip() for b in part.split(";")]
        media = bits[0].lower()
        q = 1.0
        for b in bits[1:]:
            if b.startswith("q="):
                try:
                    q = float(b[2:])
                except ValueError:
                    q = 0.0
        q -= i * 1e-6  # the first of equal ones wins
        name = "html" if media in ("text/html", "application/xhtml+xml") else MEDIA.get(media)
        if name and q > best_q:
            best, best_q = name, q
    return None if best in (None, "html") else best


# ---------- SPARQL: read-only queries over a namespace's graph ----------
SPARQL_ROWS = 10000
SPARQL_PREFIXES = {"dcterms": DCTERMS, "dcmitype": DCMITYPE, "foaf": FOAF, "skos": SKOS, "owl": OWL, "rdf": RDF, "rdfs": RDFS, "xsd": XSD}


class QueryProblem(ValueError):
    pass


def _walk(node):
    """Every part of a parsed query's algebra."""
    yield node
    if isinstance(node, dict):
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list | tuple):
        for v in node:
            yield from _walk(v)


def check_query(text: str, u: Uris):
    """A read-only query that reaches nothing outside the graph: SELECT, ASK, CONSTRUCT or DESCRIBE, without SERVICE
    (another endpoint) or FROM (a graph to load). Returns the prepared query; QueryProblem otherwise."""
    from rdflib.plugins.sparql import prepareQuery

    try:
        q = prepareQuery(text, initNs={**SPARQL_PREFIXES, "lens": u.vocab})
    except Exception as e:  # rdflib/pyparsing raise many kinds; an update isn't a query either
        raise QueryProblem(f"that isn't a SPARQL query Lens runs (SELECT, ASK, CONSTRUCT or DESCRIBE): {str(e)[:300]}") from None
    for part in _walk(q.algebra):
        if getattr(part, "name", None) == "ServiceGraphPattern":
            raise QueryProblem("SERVICE isn't allowed: queries run on this archive only")
        if isinstance(part, dict) and dict.get(part, "datasetClause"):  # CompValue.get answers a missing key's name
            raise QueryProblem("FROM isn't allowed: queries run on the namespace's graph")
    return q


def _term_json(t):
    if isinstance(t, URIRef):
        return {"type": "uri", "value": str(t)}
    if isinstance(t, BNode):
        return {"type": "bnode", "value": str(t)}
    out = {"type": "literal", "value": str(t)}
    if t.language:
        out["xml:lang"] = t.language
    elif t.datatype:
        out["datatype"] = str(t.datatype)
    return out


def sparql(g: Graph, text: str, base: str):
    """Run a read-only query: ("results", SPARQL 1.1 JSON results) for SELECT and ASK, ("graph", Graph) for CONSTRUCT
    and DESCRIBE."""
    import rdflib.plugins.sparql as rsparql

    rsparql.SPARQL_LOAD_GRAPHS = False  # never fetch a graph a query names
    q = check_query(text, Uris(base))
    try:
        res = g.query(q)
    except Exception as e:
        raise QueryProblem(f"the query failed: {str(e)[:300]}") from None
    if res.type == "ASK":
        return "results", {"head": {}, "boolean": bool(res.askAnswer)}
    if res.type == "SELECT":
        names = [str(v) for v in res.vars or []]
        rows = []
        for i, row in enumerate(res):
            if i >= SPARQL_ROWS:
                break
            rows.append({n: _term_json(row[n]) for n in names if row[n] is not None})
        out = {"head": {"vars": names}, "results": {"bindings": rows}}
        if len(rows) == SPARQL_ROWS:
            out["head"]["link"] = [f"truncated at {SPARQL_ROWS} rows: add LIMIT and OFFSET"]
        return "results", out
    out_g = new_graph(Uris(base))
    for t in res:
        out_g.add(t)
    return "graph", out_g
