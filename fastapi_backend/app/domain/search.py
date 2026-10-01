"""Full-text search over every segment through SurrealDB's BM25 index, with highlighted snippets.

Words are ANDed and matched after English stemming (exploit finds exploits and exploiting),
"quoted phrases" must appear as written, and OR separates alternatives.
"""

from __future__ import annotations

import html
import re
from collections import Counter

from . import store

M0, M1 = "\x02", "\x03"
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
):
    """Transcript lines (and, unless screen is false, text on screen in videos; with files, the lines of supplementary
    transcripts, captions, translations and indexes) matching q. spaces limits the search to namespaces someone may
    read, and `also` adds recordings they may read beyond those (in collections they were given a role on); recordings
    limits it to a set of recordings (such as the transcripts a visitor may read). With facets, also how many of all the
    matching moments (up to FACET_CAP) are in each namespace, speaker, emotion and recording."""
    groups = parse_query(q)
    empty = {"q": q, "query": "", "total": 0, "capped": False, "hits": []}
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
    fields = "record::id(id) AS id, recording, idx, t0, t1, emotion, speaker, space, text"
    rows = None
    if db.ready_fulltext():
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
    for h in hits:
        h["source"] = "said"
    if screen and not speaker and not emotion:  # text shown on screen in videos has no speaker or emotion
        hits += _screen(db, groups, space_filter(ns, spaces, recording, params), cap, params)
    if files and not speaker and not emotion:  # nor do the lines of files (a speaker there is just a label)
        hits += _file_lines(db, groups, space_filter(ns, spaces, recording, params), cap, params)
    hits.sort(key=lambda r: (-r["_score"], r["recording"], r.get("idx") or 0))
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
                if h["source"] == "screen"
                else {}
            ),
            **(_in_file(in_files.get(h["file"]) or {"id": h["file"]}, h.get("line")) if h["source"] == "file" else {}),
        }
        for h in page
    ]
    res = {"q": q, "query": describe(groups), "total": len(hits), "capped": len(rows) >= cap, "hits": out}
    if facets:
        alone = not speaker and not emotion
        res["facets"] = _facets(db, groups, where_f, base_params, screen and alone, ns, spaces, recording, files and alone)
    return res


def _in_file(f, line):
    """Which supplementary file a line was found in, and which of its lines it is."""
    return {"file": f["id"], "file_role": f.get("role"), "file_label": f.get("label") or f.get("name"), "line": line}


def _matches(db, groups, table, fields, where_f, base):
    """Every row of `table` matching the groups (up to FACET_CAP + 1), with the phrases checked."""
    params = dict(base)
    phrased = any(g["phrases"] for g in groups)
    cols = fields + (", text" if phrased else "")
    rows = None
    if db.ready_fulltext():
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


def _facets(db, groups, where_f, base, screen, ns, spaces, recording, files=False):
    """How many matching moments are in each namespace, speaker, emotion and recording, most first."""
    said = _matches(db, groups, "segment", "recording, space, speaker, emotion", where_f, base)
    seen = _matches(db, groups, "ocr_span", "recording, space", space_filter(ns, spaces, recording, base), base) if screen else []
    filed = _matches(db, groups, "file_line", "recording, space", space_filter(ns, spaces, recording, base), base) if files else []
    rows = (said + seen + filed)[:FACET_CAP]
    partial = len(said) + len(seen) + len(filed) > FACET_CAP
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
    return {
        "moments": min(len(said) + len(seen) + len(filed), FACET_CAP),
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


def _file_lines(db, groups, where_f, cap, base):
    """Hits in the lines of supplementary files, ranked alongside the transcript."""
    return _layer(db, groups, where_f, cap, base, "file_line", "file, idx AS line", "file")


def _layer(db, groups, where_f, cap, base, table, extra, source):
    """Hits in another table of text with times (`extra` names the fields it adds), marked as from `source`."""
    params = {k: v for k, v in base.items() if k in ("m0", "m1", "sp", "allowed", "also", "rec") or k.startswith("q")}
    fields = f"record::id(id) AS id, recording, t0, t1, space, text, {extra}"
    rows = None
    if db.ready_fulltext():
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
