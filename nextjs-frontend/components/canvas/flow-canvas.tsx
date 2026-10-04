"use client";

/**
 * The canvas pipelines and workflows are drawn on: nodes with an input port on the left and output ports on the
 * right (a condition has yes and no), connections between them, and a palette to drag from. Controlled: the parent
 * keeps the graph and hears about every move, connection and deletion.
 */
import {
  Background,
  Controls,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useNodesInitialized,
  useReactFlow,
  type Connection,
  type Edge,
  type EdgeChange,
  type Node,
  type NodeChange,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { LucideIcon } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent, type ReactNode } from "react";

import { cn } from "@/lib/utils";

export type CanvasNode = {
  id: string;
  x: number;
  y: number;
  title: string;
  subtitle?: string;
  icon?: LucideIcon;
  /** 0: nothing comes in; 1: one connection; -1: any number. */
  inputs: number;
  /** Named input ports, when a node has several (or one not called in). */
  inPorts?: string[];
  /** Output ports, by name: [] for none, ["out"], or ["yes", "no"]. */
  outputs: string[];
  tone?: "blue" | "green" | "gold" | "red" | "purple" | "neutral";
  /** What the last try did: ran, was skipped or failed, and a peek at what it passed on. */
  status?: "done" | "skipped" | "failed";
  peek?: string;
  /** It has a body of nodes, opened by double-clicking. */
  opens?: boolean;
  problem?: string;
  /** What reaches it and what it passes on, shown under the title. */
  io?: string;
};

export type CanvasEdge = { source: string; target: string; port?: string; input?: string };

type Data = CanvasNode & Record<string, unknown>;

const TONE: Record<NonNullable<CanvasNode["tone"]>, string> = {
  blue: "bg-blue",
  green: "bg-green",
  gold: "bg-gold",
  red: "bg-red",
  purple: "bg-[#8b5cf6]",
  neutral: "bg-fg-muted",
};

export const DRAG_TYPE = "application/x-lens-node";

function CardNode({ data, selected }: NodeProps<Node<Data>>) {
  const Icon = data.icon;
  return (
    <div
      className={cn(
        "relative w-[208px] rounded-md border bg-background px-3 py-2.5 text-left shadow-sm",
        selected ? "border-blue ring-2 ring-blue/30" : "border-border",
        data.problem && "border-red-border",
      )}
    >
      <span aria-hidden className={cn("absolute inset-y-0 left-0 w-1 rounded-l-md", TONE[data.tone ?? "blue"])} />
      {data.inputs !== 0 &&
        (data.inPorts && (data.inPorts.length > 1 || data.inPorts[0] !== "in") ? (
          data.inPorts.map((port, k) => (
            <Handle
              key={port}
              id={port}
              type="target"
              position={Position.Left}
              style={{ top: `${((k + 1) * 100) / (data.inPorts!.length + 1)}%` }}
              className="!size-3 !border-2 !border-background !bg-fg-muted"
              aria-label={`Input: ${port}`}
            >
              <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 whitespace-nowrap text-[10px] font-bold text-fg-muted">
                {port}
              </span>
            </Handle>
          ))
        ) : (
          <Handle
            type="target"
            id="in"
            position={Position.Left}
            className="!size-3 !border-2 !border-background !bg-fg-muted"
            aria-label="Input"
          />
        ))}
      {data.status && (
        <span
          title={
            data.status === "done"
              ? "Ran in the last try"
              : data.status === "skipped"
                ? "Skipped in the last try"
                : "Failed in the last try"
          }
          className={cn(
            "absolute -right-1.5 -top-1.5 size-3 rounded-full border-2 border-background",
            data.status === "done" ? "bg-green" : data.status === "failed" ? "bg-red" : "bg-fg-muted",
          )}
        />
      )}
      <div className="flex items-center gap-1.5">
        {Icon && <Icon aria-hidden className="size-4 shrink-0 text-fg-secondary" />}
        <span className="truncate text-[13px] font-bold text-fg">{data.title}</span>
      </div>
      {data.subtitle && <p className="mt-0.5 line-clamp-2 text-[11.5px] text-fg-secondary">{data.subtitle}</p>}
      {data.io && <p className="mt-1 truncate font-mono text-[10.5px] text-fg-muted">{data.io}</p>}
      {data.peek && (
        <p
          className="mt-1 truncate rounded-sm bg-surface px-1 font-mono text-[10.5px] text-fg-secondary"
          title={data.peek}
        >
          {data.peek}
        </p>
      )}
      {data.opens && <p className="mt-1 text-[10.5px] text-fg-muted">Double-click to open</p>}
      {data.problem && <p className="mt-1 text-[11px] font-medium text-red-dark">{data.problem}</p>}
      {data.outputs.map((port, k) => {
        const top = data.outputs.length === 1 ? "50%" : `${((k + 1) * 100) / (data.outputs.length + 1)}%`;
        return (
          <Handle
            key={port}
            id={port}
            type="source"
            position={Position.Right}
            style={{ top }}
            className={cn(
              "!size-3 !border-2 !border-background",
              port === "no"
                ? "!bg-red"
                : port === "yes"
                  ? "!bg-green"
                  : port === "default"
                    ? "!bg-fg-muted"
                    : "!bg-blue",
            )}
            aria-label={port === "out" ? "Output" : `Output: ${port}`}
          >
            {(data.outputs.length > 1 || port !== "out") && (
              <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 whitespace-nowrap text-[10px] font-bold uppercase text-fg-muted">
                {port}
              </span>
            )}
          </Handle>
        );
      })}
    </div>
  );
}

const nodeTypes = { card: CardNode };

type Props = {
  nodes: CanvasNode[];
  edges: CanvasEdge[];
  selected: string | null;
  /** Nodes selected together (Shift-drag, Ctrl-click), for folding them into a group or a custom node. */
  multi?: string[];
  onMultiSelect?: (ids: string[]) => void;
  /** A node double-clicked (to open its body). */
  onOpen?: (id: string) => void;
  readOnly?: boolean;
  onSelect: (id: string | null) => void;
  onMove: (id: string, x: number, y: number) => void;
  onConnect: (e: CanvasEdge) => void;
  onDeleteNodes: (ids: string[]) => void;
  onDeleteEdges: (edges: CanvasEdge[]) => void;
  /** A palette item dropped on the canvas: its payload and where it landed. */
  onDrop?: (payload: string, x: number, y: number) => void;
  className?: string;
};

const FIT = { padding: 0.2, minZoom: 0.1, maxZoom: 1 };

const edgeId = (e: CanvasEdge) => `${e.source}:${e.port ?? "out"}->${e.target}:${e.input ?? "in"}`;

function Inner({
  nodes,
  edges,
  selected,
  multi,
  onMultiSelect,
  onOpen,
  readOnly,
  onSelect,
  onMove,
  onConnect,
  onDeleteNodes,
  onDeleteEdges,
  onDrop,
  className,
}: Props) {
  const flow = useReactFlow();
  const lastClick = useRef<{ id: string; at: number }>({ id: "", at: 0 });
  // The whole graph stays in view until the person pans or zooms: refit once the nodes are measured, when nodes come
  // or go, and when the canvas changes size (a panel opening, the window resizing). A fit on first render alone left
  // nodes off the edges, or the canvas empty, when they were measured or the canvas grew after it.
  const box = useRef<HTMLDivElement>(null);
  const moved = useRef(false);
  const measured = useNodesInitialized();
  const fit = useCallback(() => {
    if (!moved.current) requestAnimationFrame(() => void flow.fitView(FIT));
  }, [flow]);
  useEffect(() => {
    if (measured) fit();
  }, [measured, nodes.length, fit]);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const watch = new ResizeObserver(fit);
    watch.observe(el);
    return () => watch.disconnect();
  }, [fit]);
  // Each node's measured size, kept with the nodes as React Flow measures them: the minimap draws from it.
  const [sizes, setSizes] = useState<Record<string, { width: number; height: number }>>({});
  const rfNodes = useMemo<Node<Data>[]>(
    () =>
      nodes.map((n) => ({
        id: n.id,
        type: "card",
        position: { x: n.x, y: n.y },
        measured: sizes[n.id],
        data: n as Data,
        selected: n.id === selected || Boolean(multi?.includes(n.id)),
        deletable: !readOnly && n.inputs !== 0,
      })),
    [nodes, selected, multi, readOnly, sizes],
  );
  const rfEdges = useMemo<Edge[]>(
    () =>
      edges.map((e) => ({
        id: edgeId(e),
        source: e.source,
        target: e.target,
        sourceHandle: e.port ?? "out",
        targetHandle: e.input ?? "in",
        // The port is named on its handle; only a condition’s yes or no is said on the line too.
        label: e.port === "yes" || e.port === "no" ? e.port : undefined,
        animated: false,
        deletable: !readOnly,
        style: { strokeWidth: 2 },
      })),
    [edges, readOnly],
  );
  const onNodesChange = useCallback(
    (changes: NodeChange<Node<Data>>[]) => {
      const gone: string[] = [];
      // Selection is kept by the parent: one node (its settings show), or several (Shift or Ctrl), to fold together.
      const picked = new Set(rfNodes.filter((n) => n.selected).map((n) => n.id));
      let last: string | null | undefined;
      const measuredNow: Record<string, { width: number; height: number }> = {};
      for (const c of changes) {
        if (c.type === "dimensions" && c.dimensions) measuredNow[c.id] = c.dimensions;
        else if (c.type === "position" && c.position && !readOnly) onMove(c.id, c.position.x, c.position.y);
        else if (c.type === "remove") gone.push(c.id);
        else if (c.type === "select") {
          if (c.selected) {
            picked.add(c.id);
            last = c.id;
          } else picked.delete(c.id);
        }
      }
      if (last !== undefined || changes.some((c) => c.type === "select")) {
        onMultiSelect?.(picked.size > 1 ? [...picked] : []);
        if (last) onSelect(last);
        else if (!picked.size) onSelect(null);
        else if (picked.size === 1) onSelect([...picked][0]);
      }
      if (Object.keys(measuredNow).length) setSizes((was) => ({ ...was, ...measuredNow }));
      if (gone.length && !readOnly) onDeleteNodes(gone);
    },
    [onMove, onDeleteNodes, onSelect, onMultiSelect, readOnly, rfNodes],
  );
  const onEdgesChange = useCallback(
    (changes: EdgeChange[]) => {
      const gone = changes.filter((c) => c.type === "remove").map((c) => (c as { id: string }).id);
      if (gone.length && !readOnly) onDeleteEdges(edges.filter((e) => gone.includes(edgeId(e))));
    },
    [edges, onDeleteEdges, readOnly],
  );
  const connect = useCallback(
    (c: Connection) => {
      if (readOnly || !c.source || !c.target) return;
      onConnect({ source: c.source, target: c.target, port: c.sourceHandle ?? "out", input: c.targetHandle ?? "in" });
    },
    [onConnect, readOnly],
  );
  const drop = (e: DragEvent) => {
    const payload = e.dataTransfer.getData(DRAG_TYPE);
    if (!payload || !onDrop || readOnly) return;
    e.preventDefault();
    const p = flow.screenToFlowPosition({ x: e.clientX, y: e.clientY });
    onDrop(payload, p.x - 100, p.y - 30);
  };
  return (
    <div
      ref={box}
      className={cn("h-full min-h-[480px] w-full", className)}
      onDragOver={(e) => {
        if (e.dataTransfer.types.includes(DRAG_TYPE)) {
          e.preventDefault();
          e.dataTransfer.dropEffect = "copy";
        }
      }}
      onDrop={drop}
    >
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={connect}
        onPaneClick={() => onSelect(null)}
        onNodeClick={(_, n) => {
          // A double click, told from two clicks on the same node: React Flow's own double click is lost when the
          // first click selects the node and the canvas redraws under the pointer.
          const now = Date.now();
          if (lastClick.current.id === n.id && now - lastClick.current.at < 400) onOpen?.(n.id);
          lastClick.current = { id: n.id, at: now };
        }}
        multiSelectionKeyCode={["Meta", "Control", "Shift"]}
        selectionKeyCode="Shift"
        zoomOnDoubleClick={false}
        nodesDraggable={!readOnly}
        nodesConnectable={!readOnly}
        deleteKeyCode={readOnly ? null : ["Delete", "Backspace"]}
        fitView
        fitViewOptions={FIT}
        minZoom={0.1}
        maxZoom={8}
        onMoveStart={(e) => {
          if (e) moved.current = true; // a pan or zoom by the person (fitting the view has no event)
        }}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={20} />
        <Controls showInteractive={false} />
        <MiniMap pannable zoomable style={{ width: 150, height: 100 }} className="!hidden md:!block" />
      </ReactFlow>
    </div>
  );
}

export function FlowCanvas(props: Props) {
  return (
    <ReactFlowProvider>
      <Inner {...props} />
    </ReactFlowProvider>
  );
}

/** A palette entry: click to add, or drag onto the canvas. */
export function PaletteItem({
  payload,
  icon: Icon,
  title,
  hint,
  onAdd,
  disabled,
}: {
  payload: string;
  icon?: LucideIcon;
  title: ReactNode;
  hint?: string;
  onAdd: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      draggable={!disabled}
      disabled={disabled}
      onDragStart={(e) => {
        e.dataTransfer.setData(DRAG_TYPE, payload);
        e.dataTransfer.effectAllowed = "copy";
      }}
      onClick={onAdd}
      title={hint}
      className="flex w-full items-start gap-2 rounded-sm border border-border bg-background px-2.5 py-2 text-left hover:border-blue-border hover:bg-blue-surface disabled:cursor-not-allowed disabled:opacity-50"
    >
      {Icon && <Icon aria-hidden className="mt-0.5 size-4 shrink-0 text-fg-secondary" />}
      <span className="min-w-0">
        <span className="block text-[12.5px] font-semibold text-fg">{title}</span>
        {hint && <span className="line-clamp-2 block text-[11px] text-fg-muted">{hint}</span>}
      </span>
    </button>
  );
}

/** A fresh node id not used yet: n1, n2… */
export function freshId(taken: Iterable<string>, prefix = "n"): string {
  const used = new Set(taken);
  let k = 1;
  while (used.has(`${prefix}${k}`)) k++;
  return `${prefix}${k}`;
}
