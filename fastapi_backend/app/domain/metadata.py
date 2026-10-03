"""Descriptive metadata for recordings and namespaces.

Two layers, as IIIF intends: what viewers display (language maps for label and summary, label/value pairs, rights,
required attribution, provider, date), and a richer machine-readable record linked from each Manifest with seeAlso
(schema.org JSON-LD and Dublin Core). Values someone saves override values derived from the recording (title,
date, language, speakers, topics, summary); every change is kept and can be reverted.

Three fields are the recording's access (see access.py and docs/access.md) rather than description: access (public,
restricted or private), open (the parts of a public recording anyone may use) and featured. They are kept in the
recording's own fields, so lists can filter by them, and the namespace profile holds their defaults.

A recording's custom field values (fields.py) are kept in its own `fields` too, and are part of what the history keeps
(`fields` in a stored snapshot), so reverting puts them back as well. They are saved through fields.py's checks
(save_fields), never with the other fields, and the effective metadata leaves them out: what is published of them is
decided field by field.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import re
from xml.sax.saxutils import escape, quoteattr

from . import access as acc, render, store

R = store.R
ACCESS = acc.LEVELS
COLUMNS = {"access": "access", "open": "access_parts", "featured": "featured"}  # stored on the recording, not in meta_json
FIELDS = (
    "label",
    "summary",
    "metadata",
    "rights",
    "attribution",
    "provider",
    "navDate",
    "language",
    "creators",
    "contributors",
    "subjects",
    "identifiers",
    "homepage",
    "related",
    "terms",
    "statements",
    "access",
    "open",
    "featured",
)
LANG_RX = re.compile(r"^(none|[a-zA-Z]{2,3}(-[A-Za-z0-9]{2,8})*)$")
# The DCMI Metadata Terms a recording can be given directly (`terms`: {term: [text or http(s) address]}), beyond the
# ones its other fields already say (title, description, creator, contributor, subject, created, language, license,
# rights, publisher, identifier, relation, isPartOf, type, format, extent). See rdf.py.
DC_TERMS = (
    "abstract",
    "accessRights",
    "accrualMethod",
    "accrualPeriodicity",
    "accrualPolicy",
    "alternative",
    "audience",
    "available",
    "bibliographicCitation",
    "conformsTo",
    "coverage",
    "dateAccepted",
    "dateCopyrighted",
    "dateSubmitted",
    "educationLevel",
    "hasFormat",
    "hasPart",
    "hasVersion",
    "instructionalMethod",
    "isFormatOf",
    "isReferencedBy",
    "isReplacedBy",
    "isRequiredBy",
    "issued",
    "isVersionOf",
    "mediator",
    "medium",
    "modified",
    "provenance",
    "replaces",
    "requires",
    "rightsHolder",
    "source",
    "spatial",
    "tableOfContents",
    "temporal",
    "valid",
)
TERM_MAX, TERM_VALUES = 2000, 50
RIGHTS_RX = re.compile(r"^https?://(creativecommons\.org/(licenses|publicdomain)/|rightsstatements\.org/vocab/)")


class MetaProblem(ValueError):
    pass


def langmap(v, field):
    """Plain text or {"en": ["..."], "none": ["..."]} -> a IIIF language map."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if isinstance(v, str):
        return {"none": [v.strip()]}
    if not isinstance(v, dict):
        raise MetaProblem(f'{field} must be text or a language map like {{"en": ["..."]}}')
    out = {}
    for lang, vals in v.items():
        if not LANG_RX.match(str(lang)):
            raise MetaProblem(f"{field}: '{lang}' isn't a language code (use none when it's unknown)")
        vals = [vals] if isinstance(vals, str) else vals
        if not isinstance(vals, list) or not all(isinstance(x, str) for x in vals):
            raise MetaProblem(f"{field}: each language holds a list of text")
        vals = [x.strip() for x in vals if x.strip()]
        if vals:
            out[str(lang)] = vals
    return out or None


def first(lm):
    for vals in (lm or {}).values():
        if vals:
            return vals[0]
    return None


def _uri(v, field):
    if v in (None, ""):
        return None
    if not isinstance(v, str) or not re.match(r"^https?://[^\s<>\"]+$", v):
        raise MetaProblem(f"{field} must be an http(s) address")
    return v


def _terms(v):
    if not isinstance(v, dict):
        raise MetaProblem('terms is an object of Dublin Core terms and their values, like {"spatial": ["Berlin"]}')
    out = {}
    for term, vals in v.items():
        if term not in DC_TERMS:
            raise MetaProblem(f"'{term}' isn't one of the Dublin Core terms a recording can be given here")
        vals = [vals] if isinstance(vals, str) else vals
        if vals is None:
            continue
        if not isinstance(vals, list) or not all(isinstance(x, str) for x in vals):
            raise MetaProblem(f"{term}: each value is text or an http(s) address")
        vals = list(dict.fromkeys(x.strip()[:TERM_MAX] for x in vals if x.strip()))[:TERM_VALUES]
        if vals:
            out[term] = vals
    return out


STATEMENTS_MAX = 500


def _statements(v):
    """Other RDF statements about the recording, kept as they came (an RDF import: rdf.py says them again):
    [{p: property URI, o: value, uri: whether the value is a resource, lang, datatype}]."""
    if not isinstance(v, list):
        raise MetaProblem("statements is a list of {p, o} pairs")
    out = []
    for x in v[:STATEMENTS_MAX]:
        if not isinstance(x, dict) or not isinstance(x.get("o"), str):
            raise MetaProblem("every statement has a property (p) and a value (o)")
        p = _uri(x.get("p"), "a statement's property")
        if not p:
            raise MetaProblem("every statement has a property (p) and a value (o)")
        uri = bool(x.get("uri"))
        if uri:
            _uri(x["o"], "a statement's value")
        if x.get("lang") and not LANG_RX.match(str(x["lang"])):
            raise MetaProblem(f"'{x['lang']}' isn't a language code")
        st = store.clean(
            {
                "p": p,
                "o": x["o"][:TERM_MAX],
                "uri": uri or None,
                "lang": None if uri else x.get("lang") or None,
                "datatype": None if uri else _uri(x.get("datatype"), "a statement's datatype"),
            }
        )
        if st not in out:
            out.append(st)
    return out


def clean(patch):
    """Validate and normalise fields someone is saving. None clears a field (it won't fall back to a default)."""
    out = {}
    for k, v in (patch or {}).items():
        if k not in FIELDS:
            raise MetaProblem(f"unknown field {k}")
        if k == "open" and v == []:
            out[k] = []  # a public recording with every part closed: its page and description only
        elif v is None or v == "" or v == []:
            out[k] = None
        elif k in ("label", "summary", "attribution"):
            out[k] = langmap(v, k)
        elif k == "metadata":
            if not isinstance(v, list):
                raise MetaProblem("metadata is a list of {label, value} pairs")
            pairs = [
                {"label": langmap(x.get("label"), "metadata label"), "value": langmap(x.get("value"), "metadata value")}
                for x in v
                if isinstance(x, dict)
            ]
            if any(not p["label"] or not p["value"] for p in pairs):
                raise MetaProblem("every metadata pair needs a label and a value")
            out[k] = pairs
        elif k == "rights":
            u = _uri(v, "rights")
            if not RIGHTS_RX.match(u):
                raise MetaProblem("rights must be a Creative Commons licence or a RightsStatements.org statement URI")
            out[k] = u.replace("https://", "http://", 1)  # the canonical form IIIF expects
        elif k == "provider":
            if not isinstance(v, dict) or not str(v.get("name") or "").strip():
                raise MetaProblem("the provider needs a name")
            out[k] = store.clean(
                {
                    "name": str(v["name"]).strip()[:200],
                    "homepage": _uri(v.get("homepage"), "provider homepage"),
                    "logo": _uri(v.get("logo"), "provider logo"),
                }
            )
        elif k == "navDate":
            try:
                d = dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
            except ValueError:
                raise MetaProblem("the date must look like 2026-09-30 or 2026-09-30T14:00:00Z") from None
            out[k] = (d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        elif k == "language":
            langs = [v] if isinstance(v, str) else v
            if not isinstance(langs, list) or not all(isinstance(x, str) and LANG_RX.match(x) and x != "none" for x in langs):
                raise MetaProblem("language is a list of codes such as en, de or zh")
            out[k] = list(dict.fromkeys(langs))
        elif k in ("creators", "contributors"):
            people = []
            for p in v if isinstance(v, list) else [v]:
                p = {"name": p} if isinstance(p, str) else dict(p)
                if not str(p.get("name") or "").strip():
                    raise MetaProblem(f"every {k[:-1]} needs a name")
                people.append(
                    store.clean(
                        {
                            "name": p["name"].strip()[:200],
                            "role": p.get("role") or None,
                            "uri": _uri(p.get("uri"), f"{k[:-1]} link"),
                            "speaker": p.get("speaker"),
                        }
                    )
                )
            out[k] = people
        elif k == "subjects":
            subs = []
            for x in v if isinstance(v, list) else [v]:
                x = {"label": x} if isinstance(x, str) else dict(x)
                if not str(x.get("label") or "").strip():
                    raise MetaProblem("every subject needs a label")
                subs.append(
                    store.clean({"label": x["label"].strip()[:200], "uri": _uri(x.get("uri"), "subject link"), "entity": x.get("entity")})
                )
            out[k] = subs
        elif k == "identifiers":
            ids = []
            for x in v if isinstance(v, list) else [v]:
                x = {"value": x} if isinstance(x, str) else dict(x)
                if not str(x.get("value") or "").strip():
                    raise MetaProblem("identifiers need a value")
                ids.append(store.clean({"type": x.get("type") or None, "value": str(x["value"]).strip()[:200]}))
            out[k] = ids
        elif k == "homepage":
            out[k] = _uri(v, "homepage")
        elif k == "related":
            out[k] = [
                store.clean(
                    {
                        "id": _uri(x.get("id") if isinstance(x, dict) else x, "related link"),
                        "label": x.get("label") if isinstance(x, dict) else None,
                    }
                )
                for x in (v if isinstance(v, list) else [v])
            ]
        elif k == "terms":
            out[k] = _terms(v) or None
        elif k == "statements":
            out[k] = _statements(v) or None
        elif k == "open":
            try:
                out[k] = acc.parts(v)
            except ValueError as e:
                raise MetaProblem(str(e)) from None
        elif k == "featured":
            if not isinstance(v, bool):
                raise MetaProblem("featured is true or false")
            out[k] = v
        else:  # access
            if v not in ACCESS:
                raise MetaProblem(f"access is one of {', '.join(ACCESS)}")
            out[k] = v
    return out


def _load(v):
    return json.loads(v) if isinstance(v, str) and v else {}


# ---------- namespaces: collection metadata and a profile for their recordings ----------
def namespace(db, sid):
    row = db.one("SELECT name, meta_json, profile_json FROM $r", r=R("space", sid)) or {}
    return {"name": row.get("name"), "meta": _load(row.get("meta_json")), "profile": _load(row.get("profile_json"))}


def check_profile(p):
    p = dict(p or {})
    out = {
        "required": [f for f in p.get("required") or [] if f in FIELDS],
        "defaults": clean(p.get("defaults") or {}),
        "vocabularies": {
            k: [str(x) for x in v]
            for k, v in (p.get("vocabularies") or {}).items()
            if k in ("subjects", "language") and isinstance(v, list)
        },
        "order": [f for f in p.get("order") or [] if f in FIELDS],
        "default_access": p.get("default_access") or "private",
    }
    if out["default_access"] not in ACCESS:
        raise MetaProblem(f"default access is one of {', '.join(ACCESS)}")
    if p.get("default_open") is not None:
        try:
            out["default_open"] = acc.parts(p["default_open"])
        except ValueError as e:
            raise MetaProblem(f"default {e}") from None
    return out


def save_namespace(db, sid, meta=None, profile=None, user=None):
    cur = namespace(db, sid)
    after = {
        "meta": {**cur["meta"], **clean(meta)} if meta is not None else cur["meta"],
        "profile": check_profile(profile) if profile is not None else cur["profile"],
    }
    inheriting = db.rows(
        "SELECT record::id(id) AS id, space, access, access_parts, featured FROM recording WHERE space = $s AND access = NONE", s=sid
    )
    before = acc.many(db, inheriting)
    db.q("UPDATE $r SET meta_json = $m, profile_json = $p", r=R("space", sid), m=json.dumps(after["meta"]), p=json.dumps(after["profile"]))
    _history(db, f"space:{sid}", {"meta": cur["meta"], "profile": cur["profile"]}, after, user)
    # recordings that follow the namespace's default access are published, withdrawn or changed with it
    for rid, a in acc.many(db, inheriting).items():
        if a["access"] != before[rid]["access"] or (acc.published(a) and a["open"] != before[rid]["open"]):
            acc.announce(db, rid, before[rid]["access"], a["access"])
    return after


# ---------- recordings ----------
def _split(row):
    """The metadata someone saved on a recording: meta_json, plus the access fields kept in their own columns."""
    meta = _load(row.get("meta_json"))
    level = meta.pop("access", None)  # meta_json held it before access had its own fields (converted on first start)
    if level is not None and row.get("access") is None:
        if level not in ACCESS:  # transcript or signed-in, from the IIIF-only levels
            level, open_ = acc.LEGACY.get(level) or ("private", None)
            if open_ is not None and "open" not in meta:
                meta["open"] = open_
        meta["access"] = level
    for field, col in COLUMNS.items():
        if row.get(col) is not None:
            meta[field] = row[col]
    if row.get("fields"):
        meta["fields"] = row["fields"]
    return meta


def stored(db, rid):
    """What has been saved on a recording, its custom field values (`fields`) included."""
    return _split(db.one("SELECT meta_json, access, access_parts, featured, fields FROM $r", r=R("recording", rid)) or {})


def _write(db, rid, meta):
    """Save a recording's metadata: the access fields to their columns (NONE: follow the namespace), its custom field
    values to `fields`, the rest as JSON."""
    rest = {k: v for k, v in meta.items() if k not in COLUMNS and k != "fields"}
    if "access" in meta and meta["access"] not in ACCESS:  # an old level from a history entry
        meta = {**meta, **dict(zip(("access", "open"), acc.LEGACY.get(meta["access"]) or ("private", None)))}
    sets, params = ["meta_json = $m", "fields = $f" if meta.get("fields") else "fields = NONE"], {"m": json.dumps(rest)}
    if meta.get("fields"):
        params["f"] = meta["fields"]
    for field, col in COLUMNS.items():
        if meta.get(field) is None:
            sets.append(f"{col} = NONE")
        else:
            sets.append(f"{col} = $v_{col}")  # $access itself is a protected parameter name
            params[f"v_{col}"] = meta[field]
    db.q(f"UPDATE $r SET {', '.join(sets)}", r=R("recording", rid), **params)


def defaults(db, cfg, rid):
    """What a recording says about itself before anyone edits its metadata."""
    rec = db.one("SELECT title, recorded_at, language, summary, space FROM $r", r=R("recording", rid)) or {}
    ns = namespace(db, rec.get("space"))
    lang = cfg["iiif"].get("default_language") or "none"
    level, open_ = acc.namespace_defaults(db, [rec["space"]]).get(rec.get("space"), ("private", list(acc.PARTS)))
    d = {"label": {lang: [rec.get("title") or f"Recording {rid}"]}, "access": level, "open": open_, "featured": False}
    if rec.get("recorded_at"):
        try:
            d["navDate"] = clean({"navDate": rec["recorded_at"]})["navDate"]
        except MetaProblem:
            pass
    if rec.get("language") and LANG_RX.match(rec["language"]) and rec["language"] not in ("none", "nospeech"):
        d["language"] = [rec["language"]]
    apps = db.rows("SELECT speaker FROM appearance WHERE recording = $r", r=rid)
    names = render.speaker_names(db, [a["speaker"] for a in apps])
    if names:
        d["contributors"] = [{"name": n, "role": "speaker", "speaker": sid} for sid, n in names.items()]
    counts = {}
    for m in db.rows("SELECT entity FROM mentions WHERE recording = $r", r=rid):
        counts[m["entity"]] = counts.get(m["entity"], 0) + 1
    if counts:
        ents = {
            e["id"]: e
            for e in db.rows(
                "SELECT record::id(id) AS id, name, type FROM entity WHERE id IN $ids AND hidden != true",
                ids=[R("entity", i) for i in counts],
            )
        }
        top = [ents[i] for i in sorted(counts, key=lambda i: -counts[i]) if i in ents and ents[i]["type"] not in ("NUMBER", "DATE")][:10]
        d["subjects"] = [{"label": e["name"], "entity": e["id"]} for e in top]
    notes = (db.one("SELECT * FROM $o", o=R("output", f"{rid}-meeting_notes")) or {}).get(
        "value"
    ) or {}  # "SELECT value" parses as SELECT VALUE
    summary = (rec.get("summary") or {}).get("tldr") or (rec.get("summary") or {}).get("summary") or notes.get("tldr")
    if summary:
        d["summary"] = {lang: [summary]}
    for k in ("rights", "attribution", "provider"):
        if cfg["iiif"].get(k):
            try:
                d.update(clean({k: cfg["iiif"][k]}))
            except MetaProblem:
                pass
    d.update({k: v for k, v in (ns["profile"].get("defaults") or {}).items() if v is not None})
    return d


def effective(db, cfg, rid):
    """The recording's description: what was saved over what it says about itself (custom fields aside)."""
    merged = {**defaults(db, cfg, rid), **stored(db, rid)}
    return {k: v for k, v in merged.items() if v is not None and k != "fields"}


def problems(meta, profile):
    out = [{"field": f, "message": "required by this namespace"} for f in (profile or {}).get("required", []) if not meta.get(f)]
    for f, vocab in ((profile or {}).get("vocabularies") or {}).items():
        values = [x["label"] for x in meta.get(f) or []] if f == "subjects" else list(meta.get(f) or [])
        for x in values:
            if x not in vocab:
                out.append({"field": f, "message": f"'{x}' isn't in this namespace's list"})
    return out


def access_of(db, cfg, rid):
    return acc.of(db, rid)["access"]


def get(db, cfg, rid):
    rec = db.one("SELECT space FROM $r", r=R("recording", rid)) or {}
    meta = effective(db, cfg, rid)
    return {
        "meta": meta,
        "stored": {k: v for k, v in stored(db, rid).items() if k != "fields"},
        "defaults": defaults(db, cfg, rid),
        "problems": problems(meta, namespace(db, rec.get("space"))["profile"]),
    }


def _history(db, target, before, after, user):
    db.q(
        "CREATE $r CONTENT $d",
        r=R("meta_edit", db.next_id("meta_edit")),
        d=store.clean({"target": target, "before": json.dumps(before), "after": json.dumps(after), "by": user, "at": store.now()}),
    )


def save(db, cfg, rid, patch=None, reset=(), user=None):
    """Save fields (None clears one); reset puts fields back to their derived values. Returns (old access, new access)."""
    changes = clean(patch)
    before = stored(db, rid)
    old = acc.of(db, rid)
    after = {k: v for k, v in {**before, **changes}.items() if k not in set(reset)}
    _write(db, rid, after)
    _history(db, f"recording:{rid}", before, after, user)
    new = acc.of(db, rid)
    note_change(db, rid, old["access"], new["access"])
    return old["access"], new["access"]


def save_fields(db, cfg, rid, fields, user=None):
    """Keep a recording's custom field values (a `fields` object checked by fields.py), in its history like any other
    change. A published recording's manifest changes, so harvesters hear an Update."""
    before = stored(db, rid)
    after = {k: v for k, v in before.items() if k != "fields"}
    if fields:
        after["fields"] = fields
    _write(db, rid, after)
    _history(db, f"recording:{rid}", before, after, user)
    touched(db, cfg, rid)


def history(db, target):
    rows = db.rows(
        "SELECT record::id(id) AS id, before, after, by, at FROM meta_edit WHERE target = $t ORDER BY id DESC LIMIT 100", t=target
    )
    for r in rows:
        b, a = json.loads(r.pop("before")), json.loads(r.pop("after"))
        r["changed"] = sorted(k for k in set(b) | set(a) if b.get(k) != a.get(k))
        r["before"], r["after"] = b, a
    return rows


def revert(db, cfg, eid, user=None):
    e = db.one("SELECT target, before FROM $r", r=R("meta_edit", eid))
    if not e:
        raise KeyError(eid)
    kind, key = e["target"].split(":")
    before = json.loads(e["before"])
    if kind == "recording":
        rid = int(key)
        old = access_of(db, cfg, rid)
        cur = stored(db, rid)
        _write(db, rid, before)
        _history(db, e["target"], cur, before, user)
        note_change(db, rid, old, access_of(db, cfg, rid))
    else:
        save_namespace(db, int(key), before.get("meta") or {}, before.get("profile") or {}, user)
    return e["target"]


def bulk(db, cfg, rids, set_fields=None, clear=(), user=None, dry_run=True):
    changes = clean(set_fields)
    changes.update({k: None for k in clear if k in FIELDS})
    affected = [rid for rid in rids if any(stored(db, rid).get(k, "∅") != v for k, v in changes.items())]
    if not dry_run:
        for rid in affected:
            save(db, cfg, rid, changes, user=user)
    return {"recordings": len(rids), "would_change" if dry_run else "changed": len(affected), "fields": sorted(changes)}


# ---------- change discovery (what harvesters see) ----------
def note_change(db, rid, old_access, new_access):
    """IIIF publishes public recordings: becoming public is a Create, staying public an Update, leaving it a Delete."""
    acc.announce(db, rid, old_access, new_access)


def touched(db, cfg, rid):
    """The transcript or analysis of a published recording changed."""
    level = access_of(db, cfg, rid)
    note_change(db, rid, level, level)


# ---------- the machine-readable records linked with seeAlso ----------
def _iso_duration(ms):
    s = int((ms or 0) // 1000)
    return f"PT{s // 3600}H{s % 3600 // 60}M{s % 60}S"


# what schema.org and Dublin Core call each kind of resource (audio, and a transcript without it, are sound)
SCHEMA_TYPES = {"video": "VideoObject", "document": "DigitalDocument", "image": "ImageObject"}
DC_TYPES = {"video": "MovingImage", "document": "Text", "image": "StillImage"}


def schema_org(meta, rec, urls):
    people = lambda xs: [
        store.clean({"@type": "Person", "name": p["name"], "sameAs": p.get("uri"), "roleName": p.get("role")}) for p in xs or []
    ]  # noqa: E731
    return store.clean(
        {
            "@context": "https://schema.org",
            "@type": SCHEMA_TYPES.get(rec.get("kind") or "", "AudioObject"),
            "@id": urls["manifest"] + "#record",
            "name": first(meta.get("label")),
            "description": first(meta.get("summary")),
            "dateCreated": meta.get("navDate"),
            "duration": _iso_duration(rec.get("duration_ms")),
            "inLanguage": meta.get("language"),
            "creator": people(meta.get("creators")) or None,
            "contributor": people(meta.get("contributors")) or None,
            "about": [store.clean({"@type": "Thing", "name": s["label"], "sameAs": s.get("uri")}) for s in meta.get("subjects") or []]
            or None,
            "license": meta.get("rights"),
            "creditText": first(meta.get("attribution")),
            "provider": store.clean(
                {
                    "@type": "Organization",
                    "name": meta["provider"]["name"],
                    "url": meta["provider"].get("homepage"),
                    "logo": meta["provider"].get("logo"),
                }
            )
            if meta.get("provider")
            else None,
            "identifier": [
                store.clean({"@type": "PropertyValue", "propertyID": i.get("type"), "value": i["value"]})
                for i in meta.get("identifiers") or []
            ]
            or None,
            "additionalProperty": [
                {"@type": "PropertyValue", "name": first(p["label"]), "value": first(p["value"])} for p in meta.get("metadata") or []
            ]
            or None,
            "isPartOf": {"@type": "Collection", "@id": urls["collection"]},
            "url": meta.get("homepage") or urls.get("page"),
            "subjectOf": {"@type": "CreativeWork", "@id": urls["manifest"], "encodingFormat": "application/ld+json"},
        }
    )


# the one of the 15 Dublin Core elements each of those terms refines, for oai_dc (which has only those)
DC_ELEMENT = {
    **{t: "date" for t in ("available", "dateAccepted", "dateCopyrighted", "dateSubmitted", "issued", "modified", "valid")},
    **{t: "relation" for t in ("conformsTo", "hasFormat", "hasPart", "hasVersion", "isFormatOf", "isReferencedBy", "isReplacedBy")},
    **{t: "relation" for t in ("isRequiredBy", "isVersionOf", "replaces", "requires")},
    **{t: "coverage" for t in ("coverage", "spatial", "temporal")},
    **{t: "description" for t in ("abstract", "tableOfContents")},
    **{t: "rights" for t in ("accessRights", "rightsHolder")},
    "alternative": "title",
    "source": "source",
    "medium": "format",
    "bibliographicCitation": "identifier",
}


def dublin_core(meta, rec, urls):
    def el(tag, text, lang=None):
        attr = f" xml:lang={quoteattr(lang)}" if lang and lang != "none" else ""
        return f"  <dc:{tag}{attr}>{escape(str(text))}</dc:{tag}>" if text else ""

    lines = [el("title", v, lang) for lang, vals in (meta.get("label") or {}).items() for v in vals]
    lines += [el("description", v, lang) for lang, vals in (meta.get("summary") or {}).items() for v in vals]
    lines += [el("creator", p["name"]) for p in meta.get("creators") or []] + [
        el("contributor", p["name"]) for p in meta.get("contributors") or []
    ]
    lines += [el("subject", s["label"]) for s in meta.get("subjects") or []]
    lines += [
        el("date", (meta.get("navDate") or "")[:10]),
        el("type", DC_TYPES.get(rec.get("kind") or "", "Sound")),
        el("format", rec.get("format") or "audio"),
        el("identifier", urls["manifest"]),
    ] + [el("identifier", i["value"]) for i in meta.get("identifiers") or []]
    lines += [el("language", x) for x in meta.get("language") or []] + [
        el("rights", meta.get("rights")),
        el("rights", first(meta.get("attribution"))),
    ]
    lines += [el("publisher", (meta.get("provider") or {}).get("name")), el("relation", urls["collection"])]
    lines += [el(DC_ELEMENT[t], x) for t, vals in (meta.get("terms") or {}).items() if t in DC_ELEMENT for x in vals]
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<oai_dc:dc xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xsi:schemaLocation="http://www.openarchives.org/OAI/2.0/oai_dc/ http://www.openarchives.org/OAI/2.0/oai_dc.xsd">\n'
        + "\n".join(x for x in lines if x)
        + "\n</oai_dc:dc>\n"
    )


def copy_of(meta):
    return copy.deepcopy(meta)
