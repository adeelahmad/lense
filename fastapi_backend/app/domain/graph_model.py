"""The archive as a property graph: what the graph explorer walks and graph queries (cypher.py) run over.

Nodes, each with labels and properties (every node has `id`, `name` and `namespace`):

- `n<id>` Namespace {shared}
- `c<id>` Collection
- `r<id>` Recording {title, date, media}
- `s<id>` Speaker {seconds: talk time}
- `e<id>` Entity {type, key, mentions}, with its type as a second label (Person, Organisation, Product, Place, Event,
  Work, Topic, or a namespace's own type). In the global scope, entities with the same name in different namespaces
  are one node, `e:<key>`, as in the overview graph (graph.py); `ids` lists the entities behind it.

Relationships, all directed (a query may ignore the direction):

- CONTAINS: Namespace -> Collection -> Collection -> Recording
- HAS_SPEAKER: Recording -> Speaker {seconds}
- MENTIONS: Recording -> Entity {count}
- SAID: Speaker -> Entity {count}
- MENTIONED_WITH: Entity -> Entity, said in the same line {count}
- SPOKE_WITH: Speaker -> Speaker, in the same recording {recordings}
- SAME_AS: Speaker -> Speaker (someone said they are the same person)
- SAME_THING: Entity -> Entity (linked across namespaces)

CONTAINS, HAS_SPEAKER, MENTIONS and SAID make the hierarchy that parents, children, ancestors and descendants follow.

A projection covers whole namespaces only: the ones a caller can read, one of them (`ns:<name>`, isolated ones too),
or every shared one (`global`). Hidden entities and quiet types (dates, numbers) are left out, as in the explorer.
"""

from __future__ import annotations

import re
import time
from collections import Counter, defaultdict, deque

from . import graph_history, store
from .entities import QUIET, TYPES

R = store.R
REL_TYPES = ("CONTAINS", "HAS_SPEAKER", "MENTIONS", "SAID", "MENTIONED_WITH", "SPOKE_WITH", "SAME_AS", "SAME_THING")
HIERARCHY = ("CONTAINS", "HAS_SPEAKER", "MENTIONS", "SAID")
NODE_LABELS = ("Namespace", "Collection", "Recording", "Speaker", "Entity")
# the overview graph's edge kinds (graph.py, the web app's legend) for the relationship types that have one
KIND = {
    "SAID": "mentions",
    "MENTIONED_WITH": "mentioned together",
    "SPOKE_WITH": "together",
    "SAME_AS": "same person",
    "SAME_THING": "same thing",
    "CONTAINS": "contains",
    "HAS_SPEAKER": "speaks in",
    "MENTIONS": "mentioned in",
}
RELATIONS = ("children", "parents", "ancestors", "descendants", "neighbours")
MAX_RESULTS = 2000


class Node:
    __slots__ = ("id", "labels", "props")

    def __init__(self, nid, labels, props):
        self.id, self.labels, self.props = nid, tuple(labels), props

    def __repr__(self):
        return f"Node({self.id})"


class Rel:
    __slots__ = ("id", "type", "start", "end", "props")

    def __init__(self, rid, typ, start, end, props):
        self.id, self.type, self.start, self.end, self.props = rid, typ, start, end, props

    def other(self, nid):
        return self.end if self.start == nid else self.start

    def __repr__(self):
        return f"Rel({self.start}-{self.type}->{self.end})"


def type_label(t):
    """An entity type as a node label: PERSON -> Person, ORG -> Organisation, a namespace's own MY_TYPE -> MyType."""
    if t in TYPES:
        return TYPES[t]
    return "".join(w.capitalize() for w in re.split(r"[^A-Za-z0-9]+", str(t)) if w) or "Entity"


class Graph:
    """Nodes by id, relationships out of and into each node, and labels indexed for the query planner."""

    def __init__(self, scope, namespaces):
        self.scope, self.namespaces = scope, namespaces
        self.nodes: dict[str, Node] = {}
        self.out: dict[str, list[Rel]] = defaultdict(list)
        self.inn: dict[str, list[Rel]] = defaultdict(list)
        self.by_label: dict[str, list[str]] = defaultdict(list)
        self.rels = 0
        self.built_at = time.time()

    def add(self, nid, labels, **props):
        props = {k: v for k, v in props.items() if v is not None}
        n = Node(nid, labels, {"id": nid, **props})
        self.nodes[nid] = n
        for lb in n.labels:
            self.by_label[lb].append(nid)
        return n

    def link(self, typ, a, b, **props):
        if a not in self.nodes or b not in self.nodes or a == b:
            return None
        self.rels += 1
        r = Rel(f"{typ}:{a}:{b}", typ, a, b, {k: v for k, v in props.items() if v is not None})
        self.out[a].append(r)
        self.inn[b].append(r)
        return r

    # ---------- walking ----------
    def edges(self, nid, direction="both", types=None):
        """(relationship, the node at its other end) for one node; direction out, in or both."""
        if direction in ("out", "both"):
            for r in self.out.get(nid, ()):
                if not types or r.type in types:
                    yield r, r.end
        if direction in ("in", "both"):
            for r in self.inn.get(nid, ()):
                if not types or r.type in types:
                    yield r, r.start

    def resolve(self, ref):
        """A node id as people and agents write it: e12 (also inside a merged global entity), e:<key>, s4, r9, c2, n1."""
        ref = str(ref or "").strip()
        if ref in self.nodes:
            return ref
        m = re.fullmatch(r"e(\d+)", ref)
        if m:
            eid = int(m.group(1))
            for nid in self.by_label.get("Entity", ()):
                if eid in (self.nodes[nid].props.get("ids") or ()):
                    return nid
        raise KeyError(ref)

    def stats(self):
        return {
            "nodes": len(self.nodes),
            "relationships": self.rels,
            "labels": {lb: len(ids) for lb, ids in sorted(self.by_label.items())},
            "types": dict(sorted(Counter(r.type for rs in self.out.values() for r in rs).items())),
        }


# ---------- building ----------
def spaces_for(db, scope, readable):
    """[(space id, name, shared)] a scope covers, limited to `readable` (None: every namespace). KeyError for an unknown
    or unreadable namespace."""
    rows = db.rows("SELECT record::id(id) AS id, name, graph FROM space ORDER BY name")
    if scope and scope.startswith("ns:"):
        hit = [r for r in rows if r["name"] == scope[3:] and (readable is None or r["id"] in readable)]
        if not hit:
            raise KeyError(scope)
        rows = hit
    else:
        rows = [r for r in rows if r.get("graph") != "isolated" and (readable is None or r["id"] in readable)]
    return [(r["id"], r["name"], r.get("graph") != "isolated") for r in rows]


def build(db, scope="global", readable=None, recordings=None, as_of=None):
    """The graph of a scope over the namespaces in `readable` (None: all); `recordings` (a set of ids) keeps only those
    recordings and what is said in them, for a conversation limited to some recordings. `as_of` (a graph version) takes
    entities and their links as they were then (docs/graph-history.md); mentions and the rest are today's."""
    nss = spaces_for(db, scope, readable)
    keep = (lambda rid: rid in recordings) if recordings is not None else (lambda rid: True)
    merged = not (scope or "").startswith("ns:")
    g = Graph(scope or "global", [n for _, n, _ in nss])
    if not nss:
        return g
    sids = [s for s, _, _ in nss]
    names = {s: n for s, n, _ in nss}
    for s, n, shared in nss:
        g.add(f"n{s}", ["Namespace"], name=n, namespace=n, shared=shared)

    cols = db.rows("SELECT record::id(id) AS id, space, parent, name FROM collection WHERE space IN $s", s=sids)
    for c in cols:
        g.add(f"c{c['id']}", ["Collection"], name=c["name"], namespace=names[c["space"]])
    for c in cols:
        g.link("CONTAINS", f"c{c['parent']}" if c.get("parent") else f"n{c['space']}", f"c{c['id']}")

    for r in db.rows("SELECT record::id(id) AS id, space, collection, title, recorded_at, media FROM recording WHERE space IN $s", s=sids):
        if not keep(r["id"]):
            continue
        date = r.get("recorded_at")
        g.add(
            f"r{r['id']}",
            ["Recording"],
            name=r.get("title") or f"Recording {r['id']}",
            title=r.get("title"),
            namespace=names[r["space"]],
            date=str(date)[:10] if date else None,
            media=r.get("media"),
        )
        home = f"c{r['collection']}" if r.get("collection") and f"c{r['collection']}" in g.nodes else f"n{r['space']}"
        g.link("CONTAINS", home, f"r{r['id']}")

    talk = db.rows(
        "SELECT speaker, recording, math::sum(dur) AS ms FROM segment WHERE space IN $s AND speaker > 0 GROUP BY speaker, recording", s=sids
    )
    talk = [t for t in talk if keep(t["recording"])]
    total = Counter()
    for t in talk:
        total[t["speaker"]] += t["ms"] or 0
    for s in db.rows("SELECT record::id(id) AS id, space, name, label FROM speaker WHERE space IN $s", s=sids):
        if recordings is not None and not total[s["id"]]:
            continue
        g.add(
            f"s{s['id']}",
            ["Speaker"],
            name=s.get("name") or s.get("label") or f"Speaker {s['id']}",
            namespace=names[s["space"]],
            seconds=round(total[s["id"]] / 1000, 1),
        )
    together = defaultdict(set)
    for t in talk:
        g.link("HAS_SPEAKER", f"r{t['recording']}", f"s{t['speaker']}", seconds=round((t["ms"] or 0) / 1000, 1))
        together[t["recording"]].add(f"s{t['speaker']}")
    pairs = Counter()
    for group in together.values():
        ss = sorted(x for x in group if x in g.nodes)
        for i, a in enumerate(ss):
            for b in ss[i + 1 :]:
                pairs[(a, b)] += 1
    for (a, b), n in pairs.items():
        g.link("SPOKE_WITH", a, b, recordings=n)
    for ln in db.rows("SELECT record::id(in) AS a, record::id(out) AS b FROM same_as"):
        g.link("SAME_AS", f"s{ln['a']}", f"s{ln['b']}")

    # entities: one node each, or one per name across namespaces in the global scope
    node_of, agg = {}, {}
    past = graph_history.state_at(db, as_of, sids) if as_of is not None else None
    if past is None:
        ents = db.rows(f"SELECT {_ENTITY_FIELDS} FROM entity WHERE space IN $s", s=sids)
        links = db.rows("SELECT a, b FROM entity_link")
    else:
        ents = [{"id": k, **r} for k, r in sorted(past["entity"].items())]
        links = list(past["entity_link"].values())
    for e in ents:
        if e.get("hidden") or e["type"] in QUIET:
            continue
        nid = f"e:{e['key']}" if merged else f"e{e['id']}"
        node_of[e["id"]] = nid
        a = agg.setdefault(nid, {"name": e["name"], "type": e["type"], "key": e["key"], "ns": [], "ids": []})
        a["ns"].append(names[e["space"]])
        a["ids"].append(e["id"])
    ms = db.rows("SELECT record::id(in) AS seg, entity, speaker, recording FROM mentions WHERE space IN $s", s=sids)
    count, rec_m, spk_m, seg_e = Counter(), Counter(), Counter(), defaultdict(set)
    for m in ms:
        nid = node_of.get(m["entity"])
        if not nid or not keep(m["recording"]):
            continue
        count[nid] += 1
        rec_m[(f"r{m['recording']}", nid)] += 1
        if m.get("speaker"):
            spk_m[(f"s{m['speaker']}", nid)] += 1
        seg_e[m["seg"]].add(nid)
    for nid, a in agg.items():
        if not count[nid]:
            continue
        ns = sorted(set(a["ns"]))
        g.add(
            nid,
            ["Entity", type_label(a["type"])],
            name=a["name"],
            type=a["type"],
            key=a["key"],
            namespace=ns[0] if len(ns) == 1 else None,
            namespaces=ns,
            mentions=count[nid],
            ids=sorted(a["ids"]),
        )
    for (r, e), n in rec_m.items():
        g.link("MENTIONS", r, e, count=n)
    for (s, e), n in spk_m.items():
        g.link("SAID", s, e, count=n)
    co = Counter()
    for es in seg_e.values():
        es = sorted(es)
        for i, a in enumerate(es):
            for b in es[i + 1 :]:
                co[(a, b)] += 1
    for (a, b), n in co.items():
        g.link("MENTIONED_WITH", a, b, count=n)
    for ln in links:
        a, b = node_of.get(ln["a"]), node_of.get(ln["b"])
        if a and b and a != b:
            g.link("SAME_THING", a, b)
    return g


_ENTITY_FIELDS = "record::id(id) AS id, space, key, name, type, hidden"


# ---------- what the explorer asks for ----------
def node_out(n, depth=None):
    out = {"id": n.id, "labels": list(n.labels), **n.props}
    if depth is not None:
        out["depth"] = depth
    return out


def rel_out(r):
    return {"id": r.id, "type": r.type, "kind": KIND.get(r.type, r.type.lower()), "a": r.start, "b": r.end, **r.props}


def related(g, start, relation="neighbours", depth=1, types=None, limit=200):
    """Nodes related to `start` (an id g.resolve accepts), breadth first: {start, relation, nodes (with depth), edges,
    truncated}. children/parents go one step down/up the hierarchy, descendants/ancestors all the way (up to `depth`),
    neighbours any relationship in either direction (up to `depth`). `types` limits the relationships followed."""
    if relation not in RELATIONS:
        raise ValueError(f"relation is one of {', '.join(RELATIONS)}")
    sid = g.resolve(start)
    if relation in ("children", "parents"):
        depth = 1
    direction = {"children": "out", "descendants": "out", "parents": "in", "ancestors": "in"}.get(relation, "both")
    follow = set(types or ()) or (set(HIERARCHY) if relation != "neighbours" else None)
    limit = max(1, min(int(limit or 200), MAX_RESULTS))
    seen, edges, frontier, truncated = {sid: 0}, {}, deque([sid]), False
    while frontier:
        nid = frontier.popleft()
        if seen[nid] >= depth:
            continue
        for r, other in g.edges(nid, direction, follow):
            if other not in seen:
                if len(seen) > limit:
                    truncated = True
                    continue
                seen[other] = seen[nid] + 1
                frontier.append(other)
            edges[r.id] = r
    nodes = sorted(seen, key=lambda x: (seen[x], x != sid, g.nodes[x].props.get("name", "")))
    keep = set(nodes)
    return {
        "start": sid,
        "relation": relation,
        "nodes": [node_out(g.nodes[x], seen[x]) for x in nodes],
        "edges": [rel_out(r) for r in edges.values() if r.start in keep and r.end in keep],
        "truncated": truncated,
    }


def paths(g, a, b, max_depth=4, limit=10, types=None, directed=False, shortest=False):
    """Paths from a to b, shortest first: {paths: [{nodes, edges}], nodes, edges, truncated}. Without `shortest`, every
    simple path up to max_depth hops (at most `limit`); with it, only the shortest ones."""
    sa, sb = g.resolve(a), g.resolve(b)
    max_depth = max(1, min(int(max_depth or 4), 8))
    limit = max(1, min(int(limit or 10), 100))
    follow = set(types or ()) or None
    direction = "out" if directed else "both"
    # distance to b, so the search only steps where b is still within reach
    dist, frontier = {sb: 0}, deque([sb])
    while frontier:
        x = frontier.popleft()
        if dist[x] >= max_depth:
            continue
        for _r, y in g.edges(x, "in" if directed else "both", follow):
            if y not in dist:
                dist[y] = dist[x] + 1
                frontier.append(y)
    found, truncated, budget = [], False, [200_000]
    if sa in dist:
        best = dist[sa]
        for hops in range(best, (best if shortest else max_depth) + 1):
            _walk(g, sa, sb, hops, direction, follow, dist, [sa], [], found, limit, budget)
            if len(found) >= limit or budget[0] <= 0:
                truncated = True
                break
    found = found[:limit]
    nodes, edges = {}, {}
    for p in found:
        for n in p["nodes"]:
            nodes[n] = g.nodes[n]
        for r in p["rels"]:
            edges[r.id] = r
    return {
        "from": sa,
        "to": sb,
        "paths": [{"nodes": p["nodes"], "edges": [r.id for r in p["rels"]], "length": len(p["rels"])} for p in found],
        "nodes": [node_out(n) for n in nodes.values()],
        "edges": [rel_out(r) for r in edges.values()],
        "truncated": truncated,
    }


def _walk(g, at, goal, left, direction, follow, dist, trail, rels, found, limit, budget):
    """Depth-first: simple paths of exactly `left` more hops from `at` to `goal`."""
    if len(found) >= limit or budget[0] <= 0:
        return
    if left == 0:
        if at == goal:
            found.append({"nodes": list(trail), "rels": list(rels)})
        return
    for r, nxt in g.edges(at, direction, follow):
        budget[0] -= 1
        if nxt in trail or dist.get(nxt, left) > left - 1:
            continue
        trail.append(nxt)
        rels.append(r)
        _walk(g, nxt, goal, left - 1, direction, follow, dist, trail, rels, found, limit, budget)
        trail.pop()
        rels.pop()


def schema(g):
    """What a query can ask about, for people and agents writing Cypher: labels, relationship types (with the labels
    they join), properties per label, and counts."""
    props = defaultdict(set)
    for n in g.nodes.values():
        for lb in n.labels[:1]:
            props[lb].update(n.props)
    joins = defaultdict(set)
    for rs in g.out.values():
        for r in rs:
            joins[r.type].add((g.nodes[r.start].labels[0], g.nodes[r.end].labels[0]))
    rel_props = defaultdict(set)
    for rs in g.out.values():
        for r in rs:
            rel_props[r.type].update(r.props)
    return {
        "scope": g.scope,
        "namespaces": g.namespaces,
        "labels": [{"label": lb, "count": len(g.by_label.get(lb, ())), "properties": sorted(props.get(lb, ()))} for lb in NODE_LABELS],
        "entity_types": sorted({n.labels[1] for n in g.nodes.values() if n.labels[0] == "Entity" and len(n.labels) > 1}),
        "relationships": [
            {
                "type": t,
                "from_to": sorted(f"{a}->{b}" for a, b in joins.get(t, ())),
                "properties": sorted(rel_props.get(t, ())),
                "hierarchy": t in HIERARCHY,
            }
            for t in REL_TYPES
        ],
        "stats": g.stats(),
    }
