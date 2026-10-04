import {
  bfsLayout,
  dfsLayout,
  forceLayout,
  pickRoot,
  radialLayout,
  routeLayout,
  spanningTree,
} from "@/components/graph/layouts";
import {
  cellText,
  filterGraph,
  fromApiNode,
  looksLikeCypher,
  mergeGraph,
  nodeHref,
  type GraphNode,
} from "@/components/graph/model";

// a small tree with one extra cross link: n1 -> c1 -> r1, r2; r1 -> e1, e2; r2 -> e2; e1 - e2
const ids = ["n1", "c1", "r1", "r2", "e1", "e2"];
const edges = [
  { a: "n1", b: "c1" },
  { a: "c1", b: "r1" },
  { a: "c1", b: "r2" },
  { a: "r1", b: "e1" },
  { a: "r1", b: "e2" },
  { a: "r2", b: "e2" },
  { a: "e1", b: "e2" },
];

const inRange = (p: { x: number; y: number }) => p.x >= -1.21 && p.x <= 1.21 && p.y >= -1.21 && p.y <= 1.21;

describe("graph layouts", () => {
  it("picks the most connected root unless told", () => {
    expect(pickRoot(ids, edges)).toBe("c1"); // ties go to the first
    expect(pickRoot(ids, edges, "n1")).toBe("n1");
    expect(pickRoot(ids, edges, "nope")).toBe("c1");
  });

  it("builds BFS and DFS spanning trees that reach every node once", () => {
    for (const order of ["bfs", "dfs"] as const) {
      const t = spanningTree(ids, edges, "n1", order);
      expect(t.roots).toEqual(["n1"]);
      expect([...t.parent.keys()].sort()).toEqual([...ids].sort());
      expect(t.depth.get("c1")).toBe(1);
    }
    expect(spanningTree(ids, edges, "n1", "bfs").depth.get("e2")).toBe(3);
  });

  it("puts each BFS level on its own row, root on top", () => {
    const p = bfsLayout(ids, edges, "n1");
    expect(p.get("n1")!.y).toBe(-1);
    expect(p.get("c1")!.y).toBeLessThan(p.get("r1")!.y);
    expect(p.get("r1")!.y).toBe(p.get("r2")!.y);
    expect(p.get("e1")!.y).toBe(1);
    expect([...p.values()].every(inRange)).toBe(true);
  });

  it("centres DFS parents over their children", () => {
    const p = dfsLayout(ids, edges, "n1");
    expect(p.get("n1")!.y).toBe(-1);
    const kids = ["r1", "r2"].map((id) => p.get(id)!.x).sort((a, b) => a - b);
    expect(p.get("c1")!.x).toBeGreaterThanOrEqual(kids[0]);
    expect(p.get("c1")!.x).toBeLessThanOrEqual(kids[1]);
  });

  it("puts the root in the middle of the rings", () => {
    const p = radialLayout(ids, edges, "n1");
    expect(p.get("n1")).toEqual({ x: 0, y: 0 });
    const r = (id: string) => Math.hypot(p.get(id)!.x, p.get(id)!.y);
    expect(r("c1")).toBeLessThan(r("e1"));
  });

  it("lays a route on a line and keeps others off it", () => {
    const base = new Map(ids.map((id, i) => [id, { x: i / 5 - 0.5, y: 0 }]));
    const p = routeLayout(ids, ["n1", "c1", "r1", "e1"], base);
    expect(["n1", "c1", "r1", "e1"].map((id) => p.get(id)!.y)).toEqual([0, 0, 0, 0]);
    expect(p.get("n1")!.x).toBeLessThan(p.get("e1")!.x);
    expect(Math.abs(p.get("r2")!.y)).toBeGreaterThan(0.2);
  });

  it("settles new nodes without moving fixed ones", () => {
    const start = new Map([
      ["n1", { x: -0.5, y: 0 }],
      ["c1", { x: 0.5, y: 0 }],
    ]);
    const p = forceLayout(["n1", "c1", "r1"], [...edges], start, { fixed: new Set(["n1", "c1"]) });
    expect(p.get("n1")).toEqual({ x: -0.5, y: 0 });
    expect(p.get("r1")).not.toEqual({ x: 0, y: 0 });
    expect(inRange(p.get("r1")!)).toBe(true);
  });

  it("handles an empty graph and lone nodes", () => {
    expect(bfsLayout([], []).size).toBe(0);
    const p = dfsLayout(["a", "b"], []);
    expect(p.size).toBe(2);
  });
});

describe("explored nodes", () => {
  const rec = fromApiNode({ id: "r9", labels: ["Recording"], name: "Ep 9", namespace: "pods" });
  const ent = fromApiNode({
    id: "e:acme",
    labels: ["Entity", "Organisation"],
    name: "Acme",
    type: "ORG",
    namespaces: ["a", "b"],
    mentions: 4,
    ids: [3, 7],
  });

  it("turns API nodes into canvas nodes", () => {
    expect(rec).toMatchObject({ kind: "recording", label: "Ep 9", ns: ["pods"], refs: [9] });
    expect(ent).toMatchObject({ kind: "entity", type: "ORG", weight: 4, refs: [3, 7], ns: ["a", "b"] });
    expect(nodeHref(rec)).toBe("/recordings/9");
    expect(nodeHref(ent)).toBeNull(); // a merged node has no single page
  });

  it("merges explored nodes into the overview, keeping the overview's own", () => {
    const base = { nodes: [{ ...ent, label: "Acme (overview)" } as GraphNode], edges: [] };
    const m = mergeGraph(base, {
      nodes: [ent, rec],
      edges: [
        { a: "r9", b: "e:acme", w: 2, kind: "mentioned in" },
        { a: "e:acme", b: "r9", w: 2, kind: "mentioned in" },
      ],
    });
    expect(m.nodes.map((n) => n.label)).toEqual(["Acme (overview)", "Ep 9"]);
    expect(m.edges).toHaveLength(1);
    // recordings stay visible like speakers; their links have their own kinds
    const f = filterGraph(m, { groups: new Set(["ORG", "recording"]), kinds: new Set(["mentioned in"]), minWeight: 1 });
    expect(f.nodes.map((n) => n.id).sort()).toEqual(["e:acme", "r9"]);
  });
});

describe("asking the graph", () => {
  it("tells Cypher from a question", () => {
    expect(looksLikeCypher("MATCH (e) RETURN e")).toBe(true);
    expect(looksLikeCypher("  optional match (e) return e")).toBe(true);
    expect(looksLikeCypher("Who talks about Acme?")).toBe(false);
    expect(looksLikeCypher("matches about Acme")).toBe(false);
  });

  it("shows cells as text", () => {
    expect(cellText({ id: "e1", labels: ["Entity"], name: "Acme" })).toBe("Acme");
    expect(cellText(["a", 2, null])).toBe("a, 2, ");
    expect(cellText({ nodes: ["a", "b"], length: 1 })).toBe("path of 1");
    expect(cellText({ type: "SAID", a: "s1", b: "e1" })).toBe("s1 SAID e1");
    expect(cellText(3)).toBe("3");
  });
});
