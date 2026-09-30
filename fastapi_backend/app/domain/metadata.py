"""Descriptive metadata for recordings and namespaces.

Two layers, as IIIF intends: what viewers display (language maps for label and summary, label/value pairs, rights,
required attribution, provider, date), and a richer machine-readable record linked from each Manifest with seeAlso
(schema.org JSON-LD and Dublin Core). Values someone saves override values derived from the recording (title,
date, language, speakers, topics, summary); every change is kept and can be reverted. Access decides what IIIF
publishes: public (everything), transcript (transcript open, audio after sign-in), signed-in (both after sign-in),
private (not published).
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import re
from xml.sax.saxutils import escape, quoteattr

from . import render, store

R = store.R
ACCESS = ("public", "transcript", "signed-in", "private")
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
    "access",
)
LANG_RX = re.compile(r"^(none|[a-zA-Z]{2,3}(-[A-Za-z0-9]{2,8})*)$")
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


def clean(patch):
    """Validate and normalise fields someone is saving. None clears a field (it won't fall back to a default)."""
    out = {}
    for k, v in (patch or {}).items():
        if k not in FIELDS:
            raise MetaProblem(f"unknown field {k}")
        if v is None or v == "" or v == []:
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
    return out


def save_namespace(db, sid, meta=None, profile=None, user=None):
    cur = namespace(db, sid)
    after = {
        "meta": {**cur["meta"], **clean(meta)} if meta is not None else cur["meta"],
        "profile": check_profile(profile) if profile is not None else cur["profile"],
    }
    db.q("UPDATE $r SET meta_json = $m, profile_json = $p", r=R("space", sid), m=json.dumps(after["meta"]), p=json.dumps(after["profile"]))
    _history(db, f"space:{sid}", {"meta": cur["meta"], "profile": cur["profile"]}, after, user)
    return after


# ---------- recordings ----------
def stored(db, rid):
    return _load((db.one("SELECT meta_json FROM $r", r=R("recording", rid)) or {}).get("meta_json"))


def defaults(db, cfg, rid):
    """What a recording says about itself before anyone edits its metadata."""
    rec = db.one("SELECT title, recorded_at, language, summary, space FROM $r", r=R("recording", rid)) or {}
    ns = namespace(db, rec.get("space"))
    lang = cfg["iiif"].get("default_language") or "none"
    d = {"label": {lang: [rec.get("title") or f"Recording {rid}"]}, "access": ns["profile"].get("default_access") or "private"}
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
    merged = {**defaults(db, cfg, rid), **stored(db, rid)}
    return {k: v for k, v in merged.items() if v is not None}


def problems(meta, profile):
    out = [{"field": f, "message": "required by this namespace"} for f in (profile or {}).get("required", []) if not meta.get(f)]
    for f, vocab in ((profile or {}).get("vocabularies") or {}).items():
        values = [x["label"] for x in meta.get(f) or []] if f == "subjects" else list(meta.get(f) or [])
        for x in values:
            if x not in vocab:
                out.append({"field": f, "message": f"'{x}' isn't in this namespace's list"})
    return out


def access_of(db, cfg, rid):
    return effective(db, cfg, rid).get("access") or "private"


def get(db, cfg, rid):
    rec = db.one("SELECT space FROM $r", r=R("recording", rid)) or {}
    meta = effective(db, cfg, rid)
    return {
        "meta": meta,
        "stored": stored(db, rid),
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
    old_access = access_of(db, cfg, rid)
    after = {k: v for k, v in {**before, **changes}.items() if k not in set(reset)}
    db.q("UPDATE $r SET meta_json = $m", r=R("recording", rid), m=json.dumps(after))
    _history(db, f"recording:{rid}", before, after, user)
    new_access = access_of(db, cfg, rid)
    note_change(db, rid, old_access, new_access)
    return old_access, new_access


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
        db.q("UPDATE $r SET meta_json = $m", r=R("recording", rid), m=json.dumps(before))
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
    if old_access == "private" and new_access != "private":
        kind = "Create"
    elif old_access != "private" and new_access == "private":
        kind = "Delete"
    elif new_access != "private":
        kind = "Update"
    else:
        return
    db.q("CREATE $r CONTENT $d", r=R("iiif_activity", db.next_id("iiif_activity")), d={"type": kind, "recording": rid, "at": store.now()})


def touched(db, cfg, rid):
    """The transcript or analysis of a published recording changed."""
    level = access_of(db, cfg, rid)
    note_change(db, rid, level, level)


# ---------- the machine-readable records linked with seeAlso ----------
def _iso_duration(ms):
    s = int((ms or 0) // 1000)
    return f"PT{s // 3600}H{s % 3600 // 60}M{s % 60}S"


def schema_org(meta, rec, urls):
    people = lambda xs: [
        store.clean({"@type": "Person", "name": p["name"], "sameAs": p.get("uri"), "roleName": p.get("role")}) for p in xs or []
    ]  # noqa: E731
    return store.clean(
        {
            "@context": "https://schema.org",
            "@type": "AudioObject",
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
        el("type", "Sound"),
        el("format", rec.get("format") or "audio"),
        el("identifier", urls["manifest"]),
    ] + [el("identifier", i["value"]) for i in meta.get("identifiers") or []]
    lines += [el("language", x) for x in meta.get("language") or []] + [
        el("rights", meta.get("rights")),
        el("rights", first(meta.get("attribution"))),
    ]
    lines += [el("publisher", (meta.get("provider") or {}).get("name")), el("relation", urls["collection"])]
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<oai_dc:dc xmlns:oai_dc="http://www.openarchives.org/OAI/2.0/oai_dc/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xsi:schemaLocation="http://www.openarchives.org/OAI/2.0/oai_dc/ http://www.openarchives.org/OAI/2.0/oai_dc.xsd">\n'
        + "\n".join(x for x in lines if x)
        + "\n</oai_dc:dc>\n"
    )


def copy_of(meta):
    return copy.deepcopy(meta)
