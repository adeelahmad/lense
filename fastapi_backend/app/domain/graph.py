"""Knowledge graph of speakers and named things, per namespace or across shared namespaces."""

from __future__ import annotations

import math
from collections import Counter, defaultdict

import numpy as np

from . import speakers as spk

GRAPH_TYPES = ("PERSON", "ORG", "PRODUCT", "PLACE", "TERM", "EVENT", "WORK")
TYPE_COLORS = {
    "PERSON": "#A23B5B",
    "ORG": "#2F6690",
    "PRODUCT": "#C2571A",
    "PLACE": "#5B7F2B",
    "TERM": "#6B7A89",
    "EVENT": "#7A4E9A",
    "WORK": "#8C6D1F",
}


def scope_namespaces(db, scope, allowed=None):
    if allowed is not None:
        return [x for x in scope_namespaces(db, scope) if x[0] in allowed]
    if scope and scope.startswith("ns:"):
        row = db.one("SELECT record::id(id) AS id, name FROM space WHERE name = $n LIMIT 1", n=scope[3:])
        if not row:
            raise KeyError(scope)
        return [(row["id"], row["name"])]
    return [(r["id"], r["name"]) for r in db.rows("SELECT record::id(id) AS id, name FROM space WHERE graph = 'shared' ORDER BY name")]


def build(db, cfg, scope="global", allowed=None):
    """'ns:<name>' is one namespace; 'global' is every namespace whose graph is shared.

    Speaker nodes stay namespace-scoped. In the global graph, named things with the same name in
    different namespaces become one node, which is what connects namespaces; declared speaker links
    and (optionally) likely voice matches add same-person edges without merging anyone.
    """
    nss = scope_namespaces(db, scope, allowed)
    names = dict(nss)
    nids = list(names)
    merged = not (scope or "").startswith("ns:")
    if not nids:
        return {"scope": scope, "namespaces": [], "nodes": [], "edges": []}
    talk = {
        r["speaker"]: r["talk"]
        for r in db.rows("SELECT speaker, math::sum(dur) AS talk FROM segment WHERE space IN $s AND speaker > 0 GROUP BY speaker", s=nids)
    }
    pairs = db.rows("SELECT speaker, recording FROM segment WHERE space IN $s AND speaker > 0 GROUP BY speaker, recording", s=nids)
    recs_per = Counter(p["speaker"] for p in pairs)
    nodes = {}
    for r in db.rows("SELECT record::id(id) AS id, space, name, label FROM speaker WHERE space IN $s", s=nids):
        if r["id"] in talk:
            nodes[f"s{r['id']}"] = {
                "id": f"s{r['id']}",
                "kind": "speaker",
                "label": r.get("name") or r["label"],
                "ns": [names[r["space"]]],
                "weight": round(talk[r["id"]] / 60000, 2),
                "recordings": recs_per[r["id"]],
                "refs": [r["id"]],
            }
    counts = {r["entity"]: r["n"] for r in db.rows("SELECT entity, count() AS n FROM mentions WHERE space IN $s GROUP BY entity", s=nids)}
    shown = {}
    for r in db.rows("SELECT entity, text, count() AS n FROM mentions WHERE space IN $s GROUP BY entity, text", s=nids):
        cur = shown.get(r["entity"])
        if not cur or (r["n"], len(r["text"])) > cur[1]:
            shown[r["entity"]] = (r["text"], (r["n"], len(r["text"])))
    agg, node_of = {}, {}
    for r in db.rows(
        "SELECT record::id(id) AS id, space, key, type FROM entity WHERE space IN $s AND type IN $t AND hidden != true",
        s=nids,
        t=list(GRAPH_TYPES),
    ):
        if not counts.get(r["id"]):
            continue
        key = f"e:{r['key']}" if merged else f"e{r['id']}"
        node_of[r["id"]] = key
        a = agg.setdefault(
            key,
            {
                "id": key,
                "kind": "entity",
                "label": shown.get(r["id"], (r["key"],))[0],
                "type": r["type"],
                "ns": set(),
                "weight": 0,
                "refs": [],
            },
        )
        a["weight"] += counts[r["id"]]
        a["ns"].add(names[r["space"]])
        a["refs"].append(r["id"])
    for a in sorted(agg.values(), key=lambda x: (-x["weight"], x["label"]))[: max(10, cfg["graph"]["max_nodes"] - len(nodes))]:
        a["ns"] = sorted(a["ns"])
        nodes[a["id"]] = a
    w, kind = Counter(), {}

    def edge(a, b, k):
        key = (a, b) if a < b else (b, a)
        w[key] += 1
        kind.setdefault(key, k)

    together = defaultdict(set)
    for p in pairs:
        together[p["recording"]].add(f"s{p['speaker']}")
    for group in together.values():
        g = sorted(group)
        for i in range(len(g)):
            for j in range(i + 1, len(g)):
                edge(g[i], g[j], "together")
    seg_ents, seg_spk = defaultdict(set), {}
    for r in db.rows("SELECT record::id(in) AS seg, entity, speaker FROM mentions WHERE space IN $s", s=nids):
        n = node_of.get(r["entity"])
        if n in nodes:
            seg_ents[r["seg"]].add(n)
            seg_spk[r["seg"]] = r.get("speaker")
    for sid, es in seg_ents.items():
        who = f"s{seg_spk[sid]}" if seg_spk.get(sid) else None
        es = sorted(es)
        for i, e in enumerate(es):
            if who in nodes:
                edge(who, e, "mentions")
            for f in es[i + 1 :]:
                edge(e, f, "mentioned together")
    for ln in db.rows("SELECT record::id(in) AS a, record::id(out) AS b FROM same_as"):
        a, b = f"s{ln['a']}", f"s{ln['b']}"
        if a in nodes and b in nodes:
            edge(a, b, "same person")
            kind[tuple(sorted((a, b)))] = "same person"
    if merged and cfg["speakers"].get("cross_namespace") == "suggest":
        for a, b, _ in spk.cross_namespace_matches(db, cfg, nids):
            key = tuple(sorted((f"s{a}", f"s{b}")))
            if key not in kind and key[0] in nodes and key[1] in nodes:
                edge(key[0], key[1], "maybe the same voice")
    always = {"together", "same person", "maybe the same voice"}
    edges = [
        {"a": a, "b": b, "w": n, "kind": kind[(a, b)]}
        for (a, b), n in w.items()
        if a in nodes and b in nodes and (n >= cfg["graph"]["min_edge_weight"] or kind[(a, b)] in always)
    ]
    linked = {e["a"] for e in edges} | {e["b"] for e in edges}
    out = [v for k, v in nodes.items() if v["kind"] == "speaker" or k in linked]
    layout(out, edges)
    return {"scope": scope, "namespaces": [n for _, n in nss], "nodes": out, "edges": edges}


def layout(nodes, edges, iters=260, seed=7):
    """Fruchterman-Reingold with a little gravity; deterministic, positions in -1..1."""
    n = len(nodes)
    if not n:
        return
    idx = {v["id"]: i for i, v in enumerate(nodes)}
    pos = np.random.default_rng(seed).uniform(-1, 1, size=(n, 2))
    k = 1.8 / math.sqrt(n)
    E = np.array([(idx[e["a"]], idx[e["b"]]) for e in edges], dtype=int).reshape(-1, 2)
    W = np.log1p(np.array([e["w"] for e in edges], dtype=float)) if edges else np.zeros(0)
    t = 0.25
    for _ in range(iters):
        d = pos[:, None, :] - pos[None, :, :]
        dist2 = np.square(d).sum(axis=2) + 1e-4
        disp = (d * (k * k / dist2)[..., None]).sum(axis=1)
        if len(E):
            dd = pos[E[:, 0]] - pos[E[:, 1]]
            dl = np.linalg.norm(dd, axis=1) + 1e-6
            f = dd * (dl / k * (0.4 + W / (W.max() + 1e-9)))[:, None]
            np.add.at(disp, E[:, 0], -f)
            np.add.at(disp, E[:, 1], f)
        disp -= pos * 0.08 * math.sqrt(n)
        ln = np.linalg.norm(disp, axis=1) + 1e-9
        pos += disp / ln[:, None] * np.minimum(ln, t)[:, None]
        t = max(0.004, t * 0.985)
    pos -= pos.mean(axis=0)
    pos /= np.abs(pos).max() or 1
    for v, (x, y) in zip(nodes, pos):
        v["x"], v["y"] = round(float(x), 4), round(float(y), 4)
