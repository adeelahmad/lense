"""Full-text search over every segment through SurrealDB's BM25 index, with highlighted snippets.

Words are ANDed and matched after English stemming (exploit finds exploits and exploiting),
"quoted phrases" must appear as written, and OR separates alternatives.
"""

from __future__ import annotations

import html
import logging
import re
from collections import Counter

from . import semantic, store, textindex

log = logging.getLogger(__name__)

M0, M1 = "\x02", "\x03"
# words not worth marking in a passage found by meaning
STOP = set(
    "a an and are as at be but by for from has have i in is it its of on or our so that the their they this to was we were what with you your about".split()
)
FACET_CAP = 20000  # moments counted for facets; more than this and the counts say they're partial
FACET_VALUES = 50  # values listed per facet


def parse_query(q):
    groups, cur = [], {"words": [], "phrases": []}
    for p in re.findall(r'"[^"]+"|\S+', q or ""):
        if p.upper() == "OR":
            if cur["words"] or cur["phrases"]:
                groups.append(cur)
                cur = {"words": [], "phrases": []}
            continue
        if len(p) > 2 and p[0] == p[-1] == '"':
            inner = " ".join(re.findall(r"[\w'’.-]+", p[1:-1]))
            if inner:
                cur["phrases"].append(inner)
            continue
        w = re.sub(r"[^\w'’.-]", "", p.rstrip("*")).strip(".-")
        if w:
            cur["words"].append(w)
    if cur["words"] or cur["phrases"]:
        groups.append(cur)
    return groups[:4]


def describe(groups):
    return " OR ".join(" ".join(g["words"] + [f'"{x}"' for x in g["phrases"]]) for g in groups)


def snippet(marked, width=110):
    i = marked.find(M0)
    a = 0 if i < 0 else max(0, i - width)
    b = min(len(marked), (i if i >= 0 else 0) + width + (0 if i < 0 else 40))
    while a > 0 and not marked[a - 1].isspace() and i - a < width + 20:
        a -= 1
    part = marked[a:b]
    firsts = [c for c in part if c in (M0, M1)]
    if firsts and firsts[0] == M1:
        part = M0 + part
    if firsts and firsts[-1] == M0:
        part += M1
    return ("…" if a > 0 else "") + html.escape(part).replace(M0, "<mark>").replace(M1, "</mark>") + ("…" if b < len(marked) else "")


def _mark_plain(text, words):
    rx = re.compile("|".join(re.escape(w) for w in sorted(words, key=len, reverse=True)), re.I) if words else None
    return rx.sub(lambda m: M0 + m.group(0) + M1, text) if rx else text


def search(
    db,
    q,
    ns=None,
    speaker=None,
    emotion=None,
    recording=None,
    limit=50,
    offset=0,
    spaces=None,
    recordings=None,
    screen=True,
    facets=False,
    also=None,
    files=False,
    objects=False,
    obj=None,
    described=False,
    cfg=None,
    mode="keyword",
):
    """Transcript lines (and, unless screen is false, text on screen in videos; with files, the lines of supplementary
    transcripts, captions, translations and indexes; with objects, the kinds of object seen in videos, documents and
    images; with described, what a model that can see said their pages and shots show) matching q. spaces limits the search to namespaces someone may read, and `also` adds recordings they may
    read beyond those (in collections they were given a role on); recordings limits it to a set of recordings (such as
    the transcripts a visitor may read), and obj to those a kind of object is seen in. With facets, also how many of
    all the matching moments (up to FACET_CAP) are in each namespace, speaker, emotion and recording, and which kinds
    of object the recordings they're in have.

    `mode` is how the query is matched: "keyword" (its words, through the BM25 index), "semantic" (by meaning: the
    passages an embedding model finds most alike, semantic.py), "hybrid" (both, fused by reciprocal rank) or "auto"
    (hybrid when search by meaning is available and the query has no "phrases" or OR, else keyword). Search by
    meaning needs `cfg`; when it can't be used the search is by keyword, and `meaning` says why."""
    groups = parse_query(q)
    can = cfg is not None and semantic.available(db, cfg)
    used, note = _mode(cfg, mode, groups, can)
    empty = {"q": q, "query": "", "total": 0, "capped": False, "hits": [], "mode": used, "meaning": note, "semantic": can}
    if obj:
        having = set(db.values("SELECT VALUE record::id(id) FROM recording WHERE objects CONTAINS $o", o=" ".join(obj.split()).casefold()))
        recordings = having if recordings is None else set(recordings) & having
    if not groups or (recordings is not None and not recordings):
        return empty
    filt, params = [], {"m0": M0, "m1": M1}
    if ns:
        try:
            params["sp"] = store.ns_id(db, ns, create=False)
        except KeyError:
            return empty
        filt.append("space = $sp")
    if spaces is not None:  # only namespaces this person may read, and the recordings they may read beyond them
        filt.append("(space IN $allowed OR recording IN $also)" if also else "space IN $allowed")
        params["allowed"] = sorted(spaces)
        if also:
            params["also"] = sorted(also)
    if recordings is not None:
        filt.append("recording IN $recs")
        params["recs"] = sorted(recordings)
    for cond, key, val in (("speaker = $spk", "spk", speaker), ("emotion = $emo", "emo", emotion), ("recording = $rec", "rec", recording)):
        if val not in (None, ""):
            filt.append(cond)
            params[key] = int(val) if key in ("spk", "rec") else val
    where_f = (" AND " + " AND ".join(filt)) if filt else ""
    base_params = dict(params)
    cap = min(1000, (offset + limit) * 3 + 50)
    fields = "record::id(id) AS id, recording, idx, t0, t1, emotion, speaker, space, text, page, box"
    rows = [] if used == "semantic" else textindex.rows(db, "segment", groups, fields, where_f, params, cap, (M0, M1))
    if rows is None and db.ready_fulltext():
        conds, sel = [], []
        for k, g in enumerate(groups, 1):
            params[f"q{k}"] = " ".join(g["words"] + g["phrases"])
            conds.append(f"text @{k}@ $q{k}")
            sel.append(f"search::highlight($m0, $m1, {k}) AS h{k}, search::score({k}) AS s{k}")
        try:
            rows = db.rows(f"SELECT {fields}, {', '.join(sel)} FROM segment WHERE ({' OR '.join(conds)}){where_f} LIMIT {cap}", **params)
        except Exception:  # noqa: BLE001 - fall back to a plain scan below
            rows = None
    if rows is None:
        conds = []
        for k, g in enumerate(groups, 1):
            ws = g["words"] + g["phrases"]
            conds.append("(" + " AND ".join(f"string::contains(string::lowercase(text), $w{k}_{j})" for j in range(len(ws))) + ")")
            params.update({f"w{k}_{j}": w.lower() for j, w in enumerate(ws)})
        rows = db.rows(f"SELECT {fields} FROM segment WHERE ({' OR '.join(conds)}){where_f} LIMIT {cap}", **params)
        for r in rows:
            r["h1"] = _mark_plain(r["text"], [w for g in groups for w in g["words"] + g["phrases"]])
            r["s1"] = r["h1"].count(M0)
    hits = []
    for r in rows:
        low = r["text"].lower()
        if not any(all(re.search(r"\b" + re.escape(p.lower()) + r"\b", low) for p in g["phrases"]) for g in groups):
            continue
        marks = [r.get(f"h{k}") for k in range(1, len(groups) + 1) if isinstance(r.get(f"h{k}"), str)]
        best = max(marks, key=lambda h: h.count(M0)) if marks else r["text"]
        r["_score"] = sum(abs(r.get(f"s{k}") or 0) for k in range(1, len(groups) + 1))  # BM25; some engines return it negated
        r["_snip"] = snippet(best)
        hits.append(r)
    for h in hits:  # a document's or an image's text is on its pages
        h["source"] = "said" if h.get("page") is None else "page"
    alone = not speaker and not emotion
    if used != "semantic":
        if screen and alone:  # text shown on screen in videos has no speaker or emotion
            hits += _screen(db, groups, space_filter(ns, spaces, recording, params), cap, params)
        if files and alone:  # nor do the lines of files (a speaker there is just a label)
            hits += _file_lines(db, groups, space_filter(ns, spaces, recording, params), cap, params)
        if objects and alone:  # nor do the objects seen
            hits += _objects(db, groups, space_filter(ns, spaces, recording, params), cap, params)
        if described and alone:  # nor what pages and shots show
            hits += _described(db, groups, space_filter(ns, spaces, recording, params), cap, params)
    hits.sort(key=lambda r: (-r["_score"], r["recording"], r.get("idx") or 0))
    meant = []
    if used != "keyword":
        kinds = {"said", "page"} | ({"described"} if described and alone else set())
        try:
            meant = _meaning(db, cfg, q, groups, kinds, offset + limit, base_params, ns, spaces, recording, speaker, emotion)
        except semantic.EmbedError as e:
            log.warning("search by meaning is unavailable: %s", e)
            note = "search by meaning is unavailable right now, so these match the words"
            if used == "semantic":  # it was to be by meaning alone: by the words instead
                args = dict(ns=ns, speaker=speaker, emotion=emotion, recording=recording, limit=limit, offset=offset, spaces=spaces)
                more = dict(recordings=recordings, screen=screen, facets=facets, also=also, files=files, objects=objects, obj=obj)
                return search(db, q, **args, **more, described=described) | {"meaning": note}
            used = "keyword"
        hits, meant = _fuse(hits, meant, used)
    page = hits[offset : offset + limit]
    recs = (
        {
            x["id"]: x
            for x in db.rows(
                "SELECT record::id(id) AS id, title, recorded_at FROM recording WHERE id IN $ids",
                ids=[store.R("recording", i) for i in {h["recording"] for h in page}],
            )
        }
        if page
        else {}
    )
    spk = (
        {
            x["id"]: x.get("name") or x["label"]
            for x in db.rows(
                "SELECT record::id(id) AS id, name, label FROM speaker WHERE id IN $ids",
                ids=[store.R("speaker", i) for i in {h["speaker"] for h in page if h.get("speaker")}],
            )
        }
        if page
        else {}
    )
    spaces = store.space_names(db) if page else {}
    in_files = (
        {
            x["id"]: x
            for x in db.rows(
                "SELECT record::id(id) AS id, role, label, name FROM resource_file WHERE id IN $ids",
                ids=[store.R("resource_file", i) for i in {h["file"] for h in page if h["source"] == "file"}],
            )
        }
        if any(h["source"] == "file" for h in page)
        else {}
    )
    out = [
        {
            "id": h["id"],
            "recording_id": h["recording"],
            "idx": h.get("idx"),
            "t0": h.get("t0"),
            "t1": h.get("t1"),
            "emotion": h.get("emotion"),
            "speaker_id": h.get("speaker"),
            "speaker": spk.get(h.get("speaker")),
            "title": recs.get(h["recording"], {}).get("title"),
            "recorded_at": recs.get(h["recording"], {}).get("recorded_at"),
            "namespace": spaces.get(h["space"]),
            "snippet": h["_snip"],
            "source": h["source"],
            **(
                {"frame": f"{store.API}/recordings/{h['recording']}/frames/{h['frame']}" if h.get("frame") else None, "box": h.get("box")}
                if h["source"] in ("screen", "object", "described")
                else {}
            ),
            **({"t0": None, "t1": None, "page": h["t0"]} if h["source"] in ("object", "described") and h.get("paged") else {}),
            **({"page": h["page"], "box": h.get("box")} if h["source"] == "page" else {}),
            **(_in_file(in_files.get(h["file"]) or {"id": h["file"]}, h.get("line")) if h["source"] == "file" else {}),
            "match": h.get("match") or "words",
            "similarity": round(h["similarity"], 3) if h.get("similarity") is not None else None,
        }
        for h in page
    ]
    res = {
        "q": q,
        "query": describe(groups),
        "total": len(hits),
        "capped": len(rows) >= cap,
        "hits": out,
        "mode": used,
        "meaning": note,
        "semantic": can,
    }
    if facets:
        res["facets"] = _facets(
            db,
            groups,
            where_f,
            base_params,
            screen and alone,
            ns,
            spaces,
            recording,
            files and alone,
            objects and alone,
            described and alone,
            meant,
            words=used != "semantic",
        )
    return res


def _mode(cfg, mode, groups, can):
    """(how a search is matched, why not by meaning when it was asked to be)."""
    if mode not in ("keyword", "semantic", "hybrid", "auto"):
        raise ValueError("mode is keyword, semantic, hybrid or auto")
    if mode == "keyword":
        return "keyword", None
    if mode == "auto" and (len(groups) > 1 or any(g["phrases"] for g in groups)):
        return "keyword", None  # "phrases" and OR ask for exactly those words
    if cfg is None or not semantic.configured(cfg):
        return "keyword", (None if mode == "auto" else "search by meaning is off, or has no embeddings server")
    if not can:
        return "keyword", (None if mode == "auto" else "nothing is indexed for search by meaning with this model yet")
    return ("hybrid" if mode == "auto" else mode), None


def _meaning(db, cfg, q, groups, kinds, need, base, ns, spaces, recording, speaker, emotion):
    """The passages most like the query, as hits shaped like the keyword ones (best first), each at the line of its
    passage that best matches: the speaker or emotion asked for, else the one with most of the query's words."""
    params = {k: v for k, v in base.items() if k in ("sp", "allowed", "also", "rec", "recs", "spk", "emo")}
    where = space_filter(ns, spaces, recording, params)
    if "recs" in params:
        where += " AND recording IN $recs"
    if "spk" in params:
        where += " AND $spk IN speakers"
    if "emo" in params:
        where += " AND $emo IN emotions"
    k = min(500, max(int(semantic._section(cfg).get("neighbours") or 40), need))
    found = semantic.nearest(db, cfg, q, where, params, kinds, k)
    words = [w for g in groups for w in g["words"] + g["phrases"] if w.lower() not in STOP]  # marked where they're said
    lined = [p for p in found if p["kind"] != "described"]
    segs = (
        {
            (r["recording"], r["idx"]): r
            for r in db.rows(
                "SELECT record::id(id) AS id, recording, idx, t0, t1, emotion, speaker, space, text, page, box FROM segment WHERE id IN $ids",
                ids=[store.R("segment", p["recording"] * store.SEG + i) for p in lined for i in range(p["idx0"], p["idx1"] + 1)],
            )
        }
        if lined
        else {}
    )
    rx = re.compile("|".join(r"\b" + re.escape(w.lower()) for w in words)) if words else None
    out = []
    for p in found:
        if p["kind"] == "described":
            out.append(
                {
                    **{k: p.get(k) for k in ("id", "recording", "t0", "t1", "space", "frame", "paged", "text")},
                    "source": "described",
                    "similarity": p["similarity"],
                    "_snip": snippet(_mark_plain(p["text"], words) if words else p["text"], SNIPPET),
                }
            )
            continue
        lines = [segs[(p["recording"], i)] for i in range(p["idx0"], p["idx1"] + 1) if (p["recording"], i) in segs]
        if not lines:  # the transcript changed since it was indexed
            continue
        fits = [
            s
            for s in lines
            if (speaker in (None, "") or s.get("speaker") == int(speaker)) and (emotion in (None, "") or s.get("emotion") == emotion)
        ]
        if not fits:  # none of its lines is by that speaker or in that mood (any more)
            continue
        best = max(fits, key=lambda s: len(rx.findall(s["text"].lower())) if rx else 0)
        out.append(
            {
                **best,
                "source": "said" if best.get("page") is None else "page",
                "similarity": p["similarity"],
                "_span": (p["idx0"], p["idx1"]),
                "_snip": _context(lines, best, words),
            }
        )
    return out


SNIPPET = 260  # characters around the line a passage found by meaning is shown at


def _context(lines, best, words):
    """The line a passage is shown at, with as much of the lines around it as fits, the query's words marked."""
    k = lines.index(best)
    a, b, size = k, k + 1, len(best["text"])
    while size < SNIPPET and (a > 0 or b < len(lines)):
        if b < len(lines):
            size += len(lines[b]["text"]) + 1
            b += 1
        if a > 0 and size < SNIPPET:
            a -= 1
            size += len(lines[a]["text"]) + 1
    text = " ".join(" ".join(s["text"].split()) for s in lines[a:b])
    marked = _mark_plain(text, words) if words else text
    return snippet(marked, SNIPPET) if M0 in marked else _clip(marked, a > 0, b < len(lines))


def _clip(text, before, after):
    cut = text[: SNIPPET + 40]
    more = len(cut) < len(text)
    if more:
        cut = cut[: cut.rfind(" ")] if " " in cut[SNIPPET // 2 :] else cut
    return ("…" if before else "") + html.escape(cut) + ("…" if more or after else "")


RRF = 60  # reciprocal rank fusion: a hit's score is the sum of 1 / (RRF + its rank) in each list it's in


def _fuse(hits, meant, used):
    """One list from the keyword hits and those found by meaning, best first. A passage that holds a keyword hit adds
    its rank to that hit (the first of them) instead of standing on its own. Also the hits found by meaning alone."""
    if used == "semantic":
        for h in meant:
            h["match"], h["_score"] = "meaning", h["similarity"]
        return meant, meant
    for i, h in enumerate(hits):
        h["_score"], h["match"] = 1 / (RRF + i + 1), "words"
    lines, shown = {}, {}
    for h in hits:
        if h["source"] in ("said", "page"):
            lines.setdefault(h["recording"], []).append(h)
        elif h["source"] == "described":
            shown.setdefault((h["recording"], h.get("t0")), h)
    alone = []
    for j, m in enumerate(meant):
        if m["source"] == "described":
            same = shown.get((m["recording"], m.get("t0")))
        else:
            a, b = m["_span"]
            same = next((h for h in lines.get(m["recording"], []) if a <= (h.get("idx") or 0) <= b and h["match"] == "words"), None)
        if same:
            same["_score"] += 1 / (RRF + j + 1)
            same["match"], same["similarity"] = "both", m["similarity"]
        else:
            m["_score"], m["match"] = 1 / (RRF + j + 1), "meaning"
            alone.append(m)
    out = hits + alone
    out.sort(key=lambda r: (-r["_score"], r["recording"], r.get("idx") or 0))
    return out, alone


def _in_file(f, line):
    """Which supplementary file a line was found in, and which of its lines it is."""
    return {"file": f["id"], "file_role": f.get("role"), "file_label": f.get("label") or f.get("name"), "line": line}


def _matches(db, groups, table, fields, where_f, base):
    """Every row of `table` matching the groups (up to FACET_CAP + 1), with the phrases checked."""
    params = dict(base)
    phrased = any(g["phrases"] for g in groups)
    cols = fields + (", text" if phrased else "")
    rows = textindex.rows(db, table, groups, cols, where_f, params, FACET_CAP + 1)
    if rows is None and db.ready_fulltext():
        conds = []
        for k, g in enumerate(groups, 1):
            params[f"q{k}"] = " ".join(g["words"] + g["phrases"])
            conds.append(f"text @{k}@ $q{k}")
        try:
            rows = db.rows(f"SELECT {cols} FROM {table} WHERE ({' OR '.join(conds)}){where_f} LIMIT {FACET_CAP + 1}", **params)
        except Exception:  # noqa: BLE001 - fall back to a plain scan below
            rows = None
    if rows is None:
        conds = []
        for k, g in enumerate(groups, 1):
            ws = g["words"] + g["phrases"]
            conds.append("(" + " AND ".join(f"string::contains(string::lowercase(text), $w{k}_{j})" for j in range(len(ws))) + ")")
            params.update({f"w{k}_{j}": w.lower() for j, w in enumerate(ws)})
        rows = db.rows(f"SELECT {cols} FROM {table} WHERE ({' OR '.join(conds)}){where_f} LIMIT {FACET_CAP + 1}", **params)
    if phrased:
        rows = [
            r
            for r in rows
            if any(all(re.search(r"\b" + re.escape(p.lower()) + r"\b", r["text"].lower()) for p in g["phrases"]) for g in groups)
        ]
    return rows


def _facets(db, groups, where_f, base, screen, ns, spaces, recording, files=False, objects=False, described=False, meant=(), words=True):
    """How many matching moments are in each namespace, speaker, emotion and recording, most first; and the kinds of
    object seen in the recordings they're in, with how many of those recordings each is in. `meant`: the moments found
    by meaning alone, counted too; without `words`, only they are."""
    said = _matches(db, groups, "segment", "recording, space, speaker, emotion", where_f, base) if words else []
    said += [m for m in meant if m["source"] != "described"]
    seen = _matches(db, groups, "ocr_span", "recording, space", space_filter(ns, spaces, recording, base), base) if screen and words else []
    filed = (
        _matches(db, groups, "file_line", "recording, space", space_filter(ns, spaces, recording, base), base) if files and words else []
    )
    spotted = (
        _matches(db, groups, "object_track", "recording, space", space_filter(ns, spaces, recording, base), base)
        if objects and words
        else []
    )
    shown = (
        _matches(db, groups, "description", "recording, space", space_filter(ns, spaces, recording, base), base)
        if described and words
        else []
    )
    spotted += shown + [m for m in meant if m["source"] == "described"]
    rows = (said + seen + filed + spotted)[:FACET_CAP]
    partial = len(said) + len(seen) + len(filed) + len(spotted) > FACET_CAP
    by_space, by_rec = Counter(r["space"] for r in rows), Counter(r["recording"] for r in rows)
    by_spk = Counter(r["speaker"] for r in rows if r.get("speaker"))
    by_emo = Counter(r["emotion"] for r in rows if r.get("emotion") and r["emotion"] != "Unknown")
    top = lambda c: c.most_common()[:FACET_VALUES]  # noqa: E731
    space_names = store.space_names(db)
    spk = top(by_spk)
    people = {
        x["id"]: x
        for x in (
            db.rows(
                "SELECT record::id(id) AS id, name, label, space FROM speaker WHERE id IN $ids",
                ids=[store.R("speaker", i) for i, _ in spk],
            )
            if spk
            else []
        )
    }
    recs = top(by_rec)
    titles = (
        {
            x["id"]: x.get("title")
            for x in db.rows(
                "SELECT record::id(id) AS id, title FROM recording WHERE id IN $ids", ids=[store.R("recording", i) for i, _ in recs]
            )
        }
        if recs
        else {}
    )
    speakers = [
        {
            "id": i,
            "name": (people.get(i) or {}).get("name") or (people.get(i) or {}).get("label") or f"Speaker {i}",
            "namespace": space_names.get((people.get(i) or {}).get("space")),
            "count": n,
        }
        for i, n in spk
    ]
    order = lambda xs, key: sorted(xs, key=lambda x: (-x["count"], str(x[key]).casefold()))  # noqa: E731
    kinds: Counter[str] = Counter()
    if by_rec:
        for x in db.values(
            "SELECT VALUE objects FROM recording WHERE id IN $ids AND objects != NONE", ids=[store.R("recording", i) for i in by_rec]
        ):
            kinds.update(set(x or []))
    return {
        "moments": min(len(said) + len(seen) + len(filed) + len(spotted), FACET_CAP),
        "objects": order([{"name": k, "count": n} for k, n in top(kinds)], "name"),
        "partial": partial,
        "namespaces": order([{"name": space_names.get(k) or str(k), "count": n} for k, n in top(by_space)], "name"),
        "speakers": order(speakers, "name"),
        "emotions": order([{"name": k, "count": n} for k, n in top(by_emo)], "name"),
        "recordings": order([{"id": k, "title": titles.get(k), "count": n} for k, n in recs], "id"),
    }


def space_filter(ns, spaces, recording, params):
    parts = []
    if "sp" in params:
        parts.append("space = $sp")
    if spaces is not None:
        parts.append("(space IN $allowed OR recording IN $also)" if "also" in params else "space IN $allowed")
    if recording not in (None, ""):
        parts.append("recording = $rec")
    return (" AND " + " AND ".join(parts)) if parts else ""


def _screen(db, groups, where_f, cap, base):
    """Hits in text read off video frames (OCR), ranked alongside the transcript."""
    return _layer(db, groups, where_f, cap, base, "ocr_span", "frame, box", "screen")


def _described(db, groups, where_f, cap, base):
    """Hits in what a model that can see said a page or a shot shows, at that page or shot."""
    return _layer(db, groups, where_f, cap, base, "description", "frame, paged", "described")


def _file_lines(db, groups, where_f, cap, base):
    """Hits in the lines of supplementary files, ranked alongside the transcript."""
    return _layer(db, groups, where_f, cap, base, "file_line", "file, idx AS line", "file")


def _objects(db, groups, where_f, cap, base):
    """Hits in the kinds of object seen in videos, documents and images (person, car …), at where each is first seen."""
    return _layer(db, groups, where_f, cap, base, "object_track", "frame, box, paged", "object")


def _layer(db, groups, where_f, cap, base, table, extra, source):
    """Hits in another table of text with times (`extra` names the fields it adds), marked as from `source`."""
    params = {k: v for k, v in base.items() if k in ("m0", "m1", "sp", "allowed", "also", "rec") or k.startswith("q")}
    fields = f"record::id(id) AS id, recording, t0, t1, space, text, {extra}"
    rows = textindex.rows(db, table, groups, fields, where_f, params, cap, (M0, M1))
    if rows is None and db.ready_fulltext():
        conds = [f"text @{k}@ $q{k}" for k in range(1, len(groups) + 1)]
        sel = [f"search::highlight($m0, $m1, {k}) AS h{k}, search::score({k}) AS s{k}" for k in range(1, len(groups) + 1)]
        try:
            rows = db.rows(f"SELECT {fields}, {', '.join(sel)} FROM {table} WHERE ({' OR '.join(conds)}){where_f} LIMIT {cap}", **params)
        except Exception:  # noqa: BLE001
            rows = None
    if rows is None:
        rows = [
            r
            for r in db.rows(f"SELECT {fields} FROM {table} WHERE true{where_f} LIMIT 5000", **params)
            if any(all(w.lower() in r["text"].lower() for w in g["words"] + g["phrases"]) for g in groups)
        ]
        for r in rows:
            r["h1"], r["s1"] = _mark_plain(r["text"], [w for g in groups for w in g["words"] + g["phrases"]]), 1
    out = []
    for r in rows:
        low = r["text"].lower()
        if not any(all(re.search(r"\b" + re.escape(p.lower()) + r"\b", low) for p in g["phrases"]) for g in groups):
            continue
        marks = [r.get(f"h{k}") for k in range(1, len(groups) + 1) if isinstance(r.get(f"h{k}"), str)]
        r["_snip"] = snippet(max(marks, key=lambda h: h.count(M0)) if marks else r["text"])
        r["_score"] = sum(abs(r.get(f"s{k}") or 0) for k in range(1, len(groups) + 1))
        r["source"] = source
        out.append(r)
    return out


def terms(db, prefix, spaces, ns=None, limit=8):
    """Words said in these namespaces (or in `ns`) that start with `prefix`, the most said first:
    [{word, count, recordings}]. For "Try …" when someone types interp* (prefix search isn't supported)."""
    p = re.sub(r"[^\w'’.-]", "", (prefix or "").rstrip("*")).lower().strip(".-")
    if len(p) < 2:
        return []
    where, params = ["space IN $s", "string::starts_with(surface, $p)"], {"s": sorted(spaces), "p": p}
    if ns:
        try:
            params["ns"] = store.ns_id(db, ns, create=False)
        except KeyError:
            return []
        where.append("space = $ns")
    rows = db.rows(f"SELECT surface, n, recording FROM term WHERE {' AND '.join(where)}", **params)
    count, recs = Counter(), {}
    for r in rows:
        w = r.get("surface") or ""
        if " " in w or w == p:
            continue
        count[w] += r.get("n") or 0
        recs.setdefault(w, set()).add(r["recording"])
    best = sorted(count, key=lambda w: (-count[w], w))[:limit]
    return [{"word": w, "count": count[w], "recordings": len(recs[w])} for w in best]
