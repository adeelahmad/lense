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
import { useCallback, useMemo, type DragEvent, type ReactNode } from "react";

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
  /** Output ports, by name: [] for none, ["out"], or ["yes", "no"]. */
  outputs: string[];
  tone?: "blue" | "green" | "gold" | "red" | "neutral";
  problem?: string;
  /** What reaches it and what it passes on, shown under the title. */
  io?: string;
};

export type CanvasEdge = { source: string; target: string; port?: string };

type Data = CanvasNode & Record<string, unknown>;

const TONE: Record<NonNullable<CanvasNode["tone"]>, string> = {
  blue: "bg-blue",
  green: "bg-green",
  gold: "bg-gold",
  red: "bg-red",
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
      {data.inputs !== 0 && (
        <Handle
          type="target"
          position={Position.Left}
          className="!size-3 !border-2 !border-background !bg-fg-muted"
          aria-label="Input"
        />
      )}
      <div className="flex items-center gap-1.5">
        {Icon && <Icon aria-hidden className="size-4 shrink-0 text-fg-secondary" />}
        <span className="truncate text-[13px] font-bold text-fg">{data.title}</span>
      </div>
      {data.subtitle && <p className="mt-0.5 line-clamp-2 text-[11.5px] text-fg-secondary">{data.subtitle}</p>}
      {data.io && <p className="mt-1 truncate font-mono text-[10.5px] text-fg-muted">{data.io}</p>}
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
              port === "no" ? "!bg-red" : port === "yes" ? "!bg-green" : "!bg-blue",
            )}
            aria-label={port === "out" ? "Output" : `Output: ${port}`}
          >
            {data.outputs.length > 1 && (
              <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[10px] font-bold uppercase text-fg-muted">
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

const edgeId = (e: CanvasEdge) => `${e.source}->${e.target}:${e.port ?? "out"}`;

function Inner({
  nodes,
  edges,
  selected,
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
  const rfNodes = useMemo<Node<Data>[]>(
    () =>
      nodes.map((n) => ({
        id: n.id,
        type: "card",
        position: { x: n.x, y: n.y },
        data: n as Data,
        selected: n.id === selected,
        deletable: !readOnly && n.inputs !== 0,
      })),
    [nodes, selected, readOnly],
  );
  const rfEdges = useMemo<Edge[]>(
    () =>
      edges.map((e) => ({
        id: edgeId(e),
        source: e.source,
        target: e.target,
        sourceHandle: e.port ?? "out",
        label: e.port && e.port !== "out" ? e.port : undefined,
        animated: false,
        deletable: !readOnly,
        style: { strokeWidth: 2 },
      })),
    [edges, readOnly],
  );
  const onNodesChange = useCallback(
    (changes: NodeChange<Node<Data>>[]) => {
      const gone: string[] = [];
      for (const c of changes) {
        if (c.type === "position" && c.position && !readOnly) onMove(c.id, c.position.x, c.position.y);
        else if (c.type === "remove") gone.push(c.id);
        else if (c.type === "select" && c.selected) onSelect(c.id);
      }
      if (gone.length && !readOnly) onDeleteNodes(gone);
    },
    [onMove, onDeleteNodes, onSelect, readOnly],
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
      onConnect({ source: c.source, target: c.target, port: c.sourceHandle ?? "out" });
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
        nodesDraggable={!readOnly}
        nodesConnectable={!readOnly}
        deleteKeyCode={readOnly ? null : ["Delete", "Backspace"]}
        fitView
        fitViewOptions={{ padding: 0.2, maxZoom: 1 }}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={20} />
        <Controls showInteractive={false} />
        <MiniMap pannable zoomable className="!hidden md:!block" />
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
