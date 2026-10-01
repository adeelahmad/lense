"""Entities: browse, inspect, explore and curate the people, organisations, products, places and topics found in
transcripts. An entity belongs to one namespace.

Curation survives re-analysis: merged names become aliases, moved or removed mentions become per-line overrides, and
hidden entities stay hidden. Merges can be undone. Callers check roles and write the audit log.
"""

from __future__ import annotations

import datetime as dt
import difflib
import math
import re
from collections import Counter, defaultdict, deque

from . import analyze, render, store

R = store.R
TYPES = {
    "PERSON": "Person",
    "ORG": "Organisation",
    "PRODUCT": "Product",
    "PLACE": "Place",
    "EVENT": "Event",
    "WORK": "Work",
    "TERM": "Topic",
    "DATE": "Date",
    "NUMBER": "Number",
}
QUIET = ("DATE", "NUMBER")  # extracted, but hidden unless a filter asks for them
FIELDS = "record::id(id) AS id, space, key, name, type, hidden, hidden_reason"


def _day(s):
    return (s or "")[:10]


def _months(n=12):
    t = dt.date.today().replace(day=1)
    out = []
    for _ in range(n):
        out.append(t.strftime("%Y-%m"))
        t = (t - dt.timedelta(days=1)).replace(day=1)
    return out[::-1]


def _entity(db, eid):
    e = db.one(f"SELECT {FIELDS} FROM $r", r=R("entity", int(eid)))
    if not e:
        raise KeyError(eid)
    return e


def aliases(db, eids):
    out = defaultdict(list)
    for a in db.rows("SELECT key, entity FROM entity_alias WHERE entity IN $e", e=list(eids)) if eids else []:
        out[a["entity"]].append(a["key"])
    return out


def _stats(db, spaces, speaker=None, recording=None, within=None):
    """Per entity: its mentions, the recordings and speakers they're in, their days, and whether one is in `speaker`'s
    lines or `recording`. `within` keeps to those recordings (what someone sees of a namespace)."""
    ms = db.rows("SELECT entity, recording, speaker FROM mentions WHERE space IN $s", s=sorted(spaces)) if spaces else []
    if within is not None:
        ms = [m for m in ms if m["recording"] in within]
    dates = (
        {
            r["id"]: _day(r.get("recorded_at"))
            for r in db.rows("SELECT record::id(id) AS id, recorded_at FROM recording WHERE space IN $s", s=sorted(spaces))
        }
        if spaces
        else {}
    )
    st = defaultdict(lambda: {"mentions": 0, "recordings": set(), "speakers": Counter(), "dates": [], "hit": False})
    for m in ms:
        x = st[m["entity"]]
        x["mentions"] += 1
        x["recordings"].add(m["recording"])
        x["dates"].append(dates.get(m["recording"], ""))
        if m.get("speaker"):
            x["speakers"][m["speaker"]] += 1
        if (speaker is None or m.get("speaker") == speaker) and (recording is None or m["recording"] == recording):
            x["hit"] = True
    return st


def list_entities(
    db,
    spaces,
    q=None,
    types=None,
    namespaces=None,
    speaker=None,
    recording=None,
    date_from=None,
    date_to=None,
    min_mentions=1,
    hidden=False,
    sort="mentions",
    limit=50,
    offset=0,
    group=False,
    within=None,
):
    """Entities of these namespaces (filtered and sorted); `within` counts only those recordings' mentions (someone who
    sees a namespace only through roles on some of its collections)."""
    names = store.space_names(db)
    sp = {s for s in spaces if not namespaces or names.get(s) in namespaces}
    st = _stats(db, sp, speaker, recording, within)
    ents = db.rows(f"SELECT {FIELDS} FROM entity WHERE space IN $s", s=sorted(sp)) if sp else []
    al = aliases(db, [e["id"] for e in ents])
    months, today = _months(), dt.date.today()
    recent, before = (today - dt.timedelta(days=90)).isoformat(), (today - dt.timedelta(days=180)).isoformat()
    ql = (q or "").strip().lower()
    rows = []
    for e in ents:
        s = st.get(e["id"])
        if not s or s["mentions"] < max(1, min_mentions) or ((speaker or recording) and not s["hit"]):
            continue
        if bool(e.get("hidden")) != bool(hidden) or (types and e["type"] not in types) or (not types and e["type"] in QUIET):
            continue
        ds = sorted(d for d in s["dates"] if d)
        if (date_from and (not ds or ds[-1] < date_from)) or (date_to and (not ds or ds[0] > date_to)):
            continue
        score = 0.0
        if ql:
            cands = [e["name"].lower(), e["key"]] + al.get(e["id"], [])
            score = max(
                (1.0 if ql in c else max((difflib.SequenceMatcher(None, ql, w).ratio() for w in c.split()), default=0)) for c in cands
            )
            if score < 0.75:
                continue
        per = Counter(d[:7] for d in ds)
        rows.append(
            {
                "id": e["id"],
                "name": e["name"],
                "type": e["type"],
                "type_label": TYPES.get(e["type"], e["type"].title()),
                "key": e["key"],
                "namespace": names.get(e["space"]),
                "aliases": al.get(e["id"], []),
                "mentions": s["mentions"],
                "recordings": len(s["recordings"]),
                "top_speakers": s["speakers"].most_common(3),
                "first": ds[0] if ds else None,
                "last": ds[-1] if ds else None,
                "spark": [per.get(m, 0) for m in months],
                "rising": sum(d >= recent for d in ds) - sum(before <= d < recent for d in ds),
                "match": round(score, 3),
                "hidden": bool(e.get("hidden")),
            }
        )
    spk_names = render.speaker_names(db, [sid for r in rows for sid, _ in r["top_speakers"]])
    for r in rows:
        r["top_speakers"] = [{"id": sid, "name": spk_names.get(sid, "?"), "mentions": n} for sid, n in r["top_speakers"]]
    if group:  # one row per name across namespaces
        g = {}
        for r in rows:
            x = g.setdefault(
                r["key"], {**r, "ids": [], "namespaces": [], "mentions": 0, "recordings": 0, "spark": [0] * len(months), "rising": 0}
            )
            x["ids"].append(r["id"])
            x["namespaces"].append(r["namespace"])
            x["mentions"] += r["mentions"]
            x["recordings"] += r["recordings"]
            x["rising"] += r["rising"]
            x["spark"] = [a + b for a, b in zip(x["spark"], r["spark"])]
            x["first"] = min(filter(None, [x["first"], r["first"]]), default=None)
            x["last"] = max(filter(None, [x["last"], r["last"]]), default=None)
        rows = list(g.values())
    order = {
        "mentions": lambda r: (-r["mentions"], r["name"].lower()),
        "recordings": lambda r: (-r["recordings"], -r["mentions"]),
        "recent": lambda r: (r["last"] or "") and "".join(chr(0x10FFFF - ord(c)) for c in r["last"]),
        "rising": lambda r: (-r["rising"], -r["mentions"]),
        "name": lambda r: r["name"].lower(),
    }
    rows.sort(key=order.get(sort, order["mentions"]))
    if ql and sort == "mentions":
        rows.sort(key=lambda r: -r["match"])
    return {
        "total": len(rows),
        "items": rows[offset : offset + limit],
        "months": months,
        "facets": {
            "types": Counter(r["type"] for r in rows),
            "namespaces": Counter(n for r in rows for n in (r.get("namespaces") or [r["namespace"]])),
        },
    }


def detail(db, eid, spaces):
    e = _entity(db, eid)
    if e["space"] not in spaces:
        raise KeyError(eid)
    names = store.space_names(db)
    s = _stats(db, {e["space"]}).get(e["id"]) or {"mentions": 0, "recordings": set(), "speakers": Counter(), "dates": []}
    ds = sorted(d for d in s["dates"] if d)
    links = []
    for ln in db.rows("SELECT a, b FROM entity_link WHERE a = $e OR b = $e", e=e["id"]):
        other = db.one(f"SELECT {FIELDS} FROM $r", r=R("entity", ln["b"] if ln["a"] == e["id"] else ln["a"]))
        if other and other["space"] in spaces:
            links.append({"id": other["id"], "name": other["name"], "namespace": names.get(other["space"])})
    same_name = [
        {"id": x["id"], "namespace": names.get(x["space"])}
        for x in db.rows(f"SELECT {FIELDS} FROM entity WHERE key = $k", k=e["key"])
        if x["id"] != e["id"] and x["space"] in spaces
    ]
    return {
        **e,
        "type_label": TYPES.get(e["type"], e["type"]),
        "namespace": names.get(e["space"]),
        "aliases": aliases(db, [e["id"]]).get(e["id"], []),
        "mentions": s["mentions"],
        "recordings": len(s["recordings"]),
        "speakers": len(s["speakers"]),
        "first": ds[0] if ds else None,
        "last": ds[-1] if ds else None,
        "links": links,
        "same_name_elsewhere": same_name,
        "hidden": bool(e.get("hidden")),
    }


def _mark(text, said):
    i = text.lower().find((said or "").lower())
    return [i, i + len(said)] if i >= 0 and said else None


def mentions(db, eid, spaces, speaker=None, recording=None, date_from=None, date_to=None, sort="newest", limit=50, offset=0):
    e = _entity(db, eid)
    if e["space"] not in spaces:
        raise KeyError(eid)
    ms = db.rows(
        "SELECT record::id(id) AS mention, record::id(in) AS segment, recording, speaker, text FROM mentions WHERE entity = $e", e=e["id"]
    )
    recs = (
        {
            r["id"]: r
            for r in db.rows(
                "SELECT record::id(id) AS id, title, recorded_at, space FROM recording WHERE id IN $ids",
                ids=[R("recording", i) for i in {m["recording"] for m in ms}],
            )
        }
        if ms
        else {}
    )
    ms = [
        m
        for m in ms
        if (speaker is None or m.get("speaker") == speaker)
        and (recording is None or m["recording"] == recording)
        and (not date_from or _day(recs.get(m["recording"], {}).get("recorded_at")) >= date_from)
        and (not date_to or _day(recs.get(m["recording"], {}).get("recorded_at")) <= date_to)
    ]
    per_rec = Counter(m["recording"] for m in ms)
    key = {
        "newest": lambda m: ("".join(chr(0x10FFFF - ord(c)) for c in recs.get(m["recording"], {}).get("recorded_at") or ""), m["segment"]),
        "oldest": lambda m: (recs.get(m["recording"], {}).get("recorded_at") or "", m["segment"]),
        "most": lambda m: (-per_rec[m["recording"]], m["segment"]),
    }
    ms.sort(key=key.get(sort, key["newest"]))
    page = ms[offset : offset + limit]
    segs = (
        {
            x["id"]: x
            for x in db.rows(
                "SELECT record::id(id) AS id, t0, t1, text FROM segment WHERE id IN $ids", ids=[R("segment", m["segment"]) for m in page]
            )
        }
        if page
        else {}
    )
    names, spaces_n = render.speaker_names(db, [m.get("speaker") for m in page]), store.space_names(db)
    out = []
    for m in page:
        sg, rec = segs.get(m["segment"], {}), recs.get(m["recording"], {})
        out.append(
            {
                "mention": m["mention"],
                "recording_id": m["recording"],
                "title": rec.get("title"),
                "namespace": spaces_n.get(rec.get("space")),
                "recorded_at": rec.get("recorded_at"),
                "segment": m["segment"],
                "idx": m["segment"] % store.SEG,
                "t0": sg.get("t0"),
                "time": store.tc(sg.get("t0")),
                "speaker_id": m.get("speaker"),
                "speaker": names.get(m.get("speaker")),
                "said": m["text"],
                "text": sg.get("text"),
                "highlight": _mark(sg.get("text") or "", m["text"]),
            }
        )
    return {"total": len(ms), "items": out}


def timeline(db, eids, spaces, by="month"):
    ents = [
        e
        for e in (db.rows(f"SELECT {FIELDS} FROM entity WHERE id IN $ids", ids=[R("entity", int(i)) for i in eids]) if eids else [])
        if e["space"] in spaces
    ]
    names = store.space_names(db)
    ms = db.rows("SELECT recording, space FROM mentions WHERE entity IN $e", e=[e["id"] for e in ents]) if ents else []
    dates = (
        {
            r["id"]: _day(r.get("recorded_at"))
            for r in db.rows(
                "SELECT record::id(id) AS id, recorded_at FROM recording WHERE id IN $ids",
                ids=[R("recording", i) for i in {m["recording"] for m in ms}],
            )
        }
        if ms
        else {}
    )
    series = defaultdict(Counter)
    for m in ms:
        d = dates.get(m["recording"])
        if d:
            period = (
                d[:7] if by == "month" else (dt.date.fromisoformat(d) - dt.timedelta(days=dt.date.fromisoformat(d).weekday())).isoformat()
            )
            series[names.get(m["space"])][period] += 1
    periods = sorted({p for c in series.values() for p in c})
    return {"by": by, "periods": periods, "series": {ns: [c.get(p, 0) for p in periods] for ns, c in series.items()}}


def connections(db, eid, spaces, limit=20):
    e = _entity(db, eid)
    if e["space"] not in spaces:
        raise KeyError(eid)
    mine = db.rows("SELECT record::id(in) AS segment, recording, speaker FROM mentions WHERE entity = $e", e=e["id"])
    segs = sorted({m["segment"] for m in mine})
    co = Counter()
    for m in (
        db.rows("SELECT entity FROM mentions WHERE in IN $s AND entity != $e", s=[R("segment", x) for x in segs], e=e["id"]) if segs else []
    ):
        co[m["entity"]] += 1
    totals = Counter(m["entity"] for m in db.rows("SELECT entity FROM mentions WHERE entity IN $e", e=list(co))) if co else Counter()
    info = {x["id"]: x for x in db.rows(f"SELECT {FIELDS} FROM entity WHERE id IN $ids", ids=[R("entity", i) for i in co])} if co else {}
    n = max(1, len(mine))
    ents = [
        {
            "id": i,
            "name": info[i]["name"],
            "type": info[i]["type"],
            "together": c,
            "strength": round(c / math.sqrt(n * max(1, totals[i])), 3),
        }
        for i, c in co.most_common()
        if i in info and not info[i].get("hidden") and info[i]["type"] not in QUIET
    ][:limit]
    spk = Counter(m["speaker"] for m in mine if m.get("speaker"))
    names = render.speaker_names(db, list(spk))
    recs = Counter(m["recording"] for m in mine)
    titles = (
        {
            r["id"]: r["title"]
            for r in db.rows("SELECT record::id(id) AS id, title FROM recording WHERE id IN $ids", ids=[R("recording", i) for i in recs])
        }
        if recs
        else {}
    )
    return {
        "entities": ents,
        "speakers": [{"id": s, "name": names.get(s, "?"), "mentions": c} for s, c in spk.most_common(limit)],
        "recordings": [{"id": r, "title": titles.get(r), "mentions": c} for r, c in recs.most_common(limit)],
    }


# ---------- graph exploration ----------
def _graph(db, spaces):
    """Adjacency over entities (e<id>) and speakers (s<id>), with typed, weighted edges."""
    ms = (
        db.rows("SELECT record::id(in) AS segment, entity, speaker, recording FROM mentions WHERE space IN $s", s=sorted(spaces))
        if spaces
        else []
    )
    ents = {e["id"]: e for e in db.rows(f"SELECT {FIELDS} FROM entity WHERE space IN $s", s=sorted(spaces))} if spaces else {}
    ok = {i for i, e in ents.items() if not e.get("hidden") and e["type"] not in QUIET}
    by_seg, w, kind = defaultdict(set), Counter(), {}

    def add(a, b, k, n=1):
        key = (a, b) if a < b else (b, a)
        w[key] += n
        kind[key] = k

    for m in ms:
        if m["entity"] in ok:
            by_seg[m["segment"]].add(f"e{m['entity']}")
            if m.get("speaker"):
                add(f"s{m['speaker']}", f"e{m['entity']}", "mentions")
    for group in by_seg.values():
        g = sorted(group)
        for i, a in enumerate(g):
            for b in g[i + 1 :]:
                add(a, b, "mentioned together")
    for ln in db.rows("SELECT a, b FROM entity_link"):
        if ln["a"] in ok and ln["b"] in ok:
            add(f"e{ln['a']}", f"e{ln['b']}", "same thing")
    for ln in db.rows("SELECT record::id(in) AS a, record::id(out) AS b FROM same_as"):
        add(f"s{ln['a']}", f"s{ln['b']}", "same person")
    adj = defaultdict(dict)
    for (a, b), n in w.items():
        adj[a][b] = adj[b][a] = (kind[(a, b)], n)
    return adj, ents, ok


def _label_nodes(db, ids, ents):
    spk = render.speaker_names(db, [int(i[1:]) for i in ids if i.startswith("s")])
    names = store.space_names(db)
    out = {}
    for i in ids:
        if i.startswith("e"):
            e = ents[int(i[1:])]
            out[i] = {"id": i, "kind": "entity", "label": e["name"], "type": e["type"], "namespace": names.get(e["space"])}
        else:
            out[i] = {"id": i, "kind": "speaker", "label": spk.get(int(i[1:]), "Speaker")}
    return out


def neighbourhood(db, focus, spaces, depth=1, types=None, kinds=None, min_weight=1, limit=100):
    adj, ents, _ = _graph(db, spaces)
    if focus not in adj and not (focus.startswith("e") and int(focus[1:]) in ents):
        raise KeyError(focus)
    seen, frontier = {focus: 0}, deque([focus])
    while frontier:
        n = frontier.popleft()
        if seen[n] >= depth:
            continue
        for m, (k, wt) in adj[n].items():
            if m in seen or wt < min_weight or (kinds and k not in kinds):
                continue
            if types and m.startswith("e") and ents[int(m[1:])]["type"] not in types:
                continue
            seen[m] = seen[n] + 1
            frontier.append(m)
    keep = sorted(seen, key=lambda x: (seen[x], -sum(v[1] for v in adj[x].values())))[:limit]
    ks = set(keep)
    nodes = _label_nodes(db, keep, ents)
    for x in keep:
        nodes[x].update(depth=seen[x], weight=sum(v[1] for v in adj[x].values()))
    edges = [
        {"a": a, "b": b, "kind": k, "weight": wt}
        for a in keep
        for b, (k, wt) in adj[a].items()
        if b in ks and a < b and wt >= min_weight and (not kinds or k in kinds)
    ]
    return {"focus": focus, "nodes": [nodes[x] for x in keep], "edges": edges, "truncated": len(seen) > limit}


def path(db, a, b, spaces, max_depth=5):
    """The shortest chain between two nodes, with the mentions that support each link."""
    adj, ents, _ = _graph(db, spaces)
    prev, frontier = {a: None}, deque([a])
    while frontier and b not in prev:
        n = frontier.popleft()
        for m in adj[n]:
            if m not in prev:
                prev[m] = n
                frontier.append(m)
    if b not in prev:
        return {"found": False, "nodes": [], "links": []}
    chain, x = [], b
    while x is not None:
        chain.append(x)
        x = prev[x]
    chain = chain[::-1]
    if len(chain) - 1 > max_depth:
        return {"found": False, "nodes": [], "links": []}
    nodes = _label_nodes(db, chain, ents)
    links = []
    for x, y in zip(chain, chain[1:]):
        k, wt = adj[x][y]
        links.append({"a": x, "b": y, "kind": k, "weight": wt, "evidence": _evidence(db, x, y)})
    return {"found": True, "nodes": [nodes[c] for c in chain], "links": links}


def _evidence(db, x, y, n=2):
    ents = [int(i[1:]) for i in (x, y) if i.startswith("e")]
    spk = [int(i[1:]) for i in (x, y) if i.startswith("s")]
    if len(ents) == 2:
        a = {m["segment"] for m in db.rows("SELECT record::id(in) AS segment FROM mentions WHERE entity = $e", e=ents[0])}
        b = {m["segment"] for m in db.rows("SELECT record::id(in) AS segment FROM mentions WHERE entity = $e", e=ents[1])}
        segs = sorted(a & b)[:n]
    elif ents and spk:
        segs = sorted(
            {
                m["segment"]
                for m in db.rows("SELECT record::id(in) AS segment FROM mentions WHERE entity = $e AND speaker = $s", e=ents[0], s=spk[0])
            }
        )[:n]
    else:
        return []
    rows = (
        db.rows("SELECT record::id(id) AS id, recording, t0, text FROM segment WHERE id IN $ids", ids=[R("segment", s) for s in segs])
        if segs
        else []
    )
    return [{"recording_id": r["recording"], "t0": r["t0"], "time": store.tc(r["t0"]), "text": r["text"]} for r in rows]


# ---------- merge suggestions ----------
def _squash(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _initials(s):
    words = re.findall(r"[A-Za-z][A-Za-z0-9]*", s)
    return "".join(w[0] for w in words).lower() if len(words) >= 2 else ""


def _soundex(s):
    s = re.sub(r"[^a-z]", "", s.lower())
    if not s:
        return ""
    codes = {
        **dict.fromkeys("bfpv", "1"),
        **dict.fromkeys("cgjkqsxz", "2"),
        **dict.fromkeys("dt", "3"),
        "l": "4",
        **dict.fromkeys("mn", "5"),
        "r": "6",
    }
    out, last = s[0], codes.get(s[0], "")
    for ch in s[1:]:
        c = codes.get(ch, "")
        if c and c != last:
            out += c
        if ch not in "hw":
            last = c
    return (out + "000")[:4]


SMALL = {"the", "a", "an", "of", "and", "for", "in", "on", "at", "to"}


def _pairs(ents):
    """Candidate (a, b, reason, confidence) within one namespace; cheap blocking instead of comparing everything."""
    found = {}

    def put(a, b, reason, conf):
        k = (min(a["id"], b["id"]), max(a["id"], b["id"]))
        if a["id"] != b["id"] and conf > found.get(k, (None, 0))[1]:
            found[k] = (reason, round(conf, 2))

    squash, acro, tokens, blocks = defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list)
    for e in ents:
        sq = _squash(e["name"])
        squash[sq].append(e)
        if _initials(e["name"]):
            acro[_initials(e["name"])].append(e)
        for t in set(re.findall(r"[a-z0-9]+", e["name"].lower())) - SMALL:
            tokens[t].append(e)
        blocks[sq[:2]].append(e)
    for group in squash.values():
        for i, a in enumerate(group):
            for b in group[i + 1 :]:
                put(a, b, "same letters", 0.95)
    for e in ents:
        sq = _squash(e["name"])
        if 2 <= len(sq) <= 6 and e["name"].replace(".", "").isupper():
            for b in acro.get(sq, []):
                put(e, b, "acronym", 0.85)
        words = [w for w in re.findall(r"[a-z0-9]+", e["name"].lower()) if w not in SMALL]
        if words:
            for b in tokens.get(words[0], []):
                bw = set(re.findall(r"[a-z0-9]+", b["name"].lower()))
                if b is not e and len(bw) > len(words) and set(words) <= bw and len(words[0]) > 2:
                    put(e, b, "contains", 0.7)
    for group in blocks.values():
        for i, a in enumerate(group):
            sa = _squash(a["name"])
            for b in group[i + 1 :]:
                sb = _squash(b["name"])
                if abs(len(sa) - len(sb)) > 3 or sa == sb:
                    continue
                r = difflib.SequenceMatcher(None, sa, sb).ratio()
                if r >= 0.86:
                    put(a, b, "spelling", r)
                elif r >= 0.72 and _soundex(sa) == _soundex(sb):
                    put(a, b, "sounds alike", 0.8)
    return found


def suggestions(db, spaces, eid=None, limit=50):
    ents = (
        [
            e
            for e in db.rows(f"SELECT {FIELDS} FROM entity WHERE space IN $s", s=sorted(spaces))
            if not e.get("hidden") and e["type"] not in QUIET
        ]
        if spaces
        else []
    )
    counts = (
        Counter(m["entity"] for m in db.rows("SELECT entity FROM mentions WHERE space IN $s", s=sorted(spaces))) if spaces else Counter()
    )
    ents = [e for e in ents if counts[e["id"]]]
    distinct = {tuple(sorted((d["a"], d["b"]))) for d in db.rows("SELECT a, b FROM entity_distinct")}
    by_space, out = defaultdict(list), []
    for e in ents:
        by_space[e["space"]].append(e)
    info = {e["id"]: e for e in ents}
    for group in by_space.values():
        for (a, b), (reason, conf) in _pairs(group).items():
            if (a, b) in distinct or (eid and eid not in (a, b)):
                continue
            out.append({"a": a, "b": b, "reason": reason, "confidence": conf, "mentions": counts[a] + counts[b]})
    out.sort(key=lambda x: (-x["confidence"], -x["mentions"]))
    names = store.space_names(db)
    res = []
    for s in out[:limit]:
        res.append(
            {
                "reason": s["reason"],
                "confidence": s["confidence"],
                "namespace": names.get(info[s["a"]]["space"]),
                **{
                    side: {
                        "id": info[i]["id"],
                        "name": info[i]["name"],
                        "type": info[i]["type"],
                        "mentions": counts[i],
                        "samples": mentions(db, i, spaces, limit=2)["items"],
                    }
                    for side, i in (("a", s["a"]), ("b", s["b"]))
                },
            }
        )
    return res


# ---------- curation ----------
def not_same(db, a, b):
    lo, hi = sorted((int(a), int(b)))
    db.q("UPSERT $r CONTENT $d", r=R("entity_distinct", f"{lo}-{hi}"), d={"a": lo, "b": hi, "at": store.now()})


def retype(db, eids, typ):
    if typ not in TYPES:
        raise ValueError(f"type is one of {', '.join(TYPES)}")
    db.q("UPDATE $ids SET type = $t", ids=[R("entity", int(i)) for i in eids], t=typ)


def hide(db, eid, hidden=True, reason=None):
    _entity(db, eid)
    db.q(
        "UPDATE $r SET hidden = $h, hidden_reason = $why", r=R("entity", int(eid)), h=bool(hidden), why=(reason or None) if hidden else None
    )


def _add_alias(db, space, key, eid):
    if key:
        db.q("UPSERT $r CONTENT $d", r=R("entity_alias", f"{space}:{key}"), d={"space": space, "key": key, "entity": eid})


def rename(db, eid, name, keep_alias=True, correct=False, dry_run=False):
    """Rename; optionally rewrite the words in the transcripts too. dry_run shows every line that would change."""
    e = _entity(db, eid)
    new = re.sub(r"\s+", " ", (name or "")).strip()
    if not new:
        raise ValueError("give the entity a name")
    new_key = analyze.ent_key(new)
    clash = db.one("SELECT record::id(id) AS id FROM entity WHERE ekey = $k", k=f"{e['space']}:{new_key}")
    if clash and clash["id"] != e["id"]:
        raise ValueError(f"another entity in this namespace is already called {new}; merge them instead")
    lines = []
    if correct:
        ms = db.rows("SELECT record::id(in) AS segment, recording, text FROM mentions WHERE entity = $e", e=e["id"])
        said = defaultdict(set)
        for m in ms:
            said[m["segment"]].add(m["text"])
        segs = (
            {
                s["id"]: s
                for s in db.rows(
                    "SELECT record::id(id) AS id, recording, idx, text FROM segment WHERE id IN $ids", ids=[R("segment", x) for x in said]
                )
            }
            if said
            else {}
        )
        for sid, words in said.items():
            s = segs.get(sid)
            if not s:
                continue
            after = s["text"]
            for w in sorted(words, key=len, reverse=True):
                if w != new:
                    after = re.sub(r"(?<!\w)" + re.escape(w) + r"(?!\w)", new, after, flags=re.I)
            if after != s["text"]:
                lines.append({"segment": sid, "recording": s["recording"], "idx": s["idx"], "before": s["text"], "after": after})
    if dry_run:
        return {"name": new, "lines": len(lines), "recordings": len({x["recording"] for x in lines}), "preview": lines[:200]}
    if keep_alias and e["key"] != new_key:
        _add_alias(db, e["space"], e["key"], e["id"])
    db.q("UPDATE $r SET name = $n, key = $k, ekey = $ek", r=R("entity", e["id"]), n=new, k=new_key, ek=f"{e['space']}:{new_key}")
    db.q("DELETE entity_alias WHERE space = $s AND key = $k", s=e["space"], k=new_key)
    for x in lines:
        db.q("UPDATE $s SET text = $t", s=R("segment", x["segment"]), t=x["after"])
        db.q(
            "CREATE segment_edit CONTENT $d",
            d={
                "recording": x["recording"],
                "idx": x["idx"],
                "before": {"text": x["before"]},
                "after": {"text": x["after"]},
                "by": "entity rename",
                "at": store.now(),
            },
        )
    return {"name": new, "lines": len(lines), "recordings": sorted({x["recording"] for x in lines})}


def merge(db, keep, others, user=None):
    """Fold other entities in the same namespace into one. Returns a merge id for undo."""
    k = _entity(db, keep)
    snaps = []
    for o in others:
        o = _entity(db, o)
        if o["id"] == k["id"]:
            continue
        if o["space"] != k["space"]:
            raise ValueError("entities in different namespaces are linked, not merged")
        ms = db.rows("SELECT record::id(in) AS segment, recording, space, speaker, text FROM mentions WHERE entity = $e", e=o["id"])
        snaps.append(
            {
                "entity": o,
                "aliases": aliases(db, [o["id"]]).get(o["id"], []),
                "mentions": ms,
                "links": db.rows("SELECT a, b FROM entity_link WHERE a = $e OR b = $e", e=o["id"]),
                "overrides": db.rows("SELECT record::id(id) AS id FROM entity_override WHERE target = $e", e=o["id"]),
            }
        )
    if not snaps:
        raise ValueError("pick at least one other entity")
    for sn in snaps:
        o = sn["entity"]
        rows = [
            store.clean(
                {
                    "in": R("segment", m["segment"]),
                    "out": R("entity", k["id"]),
                    "recording": m["recording"],
                    "space": m["space"],
                    "entity": k["id"],
                    "speaker": m.get("speaker"),
                    "text": m["text"],
                }
            )
            for m in sn["mentions"]
        ]
        stmts = ["DELETE mentions WHERE entity = $o"] + (["INSERT RELATION INTO mentions $rows"] if rows else []) + ["DELETE $oref"]
        db.run(stmts, o=o["id"], oref=R("entity", o["id"]), rows=rows)
        for key in [o["key"]] + sn["aliases"]:
            _add_alias(db, k["space"], key, k["id"])
        db.q("UPDATE entity_override SET target = $k WHERE target = $o", k=k["id"], o=o["id"])
        for ln in sn["links"]:
            other = ln["b"] if ln["a"] == o["id"] else ln["a"]
            db.q("DELETE entity_link WHERE (a = $x AND b = $y) OR (a = $y AND b = $x)", x=o["id"], y=other)
            if other != k["id"]:
                link(db, k["id"], other, check_spaces=False)
    mid = db.next_id("entity_merge")
    db.q(
        "CREATE $r CONTENT $d",
        r=R("entity_merge", mid),
        d={"keep": k["id"], "space": k["space"], "snapshots": snaps, "by": user, "at": store.now(), "undone": False},
    )
    return mid


def undo_merge(db, mid):
    m = db.one("SELECT * FROM $r", r=R("entity_merge", int(mid)))
    if not m or m.get("undone"):
        raise ValueError("nothing to undo")
    keep = m["keep"]
    for sn in m["snapshots"]:
        o = sn["entity"]
        db.q(
            "CREATE $r CONTENT $d",
            r=R("entity", o["id"]),
            d=store.clean(
                {
                    "space": o["space"],
                    "key": o["key"],
                    "ekey": f"{o['space']}:{o['key']}",
                    "name": o["name"],
                    "type": o["type"],
                    "hidden": o.get("hidden"),
                }
            ),
        )
        for key in [o["key"]] + sn["aliases"]:
            db.q("DELETE entity_alias WHERE space = $s AND key = $k AND entity = $e", s=o["space"], k=key, e=keep)
        for key in sn["aliases"]:
            _add_alias(db, o["space"], key, o["id"])
        segs = [R("segment", x["segment"]) for x in sn["mentions"]]
        alive = set(db.values("SELECT VALUE record::id(id) FROM segment WHERE id IN $s", s=segs)) if segs else set()
        mentions = [x for x in sn["mentions"] if x["segment"] in alive]  # a deleted recording's are gone
        for x in mentions:
            db.q("DELETE mentions WHERE entity = $k AND in = $s AND text = $t", k=keep, s=R("segment", x["segment"]), t=x["text"])
        rows = [
            store.clean(
                {
                    "in": R("segment", x["segment"]),
                    "out": R("entity", o["id"]),
                    "recording": x["recording"],
                    "space": x["space"],
                    "entity": o["id"],
                    "speaker": x.get("speaker"),
                    "text": x["text"],
                }
            )
            for x in mentions
        ]
        if rows:
            db.run(["INSERT RELATION INTO mentions $rows"], rows=rows)
        if sn["overrides"]:
            db.q("UPDATE $ids SET target = $o", ids=[R("entity_override", x["id"]) for x in sn["overrides"]], o=o["id"])
        for ln in sn["links"]:
            link(db, ln["a"], ln["b"], check_spaces=False)
    db.q("UPDATE $r SET undone = true", r=R("entity_merge", int(mid)))


def merges(db, spaces):
    return [
        {
            "id": r["id"],
            "keep": r["keep"],
            "names": [s["entity"]["name"] for s in r.get("snapshots") or []],
            "by": r.get("by"),
            "at": r.get("at"),
            "undone": r.get("undone"),
        }
        for r in db.rows(
            "SELECT record::id(id) AS id, keep, space, snapshots, by, at, undone FROM entity_merge WHERE space IN $s "
            "ORDER BY at DESC LIMIT 50",
            s=sorted(spaces),
        )
    ]


def move_mention(db, mention, target=None, new_name=None, new_type="TERM", remove=False):
    """Point one mention at another entity (or a new one), or say it isn't an entity. Survives re-analysis."""
    m = db.one("SELECT record::id(in) AS segment, recording, space, speaker, text, entity FROM $r", r=R("mentions", mention))
    if not m:
        raise KeyError(mention)
    key = analyze.ent_key(m["text"])
    tid = 0
    if not remove:
        if new_name:
            nk = analyze.ent_key(new_name)
            row = db.one("SELECT record::id(id) AS id FROM entity WHERE ekey = $k", k=f"{m['space']}:{nk}")
            if row:
                tid = row["id"]
            else:
                if new_type not in TYPES:
                    raise ValueError(f"type is one of {', '.join(TYPES)}")
                tid = db.next_id("entity")
                db.q(
                    "CREATE $r CONTENT $d",
                    r=R("entity", tid),
                    d={"space": m["space"], "key": nk, "ekey": f"{m['space']}:{nk}", "name": new_name.strip(), "type": new_type},
                )
        else:
            t = _entity(db, target)
            if t["space"] != m["space"]:
                raise ValueError("a mention can only move to an entity in the same namespace")
            tid = t["id"]
    db.q("DELETE $r", r=R("mentions", mention))
    if tid:
        db.q(
            "RELATE $a->mentions->$b CONTENT $d",
            a=R("segment", m["segment"]),
            b=R("entity", tid),
            d=store.clean(
                {"recording": m["recording"], "space": m["space"], "entity": tid, "speaker": m.get("speaker"), "text": m["text"]}
            ),
        )
    db.q(
        "UPSERT $r CONTENT $d",
        r=R("entity_override", f"{m['segment']}:{key}"),
        d={"segment": m["segment"], "key": key, "recording": m["recording"], "space": m["space"], "target": tid},
    )
    return tid


def link(db, a, b, check_spaces=True):
    x, y = _entity(db, a), _entity(db, b)
    if check_spaces and x["space"] == y["space"]:
        raise ValueError("these are in the same namespace: merge them instead")
    lo, hi = sorted((x["id"], y["id"]))
    db.q("UPSERT $r CONTENT $d", r=R("entity_link", f"{lo}-{hi}"), d={"a": lo, "b": hi})


def unlink(db, a, b):
    lo, hi = sorted((int(a), int(b)))
    db.q("DELETE $r", r=R("entity_link", f"{lo}-{hi}"))
