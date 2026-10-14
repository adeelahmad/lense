"""Similar content: the resources most like a text, a resource, a topic or an entity, each with why it is alike.

Four lists, fused by reciprocal rank (as search.py fuses words and meaning):

- meaning: the passages an embedding model finds closest (semantic.py) to the text, or to the centre of a resource's
  own passages. Only when search by meaning is set up and indexed, so the default install (no embeddings server)
  goes by the other three;
- words: the text's key words (the ones the archive says least often first, from the `term` table), or a resource's
  keywords (analyze.keywords), any of them, through the word index (textindex.py): resources sharing more of them,
  and saying them more, first;
- topics: resources about the same topics (`topic_about`), a topic fewer resources are about counting for more;
- entities: resources mentioning the same people, organisations, places... (`mentions`), the rarer counting for more.

Every result cites why: its closest passages or the lines with the shared words (each a moment to open), the words,
and the topics and entities in common. Nothing here calls a language model; the one model call is embedding a text
(semantic.query_vector remembers it), which goes through the activity ledger like every other.
"""

from __future__ import annotations

import math
import re
from collections import Counter

from . import analyze, render, semantic, store, textindex
from . import entities as ents
from . import topics as topicmod
from .search import M0, M1, _mark_plain, snippet

R = store.R
LIMIT_MAX = 50
TEXT_MAX = 2000  # the most of a text compared (an embedding model reads about this much)
TERMS = 12  # key words looked for
CITES = 3  # moments cited per resource
ENTITIES_MAX = 25  # a resource's most mentioned entities compared
RRF = 60
KINDS = {"said", "page", "described"}
SNIPPET = 220
BASES = ("text", "recording", "topic", "entity")


def similar(
    db, cfg, *, text=None, recording=None, topic=None, entity=None, spaces=(), also=(), within=None, exclude=None, limit=10, offset=0
):
    """The resources in namespaces `spaces` (and recordings `also`) most like one of `text`, `recording`, `topic` or
    `entity`, best first: {basis, label, text, terms, mode, meaning, semantic, total, items}. `within` keeps to a set of
    recordings (a conversation's scope). A resource is never like itself, and `exclude` leaves out one more (the
    resource a text was selected in). KeyError when the
    recording, topic or entity isn't one these namespaces hold; ValueError unless exactly one of them is given."""
    given = [k for k, v in (("text", text), ("recording", recording), ("topic", topic), ("entity", entity)) if v not in (None, "")]
    if len(given) != 1:
        raise ValueError("give one of text, recording, topic or entity")
    spaces, also = {int(s) for s in spaces}, {int(r) for r in also or ()}
    b = _basis(db, cfg, given[0], text, recording, topic, entity, spaces)
    can = semantic.available(db, cfg)
    out = {
        "basis": b["basis"],
        "label": b["label"],
        "text": b["text"],
        "terms": b["terms"],
        "mode": "words",
        "meaning": None,
        "semantic": can,
        "total": 0,
        "items": [],
    }
    if (not spaces and not also) or (within is not None and not within):
        return out
    skip = {int(x) for x in (b.get("recording"), exclude) if x not in (None, "")}
    where, params = _where(spaces, also, skip, within)
    limit, offset = max(1, min(int(limit), LIMIT_MAX)), max(0, int(offset))
    need = offset + limit
    lists = {}
    if can and (b["vector"] or b["text"]):
        try:
            lists["meaning"] = _by_meaning(db, cfg, b, where, params, need)
            out["mode"] = "meaning"
        except semantic.EmbedError:
            out["meaning"] = "search by meaning is unavailable right now, so these are alike by their words, topics and entities"
    elif semantic.configured(cfg):
        out["meaning"] = "nothing is indexed for search by meaning with this model yet"
    if b["terms"]:
        lists["words"] = _by_words(db, b["terms"], where, params, need)
    if b["topics"]:
        lists["topics"] = _by_topics(db, b["topics"], where, params)
    if b["entities"]:
        lists["entities"] = _by_entities(db, b["entities"], where, params)
    fused = _fuse(lists)
    out["total"] = len(fused)
    out["items"] = _items(db, fused[offset : offset + limit])
    return out


# ---------- what to compare with ----------
def _basis(db, cfg, kind, text, rid, tid, eid, spaces):
    """What the four lists look for: {basis, label, text (for meaning), vector, terms (words), topics, entities,
    recording (left out of the results)}."""
    b = {"basis": kind, "label": None, "text": None, "vector": None, "terms": [], "topics": {}, "entities": {}, "recording": None}
    if kind == "text":
        t = " ".join(str(text).split())[:TEXT_MAX]
        b.update(label=t[:120], text=t, terms=key_terms(db, t, spaces))
        return b
    if kind == "recording":
        rec = db.one("SELECT space, title FROM $r", r=R("recording", int(rid)))
        if not rec:
            raise KeyError(rid)
        rid = int(rid)
        terms = [w for w, _ in analyze.keywords(db, rid, top=TERMS)]
        b.update(
            label=rec.get("title"),
            recording=rid,
            terms=terms,
            topics={t["id"]: t["label"] for t in topicmod.of_recording(db, rid)},
            entities=_entities_of(db, rid),
        )
        b["vector"] = semantic.centre(db, cfg, rid)
        if b["vector"] is None:  # not indexed (yet): what it's called and its keywords stand for it
            b["text"] = " ".join([rec.get("title") or "", *terms]).strip() or None
        return b
    if kind == "topic":
        t = topicmod._topic(db, int(tid))
        if t["space"] not in spaces:
            raise KeyError(tid)
        labels = [t["label"], *(t.get("alt") or [])]
        b.update(
            label=t["label"],
            text=". ".join(x for x in [", ".join(labels), t.get("definition") or ""] if x)[:TEXT_MAX],
            terms=_dedupe(labels)[:TERMS],
            topics={t["id"]: t["label"]},
        )
        return b
    e = ents._entity(db, int(eid))
    if e["space"] not in spaces:
        raise KeyError(eid)
    names = _dedupe([e["name"], *ents.aliases(db, [e["id"]]).get(e["id"], [])])
    b.update(
        label=e["name"],
        text=". ".join(x for x in [e["name"], e.get("description") or ""] if x)[:TEXT_MAX],
        terms=names[:TERMS],
        entities={e["id"]: e["name"]},
    )
    return b


def key_terms(db, text, spaces, n=TERMS):
    """The words (and two-word phrases) that say most about `text`: the ones it repeats and the archive says in few
    recordings first, as analyze.keywords weighs a recording's. Words the namespaces' recordings never say are left
    out, since nothing could match them; when nothing is analysed yet, the ones it repeats and the longer ones."""
    ws = analyze.words(text)
    tf, surface = Counter(), {}
    for st, sf in ws:
        tf[st] += 1
        surface.setdefault(st, sf)
    for (a, sa), (b2, sb) in zip(ws, ws[1:]):
        tf[a + " " + b2] += 1
        surface.setdefault(a + " " + b2, sa + " " + sb)
    if not tf:
        return []
    df = (
        {
            r["term"]: r["df"]
            for r in db.rows(
                "SELECT term, count() AS df FROM term WHERE space IN $s AND term IN $t GROUP BY term", s=sorted(spaces), t=list(tf)
            )
        }
        if spaces
        else {}
    )
    if df:
        n_recs = int((db.one("SELECT count() AS n FROM recording WHERE space IN $s GROUP ALL", s=sorted(spaces)) or {}).get("n") or 1)
        scored = [
            (surface[t], (1 + math.log(k)) * (math.log((n_recs + 1) / (df[t] + 1)) + 1) * (1.3 if " " in t else 1))
            for t, k in tf.items()
            if t in df
        ]
    else:
        scored = [(surface[t], k * (1 + min(len(t), 12) / 6)) for t, k in tf.items() if " " not in t]
    scored.sort(key=lambda x: -x[1])
    out: list[str] = []
    for word, _ in scored:  # a word inside a phrase already chosen adds little
        if word in out or (" " not in word and any(word in o.split() for o in out)):
            continue
        out.append(word)
        if len(out) == n:
            break
    return out


def _entities_of(db, rid):
    """A recording's most mentioned entities, {id: name}: not hidden, and not dates or numbers."""
    counts = db.rows("SELECT entity, count() AS n FROM mentions WHERE recording = $r GROUP BY entity", r=rid)
    if not counts:
        return {}
    n = {c["entity"]: c["n"] for c in counts}
    rows = db.rows(
        "SELECT record::id(id) AS id, name, type, hidden FROM entity WHERE id IN $ids", ids=[R("entity", e) for e in n if e is not None]
    )
    keep = [r for r in rows if not r.get("hidden") and r.get("type") not in ents.QUIET]
    keep.sort(key=lambda r: (-n.get(r["id"], 0), r["name"].lower()))
    return {r["id"]: r["name"] for r in keep[:ENTITIES_MAX]}


def _dedupe(labels):
    seen, out = set(), []
    for x in labels:
        x = " ".join(str(x or "").split())
        if x and x.lower() not in seen:
            seen.add(x.lower())
            out.append(x)
    return out


def _where(spaces, also, skip, within=None):
    """The filter (" AND ...") every table here is read through: the namespaces and recordings that may be read,
    less the ones left out. Passages, lines, topic_about and mentions all carry `space` and `recording`."""
    params = {"allowed": sorted(spaces)}
    where = " AND (space IN $allowed OR recording IN $also)" if also else " AND space IN $allowed"
    if also:
        params["also"] = sorted(also)
    if within is not None:
        where += " AND recording IN $within"
        params["within"] = sorted(int(r) for r in within)
    if skip:
        where += " AND recording NOT IN $skip"
        params["skip"] = sorted(skip)
    return where, params


# ---------- the four lists: [(recording, {cites, words, topics, entities, similarity})], best first ----------
def _by_meaning(db, cfg, b, where, params, need):
    k = min(500, max(int(semantic._section(cfg).get("neighbours") or 40), need * 8))
    if b["vector"] is not None:
        found = semantic.nearest_to(db, cfg, b["vector"], where, params, KINDS, k)
    else:
        found = semantic.nearest(db, cfg, b["text"], where, params, KINDS, k)
    marks = [t for t in b["terms"] if " " not in t]
    by: dict = {}
    for p in found:  # most alike first
        x = by.setdefault(p["recording"], {"cites": [], "similarity": round(p["similarity"], 3)})
        if len(x["cites"]) < CITES:
            x["cites"].append(_passage(p, marks))
    return list(by.items())


def _passage(p, marks):
    text = p.get("text") or ""
    marked = _mark_plain(text, marks) if marks else text
    cite = {"match": "meaning", "similarity": round(p["similarity"], 3), "snippet": _snip(marked)}
    if p["kind"] == "described":  # a shot's time, or on a document its page (kept in t0)
        paged = bool(p.get("paged"))
        return {**cite, "source": "described", "idx": None, "t0": None if paged else p.get("t0"), "page": p.get("t0") if paged else None}
    return {**cite, "source": p["kind"], "idx": p.get("idx0"), "t0": p.get("t0"), "page": p.get("page")}


def _by_words(db, terms, where, params, need):
    groups = [{"words": [], "phrases": [t]} if " " in t else {"words": [t], "phrases": []} for t in terms]
    fields = "record::id(id) AS id, recording, idx, t0, page, text"
    cap = min(2000, max(200, need * 20))
    rows = textindex.rows(db, "segment", groups, fields, where, params, cap, (M0, M1))
    if rows is None:  # no word index here (search.engine "surrealdb", or it's down): read the lines
        words = {f"w{j}": t.lower() for j, t in enumerate(terms)}
        cond = " OR ".join(f"string::contains(string::lowercase(text), ${k})" for k in words)
        rows = db.rows(f"SELECT {fields} FROM segment WHERE ({cond}){where} LIMIT {cap}", **params, **words)
        for r in rows:
            r["h1"] = _mark_plain(r["text"], terms)
            r["s1"] = r["h1"].count(M0)
    by: dict = {}
    for r in rows:
        said = {m.lower() for m in re.findall(f"{M0}(.*?){M1}", r.get("h1") or "")}
        x = by.setdefault(r["recording"], {"lines": [], "words": set()})
        x["lines"].append(r)
        x["words"] |= said
    for x in by.values():
        x["lines"].sort(key=lambda r: -(r.get("s1") or 0))
    ranked = sorted(by.items(), key=lambda kv: (-len(kv[1]["words"]), -sum(r.get("s1") or 0 for r in kv[1]["lines"][:CITES]), kv[0]))
    return [
        (
            rid,
            {
                "cites": [
                    {
                        "match": "words",
                        "similarity": None,
                        "snippet": snippet(r.get("h1") or r["text"], SNIPPET // 2),
                        "source": "said" if r.get("page") is None else "page",
                        "idx": r.get("idx"),
                        "t0": r.get("t0"),
                        "page": r.get("page"),
                    }
                    for r in x["lines"][:CITES]
                ],
                "words": sorted(x["words"])[:8],
            },
        )
        for rid, x in ranked
    ]


def _by_topics(db, tids, where, params):
    rows = db.rows(
        f"SELECT recording, topic, weight FROM topic_about WHERE topic IN $t AND status != 'dismissed'{where}", t=sorted(tids), **params
    )
    df = Counter(r["topic"] for r in rows)
    by: dict = {}
    for r in rows:
        x = by.setdefault(r["recording"], {"score": 0.0, "topics": []})
        x["score"] += 1 / math.log2(1 + df[r["topic"]])
        x["topics"].append({"id": r["topic"], "label": tids[r["topic"]]})
    ranked = sorted(by.items(), key=lambda kv: (-kv[1]["score"], kv[0]))
    return [(rid, {"topics": sorted(x["topics"], key=lambda t: t["label"].lower())}) for rid, x in ranked]


def _by_entities(db, eids, where, params):
    rows = db.rows(
        f"SELECT entity, recording, count() AS n FROM mentions WHERE entity IN $e{where} GROUP BY entity, recording",
        e=sorted(eids),
        **params,
    )
    df = Counter(r["entity"] for r in rows)
    by: dict = {}
    for r in rows:
        x = by.setdefault(r["recording"], {"score": 0.0, "n": 0, "entities": []})
        x["score"] += 1 / math.log2(1 + df[r["entity"]])
        x["n"] += r["n"]
        x["entities"].append({"id": r["entity"], "name": eids[r["entity"]]})
    ranked = sorted(by.items(), key=lambda kv: (-kv[1]["score"], -kv[1]["n"], kv[0]))
    return [(rid, {"entities": sorted(x["entities"], key=lambda e: e["name"].lower())}) for rid, x in ranked]


# ---------- one list ----------
def _fuse(lists):
    """The lists as one, best first: a resource's score is the sum of 1 / (RRF + its rank) in each list it's in."""
    acc: dict = {}
    for name in ("meaning", "words", "topics", "entities"):
        for i, (rid, d) in enumerate(lists.get(name) or []):
            x = acc.setdefault(
                rid,
                {"recording": rid, "score": 0.0, "match": [], "similarity": None, "cites": [], "words": [], "topics": [], "entities": []},
            )
            x["score"] += 1 / (RRF + i + 1)
            x["match"].append(name)
            if d.get("similarity") is not None:
                x["similarity"] = d["similarity"]
            for c in d.get("cites") or []:  # the closest passages first, then the lines with the words; each moment once
                if len(x["cites"]) < CITES and not any(
                    (c["source"], c["idx"], c["page"], c["t0"]) == (o["source"], o["idx"], o["page"], o["t0"]) for o in x["cites"]
                ):
                    x["cites"].append(c)
            for k in ("words", "topics", "entities"):
                x[k] += d.get(k) or []
    return sorted(acc.values(), key=lambda x: (-x["score"], x["recording"]))


def _items(db, page):
    if not page:
        return []
    recs = {
        r["id"]: r
        for r in db.rows(
            "SELECT record::id(id) AS id, title, recorded_at, space, source, media FROM recording WHERE id IN $ids",
            ids=[R("recording", x["recording"]) for x in page],
        )
    }
    names = store.space_names(db)
    out = []
    for x in page:
        r = recs.get(x["recording"])
        if not r:  # deleted since its passages or lines were read
            continue
        out.append(
            {
                "recording_id": x["recording"],
                "title": r.get("title"),
                "recorded_at": r.get("recorded_at"),
                "namespace": names.get(r["space"]),
                "media_kind": render.kind(r),
                "score": round(x["score"], 4),
                "match": x["match"],
                "similarity": x["similarity"],
                "cites": x["cites"],
                "words": x["words"],
                "topics": x["topics"],
                "entities": x["entities"],
            }
        )
    return out


def _snip(marked):
    """A passage as a snippet: around its first marked word, else its start."""
    if M0 in marked:
        return snippet(marked, SNIPPET // 2)
    return snippet(marked, SNIPPET)
