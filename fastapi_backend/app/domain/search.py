"""Full-text search over every segment through SurrealDB's BM25 index, with highlighted snippets.

Words are ANDed and matched after English stemming (exploit finds exploits and exploiting),
"quoted phrases" must appear as written, and OR separates alternatives.
"""
from __future__ import annotations

import html
import re

from . import store

M0, M1 = "\x02", "\x03"


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


def search(db, q, ns=None, speaker=None, emotion=None, recording=None, limit=50, offset=0, spaces=None):
    groups = parse_query(q)
    empty = {"q": q, "query": "", "total": 0, "capped": False, "hits": []}
    if not groups:
        return empty
    filt, params = [], {"m0": M0, "m1": M1}
    if ns:
        try:
            params["sp"] = store.ns_id(db, ns, create=False)
        except KeyError:
            return empty
        filt.append("space = $sp")
    if spaces is not None:  # only namespaces this person may read
        filt.append("space IN $allowed")
        params["allowed"] = sorted(spaces)
    for cond, key, val in (("speaker = $spk", "spk", speaker), ("emotion = $emo", "emo", emotion), ("recording = $rec", "rec", recording)):
        if val not in (None, ""):
            filt.append(cond)
            params[key] = int(val) if key in ("spk", "rec") else val
    where_f = (" AND " + " AND ".join(filt)) if filt else ""
    cap = min(1000, (offset + limit) * 3 + 50)
    fields = "record::id(id) AS id, recording, idx, t0, t1, emotion, speaker, space, text"
    rows = None
    if getattr(db, "fulltext", None):
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
    if not speaker and not emotion:  # text shown on screen in videos has no speaker or emotion
        hits += _screen(db, groups, space_filter(ns, spaces, recording, params), cap, params)
    hits.sort(key=lambda r: (-r["_score"], r["recording"], r.get("idx") or 0))
    page = hits[offset:offset + limit]
    recs = {x["id"]: x for x in db.rows("SELECT record::id(id) AS id, title, recorded_at FROM recording WHERE id IN $ids",
                                        ids=[store.R("recording", i) for i in {h["recording"] for h in page}])} if page else {}
    spk = {x["id"]: x.get("name") or x["label"] for x in db.rows("SELECT record::id(id) AS id, name, label FROM speaker WHERE id IN $ids",
                                                                 ids=[store.R("speaker", i) for i in {h["speaker"] for h in page if h.get("speaker")}])} if page else {}
    spaces = store.space_names(db) if page else {}
    out = [{"id": h["id"], "recording_id": h["recording"], "idx": h.get("idx"), "t0": h["t0"], "t1": h["t1"], "emotion": h.get("emotion"),
            "speaker_id": h.get("speaker"), "speaker": spk.get(h.get("speaker")), "title": recs.get(h["recording"], {}).get("title"),
            "recorded_at": recs.get(h["recording"], {}).get("recorded_at"), "namespace": spaces.get(h["space"]), "snippet": h["_snip"], "source": h["source"],
            **({"frame": f"{store.API}/recordings/{h['recording']}/frames/{h['frame']}" if h.get("frame") else None, "box": h.get("box")} if h["source"] == "screen" else {})}
           for h in page]
    return {"q": q, "query": describe(groups), "total": len(hits), "capped": len(rows) >= cap, "hits": out}


def space_filter(ns, spaces, recording, params):
    parts = []
    if "sp" in params:
        parts.append("space = $sp")
    if spaces is not None:
        parts.append("space IN $allowed")
    if recording not in (None, ""):
        parts.append("recording = $rec")
    return (" AND " + " AND ".join(parts)) if parts else ""


def _screen(db, groups, where_f, cap, base):
    """Hits in text read off video frames (OCR), ranked alongside the transcript."""
    params = {k: v for k, v in base.items() if k in ("m0", "m1", "sp", "allowed", "rec") or k.startswith("q")}
    rows = None
    if getattr(db, "fulltext", None):
        conds = [f"text @{k}@ $q{k}" for k in range(1, len(groups) + 1)]
        sel = [f"search::highlight($m0, $m1, {k}) AS h{k}, search::score({k}) AS s{k}" for k in range(1, len(groups) + 1)]
        try:
            rows = db.rows(f"SELECT record::id(id) AS id, recording, t0, t1, space, text, frame, box, {', '.join(sel)} FROM ocr_span "
                           f"WHERE ({' OR '.join(conds)}){where_f} LIMIT {cap}", **params)
        except Exception:  # noqa: BLE001
            rows = None
    if rows is None:
        rows = [r for r in db.rows(f"SELECT record::id(id) AS id, recording, t0, t1, space, text, frame, box FROM ocr_span WHERE true{where_f} LIMIT 5000", **params)
                if any(all(w.lower() in r["text"].lower() for w in g["words"] + g["phrases"]) for g in groups)]
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
        r["source"] = "screen"
        out.append(r)
    return out
