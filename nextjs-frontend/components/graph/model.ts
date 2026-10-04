/**
 * The knowledge graph as the API returns it (GET /graph): speaker and entity nodes with positions in -1..1 from the
 * server's layout, and typed, weighted edges. Filtering, sizing, keyboard movement and the text summary live here.
 */

export type NodeKind = "speaker" | "entity" | "recording" | "collection" | "namespace" | "topic";

export type GraphNode = {
  id: string;
  kind: NodeKind;
  label: string;
  type?: string;
  ns: string[];
  weight: number;
  recordings?: number;
  refs: number[];
  x: number;
  y: number;
};

export type GraphEdge = { a: string; b: string; w: number; kind: string };

export type GraphData = {
  scope: string;
  namespaces: string[];
  nodes: GraphNode[];
  edges: GraphEdge[];
};

/** Node groups in the filter list, in the design's order. Speakers first, then entity types. */
export const NODE_GROUPS: { key: string; label: string; shape: Shape }[] = [
  { key: "speaker", label: "Speakers", shape: "circle" },
  { key: "PERSON", label: "People", shape: "ring" },
  { key: "ORG", label: "Organisations", shape: "square" },
  { key: "PRODUCT", label: "Products", shape: "diamond" },
  { key: "PLACE", label: "Places", shape: "triangle" },
  { key: "TERM", label: "Terms", shape: "pill" },
  { key: "EVENT", label: "Events", shape: "hexagon" },
  { key: "WORK", label: "Works", shape: "rounded" },
  { key: "recording", label: "Recordings", shape: "doc" },
  { key: "collection", label: "Collections", shape: "folder" },
  { key: "namespace", label: "Namespaces", shape: "octagon" },
  { key: "topic", label: "Topics", shape: "tag" },
];

export type Shape =
  | "circle"
  | "ring"
  | "square"
  | "diamond"
  | "triangle"
  | "pill"
  | "hexagon"
  | "rounded"
  | "doc"
  | "folder"
  | "octagon"
  | "tag";

/** Node kinds that only appear once you explore (a node's parents, ancestors…), never in the overview. */
export const STRUCTURE_KINDS = new Set<NodeKind>(["recording", "collection", "namespace", "topic"]);

/** Edge kinds: each has its own dash pattern, so they differ by more than colour. */
export const EDGE_KINDS: {
  key: string;
  label: string;
  dash?: string;
  double?: boolean;
  gold?: boolean;
  width: number;
}[] = [
  { key: "together", label: "Spoke together", width: 2.2 },
  { key: "mentions", label: "Mentions", width: 1.1 },
  {
    key: "mentioned together",
    label: "Mentioned together",
    dash: "1.5 3",
    width: 1.3,
  },
  { key: "same person", label: "Same person", double: true, width: 1.2 },
  { key: "same thing", label: "Same thing", double: true, width: 1.2 },
  {
    key: "maybe the same voice",
    label: "Maybe same voice",
    dash: "6 4",
    gold: true,
    width: 1.6,
  },
  { key: "contains", label: "Contains", width: 1.2 },
  { key: "speaks in", label: "Speaks in", dash: "4 2", width: 1.1 },
  { key: "mentioned in", label: "Mentioned in", dash: "2 2", width: 1 },
  { key: "about", label: "About", dash: "5 2", width: 1.2 },
  { key: "narrower", label: "Narrower", width: 1.4 },
  { key: "related", label: "Related", dash: "1 2", width: 1.1 },
];

export function edgeStyle(kind: string) {
  return (
    EDGE_KINDS.find((k) => k.key === kind) ?? {
      key: kind,
      label: kind,
      width: 1,
    }
  );
}

export function nodeGroup(n: Pick<GraphNode, "kind" | "type">): string {
  if (n.kind === "entity") return n.type ?? "TERM";
  return n.kind;
}

export function nodeShape(n: Pick<GraphNode, "kind" | "type">): Shape {
  return NODE_GROUPS.find((g) => g.key === nodeGroup(n))?.shape ?? "circle";
}

/** "Organisation", "Speaker"… */
export function typeLabel(n: Pick<GraphNode, "kind" | "type">): string {
  const g = NODE_GROUPS.find((x) => x.key === nodeGroup(n));
  if (!g) return "Entity";
  return g.label.replace(/s$/, "");
}

/** Identity links are shown whatever their weight. */
const ALWAYS = new Set(["same person", "same thing", "maybe the same voice"]);

export type GraphFilter = {
  groups: Set<string>;
  kinds: Set<string>;
  minWeight: number;
};

/** The visible part of the graph. Entities left with no visible link are hidden; speakers always stay. */
export function filterGraph(
  g: Pick<GraphData, "nodes" | "edges">,
  f: GraphFilter,
): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const inGroup = new Set(g.nodes.filter((n) => f.groups.has(nodeGroup(n))).map((n) => n.id));
  const edges = g.edges.filter(
    (e) => inGroup.has(e.a) && inGroup.has(e.b) && f.kinds.has(e.kind) && (ALWAYS.has(e.kind) || e.w >= f.minWeight),
  );
  const linked = new Set(edges.flatMap((e) => [e.a, e.b]));
  const nodes = g.nodes.filter((n) => inGroup.has(n.id) && (n.kind !== "entity" || linked.has(n.id)));
  return { nodes, edges };
}

export function neighbours(edges: GraphEdge[], id: string): Set<string> {
  const out = new Set<string>();
  for (const e of edges) {
    if (e.a === id) out.add(e.b);
    else if (e.b === id) out.add(e.a);
  }
  return out;
}

/** Links of a node with their weights, strongest first. */
export function connections(edges: GraphEdge[], id: string): { id: string; w: number; kind: string }[] {
  return edges
    .filter((e) => e.a === id || e.b === id)
    .map((e) => ({ id: e.a === id ? e.b : e.a, w: e.w, kind: e.kind }))
    .sort((x, y) => y.w - x.w);
}

export type Dir = "left" | "right" | "up" | "down";

/** Arrow-key movement: the nearest node in that direction (distance plus a penalty for drifting sideways). */
export function nearestInDirection(
  nodes: Pick<GraphNode, "id" | "x" | "y">[],
  fromId: string | null,
  dir: Dir,
): string | null {
  const from = nodes.find((n) => n.id === fromId);
  if (!from) return nodes[0]?.id ?? null;
  let best: string | null = null;
  let bestScore = Infinity;
  for (const n of nodes) {
    if (n.id === from.id) continue;
    const dx = n.x - from.x;
    const dy = n.y - from.y;
    const along = dir === "right" ? dx : dir === "left" ? -dx : dir === "down" ? dy : -dy;
    const across = dir === "left" || dir === "right" ? Math.abs(dy) : Math.abs(dx);
    if (along <= 1e-6) continue;
    const score = Math.hypot(dx, dy) + across * 1.5;
    if (score < bestScore) {
      bestScore = score;
      best = n.id;
    }
  }
  return best;
}

/** Node size: speakers by talk time, entities by mentions (area grows with the weight). */
export function nodeRadius(
  n: Pick<GraphNode, "kind" | "weight">,
  maxWeight: { speaker: number; entity: number },
): number {
  if (n.kind === "namespace") return 14;
  if (n.kind === "collection") return 11;
  if (n.kind === "recording") return 9;
  const max = n.kind === "speaker" ? maxWeight.speaker : maxWeight.entity;
  const t = max > 0 ? Math.sqrt(Math.max(0, n.weight) / max) : 0;
  return n.kind === "speaker" ? 9 + 13 * t : 6 + 9 * t;
}

export function maxWeights(nodes: Pick<GraphNode, "kind" | "weight">[]): {
  speaker: number;
  entity: number;
} {
  const m = { speaker: 0, entity: 0 };
  for (const n of nodes) if (n.kind === "speaker" || n.kind === "entity") m[n.kind] = Math.max(m[n.kind], n.weight);
  return m;
}

/** The text summary that stands in for the picture. */
export function summarize(nodes: GraphNode[], edges: GraphEdge[], scopeLabel: string): string {
  if (!nodes.length) return `The graph for ${scopeLabel} is empty.`;
  const degree = new Map<string, number>();
  for (const e of edges) {
    degree.set(e.a, (degree.get(e.a) ?? 0) + 1);
    degree.set(e.b, (degree.get(e.b) ?? 0) + 1);
  }
  const top = [...nodes]
    .sort((a, b) => (degree.get(b.id) ?? 0) - (degree.get(a.id) ?? 0))
    .slice(0, 3)
    .filter((n) => degree.get(n.id));
  const speakers = nodes.filter((n) => n.kind === "speaker").length;
  const head = `${nodes.length} ${nodes.length === 1 ? "node" : "nodes"} (${speakers} ${speakers === 1 ? "speaker" : "speakers"}) and ${edges.length} ${edges.length === 1 ? "link" : "links"} in ${scopeLabel}.`;
  if (!top.length) return head;
  return `${head} Most connected: ${top.map((n) => `${n.label} (${degree.get(n.id)} ${degree.get(n.id) === 1 ? "link" : "links"})`).join(", ")}.`;
}

/** Find a node from a `?focus=` value: "s4" (speaker), "e12" (an entity id, also inside merged global nodes). */
export function findFocus(nodes: GraphNode[], focus: string | null | undefined): GraphNode | undefined {
  if (!focus) return undefined;
  const direct = nodes.find((n) => n.id === focus);
  if (direct) return direct;
  const m = /^e(\d+)$/.exec(focus);
  if (m) return nodes.find((n) => n.kind === "entity" && n.refs.includes(Number(m[1])));
  return undefined;
}

/** Nodes whose label contains the text (case-insensitive), best (prefix, heavier) first. */
export function findNodes(nodes: GraphNode[], text: string): GraphNode[] {
  const t = text.trim().toLowerCase();
  if (!t) return [];
  return nodes
    .filter((n) => n.label.toLowerCase().includes(t))
    .sort(
      (a, b) =>
        Number(b.label.toLowerCase().startsWith(t)) - Number(a.label.toLowerCase().startsWith(t)) ||
        b.weight - a.weight,
    );
}

/** A node or relationship as the graph API returns it (/graph/related, /graph/paths, /graph/query). */
export type ApiNode = {
  id: string;
  labels: string[];
  name?: string;
  type?: string;
  namespace?: string | null;
  namespaces?: string[];
  mentions?: number;
  seconds?: number;
  ids?: number[];
  depth?: number;
  [key: string]: unknown;
};
export type ApiEdge = {
  id: string;
  type: string;
  kind: string;
  a: string;
  b: string;
  count?: number;
  [key: string]: unknown;
};

/** An API node as the canvas draws it. */
export function fromApiNode(n: ApiNode): GraphNode {
  const label = n.labels[0]?.toLowerCase();
  const kind: NodeKind =
    label === "speaker" || label === "recording" || label === "collection" || label === "namespace" || label === "topic"
      ? label
      : "entity";
  const num = Number(n.id.replace(/^[a-z]/, ""));
  return {
    id: n.id,
    kind,
    label: String(n.name ?? n.id),
    type: kind === "entity" ? n.type : undefined,
    ns: n.namespaces ?? (n.namespace ? [n.namespace] : []),
    weight: kind === "entity" ? (n.mentions ?? 1) : kind === "speaker" ? (n.seconds ?? 0) / 60 : 1,
    refs: n.ids ?? (Number.isFinite(num) ? [num] : []),
    x: 0,
    y: 0,
  };
}

export function fromApiEdge(e: ApiEdge): GraphEdge {
  return { a: e.a, b: e.b, w: Number(e.count ?? e.recordings ?? 1) || 1, kind: e.kind };
}

/** The overview plus nodes and links found by exploring; an overview node wins over the same node found later. */
export function mergeGraph(
  base: Pick<GraphData, "nodes" | "edges">,
  extra: { nodes: GraphNode[]; edges: GraphEdge[] },
): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const ids = new Set(base.nodes.map((n) => n.id));
  const nodes = [...base.nodes, ...extra.nodes.filter((n) => !ids.has(n.id))];
  const key = (e: GraphEdge) => (e.a < e.b ? `${e.a}|${e.b}|${e.kind}` : `${e.b}|${e.a}|${e.kind}`);
  const seen = new Set(base.edges.map(key));
  const edges = [...base.edges];
  for (const e of extra.edges) {
    if (seen.has(key(e))) continue;
    seen.add(key(e));
    edges.push(e);
  }
  return { nodes, edges };
}

/** Where a node's own page is, when it has one. */
export function nodeHref(n: Pick<GraphNode, "kind" | "refs" | "id">): string | null {
  const id = n.refs[0];
  if (n.kind === "speaker") return `/speakers/${id}`;
  if (n.kind === "recording") return `/recordings/${id}`;
  if (n.kind === "collection") return `/collections/${id}`;
  if (n.kind === "entity" && n.refs.length === 1) return `/entities/${id}`;
  if (n.kind === "topic" && n.refs.length === 1) return `/topics/${id}`;
  return null;
}

/** Text that reads as Cypher rather than a question: it starts with a clause. */
export function looksLikeCypher(text: string): boolean {
  return /^\s*(MATCH|OPTIONAL\s+MATCH|RETURN|WITH|UNWIND|CALL)\b/i.test(text);
}

/** A cell of the answer as text: a node by its name, a list or path joined up. */
export function cellText(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (Array.isArray(v)) return v.map(cellText).join(", ");
  if (typeof v === "object") {
    const o = v as Record<string, unknown>;
    if (typeof o.name === "string") return o.name;
    if (Array.isArray(o.nodes) && typeof o.length === "number") return `path of ${o.length}`;
    if (typeof o.type === "string" && typeof o.a === "string") return `${o.a} ${o.type} ${o.b}`;
    return JSON.stringify(o);
  }
  return String(v);
}
