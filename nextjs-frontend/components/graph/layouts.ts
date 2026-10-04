/**
 * One-click layouts for the graph explorer. Each takes node ids and edges and returns positions in -1..1 (the same
 * space as the server's force layout), so the canvas draws them the same way and can animate between them.
 *
 * - force: the server's positions, with nodes added since (from a node's menu) placed by a short force pass
 * - bfs: a layered tree from a root, breadth first (each level a row)
 * - dfs: a tidy tree from a root in depth-first order (leaves spread left to right, parents over their children)
 * - radial: breadth-first rings around the root
 * - route: the route's stops on a line, everything else around it
 */

export type Point = { x: number; y: number };
export type Positions = Map<string, Point>;
export type LayoutKind = "force" | "bfs" | "dfs" | "radial" | "route";
type Edge = { a: string; b: string; w?: number };

export const LAYOUTS: { key: LayoutKind; label: string; hint: string }[] = [
  { key: "force", label: "Force", hint: "Linked nodes pull together" },
  { key: "bfs", label: "BFS tree", hint: "Levels by distance from the root" },
  { key: "dfs", label: "DFS tree", hint: "A tree in depth-first order" },
  { key: "radial", label: "Radial", hint: "Rings around the root" },
  { key: "route", label: "Route", hint: "Your route on a line" },
];

function adjacency(ids: string[], edges: Edge[]): Map<string, string[]> {
  const has = new Set(ids);
  const adj = new Map<string, string[]>(ids.map((id) => [id, []]));
  for (const e of edges) {
    if (!has.has(e.a) || !has.has(e.b) || e.a === e.b) continue;
    adj.get(e.a)!.push(e.b);
    adj.get(e.b)!.push(e.a);
  }
  // visit neighbours in a stable order: most connected first, then by id
  for (const list of adj.values())
    list.sort((p, q) => adj.get(q)!.length - adj.get(p)!.length || (p < q ? -1 : p > q ? 1 : 0));
  return adj;
}

/** The node to grow a tree from: the one asked for, else the most connected. */
export function pickRoot(ids: string[], edges: Edge[], root?: string | null): string | null {
  if (root && ids.includes(root)) return root;
  const adj = adjacency(ids, edges);
  let best: string | null = null;
  for (const id of ids) if (best === null || adj.get(id)!.length > adj.get(best)!.length) best = id;
  return best;
}

/** Breadth-first spanning forest: parent of each node and its depth. Unreached parts get their own roots. */
export function spanningTree(
  ids: string[],
  edges: Edge[],
  root: string | null,
  order: "bfs" | "dfs",
): {
  parent: Map<string, string | null>;
  depth: Map<string, number>;
  children: Map<string, string[]>;
  roots: string[];
} {
  const adj = adjacency(ids, edges);
  const parent = new Map<string, string | null>();
  const depth = new Map<string, number>();
  const children = new Map<string, string[]>(ids.map((id) => [id, []]));
  const roots: string[] = [];
  const starts = [root, ...[...ids].sort((p, q) => adj.get(q)!.length - adj.get(p)!.length)].filter((x): x is string =>
    Boolean(x),
  );
  for (const s of starts) {
    if (parent.has(s)) continue;
    roots.push(s);
    parent.set(s, null);
    depth.set(s, 0);
    if (order === "bfs") {
      const queue = [s];
      while (queue.length) {
        const x = queue.shift()!;
        for (const y of adj.get(x)!) {
          if (parent.has(y)) continue;
          parent.set(y, x);
          depth.set(y, depth.get(x)! + 1);
          children.get(x)!.push(y);
          queue.push(y);
        }
      }
    } else {
      const stack: [string, number][] = [[s, 0]];
      while (stack.length) {
        const [x, i] = stack[stack.length - 1];
        const next = adj
          .get(x)!
          .slice(i)
          .find((y) => !parent.has(y));
        if (!next) {
          stack.pop();
          continue;
        }
        stack[stack.length - 1][1] = adj.get(x)!.indexOf(next) + 1;
        parent.set(next, x);
        depth.set(next, depth.get(x)! + 1);
        children.get(x)!.push(next);
        stack.push([next, 0]);
      }
    }
  }
  return { parent, depth, children, roots };
}

/** Scale positions into -1..1 on both axes (keeping one axis's aspect when the other is flat). */
function normalise(raw: Map<string, Point>): Positions {
  const xs = [...raw.values()].map((p) => p.x);
  const ys = [...raw.values()].map((p) => p.y);
  const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const sx = x1 - x0 || 1;
  const sy = y1 - y0 || 1;
  const out: Positions = new Map();
  for (const [id, p] of raw)
    out.set(id, {
      x: x1 === x0 ? 0 : ((p.x - x0) / sx) * 2 - 1,
      y: y1 === y0 ? 0 : ((p.y - y0) / sy) * 2 - 1,
    });
  return out;
}

/** Layered rows: depth down the page, nodes of a row spread evenly in the order their parents sit. */
export function bfsLayout(ids: string[], edges: Edge[], root?: string | null): Positions {
  if (!ids.length) return new Map();
  const t = spanningTree(ids, edges, pickRoot(ids, edges, root), "bfs");
  const rows = new Map<number, string[]>();
  // walk the tree breadth first so siblings stay together and follow their parent's order
  let offset = 0;
  for (const r of t.roots) {
    const queue = [r];
    let maxDepth = 0;
    while (queue.length) {
      const x = queue.shift()!;
      const d = t.depth.get(x)! + offset;
      maxDepth = Math.max(maxDepth, d);
      if (!rows.has(d)) rows.set(d, []);
      rows.get(d)!.push(x);
      queue.push(...t.children.get(x)!);
    }
    offset = maxDepth + 1;
  }
  const raw = new Map<string, Point>();
  for (const [d, row] of rows) row.forEach((id, i) => raw.set(id, { x: (i + 1) / (row.length + 1), y: d }));
  return normalise(raw);
}

/** Tidy tree: leaves get consecutive columns in depth-first order; a parent sits over the middle of its children. */
export function dfsLayout(ids: string[], edges: Edge[], root?: string | null): Positions {
  if (!ids.length) return new Map();
  const t = spanningTree(ids, edges, pickRoot(ids, edges, root), "dfs");
  const raw = new Map<string, Point>();
  let column = 0;
  const place = (x: string): number => {
    const kids = t.children.get(x)!;
    let col: number;
    if (!kids.length) col = column++;
    else {
      const cols = kids.map(place);
      col = (cols[0] + cols[cols.length - 1]) / 2;
    }
    raw.set(x, { x: col, y: t.depth.get(x)! });
    return col;
  };
  for (const r of t.roots) {
    place(r);
    column += 1; // a gap between separate trees
  }
  return normalise(raw);
}

/** Breadth-first rings: the root in the middle, each ring one step further, children near their parent's angle. */
export function radialLayout(ids: string[], edges: Edge[], root?: string | null): Positions {
  if (!ids.length) return new Map();
  const t = spanningTree(ids, edges, pickRoot(ids, edges, root), "dfs");
  const leaves = new Map<string, number>();
  const count = (x: string): number => {
    const kids = t.children.get(x)!;
    const n = kids.length ? kids.reduce((a, k) => a + count(k), 0) : 1;
    leaves.set(x, n);
    return n;
  };
  const total = t.roots.reduce((a, r) => a + count(r), 0);
  const out: Positions = new Map();
  const maxDepth = Math.max(1, ...[...t.depth.values()]);
  const put = (x: string, from: number, span: number, ring: number) => {
    const angle = from + span / 2;
    const r = ring / maxDepth;
    out.set(x, { x: Math.cos(angle) * r, y: Math.sin(angle) * r });
    let at = from;
    for (const k of t.children.get(x)!) {
      const share = (span * leaves.get(k)!) / leaves.get(x)!;
      put(k, at, share, ring + 1);
      at += share;
    }
  };
  let at = -Math.PI / 2;
  for (const r of t.roots) {
    const span = (2 * Math.PI * leaves.get(r)!) / total;
    // only the main tree's root sits in the middle; other trees start on the first ring
    put(r, at, span, r === t.roots[0] ? 0 : 1);
    at += span;
  }
  return out;
}

/** The route's stops (and the hops between them) evenly along the middle; other nodes keep their place, pushed off it. */
export function routeLayout(ids: string[], route: string[], base: Positions): Positions {
  const out: Positions = new Map();
  const on = route.filter((id) => ids.includes(id));
  on.forEach((id, i) => out.set(id, { x: on.length > 1 ? (i / (on.length - 1)) * 1.8 - 0.9 : 0, y: 0 }));
  const onSet = new Set(on);
  for (const id of ids) {
    if (onSet.has(id)) continue;
    const p = base.get(id) ?? { x: 0, y: 0 };
    const y = p.y >= 0 ? 0.25 + p.y * 0.75 : -0.25 + p.y * 0.75;
    out.set(id, { x: p.x, y });
  }
  return out;
}

/**
 * Force layout on the client: keeps known positions (the server's) as the start and settles new nodes among them.
 * Fruchterman-Reingold with a little gravity, deterministic; `fixed` nodes don't move.
 */
export function forceLayout(
  ids: string[],
  edges: Edge[],
  start: Positions,
  { iterations = 160, fixed = new Set<string>() }: { iterations?: number; fixed?: Set<string> } = {},
): Positions {
  const n = ids.length;
  if (!n) return new Map();
  const idx = new Map(ids.map((id, i) => [id, i]));
  const pos = ids.map((id, i) => {
    const p = start.get(id);
    if (p) return { ...p };
    // a new node: near a placed neighbour, else on a ring
    const e = edges.find((x) => (x.a === id && start.has(x.b)) || (x.b === id && start.has(x.a)));
    const anchor = e ? start.get(e.a === id ? e.b : e.a)! : { x: 0, y: 0 };
    const a = (i * 2.399963) % (2 * Math.PI); // golden angle, so siblings fan out
    return { x: anchor.x + Math.cos(a) * 0.12, y: anchor.y + Math.sin(a) * 0.12 };
  });
  const moving = ids.map((id) => !fixed.has(id));
  if (!moving.some(Boolean)) return new Map(ids.map((id, i) => [id, pos[i]]));
  const k = 1.8 / Math.sqrt(n);
  const E = edges
    .map((e) => [idx.get(e.a), idx.get(e.b)] as const)
    .filter((e): e is readonly [number, number] => e[0] !== undefined && e[1] !== undefined);
  let t = 0.12;
  for (let it = 0; it < iterations; it++) {
    const disp = pos.map(() => ({ x: 0, y: 0 }));
    for (let i = 0; i < n; i++) {
      if (!moving[i]) continue;
      for (let j = 0; j < n; j++) {
        if (i === j) continue;
        const dx = pos[i].x - pos[j].x;
        const dy = pos[i].y - pos[j].y;
        const d2 = dx * dx + dy * dy + 1e-4;
        const f = (k * k) / d2;
        disp[i].x += dx * f;
        disp[i].y += dy * f;
      }
    }
    for (const [a, b] of E) {
      const dx = pos[a].x - pos[b].x;
      const dy = pos[a].y - pos[b].y;
      const d = Math.hypot(dx, dy) + 1e-6;
      const f = d / k;
      disp[a].x -= dx * f;
      disp[a].y -= dy * f;
      disp[b].x += dx * f;
      disp[b].y += dy * f;
    }
    for (let i = 0; i < n; i++) {
      if (!moving[i]) continue;
      disp[i].x -= pos[i].x * 0.08 * Math.sqrt(n);
      disp[i].y -= pos[i].y * 0.08 * Math.sqrt(n);
      const len = Math.hypot(disp[i].x, disp[i].y) + 1e-9;
      const step = Math.min(len, t);
      pos[i].x += (disp[i].x / len) * step;
      pos[i].y += (disp[i].y / len) * step;
    }
    t = Math.max(0.004, t * 0.97);
  }
  const out: Positions = new Map();
  ids.forEach((id, i) =>
    out.set(id, { x: Math.max(-1.2, Math.min(1.2, pos[i].x)), y: Math.max(-1.2, Math.min(1.2, pos[i].y)) }),
  );
  return out;
}
