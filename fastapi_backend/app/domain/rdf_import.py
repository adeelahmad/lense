"""Reading RDF into a namespace: Dublin Core descriptions (DCMI Metadata Terms, or the 15 elements of DC 1.1) become
recordings' metadata, through metadata.save, so every change is in their history and can be reverted.

A description is matched to a recording by its URI (this Lens's `/id/recording/<id>`, see rdf.py), else by an
identifier the recording already has (`dcterms:identifier`, or `lens:lensId`). What maps onto a metadata field
(title, description, creator, contributor, subject, created, language, license, rights, publisher, identifier,
relation, homepage, every other DCMI term) is saved there; any other statement about the recording is kept as it came
(`statements`) and said again when the recording is exported, so a round trip loses nothing. A description that
matches no recording is reported, not created (refine later: make a resource for it).

Turtle, N-Triples and JSON-LD are read. Nothing is fetched while reading: a JSON-LD document must carry its @context
inline. RDF/XML isn't read (its XML parser could be pointed at external entities).
"""

from __future__ import annotations

import json
import re

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import DCTERMS, FOAF, RDF, RDFS, SKOS

from . import metadata as md
from . import store

R = store.R
DC = Namespace("http://purl.org/dc/elements/1.1/")
FORMATS = ("turtle", "nt", "json-ld")
MAX_BYTES = 5 * 1024 * 1024
MAX_SUBJECTS = 5000


def LENS(base):  # noqa: N802
    return Namespace(f"{base}/ns#")


class ImportProblem(ValueError):
    pass


def guess(data: str) -> str:
    head = data.lstrip()[:1]
    return "json-ld" if head in ("{", "[") else "turtle"


def _no_remote_context(doc) -> None:
    """A JSON-LD document whose @context is a link (or @import's one) would make the parser fetch it."""
    if isinstance(doc, list):
        for x in doc:
            _no_remote_context(x)
    elif isinstance(doc, dict):
        for k, v in doc.items():
            if k == "@context":
                for c in v if isinstance(v, list) else [v]:
                    if isinstance(c, str) or (isinstance(c, dict) and "@import" in c):
                        raise ImportProblem("put the JSON-LD @context in the document itself: Lens doesn't fetch contexts")
            _no_remote_context(v)


def parse(data: str, fmt: str | None = None) -> Graph:
    if len(data.encode()) > MAX_BYTES:
        raise ImportProblem(f"that's more than {MAX_BYTES // 1024 // 1024} MB of RDF: split it up")
    fmt = fmt or guess(data)
    if fmt == "ttl":
        fmt = "turtle"
    if fmt not in FORMATS:
        raise ImportProblem(f"RDF is read as {', '.join(FORMATS)}")
    if fmt == "json-ld":
        try:
            _no_remote_context(json.loads(data))
        except json.JSONDecodeError as e:
            raise ImportProblem(f"that isn't JSON: {e}") from None
    g = Graph()
    try:
        g.parse(data=data, format=fmt)
    except ImportProblem:
        raise
    except Exception as e:  # rdflib raises many kinds
        raise ImportProblem(f"that isn't readable {fmt}: {str(e)[:300]}") from None
    return g


def _term(p):
    """The DCMI term a property is (dcterms:x, or dc:x of the 15 elements): its name, else None."""
    s = str(p)
    for ns in (str(DCTERMS), str(DC)):
        if s.startswith(ns):
            return s[len(ns) :]
    return None


def _label(g, node):
    for p in (FOAF.name, SKOS.prefLabel, RDFS.label, DCTERMS.title):
        v = g.value(node, p)
        if v is not None:
            return str(v)
    return None


def _id_of(prefix, node):
    s = str(node)
    if s.startswith(prefix) and re.fullmatch(r"\d+", s[len(prefix) :]):
        return int(s[len(prefix) :])
    return None


def _langmap(lits):
    out: dict[str, list[str]] = {}
    for lit in lits:
        lang = lit.language if isinstance(lit, Literal) and lit.language else "none"
        if md.LANG_RX.match(lang):
            out.setdefault(lang, []).append(str(lit))
    return out or None


def _agent(g, node, base):
    if isinstance(node, Literal):
        return {"name": str(node)}
    sid = _id_of(f"{base}/id/speaker/", node)
    name = _label(g, node)
    if sid is not None:
        return {"name": name or f"Speaker {sid}", "speaker": sid}
    if isinstance(node, URIRef):
        return {"name": name or str(node), "uri": str(node) if str(node).startswith(("http://", "https://")) else None}
    return {"name": name} if name else None


def describe(g: Graph, s, base: str):
    """What a description says, as metadata fields: (fields, other statements, notes on what was left out)."""
    fields: dict = {}
    other, notes = [], []
    by: dict[str, list] = {}
    for p, o in g.predicate_objects(s):
        t = _term(p)
        if t is not None:
            by.setdefault(t, []).append(o)
        elif p == FOAF.homepage and isinstance(o, URIRef):
            fields["homepage"] = str(o)
        elif p == RDF.type or str(p).startswith(f"{base}/ns#") or p in (FOAF.page, RDFS.seeAlso):
            continue  # what export says of itself
        elif str(p).startswith(("http://", "https://")) and isinstance(o, URIRef):
            if str(o).startswith(("http://", "https://")):
                other.append({"p": str(p), "o": str(o), "uri": True})
        elif str(p).startswith(("http://", "https://")) and isinstance(o, Literal):
            other.append({"p": str(p), "o": str(o), "lang": o.language, "datatype": str(o.datatype) if o.datatype else None})
    if by.get("title"):
        fields["label"] = _langmap(by.pop("title"))
    desc = by.pop("description", [])
    if desc:
        fields["summary"] = _langmap(desc)
    for t, k in (("creator", "creators"), ("contributor", "contributors")):
        people = [a for a in (_agent(g, o, base) for o in by.pop(t, [])) if a]
        if people:
            fields[k] = people
    subs = []
    for o in by.pop("subject", []):
        eid = _id_of(f"{base}/id/entity/", o)
        if eid is not None:
            subs.append({"label": _label(g, o) or f"Entity {eid}", "entity": eid})
        elif isinstance(o, URIRef):
            subs.append({"label": _label(g, o) or str(o), "uri": str(o) if str(o).startswith(("http://", "https://")) else None})
        else:
            subs.append({"label": str(o)})
    if subs:
        fields["subjects"] = subs
    dates = by.pop("created", []) + by.pop("date", [])
    if dates:
        fields["navDate"] = str(dates[0])
    langs = [str(x) for x in by.pop("language", []) if md.LANG_RX.match(str(x)) and str(x) != "none"]
    if langs:
        fields["language"] = langs
    for o in by.pop("license", []) + [x for x in by.get("rights", []) if isinstance(x, URIRef)]:
        if md.RIGHTS_RX.match(str(o)):
            fields["rights"] = str(o)
            break
        notes.append(f"{o} isn't a Creative Commons licence or RightsStatements.org statement: kept as a statement")
        other.append({"p": str(DCTERMS.license), "o": str(o), "uri": True})
    attribution = [x for x in by.pop("rights", []) if isinstance(x, Literal)]
    if attribution:
        fields["attribution"] = _langmap(attribution)
    pub = by.pop("publisher", [])
    if pub:
        o = pub[0]
        name = str(o) if isinstance(o, Literal) else _label(g, o)
        if name:
            logo = g.value(o, FOAF.logo)
            fields["provider"] = {
                "name": name,
                "homepage": str(o) if isinstance(o, URIRef) and str(o).startswith("http") else None,
                "logo": str(logo) if isinstance(logo, URIRef) else None,
            }
    typed = {str(g.value(n, RDF.value)): str(g.value(n, LENS(base).identifierType)) for n in g.objects(s, LENS(base).identifier)}
    ids = [{"value": str(o), "type": typed.get(str(o))} for o in by.pop("identifier", []) if isinstance(o, Literal)]
    if ids:
        fields["identifiers"] = ids
    rel = [{"id": str(o), "label": _label(g, o)} for o in by.pop("relation", []) if isinstance(o, URIRef) and str(o).startswith("http")]
    if rel:
        fields["related"] = rel
    for t in ("isPartOf", "type", "format", "extent", "references"):
        by.pop(t, None)  # where it is, and what Lens knows from the file and the analysis
    terms = {}
    for t, vals in by.items():
        if t in md.DC_TERMS:
            terms[t] = [str(v) for v in vals]
        else:
            other += [{"p": str(DCTERMS[t]), "o": str(v), "uri": isinstance(v, URIRef) or None} for v in vals]
    if terms:
        fields["terms"] = terms
    if other:
        fields["statements"] = other
    return {k: v for k, v in fields.items() if v is not None}, notes


def _match(db, g, s, base, sid, by_identifier):
    rid = _id_of(f"{base}/id/recording/", s)
    if rid is not None:
        row = db.one("SELECT space FROM $r", r=R("recording", rid))
        return rid if row and row["space"] == sid else None
    for v in list(g.objects(s, DCTERMS.identifier)) + list(g.objects(s, DC.identifier)) + list(g.objects(s, URIRef(f"{base}/ns#lensId"))):
        if str(v) in by_identifier:
            return by_identifier[str(v)]
    return None


def _identifiers(db, cfg, sid):
    out = {}
    for r in db.rows("SELECT record::id(id) AS id FROM recording WHERE space = $s", s=sid):
        out[str(r["id"])] = r["id"]
        for i in md.stored(db, r["id"]).get("identifiers") or []:
            out.setdefault(str(i["value"]), r["id"])
    return out


def run(db, cfg, base, sid, data, fmt=None, dry_run=True, user=None):
    """Read RDF into namespace `sid`. Returns what each description changes (or would), and what matched nothing."""
    g = parse(data, fmt)
    base = base.rstrip("/")
    by_identifier = _identifiers(db, cfg, sid)
    subjects = [s for s in set(g.subjects()) if any(_term(p) for p in g.predicates(s))]
    if len(subjects) > MAX_SUBJECTS:
        raise ImportProblem(f"that describes more than {MAX_SUBJECTS} things: split it up")
    items, unmatched, seen = [], [], {}
    for s in sorted(subjects, key=str):
        rid = _match(db, g, s, base, sid, by_identifier)
        if rid is None:
            unmatched.append({"subject": str(s), "title": _label(g, s)})
            continue
        fields, notes = describe(g, s, base)
        problems = []
        try:
            fields = md.clean(fields)
        except md.MetaProblem:
            fields, problems = _clean_each(fields)
        fields = {k: v for k, v in fields.items() if v is not None}
        rec_space = db.one("SELECT space FROM $r", r=R("recording", rid))["space"]
        fields, dropped = _ours(db, rec_space, fields)
        problems += dropped
        if rid in seen:
            notes.append("this recording is described more than once here: their terms and statements add up, other fields are this one's")
            fields = _combine(seen[rid], fields)
        seen[rid] = fields
        before = md.effective(db, cfg, rid)
        for k in ("creators", "contributors"):
            if k in fields:
                fields[k] = _keep_roles(fields[k], before.get(k) or [])
        if fields.get("terms"):  # terms it doesn't mention, and statements it doesn't make, stay
            fields["terms"] = {**(before.get("terms") or {}), **fields["terms"]}
        if fields.get("statements"):
            fields["statements"] = _combine({"statements": before.get("statements")}, {"statements": fields["statements"]})["statements"]
        changes = {k: v for k, v in fields.items() if before.get(k) != v}
        if changes and not dry_run:
            md.save(db, cfg, rid, changes, user=user)
        items.append(
            {
                "subject": str(s),
                "recording": rid,
                "fields": sorted(changes),
                "notes": notes + problems,
                "statements": len(fields.get("statements") or []),
            }
        )
    return {
        "dry_run": dry_run,
        "triples": len(g),
        "matched": len(items),
        "changed": sum(1 for i in items if i["fields"]),
        "items": items,
        "unmatched": unmatched,
    }


def _clean_each(fields):
    """Field by field, leaving out the ones that don't fit (with why)."""
    out, problems = {}, []
    for k, v in fields.items():
        try:
            out.update(md.clean({k: v}))
        except md.MetaProblem as e:
            problems.append(f"{k} left out: {e}")
    return out, problems


def _ours(db, sid, fields):
    """Speakers and entities named by URI must be the namespace's own: others are left as names."""
    problems = []
    for k in ("creators", "contributors"):
        for p in fields.get(k) or []:
            if p.get("speaker") is not None:
                row = db.one("SELECT space FROM $r", r=R("speaker", int(p["speaker"])))
                if not row or row["space"] != sid:
                    problems.append(f"speaker {p['speaker']} isn't in this namespace: kept as a name")
                    p.pop("speaker")
    for x in fields.get("subjects") or []:
        if x.get("entity") is not None:
            row = db.one("SELECT space FROM $r", r=R("entity", int(x["entity"])))
            if not row or row["space"] != sid:
                problems.append(f"entity {x['entity']} isn't in this namespace: kept as a label")
                x.pop("entity")
    return fields, problems


def _keep_roles(people, before):
    """RDF doesn't say a speaker's role (rdf.py), so a person already there keeps theirs."""
    roles = {(p.get("speaker"), p.get("uri"), p["name"]): p.get("role") for p in before}
    out = []
    for p in people:
        role = p.get("role") or roles.get((p.get("speaker"), p.get("uri"), p["name"]))
        out.append(store.clean({**p, "role": role}))
    return out


def _combine(a, b):
    """Two descriptions of one recording: b's fields, with both's terms and statements."""
    out = {**a, **b}
    if a.get("terms") or b.get("terms"):
        terms = {t: list(v) for t, v in (a.get("terms") or {}).items()}
        for t, v in (b.get("terms") or {}).items():
            terms[t] = list(dict.fromkeys(terms.get(t, []) + v))
        out["terms"] = terms
    if a.get("statements") or b.get("statements"):
        out["statements"] = (a.get("statements") or []) + [x for x in b.get("statements") or [] if x not in (a.get("statements") or [])]
    return out
