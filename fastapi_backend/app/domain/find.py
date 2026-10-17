"""Find resources by any attribute: who is seen in them (faces) and heard (voices), the entities they mention, the
topics they're about, their kind (audio, video, transcript, document, image) and content type (podcast, scan, email …),
when they were made, and the rest of what's known about them (tags, language, kinds of object seen, emotions heard,
length, status, collection, where they came from), alone or together with words said or written in them.

One filter for every way in: the API (POST /find), the MCP server (find), the assistant (find_resources) and the tool
graphs that call it. People and things can be named instead of numbered (faces=["Adeel"], topics=["Health"]): a name
matches the faces, speakers, entities (or the other ways an entity is said) and topics (any of their labels) called
that in the namespaces the asker reads, ignoring case, and `resolved` says which ones it matched. A topic brings its
narrower topics with it. A name that matches nothing is an error that says the closest names there are.

Every resource found says why it matched (`why`), with moments to cite: where the words are said or written, when a
face is on screen (or the pages it's on), a speaker's first lines, the lines an entity is mentioned in, and the topics,
content type, kinds of object and emotions that matched. Filters combine with AND, the values of one filter with OR.
Nothing here calls a model, except `text` searching by meaning when that's set up (one embedding, in the activity
ledger), as search does.
"""

from __future__ import annotations

import datetime as dt
import difflib
import html
import re
from collections import defaultdict

from . import analyze, content_types as ctypes, hierarchy, library, search as searchmod, store, topics as topicmod

R = store.R
NAMED = ("faces", "speakers", "entities", "topics")  # ids, or names resolved in the namespaces someone reads
WORDS = ("types", "content_types", "tags", "languages", "objects", "emotions", "status", "origins")
KEYS = ("text", "title", *NAMED, *WORDS, "date_from", "date_to", "min_duration", "max_duration", "collection", "sensitive")
SORTS = ("relevance", "-date", "date", "-title", "title", "-duration", "duration")
MAX_VALUES = 20  # values in one filter
MOMENTS = 3  # moments shown per reason
TEXT_HITS = 1000  # moments a text search looks through for the resources it finds
SPAN_RX = re.compile(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?$")


def _key(s):
    return " ".join(str(s or "").split()).casefold()


def _list(spec, k):
    v = spec.get(k)
    if v in (None, "", []):
        return []
    vals = v if isinstance(v, (list, tuple)) else [v]
    out = []
    for x in vals:
        if isinstance(x, bool) or not isinstance(x, (str, int)):
            raise ValueError(f"{k} is a list of names or ids")
        if isinstance(x, str) and not x.strip():
            continue
        out.append(x.strip() if isinstance(x, str) else x)
    if len(out) > MAX_VALUES:
        raise ValueError(f"{k} has at most {MAX_VALUES} values")
    return list(dict.fromkeys(out))


def day(value, end=False, name="date"):
    """A day from 2026-09-30, a month (2026-09) or a year (2026): its first day, or with `end` its last."""
    if value in (None, ""):
        return None
    if isinstance(value, dt.date):
        return value
    m = SPAN_RX.match(str(value).strip())
    if not m:
        raise ValueError(f"{name} is a day (2026-09-30), a month (2026-09) or a year (2026)")
    y, mo, d = int(m.group(1)), m.group(2), m.group(3)
    try:
        if d:
            return dt.date(y, int(mo), int(d))
        if mo:
            first = dt.date(y, int(mo), 1)
            return (first.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1) if end else first
        return dt.date(y, 12, 31) if end else dt.date(y, 1, 1)
    except ValueError:
        raise ValueError(f"{name} {value} isn't a date") from None


def _closest(want, names):
    near = difflib.get_close_matches(want, sorted(names), n=5, cutoff=0.5)
    near += [n for n in sorted(names) if want in n and n not in near][: 5 - len(near)]
    return f"; the closest: {', '.join(near)}" if near else ""


def _pick(what, values, rows, keys_of, norm=_key):
    """The rows these values name: an id, or a name (`norm`ed) that one of `keys_of(row)` is."""
    by_id = {r["id"]: r for r in rows}
    picked = {}
    for v in values:
        if isinstance(v, int) or (isinstance(v, str) and v.isdigit()):
            r = by_id.get(int(v))
            if not r:
                raise ValueError(f"{what} {v} isn't in a namespace you can read")
            picked[r["id"]] = r
            continue
        want = norm(v)
        hits = [r for r in rows if want in keys_of(r)]
        if not hits:
            known = {k for r in rows for k in keys_of(r)}
            raise ValueError(f'no {what} called "{v}" in the namespaces you can read{_closest(want, known)}')
        picked.update({r["id"]: r for r in hits})
    return list(picked.values())


def _faces(db, spaces, values):
    rows = db.rows("SELECT record::id(id) AS id, name, label, space FROM face WHERE space IN $s", s=spaces)
    return _pick("face", values, rows, lambda r: {_key(r.get("name")), _key(r.get("label"))} - {""})


def _speakers(db, spaces, values):
    rows = db.rows("SELECT record::id(id) AS id, name, label, space FROM speaker WHERE space IN $s", s=spaces)
    return _pick("speaker", values, rows, lambda r: {_key(r.get("name")), _key(r.get("label"))} - {""})


def _entities(db, spaces, values):
    names = [v for v in values if not (isinstance(v, int) or str(v).isdigit())]
    ids = [int(v) for v in values if isinstance(v, int) or str(v).isdigit()]
    keys = sorted({analyze.ent_key(v) for v in names})
    aliased = db.rows("SELECT key, entity FROM entity_alias WHERE key IN $k", k=keys) if keys else []
    rows = db.rows(
        "SELECT record::id(id) AS id, name, key, space FROM entity WHERE space IN $s AND (key IN $k OR id IN $ids)",
        s=spaces,
        k=keys,
        ids=[R("entity", i) for i in ids + [a["entity"] for a in aliased]],
    )
    said = defaultdict(set)
    for a in aliased:
        said[a["entity"]].add(a["key"])
    out, missing = {}, []
    for v in values:
        if isinstance(v, int) or str(v).isdigit():
            hit = [r for r in rows if r["id"] == int(v)]
            if not hit:
                raise ValueError(f"entity {v} isn't in a namespace you can read")
        else:
            k = analyze.ent_key(v)
            hit = [r for r in rows if r["key"] == k or k in said[r["id"]]]
            if not hit:
                missing.append(v)
        out.update({r["id"]: r for r in hit})
    if missing:
        want = analyze.ent_key(missing[0])
        near = db.values(
            "SELECT VALUE name FROM entity WHERE space IN $s AND string::contains(key, $w) LIMIT 5", s=spaces, w=want.split(" ")[0]
        )
        hint = f"; the closest: {', '.join(dict.fromkeys(near))}" if near else ""
        raise ValueError(f'no entity called "{missing[0]}" in the namespaces you can read{hint}')
    return list(out.values())


def _topics(db, spaces, values):
    rows = db.rows("SELECT record::id(id) AS id, label, alt, broader, space FROM topic WHERE space IN $s", s=spaces)
    named = _pick("topic", values, rows, lambda r: {topicmod.key_of(x) for x in [r["label"], *(r.get("alt") or [])]}, topicmod.key_of)
    narrower = defaultdict(list)
    for r in rows:
        for b in r.get("broader") or []:
            narrower[b].append(r["id"])
    ids, todo = {r["id"] for r in named}, [r["id"] for r in named]
    while todo:
        for c in narrower[todo.pop()]:
            if c not in ids:
                ids.add(c)
                todo.append(c)
    return named, sorted(ids)


def _content_types(db, values):
    types = ctypes.all_types(db)
    out = []
    for v in values:
        want = _key(v)
        t = next((t for t in types if t["key"] == want or _key(t["label"]) == want), None)
        if not t:
            raise ValueError(f'no content type "{v}"; there are {", ".join(t["key"] for t in types)}')
        out.append(t)
    return out


def clean(spec):
    """The filter as given, checked: ValueError naming the first problem."""
    spec = {k: v for k, v in dict(spec or {}).items() if v not in (None, "", [])}
    extra = sorted(set(spec) - set(KEYS))
    if extra:
        raise ValueError(f"find by {', '.join(KEYS)}; not {extra[0]}")
    out = {k: _list(spec, k) for k in NAMED + WORDS if k in spec}
    for k in ("text", "title"):
        if k in spec:
            if not isinstance(spec[k], str):
                raise ValueError(f"{k} is words")
            out[k] = " ".join(spec[k].split())[:300]
    for k in ("date_from", "date_to"):
        if k in spec:
            out[k] = day(spec[k], end=k == "date_to", name=k)
    for k in ("min_duration", "max_duration"):
        if k in spec:
            if isinstance(spec[k], bool) or not isinstance(spec[k], (int, float)) or spec[k] < 0:
                raise ValueError(f"{k} is a number of seconds")
            out[k] = spec[k]
    if "collection" in spec:
        if isinstance(spec["collection"], bool) or not str(spec["collection"]).isdigit():
            raise ValueError("collection is a collection's id")
        out["collection"] = int(spec["collection"])
    if "sensitive" in spec:
        if spec["sensitive"] not in ("include", "only", "exclude"):
            raise ValueError("sensitive is include, only or exclude")
        out["sensitive"] = spec["sensitive"]
    return out


def resolve(db, spaces, within, f):
    """(the library filters for this find, what its names resolved to)."""
    seen = sorted(set(spaces) | set(within or {}))
    names = store.space_names(db)
    shown = {}
    filters = {"q": f.get("title")}
    if f.get("faces"):
        rows = _faces(db, seen, f["faces"])
        filters["faces"] = [r["id"] for r in rows]
        shown["faces"] = [{"id": r["id"], "name": r.get("name") or r["label"], "namespace": names.get(r["space"])} for r in rows]
    if f.get("speakers"):
        rows = _speakers(db, seen, f["speakers"])
        filters["speakers"] = [r["id"] for r in rows]
        shown["speakers"] = [{"id": r["id"], "name": r.get("name") or r["label"], "namespace": names.get(r["space"])} for r in rows]
    if f.get("entities"):
        rows = _entities(db, seen, f["entities"])
        filters["entities"] = [r["id"] for r in rows]
        shown["entities"] = [{"id": r["id"], "name": r["name"], "namespace": names.get(r["space"])} for r in rows]
    if f.get("topics"):
        rows, ids = _topics(db, seen, f["topics"])
        filters["topics"] = ids
        shown["topics"] = [{"id": r["id"], "name": r["label"], "namespace": names.get(r["space"])} for r in rows]
        if len(ids) > len(rows):
            shown["narrower_topics"] = len(ids) - len(rows)
    if f.get("content_types"):
        types = _content_types(db, f["content_types"])
        filters["content_types"] = [t["key"] for t in types]
        shown["content_types"] = [{"key": t["key"], "name": t["label"]} for t in types]
    if f.get("types"):
        bad = [t for t in f["types"] if t not in library.MEDIA]
        if bad:
            raise ValueError(f"types are {', '.join(library.MEDIA)}; not {bad[0]}")
        filters["media"] = f["types"]
    for k, lib in (("tags", "tags"), ("languages", "languages"), ("objects", "objects"), ("emotions", "emotions"), ("origins", "origins")):
        if f.get(k):
            filters[lib] = [str(x) for x in f[k]]
    if f.get("status"):
        filters["status"] = [str(x) for x in f["status"]]
    for k in ("date_from", "date_to", "min_duration", "max_duration", "sensitive"):
        if f.get(k) is not None:
            filters[k] = f[k]
    if f.get("collection") is not None:
        c = db.one("SELECT space FROM $r", r=R("collection", f["collection"]))
        if not c or (c["space"] not in spaces and f["collection"] not in (within or {}).get(c["space"], [])):
            raise ValueError(f"collection {f['collection']} isn't one you can read")
        filters["collections"] = hierarchy.subtree(db, c["space"], f["collection"])
    return {k: v for k, v in filters.items() if v not in (None, "", [])}, shown


def _plain(snippet):
    return html.unescape(re.sub(r"</?mark>", "", snippet or ""))


def _words(db, cfg, text, spaces, within):
    """A text search's moments, best first, for the resources it finds."""
    also = hierarchy.recordings_in(db, [c for cs in (within or {}).values() for c in cs])
    return searchmod.search(
        db, text, None, limit=TEXT_HITS, spaces=set(spaces), also=also, files=True, objects=True, described=True, cfg=cfg, mode="auto"
    )


def find(db, cfg, spaces, within=None, spec=None, sort=None, limit=20, offset=0, ids=None):
    """Resources matching the filter (KEYS), a page of them, each as the library lists it with `why` it matched:
    {total, items, resolved, text}. `sort` is one of SORTS; relevance (the default with `text`) puts the resources
    whose moments match the words best first, else they're newest first. `ids` keeps to these recordings (such as a
    conversation's scope)."""
    f = clean(spec)
    sort = sort or ("relevance" if f.get("text") else "-date")
    if sort not in SORTS:
        raise ValueError(f"sort by {', '.join(SORTS)}")
    if sort == "relevance" and not f.get("text"):
        sort = "-date"
    limit, offset = max(1, min(int(limit or 20), 100)), max(0, int(offset or 0))
    filters, shown = resolve(db, spaces, within, f)
    hits, said = [], None
    if ids is not None:
        filters["only"] = {int(i) for i in ids}
    if f.get("text"):
        said = _words(db, cfg, f["text"], spaces, within)
        hits = said["hits"]
        found = {h["recording_id"] for h in hits}
        filters["only"] = found if ids is None else found & filters["only"]
    if not spaces and not within:
        return {"total": 0, "items": [], "resolved": shown, "text": _said(said)}
    if sort == "relevance":
        cond, p = library.where(db, spaces, within=within, cfg=cfg, **filters)
        found = set(db.values(f"SELECT VALUE record::id(id) FROM recording WHERE {cond}", **p))
        ranked = [i for i in dict.fromkeys(h["recording_id"] for h in hits) if i in found]
        page = ranked[offset : offset + limit]
        rows = db.rows(f"SELECT {library.FIELDS} FROM recording WHERE id IN $ids", ids=library.recs(page)) if page else []
        rows.sort(key=lambda r: page.index(r["id"]))
        items, total = library.summaries(db, rows, cfg), len(ranked)
    else:
        items, total = library.list_recordings(db, spaces, sort=sort, limit=limit, offset=offset, within=within, cfg=cfg, **filters)
    reasons = why(db, [x["id"] for x in items], filters, shown, hits, f.get("text"))
    for x in items:
        x["why"] = reasons.get(x["id"], [])
    return {"total": total, "items": items, "resolved": shown, "text": _said(said)}


def _said(res):
    if res is None:
        return None
    return {"mode": res["mode"], "meaning": res["meaning"], "moments": res["total"], "capped": res["capped"] or res["total"] >= TEXT_HITS}


def why(db, rids, filters, shown, hits=(), text=None):
    """{recording id: [{by, name, moments?, count?}]}: why each of these resources matched, with moments to cite. A
    moment has t0 and t1 (ms) on a timeline, or page (from 0) in a document or an image, and the words when it has any."""
    out = defaultdict(list)
    if not rids:
        return out
    rr = library.recs(rids)
    if text:
        per = defaultdict(list)
        for h in hits:
            if h["recording_id"] in rids and len(per[h["recording_id"]]) < MOMENTS:
                per[h["recording_id"]].append(
                    store.clean(
                        {
                            "t0": None if h.get("page") is not None else h.get("t0"),
                            "t1": None if h.get("page") is not None else h.get("t1"),
                            "page": h.get("page"),
                            "text": _plain(h.get("snippet")),
                            "speaker": h.get("speaker"),
                            "source": h.get("source"),
                        }
                    )
                )
        for rid, ms in per.items():
            out[rid].append({"by": "words", "name": text, "moments": ms})
    if filters.get("faces"):
        names = {x["id"]: x["name"] for x in shown.get("faces", [])}
        for t in db.rows(
            "SELECT recording, face, spans, paged, first_ms FROM face_track WHERE recording IN $r AND face IN $f ORDER BY first_ms",
            r=rids,
            f=filters["faces"],
        ):
            spans = (t.get("spans") or [])[:MOMENTS]
            moments = [{"page": a, "last_page": b - 1} if t.get("paged") else {"t0": a, "t1": b} for a, b in spans]
            out[t["recording"]].append({"by": "face", "name": names.get(t["face"]), "moments": moments})
    if filters.get("speakers"):
        names = {x["id"]: x["name"] for x in shown.get("speakers", [])}
        for rid in rids:
            lines = db.rows(
                "SELECT speaker, idx, t0, t1, page, text FROM segment WHERE recording = $r AND speaker IN $s ORDER BY idx LIMIT 50",
                r=rid,
                s=filters["speakers"],
            )
            first = defaultdict(list)
            for s in lines:
                if len(first[s["speaker"]]) < MOMENTS:
                    first[s["speaker"]].append(_line(s))
            for sid, ms in first.items():
                out[rid].append({"by": "voice", "name": names.get(sid), "moments": ms})
    if filters.get("entities"):
        names = {x["id"]: x["name"] for x in shown.get("entities", [])}
        ms = db.rows(
            "SELECT recording, entity, record::id(in) AS segment FROM mentions WHERE recording IN $r AND entity IN $e",
            r=rids,
            e=filters["entities"],
        )
        ms.sort(key=lambda m: m["segment"])
        keep = defaultdict(list)
        for m in ms:
            if len(keep[(m["recording"], m["entity"])]) < MOMENTS:
                keep[(m["recording"], m["entity"])].append(m["segment"])
        segs = {
            s["id"]: s
            for s in (
                db.rows(
                    "SELECT record::id(id) AS id, t0, t1, page, text FROM segment WHERE id IN $ids",
                    ids=[R("segment", i) for v in keep.values() for i in v],
                )
                if keep
                else []
            )
        }
        for (rid, eid), ids in keep.items():
            out[rid].append({"by": "entity", "name": names.get(eid), "moments": [_line(segs[i]) for i in ids if i in segs]})
    if filters.get("topics"):
        labels = {
            t["id"]: t["label"]
            for t in db.rows("SELECT record::id(id) AS id, label FROM topic WHERE id IN $t", t=[R("topic", i) for i in filters["topics"]])
        }
        for a in db.rows(
            "SELECT recording, topic FROM topic_about WHERE recording IN $r AND topic IN $t AND status = 'accepted'",
            r=rids,
            t=filters["topics"],
        ):
            out[a["recording"]].append({"by": "topic", "name": labels.get(a["topic"])})
    if filters.get("content_types"):
        labels = {x["key"]: x["name"] for x in shown.get("content_types", [])}
        keys = ctypes.keys_of(db, db.rows(f"SELECT {ctypes.TYPE_FIELDS} FROM recording WHERE id IN $r", r=rr))
        for rid, k in keys.items():
            if k in labels:
                out[rid].append({"by": "content_type", "name": labels[k]})
    if filters.get("objects"):
        want = {_key(o) for o in filters["objects"]}
        for r in db.rows("SELECT record::id(id) AS id, objects FROM recording WHERE id IN $r", r=rr):
            for o in sorted(want & set(r.get("objects") or [])):
                out[r["id"]].append({"by": "object", "name": o})
    if filters.get("emotions"):
        want = {_key(e) for e in filters["emotions"]}
        for r in db.rows("SELECT record::id(id) AS id, stats.emotions AS e FROM recording WHERE id IN $r", r=rr):
            for k, n in sorted((r.get("e") or {}).items()):
                if n and k.casefold() in want:
                    out[r["id"]].append({"by": "emotion", "name": k, "count": n})
    return out


def _line(s):
    paged = s.get("page") is not None
    return store.clean(
        {"t0": None if paged else s.get("t0"), "t1": None if paged else s.get("t1"), "page": s.get("page"), "text": s.get("text")}
    )
