"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Graph2 as GraphApi } from "@/app/openapi-client";
import {
  bfsLayout,
  dfsLayout,
  forceLayout,
  radialLayout,
  routeLayout,
  type LayoutKind,
  type Point,
  type Positions,
} from "@/components/graph/layouts";
import {
  fromApiEdge,
  fromApiNode,
  type ApiEdge,
  type ApiNode,
  type GraphEdge,
  type GraphNode,
} from "@/components/graph/model";
import { data, useApiClient } from "@/lib/api/browser";

export type Relation = "children" | "parents" | "ancestors" | "descendants" | "neighbours";

export const RELATIONS: { key: Relation; label: string; depth: number }[] = [
  { key: "parents", label: "Parents", depth: 1 },
  { key: "children", label: "Children", depth: 1 },
  { key: "ancestors", label: "All ancestors", depth: 6 },
  { key: "descendants", label: "All descendants", depth: 6 },
  { key: "neighbours", label: "Neighbours", depth: 1 },
];

/** What the canvas lights up: the result of the last thing explored (or a query's answer). */
export type Highlight = { title: string; nodes: Set<string>; edges: Set<string> };

export const edgeKey = (a: string, b: string) => (a < b ? `${a}|${b}` : `${b}|${a}`);

type Saved = { layout: LayoutKind; pins: [string, Point][] };

function load(scope: string): Saved | null {
  try {
    const raw = window.localStorage.getItem(`lens.graph.view.${scope}`);
    return raw ? (JSON.parse(raw) as Saved) : null;
  } catch {
    return null;
  }
}

function save(scope: string, s: Saved | null) {
  try {
    if (s) window.localStorage.setItem(`lens.graph.view.${scope}`, JSON.stringify(s));
    else window.localStorage.removeItem(`lens.graph.view.${scope}`);
  } catch {
    // private windows and blocked storage: the arrangement just isn't kept
  }
}

/**
 * The explorer's state over the overview graph: nodes and links found by exploring (a node's parents, ancestors…),
 * a highlight, a route through picked nodes, the layout and nodes dragged into place (kept per scope on this device).
 */
export function useExplorer(scope: string, onError: (message: string) => void) {
  const client = useApiClient();
  const [extra, setExtra] = useState<{ nodes: GraphNode[]; edges: GraphEdge[] }>({ nodes: [], edges: [] });
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [highlight, setHighlight] = useState<Highlight | null>(null);
  const [layout, setLayoutState] = useState<LayoutKind>("force");
  const [root, setRoot] = useState<string | null>(null);
  const [pins, setPins] = useState<Map<string, Point>>(new Map());
  const [route, setRoute] = useState<string[]>([]);
  const [routePath, setRoutePath] = useState<{ nodes: string[]; edges: Set<string> } | null>(null);
  const [pathStart, setPathStart] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const loaded = useRef<string | null>(null);

  // a scope's own arrangement, when one was kept
  useEffect(() => {
    const s = load(scope);
    setLayoutState(s?.layout ?? "force");
    setPins(new Map(s?.pins ?? []));
    setExtra({ nodes: [], edges: [] });
    setHidden(new Set());
    setHighlight(null);
    setRoute([]);
    setRoutePath(null);
    setPathStart(null);
    loaded.current = scope;
  }, [scope]);
  useEffect(() => {
    if (loaded.current !== scope) return;
    save(
      scope,
      layout === "force" && pins.size === 0 ? null : { layout: layout === "route" ? "force" : layout, pins: [...pins] },
    );
  }, [scope, layout, pins]);

  const add = useCallback((nodes: ApiNode[], edges: ApiEdge[]) => {
    const ns = nodes.map(fromApiNode);
    const es = edges.map(fromApiEdge);
    setExtra((cur) => {
      const have = new Set(cur.nodes.map((n) => n.id));
      const keys = new Set(cur.edges.map((e) => `${edgeKey(e.a, e.b)}|${e.kind}`));
      return {
        nodes: [...cur.nodes, ...ns.filter((n) => !have.has(n.id))],
        edges: [...cur.edges, ...es.filter((e) => !keys.has(`${edgeKey(e.a, e.b)}|${e.kind}`))],
      };
    });
    setHidden((h) => {
      if (!ns.some((n) => h.has(n.id))) return h;
      const next = new Set(h);
      for (const n of ns) next.delete(n.id);
      return next;
    });
    return { ns, es };
  }, []);

  const explore = useCallback(
    async (node: GraphNode, relation: Relation) => {
      const r = RELATIONS.find((x) => x.key === relation)!;
      setBusy(`${r.label} of ${node.label}`);
      try {
        const out = (await data(
          GraphApi.graphRelated({ client, query: { node: node.id, relation, depth: r.depth, scope, limit: 150 } }),
        )) as unknown as { start: string; nodes: ApiNode[]; edges: ApiEdge[]; truncated: boolean };
        const { ns, es } = add(out.nodes, out.edges);
        setHighlight({
          title: `${r.label} of ${node.label}: ${ns.length - 1}${out.truncated ? "+" : ""}`,
          nodes: new Set(ns.map((n) => n.id)),
          edges: new Set(es.map((e) => edgeKey(e.a, e.b))),
        });
        setRoot(out.start);
        return out;
      } catch (e) {
        onError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(null);
      }
    },
    [client, scope, add, onError],
  );

  const paths = useCallback(
    async (from: GraphNode, to: GraphNode) => {
      setBusy(`Paths from ${from.label} to ${to.label}`);
      try {
        const out = (await data(
          GraphApi.graphPaths({ client, query: { a: from.id, b: to.id, scope, max_depth: 5, limit: 8 } }),
        )) as unknown as { paths: { nodes: string[]; length: number }[]; nodes: ApiNode[]; edges: ApiEdge[] };
        const { ns, es } = add(out.nodes, out.edges);
        setHighlight({
          title: out.paths.length
            ? `${out.paths.length} ${out.paths.length === 1 ? "path" : "paths"} from ${from.label} to ${to.label}`
            : `No path from ${from.label} to ${to.label} within 5 steps`,
          nodes: new Set(ns.map((n) => n.id)),
          edges: new Set(es.map((e) => edgeKey(e.a, e.b))),
        });
        setPathStart(null);
      } catch (e) {
        onError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(null);
      }
    },
    [client, scope, add, onError],
  );

  // the route: the shortest path between each pair of consecutive stops
  useEffect(() => {
    if (route.length < 2) {
      setRoutePath(route.length ? { nodes: [...route], edges: new Set() } : null);
      return;
    }
    let live = true;
    (async () => {
      const nodes: string[] = [route[0]];
      const edges = new Set<string>();
      try {
        for (let i = 0; i + 1 < route.length; i++) {
          const out = (await data(
            GraphApi.graphPaths({
              client,
              query: { a: route[i], b: route[i + 1], scope, max_depth: 8, limit: 1, shortest: true },
            }),
          )) as unknown as { paths: { nodes: string[] }[]; nodes: ApiNode[]; edges: ApiEdge[] };
          if (!live) return;
          add(out.nodes, out.edges);
          const hop = out.paths[0]?.nodes ?? [route[i], route[i + 1]];
          for (let j = 0; j + 1 < hop.length; j++) edges.add(edgeKey(hop[j], hop[j + 1]));
          nodes.push(...hop.slice(1));
        }
        if (live) setRoutePath({ nodes, edges });
      } catch (e) {
        if (live) onError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      live = false;
    };
  }, [route, client, scope, add, onError]);

  const setLayout = useCallback((k: LayoutKind, from?: string | null) => {
    setLayoutState(k);
    if (from !== undefined) setRoot(from);
    setPins(new Map()); // a layout arranges everything afresh
  }, []);

  const reset = useCallback(() => {
    setLayoutState("force");
    setPins(new Map());
    setExtra({ nodes: [], edges: [] });
    setHidden(new Set());
    setHighlight(null);
    setRoute([]);
    setPathStart(null);
    setRoot(null);
  }, []);

  return {
    extra,
    hidden,
    hide: (id: string) => setHidden((h) => new Set(h).add(id)),
    highlight,
    setHighlight,
    clearHighlight: () => setHighlight(null),
    layout,
    setLayout,
    root,
    pins,
    pin: (id: string, p: Point) => setPins((m) => new Map(m).set(id, p)),
    route,
    routePath,
    addStop: (id: string) => setRoute((r) => (r[r.length - 1] === id ? r : [...r, id])),
    removeStop: (i: number) => setRoute((r) => r.filter((_, j) => j !== i)),
    clearRoute: () => setRoute([]),
    pathStart,
    setPathStart,
    explore,
    paths,
    add,
    busy,
    reset,
  };
}

export type Explorer = ReturnType<typeof useExplorer>;

/** Positions for the visible graph under the chosen layout, with dragged nodes where they were put. */
export function usePositions(
  nodes: GraphNode[],
  edges: GraphEdge[],
  layout: LayoutKind,
  root: string | null,
  route: string[],
  pins: Map<string, Point>,
): Positions {
  const ids = useMemo(() => nodes.map((n) => n.id), [nodes]);
  const base = useMemo(() => {
    // the server placed the overview; nodes found since settle among them
    const start: Positions = new Map();
    const fixed = new Set<string>();
    for (const n of nodes)
      if (n.x || n.y) {
        start.set(n.id, { x: n.x, y: n.y });
        fixed.add(n.id);
      }
    if (fixed.size === nodes.length) return start;
    // keep everything in view: shrink the picture when settled nodes landed outside it
    const laid = forceLayout(ids, edges, start, { fixed });
    const far = Math.max(1, ...[...laid.values()].map((p) => Math.max(Math.abs(p.x), Math.abs(p.y))));
    if (far === 1) return laid;
    return new Map([...laid].map(([id, p]) => [id, { x: p.x / far, y: p.y / far }]));
  }, [nodes, edges, ids]);
  const laid = useMemo(() => {
    if (layout === "bfs") return bfsLayout(ids, edges, root);
    if (layout === "dfs") return dfsLayout(ids, edges, root);
    if (layout === "radial") return radialLayout(ids, edges, root);
    if (layout === "route") return routeLayout(ids, route, base);
    return base;
  }, [layout, ids, edges, root, route, base]);
  return useMemo(() => {
    if (!pins.size) return laid;
    const out = new Map(laid);
    for (const [id, p] of pins) if (out.has(id)) out.set(id, p);
    return out;
  }, [laid, pins]);
}
