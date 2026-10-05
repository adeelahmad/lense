"use client";

import { Maximize, Minus, Plus } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent, type WheelEvent } from "react";

import { edgeKey, type Highlight } from "@/components/graph/explorer";
import type { Point, Positions } from "@/components/graph/layouts";
import {
  connections,
  edgeStyle,
  maxWeights,
  nearestInDirection,
  neighbours,
  nodeRadius,
  nodeShape,
  typeLabel,
  type Dir,
  type GraphEdge,
  type GraphNode,
} from "@/components/graph/model";
import { nodeFill, ShapePath } from "@/components/graph/shape";
import { cn } from "@/lib/utils";

type View = { k: number; x: number; y: number };
const FIT: View = { k: 1, x: 0, y: 0 };
// Zoom limits relative to the fitted view. Nodes keep their size on screen, so a high cap lets a dense cluster spread out.
const MIN_ZOOM = 0.25;
const MAX_ZOOM = 100;
const LONG_PRESS_MS = 520;
const MOVE_SLOP = 5;

function edgeColor(kind: string): string {
  if (kind === "together") return "var(--text-strong)";
  if (kind === "mentions") return "var(--border)";
  if (kind === "maybe the same voice") return "var(--aladdin-gold)";
  if (kind === "contains") return "var(--aladdin-gold)";
  return "var(--text-muted)";
}

/** Positions that glide to a new layout instead of jumping (and follow a drag at once). */
function useGlide(target: Positions, instant: boolean): Positions {
  const [shown, setShown] = useState<Positions>(target);
  const from = useRef<Positions>(target);
  const frame = useRef<number | null>(null);
  useEffect(() => {
    if (frame.current) cancelAnimationFrame(frame.current);
    const reduce = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (instant || reduce) {
      from.current = target;
      setShown(target);
      return;
    }
    const start = performance.now();
    const a = from.current;
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / 380);
      const e = 1 - (1 - t) ** 3;
      const next: Positions = new Map();
      for (const [id, p] of target) {
        const q = a.get(id) ?? p;
        next.set(id, { x: q.x + (p.x - q.x) * e, y: q.y + (p.y - q.y) * e });
      }
      from.current = next;
      setShown(next);
      if (t < 1) frame.current = requestAnimationFrame(tick);
    };
    frame.current = requestAnimationFrame(tick);
    return () => {
      if (frame.current) cancelAnimationFrame(frame.current);
    };
  }, [target, instant]);
  return shown;
}

type Gesture =
  | { kind: "pan"; x: number; y: number; vx: number; vy: number; moved: boolean }
  | { kind: "node"; id: string; x: number; y: number; moved: boolean; timer: number | null; long: boolean }
  | { kind: "pinch"; dist: number; cx: number; cy: number; view: View };

/**
 * The graph drawn in SVG. Speakers are circles in their colour, entities are shaped by type, recordings, collections
 * and namespaces have their own shapes; size follows talk time or mentions. Selecting a node dims everything more
 * than one step away; a highlight (what was explored, a route) dims everything outside it.
 *
 * Mouse: drag the background to pan, drag a node to move it, wheel to zoom, right-click a node for its menu.
 * Touch: one finger pans or moves a node, two fingers pinch to zoom, a long press opens a node's menu.
 * Keyboard: Tab enters, arrow keys move to the nearest node, Enter selects, M (or the menu key) opens the node's
 * menu, Esc clears; + and − zoom, 0 fits.
 */
export function GraphCanvas({
  nodes,
  edges,
  positions,
  selected,
  onSelect,
  onMove,
  onMenu,
  highlight,
  route,
  summary,
  className,
}: {
  nodes: GraphNode[];
  edges: GraphEdge[];
  positions?: Positions;
  selected: string | null;
  onSelect: (id: string | null) => void;
  onMove?: (id: string, p: Point) => void;
  onMenu?: (id: string, at: { x: number; y: number }) => void;
  highlight?: Highlight | null;
  route?: { nodes: string[]; edges: Set<string> } | null;
  summary: string;
  className?: string;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 800, h: 600 });
  const [view, setView] = useState<View>(FIT);
  const [cursor, setCursor] = useState<string | null>(null);
  const [focused, setFocused] = useState(false);
  const [dragging, setDragging] = useState(false);
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const gesture = useRef<Gesture | null>(null);

  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) =>
      setSize({
        w: Math.max(200, e.contentRect.width),
        h: Math.max(200, e.contentRect.height),
      }),
    );
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const target = useMemo(() => {
    if (positions) return positions;
    return new Map(nodes.map((n) => [n.id, { x: n.x, y: n.y }]));
  }, [positions, nodes]);
  const shown = useGlide(target, dragging);

  const maxW = useMemo(() => maxWeights(nodes), [nodes]);
  const R = Math.max(60, Math.min(size.w, size.h) / 2 - 56);
  const pos = useMemo(() => {
    const m = new Map<string, { x: number; y: number; r: number }>();
    for (const n of nodes) {
      const p = shown.get(n.id) ?? { x: n.x, y: n.y };
      m.set(n.id, {
        x: size.w / 2 + view.x + p.x * R * view.k,
        y: size.h / 2 + view.y + p.y * R * view.k,
        r: nodeRadius(n, maxW),
      });
    }
    return m;
  }, [nodes, shown, size, view, R, maxW]);
  const toGraph = (sx: number, sy: number): Point => ({
    x: (sx - size.w / 2 - view.x) / (R * view.k),
    y: (sy - size.h / 2 - view.y) / (R * view.k),
  });

  const near = useMemo(() => (selected ? neighbours(edges, selected) : null), [edges, selected]);
  const routeNodes = useMemo(() => new Set(route?.nodes ?? []), [route]);
  const focusSet = highlight?.nodes ?? (route && route.nodes.length > 1 ? routeNodes : null);
  const lit = (id: string) =>
    focusSet ? focusSet.has(id) || id === selected : !selected || id === selected || Boolean(near?.has(id));
  const edgeLit = (e: GraphEdge) => {
    const k = edgeKey(e.a, e.b);
    if (route?.edges.has(k)) return true;
    if (highlight) return highlight.edges.has(k) || (highlight.nodes.has(e.a) && highlight.nodes.has(e.b));
    if (route && route.nodes.length > 1) return false;
    return !selected || e.a === selected || e.b === selected;
  };

  const zoom = (f: number, cx = size.w / 2, cy = size.h / 2) =>
    setView((v) => {
      const k = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, v.k * f));
      const s = k / v.k;
      // Keep the point under (cx, cy) where it is.
      return {
        k,
        x: (v.x + size.w / 2 - cx) * s - size.w / 2 + cx,
        y: (v.y + size.h / 2 - cy) * s - size.h / 2 + cy,
      };
    });

  // Keep the selected node in view (e.g. picked from the table or the finder).
  useEffect(() => {
    if (!selected) return;
    const p = pos.get(selected);
    if (p && (p.x < 24 || p.y < 24 || p.x > size.w - 24 || p.y > size.h - 24))
      setView((v) => ({
        ...v,
        x: v.x + size.w / 2 - p.x,
        y: v.y + size.h / 2 - p.y,
      }));
  }, [selected]);

  const menuAt = (id: string) => {
    const p = pos.get(id);
    if (p && onMenu) onMenu(id, { x: p.x, y: p.y + p.r });
  };

  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const dirs: Record<string, Dir> = {
      ArrowLeft: "left",
      ArrowRight: "right",
      ArrowUp: "up",
      ArrowDown: "down",
    };
    const pts = nodes.map((n) => ({
      id: n.id,
      x: pos.get(n.id)!.x,
      y: pos.get(n.id)!.y,
    }));
    if (dirs[e.key]) {
      e.preventDefault();
      const next = nearestInDirection(pts, cursor ?? selected, dirs[e.key]);
      if (next) setCursor(next);
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (cursor) onSelect(cursor);
    } else if (e.key === "m" || e.key === "M" || e.key === "ContextMenu" || (e.key === "F10" && e.shiftKey)) {
      const id = cursor ?? selected;
      if (id) {
        e.preventDefault();
        menuAt(id);
      }
    } else if (e.key === "Escape") {
      if (selected) {
        e.preventDefault();
        onSelect(null);
      }
    } else if (e.key === "+" || e.key === "=") zoom(1.25);
    else if (e.key === "-") zoom(0.8);
    else if (e.key === "0") setView(FIT);
  };

  const local = (e: { clientX: number; clientY: number }) => {
    const rect = wrap.current!.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  };

  const startPinch = () => {
    const [a, b] = [...pointers.current.values()];
    gesture.current = {
      kind: "pinch",
      dist: Math.hypot(a.x - b.x, a.y - b.y) || 1,
      cx: (a.x + b.x) / 2,
      cy: (a.y + b.y) / 2,
      view,
    };
  };

  const onPointerDown = (e: PointerEvent<SVGSVGElement>) => {
    if (e.pointerType === "mouse" && e.button !== 0) return; // the right button opens a node's menu instead
    const at = local(e);
    pointers.current.set(e.pointerId, at);
    (e.currentTarget as Element).setPointerCapture(e.pointerId);
    if (pointers.current.size === 2) {
      const g = gesture.current;
      if (g?.kind === "node" && g.timer) window.clearTimeout(g.timer);
      setDragging(false);
      startPinch();
      return;
    }
    if (pointers.current.size > 2) return;
    const hit = (e.target as Element).closest("[data-node]");
    const id = hit?.getAttribute("data-node");
    if (id) {
      const g: Gesture = { kind: "node", id, x: at.x, y: at.y, moved: false, timer: null, long: false };
      if (e.pointerType !== "mouse" && onMenu)
        g.timer = window.setTimeout(() => {
          if (gesture.current === g && !g.moved) {
            g.long = true;
            onMenu(id, at);
          }
        }, LONG_PRESS_MS);
      gesture.current = g;
      return;
    }
    gesture.current = { kind: "pan", x: at.x, y: at.y, vx: view.x, vy: view.y, moved: false };
  };

  const onPointerMove = (e: PointerEvent<SVGSVGElement>) => {
    if (!pointers.current.has(e.pointerId)) return;
    const at = local(e);
    pointers.current.set(e.pointerId, at);
    const g = gesture.current;
    if (!g) return;
    if (g.kind === "pinch") {
      if (pointers.current.size < 2) return;
      const [a, b] = [...pointers.current.values()];
      const dist = Math.hypot(a.x - b.x, a.y - b.y) || 1;
      const cx = (a.x + b.x) / 2;
      const cy = (a.y + b.y) / 2;
      const k = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, g.view.k * (dist / g.dist)));
      const s = k / g.view.k;
      setView({
        k,
        x: (g.view.x + size.w / 2 - g.cx) * s - size.w / 2 + cx,
        y: (g.view.y + size.h / 2 - g.cy) * s - size.h / 2 + cy,
      });
      return;
    }
    const dx = at.x - g.x;
    const dy = at.y - g.y;
    if (!g.moved && Math.abs(dx) + Math.abs(dy) > MOVE_SLOP) {
      g.moved = true;
      if (g.kind === "node") {
        if (g.timer) window.clearTimeout(g.timer);
        setDragging(true);
      }
    }
    if (!g.moved) return;
    if (g.kind === "pan") setView((v) => ({ ...v, x: g.vx + dx, y: g.vy + dy }));
    else if (g.kind === "node" && onMove && !g.long) onMove(g.id, toGraph(at.x, at.y));
  };

  const onPointerUp = (e: PointerEvent<SVGSVGElement>) => {
    pointers.current.delete(e.pointerId);
    const g = gesture.current;
    if (g?.kind === "pinch") {
      // one finger left: carry on as a pan from where it is
      const rest = [...pointers.current.values()][0];
      gesture.current = rest ? { kind: "pan", x: rest.x, y: rest.y, vx: view.x, vy: view.y, moved: true } : null;
      return;
    }
    if (pointers.current.size) return;
    gesture.current = null;
    setDragging(false);
    if (!g) return;
    if (g.kind === "node") {
      if (g.timer) window.clearTimeout(g.timer);
      if (!g.moved && !g.long) {
        setCursor(g.id);
        onSelect(g.id === selected ? null : g.id);
      }
    } else if (g.kind === "pan" && !g.moved) onSelect(null);
  };

  const onWheel = (e: WheelEvent<SVGSVGElement>) => {
    const at = local(e);
    zoom(e.deltaY < 0 ? 1.12 : 0.89, at.x, at.y);
  };

  const cur = cursor ? nodes.find((n) => n.id === cursor) : null;
  const curText = cur
    ? `${cur.label}, ${typeLabel(cur)}, ${connections(edges, cur.id).length} links${cur.id === selected ? ", selected" : ""}. Enter selects, M opens its menu.`
    : "";

  return (
    <div ref={wrap} className={cn("relative min-h-0 overflow-hidden bg-background", className)}>
      <p id="graph-summary" className="sr-only">
        {summary}
      </p>
      <div
        tabIndex={0}
        role="application"
        aria-roledescription="graph"
        aria-label="Knowledge graph. Arrow keys move to the nearest node, Enter selects, M opens a node's menu, Escape clears, plus and minus zoom. The Table view lists everything."
        aria-describedby="graph-summary"
        onKeyDown={onKey}
        onFocus={() => {
          setFocused(true);
          if (!cursor)
            setCursor(
              selected ??
                [...nodes].sort((a, b) => connections(edges, b.id).length - connections(edges, a.id).length)[0]?.id ??
                null,
            );
        }}
        onBlur={() => setFocused(false)}
        className="absolute inset-0 outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue"
      >
        <svg
          width={size.w}
          height={size.h}
          className={cn(
            "block touch-none select-none",
            dragging ? "cursor-grabbing" : "cursor-grab active:cursor-grabbing",
          )}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          onWheel={onWheel}
          onContextMenu={(e) => {
            const id = (e.target as Element).closest("[data-node]")?.getAttribute("data-node");
            if (id && onMenu) {
              e.preventDefault();
              const g = gesture.current;
              if (g?.kind === "node" && g.long) return; // a long press already opened it
              onMenu(id, local(e));
            }
          }}
          aria-hidden
        >
          <g>
            {edges.map((e) => {
              const a = pos.get(e.a);
              const b = pos.get(e.b);
              if (!a || !b) return null;
              const s = edgeStyle(e.kind);
              const on = edgeLit(e);
              const onRoute = route?.edges.has(edgeKey(e.a, e.b));
              const color = onRoute ? "var(--aladdin-blue)" : edgeColor(e.kind);
              const width = onRoute ? 3.2 : e.kind === "together" ? 2.4 : s.width;
              if ("double" in s && s.double && !onRoute) {
                const dx = b.x - a.x;
                const dy = b.y - a.y;
                const len = Math.hypot(dx, dy) || 1;
                const ox = (-dy / len) * 1.8;
                const oy = (dx / len) * 1.8;
                return (
                  <g key={`${e.a}-${e.b}-${e.kind}`} opacity={on ? 1 : 0.2}>
                    <line x1={a.x + ox} y1={a.y + oy} x2={b.x + ox} y2={b.y + oy} stroke={color} strokeWidth={1.2} />
                    <line x1={a.x - ox} y1={a.y - oy} x2={b.x - ox} y2={b.y - oy} stroke={color} strokeWidth={1.2} />
                  </g>
                );
              }
              return (
                <line
                  key={`${e.a}-${e.b}-${e.kind}`}
                  x1={a.x}
                  y1={a.y}
                  x2={b.x}
                  y2={b.y}
                  stroke={color}
                  strokeWidth={width}
                  strokeDasharray={!onRoute && "dash" in s ? s.dash : undefined}
                  strokeLinecap="round"
                  opacity={on ? 1 : 0.2}
                />
              );
            })}
          </g>
          <g>
            {nodes.map((n) => {
              const p = pos.get(n.id)!;
              const sel = n.id === selected;
              const isCursor = focused && n.id === cursor;
              const stop = route ? route.nodes.indexOf(n.id) : -1;
              const shape = nodeShape(n);
              return (
                <g
                  key={n.id}
                  data-node={n.id}
                  transform={`translate(${p.x},${p.y})`}
                  opacity={lit(n.id) ? 1 : 0.3}
                  className="cursor-pointer"
                >
                  {/* a bigger, invisible target so fingers can hit small nodes */}
                  <circle r={Math.max(p.r + 6, 18)} fill="transparent" />
                  {(sel || isCursor || stop >= 0) && (
                    <circle
                      r={p.r + (shape === "pill" ? 9 : 6)}
                      fill="none"
                      stroke="var(--aladdin-blue)"
                      strokeWidth={sel ? 3 : 2}
                      strokeDasharray={sel || stop >= 0 ? undefined : "3 3"}
                    />
                  )}
                  <ShapePath shape={shape} r={p.r} fill={nodeFill(n)} stroke="var(--background)" strokeWidth={2} />
                  <text
                    y={p.r + 14}
                    textAnchor="middle"
                    className="font-sans"
                    fontSize={12}
                    fontWeight={sel || n.kind === "speaker" ? 700 : 500}
                    fill="var(--text-primary)"
                    stroke="var(--background)"
                    strokeWidth={3.5}
                    paintOrder="stroke"
                  >
                    {n.label.length > 28 ? `${n.label.slice(0, 27)}…` : n.label}
                  </text>
                </g>
              );
            })}
          </g>
        </svg>
      </div>
      <div aria-live="polite" className="sr-only">
        {focused ? curText : ""}
      </div>
      <div className="absolute bottom-3 right-3 flex flex-col overflow-hidden rounded-[10px] border border-border bg-background">
        <button
          type="button"
          aria-label="Zoom in"
          onClick={() => zoom(1.25)}
          className="grid size-[38px] place-items-center border-b border-border text-fg hover:bg-surface-neutral"
        >
          <Plus className="size-[15px]" />
        </button>
        <button
          type="button"
          aria-label="Zoom out"
          onClick={() => zoom(0.8)}
          className="grid size-[38px] place-items-center border-b border-border text-fg hover:bg-surface-neutral"
        >
          <Minus className="size-[15px]" />
        </button>
        <button
          type="button"
          aria-label="Fit to view"
          onClick={() => setView(FIT)}
          className="grid size-[38px] place-items-center text-fg hover:bg-surface-neutral"
        >
          <Maximize className="size-[15px]" />
        </button>
      </div>
    </div>
  );
}
