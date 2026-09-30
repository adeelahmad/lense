"use client";

import { Maximize, Minus, Plus } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent, type WheelEvent } from "react";

import { connections, edgeStyle, maxWeights, nearestInDirection, neighbours, nodeRadius, nodeShape, typeLabel, type Dir, type GraphEdge, type GraphNode } from "@/components/graph/model";
import { nodeFill, ShapePath } from "@/components/graph/shape";
import { cn } from "@/lib/utils";

type View = { k: number; x: number; y: number };
const FIT: View = { k: 1, x: 0, y: 0 };

function edgeColor(kind: string): string {
  if (kind === "together") return "var(--text-strong)";
  if (kind === "mentions") return "var(--border)";
  if (kind === "maybe the same voice") return "var(--aladdin-gold)";
  return "var(--text-muted)";
}

/**
 * The graph drawn in SVG from the server's layout. Speakers are circles in their colour, entities are shaped by type,
 * size follows talk time or mentions. Selecting a node dims everything more than one step away.
 * Keyboard: Tab enters, arrow keys move to the nearest node, Enter selects, Esc clears; + and − zoom, 0 fits.
 */
export function GraphCanvas({
  nodes,
  edges,
  selected,
  onSelect,
  summary,
  className,
}: {
  nodes: GraphNode[];
  edges: GraphEdge[];
  selected: string | null;
  onSelect: (id: string | null) => void;
  summary: string;
  className?: string;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 800, h: 600 });
  const [view, setView] = useState<View>(FIT);
  const [cursor, setCursor] = useState<string | null>(null);
  const [focused, setFocused] = useState(false);
  const drag = useRef<{ x: number; y: number; vx: number; vy: number; moved: boolean } | null>(null);

  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setSize({ w: Math.max(200, e.contentRect.width), h: Math.max(200, e.contentRect.height) }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const maxW = useMemo(() => maxWeights(nodes), [nodes]);
  const R = Math.max(60, Math.min(size.w, size.h) / 2 - 56);
  const pos = useMemo(() => {
    const m = new Map<string, { x: number; y: number; r: number }>();
    for (const n of nodes) m.set(n.id, { x: size.w / 2 + view.x + n.x * R * view.k, y: size.h / 2 + view.y + n.y * R * view.k, r: nodeRadius(n, maxW) });
    return m;
  }, [nodes, size, view, R, maxW]);
  const near = useMemo(() => (selected ? neighbours(edges, selected) : null), [edges, selected]);
  const lit = (id: string) => !selected || id === selected || Boolean(near?.has(id));

  const zoom = (f: number, cx = size.w / 2, cy = size.h / 2) =>
    setView((v) => {
      const k = Math.min(6, Math.max(0.4, v.k * f));
      const s = k / v.k;
      // Keep the point under (cx, cy) where it is.
      return { k, x: (v.x + size.w / 2 - cx) * s - size.w / 2 + cx, y: (v.y + size.h / 2 - cy) * s - size.h / 2 + cy };
    });

  // Keep the selected node in view (e.g. picked from the table or the finder).
  useEffect(() => {
    if (!selected) return;
    const p = pos.get(selected);
    if (p && (p.x < 24 || p.y < 24 || p.x > size.w - 24 || p.y > size.h - 24)) setView((v) => ({ ...v, x: v.x + size.w / 2 - p.x, y: v.y + size.h / 2 - p.y }));
  }, [selected]);

  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const dirs: Record<string, Dir> = { ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down" };
    const pts = nodes.map((n) => ({ id: n.id, x: pos.get(n.id)!.x, y: pos.get(n.id)!.y }));
    if (dirs[e.key]) {
      e.preventDefault();
      const next = nearestInDirection(pts, cursor ?? selected, dirs[e.key]);
      if (next) setCursor(next);
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (cursor) onSelect(cursor);
    } else if (e.key === "Escape") {
      if (selected) {
        e.preventDefault();
        onSelect(null);
      }
    } else if (e.key === "+" || e.key === "=") zoom(1.25);
    else if (e.key === "-") zoom(0.8);
    else if (e.key === "0") setView(FIT);
  };

  const onPointerDown = (e: PointerEvent<SVGSVGElement>) => {
    if ((e.target as Element).closest("[data-node]")) return;
    drag.current = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y, moved: false };
    (e.currentTarget as Element).setPointerCapture(e.pointerId);
  };
  const onPointerMove = (e: PointerEvent<SVGSVGElement>) => {
    const d = drag.current;
    if (!d) return;
    const dx = e.clientX - d.x;
    const dy = e.clientY - d.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) d.moved = true;
    setView((v) => ({ ...v, x: d.vx + dx, y: d.vy + dy }));
  };
  const onPointerUp = () => {
    const d = drag.current;
    drag.current = null;
    if (d && !d.moved) onSelect(null);
  };
  const onWheel = (e: WheelEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    zoom(e.deltaY < 0 ? 1.12 : 0.89, e.clientX - rect.left, e.clientY - rect.top);
  };

  const cur = cursor ? nodes.find((n) => n.id === cursor) : null;
  const curText = cur ? `${cur.label}, ${typeLabel(cur)}, ${connections(edges, cur.id).length} links${cur.id === selected ? ", selected" : ""}. Enter selects.` : "";

  return (
    <div ref={wrap} className={cn("relative min-h-0 overflow-hidden bg-background", className)}>
      <p id="graph-summary" className="sr-only">
        {summary}
      </p>
      <div
        tabIndex={0}
        role="application"
        aria-roledescription="graph"
        aria-label="Knowledge graph. Arrow keys move to the nearest node, Enter selects, Escape clears, plus and minus zoom. The Table view lists everything."
        aria-describedby="graph-summary"
        onKeyDown={onKey}
        onFocus={() => {
          setFocused(true);
          if (!cursor) setCursor(selected ?? [...nodes].sort((a, b) => connections(edges, b.id).length - connections(edges, a.id).length)[0]?.id ?? null);
        }}
        onBlur={() => setFocused(false)}
        className="absolute inset-0 outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue"
      >
        <svg width={size.w} height={size.h} className="block cursor-grab touch-none select-none active:cursor-grabbing" onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onWheel={onWheel} aria-hidden>
          <g>
            {edges.map((e) => {
              const a = pos.get(e.a);
              const b = pos.get(e.b);
              if (!a || !b) return null;
              const s = edgeStyle(e.kind);
              const on = !selected || e.a === selected || e.b === selected;
              const color = edgeColor(e.kind);
              const width = e.kind === "together" ? 2.4 : s.width;
              if ("double" in s && s.double) {
                const dx = b.x - a.x;
                const dy = b.y - a.y;
                const len = Math.hypot(dx, dy) || 1;
                const ox = (-dy / len) * 1.8;
                const oy = (dx / len) * 1.8;
                return (
                  <g key={`${e.a}-${e.b}`} opacity={on ? 1 : 0.3}>
                    <line x1={a.x + ox} y1={a.y + oy} x2={b.x + ox} y2={b.y + oy} stroke={color} strokeWidth={1.2} />
                    <line x1={a.x - ox} y1={a.y - oy} x2={b.x - ox} y2={b.y - oy} stroke={color} strokeWidth={1.2} />
                  </g>
                );
              }
              return (
                <line
                  key={`${e.a}-${e.b}`}
                  x1={a.x}
                  y1={a.y}
                  x2={b.x}
                  y2={b.y}
                  stroke={color}
                  strokeWidth={width}
                  strokeDasharray={"dash" in s ? s.dash : undefined}
                  strokeLinecap="round"
                  opacity={on ? 1 : 0.3}
                />
              );
            })}
          </g>
          <g>
            {nodes.map((n) => {
              const p = pos.get(n.id)!;
              const sel = n.id === selected;
              const isCursor = focused && n.id === cursor;
              const shape = nodeShape(n);
              return (
                <g
                  key={n.id}
                  data-node
                  transform={`translate(${p.x},${p.y})`}
                  opacity={lit(n.id) ? 1 : 0.35}
                  className="cursor-pointer"
                  onClick={(ev) => {
                    ev.stopPropagation();
                    setCursor(n.id);
                    onSelect(sel ? null : n.id);
                  }}
                >
                  {(sel || isCursor) && <circle r={p.r + (shape === "pill" ? 9 : 6)} fill="none" stroke="var(--aladdin-blue)" strokeWidth={sel ? 3 : 2} strokeDasharray={sel ? undefined : "3 3"} />}
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
        <button type="button" aria-label="Zoom in" onClick={() => zoom(1.25)} className="grid size-[34px] place-items-center border-b border-border text-fg hover:bg-surface-neutral">
          <Plus className="size-[15px]" />
        </button>
        <button type="button" aria-label="Zoom out" onClick={() => zoom(0.8)} className="grid size-[34px] place-items-center border-b border-border text-fg hover:bg-surface-neutral">
          <Minus className="size-[15px]" />
        </button>
        <button type="button" aria-label="Fit to view" onClick={() => setView(FIT)} className="grid size-[34px] place-items-center text-fg hover:bg-surface-neutral">
          <Maximize className="size-[15px]" />
        </button>
      </div>
    </div>
  );
}
