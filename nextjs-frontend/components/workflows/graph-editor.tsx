"use client";

/**
 * The canvas a workflow or a custom node is drawn on: the palette (building blocks, the scope's own nodes and the
 * custom nodes you can use), the graph, and the selected node's settings. Loops, groups and custom nodes have a body
 * of nodes inside, opened by double-clicking and left by the breadcrumb. Selected nodes can be folded into a group,
 * or saved as a custom node to use again and share.
 */
import {
  Bot,
  Braces,
  Calendar,
  ChevronRight,
  FileText,
  Filter,
  GitBranch,
  Group as GroupIcon,
  ListTree,
  Mail,
  Puzzle,
  Repeat,
  Search,
  Sparkles,
  Tags,
  Wand2,
  type LucideIcon,
} from "lucide-react";
import { useState, type ReactNode } from "react";

import type { TemplateSummary } from "@/app/openapi-client/types.gen";
import { FlowCanvas, PaletteItem, freshId, type CanvasEdge, type CanvasNode } from "@/components/canvas/flow-canvas";
import { Button } from "@/components/ui/button";
import { NodeSettings, type NsField } from "@/components/workflows/node-settings";
import {
  BODIES,
  GROUPS,
  LOOP_ARGS,
  NODES,
  bodyAt,
  defaultConfig,
  edgePorts,
  foldSelection,
  inScope,
  infoFor,
  nodeSummary,
  portsOf,
  problems as findProblems,
  replaceSelection,
  withBody,
  type BodyKind,
  type CustomDef,
  type Scope,
  type WfGraph,
  type WfNode,
} from "@/components/workflows/workflow-model";

/** Icons a custom node can carry, by name. */
export const ICONS: Record<string, LucideIcon> = {
  puzzle: Puzzle,
  sparkles: Sparkles,
  "git-branch": GitBranch,
  filter: Filter,
  braces: Braces,
  tags: Tags,
  "file-text": FileText,
  repeat: Repeat,
  search: Search,
  "list-tree": ListTree,
  "wand-2": Wand2,
  bot: Bot,
  mail: Mail,
  calendar: Calendar,
};

export type NodeTrace = {
  status: string;
  ms?: number | null;
  value?: string | null;
  error?: string | null;
  ports?: string[];
};
type CatalogType = { type: string; scopes?: string[]; settings?: string[] };

export type Folded = Exclude<ReturnType<typeof foldSelection>, string>;

export function GraphEditor({
  graph,
  setGraph,
  scope,
  root,
  readOnly,
  catalog,
  customDefs,
  selfId,
  templates,
  fields,
  trace,
  params,
  emptyPanel,
  onSaveAsCustom,
}: {
  graph: WfGraph;
  setGraph: (fn: (g: WfGraph) => WfGraph) => void;
  scope: Scope;
  /** What the outermost graph is: a workflow, or a custom node's body. */
  root: "workflow" | "custom";
  readOnly: boolean;
  catalog?: CatalogType[];
  customDefs: CustomDef[];
  /** The custom node being edited, left off its own palette. */
  selfId?: number;
  templates: TemplateSummary[];
  fields: NsField[];
  trace?: Record<string, NodeTrace>;
  params?: string[];
  emptyPanel: ReactNode;
  /** Fold the selected nodes into a new custom node: resolves to it once saved (or null if cancelled). */
  onSaveAsCustom?: (folded: Folded) => Promise<CustomDef | null>;
}) {
  const [path, setPath] = useState<string[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [multi, setMulti] = useState<string[]>([]);
  const [note, setNote] = useState<string | null>(null);
  const tpl = new Map(templates.map((t) => [t.id, t]));
  const defs = new Map(customDefs.map((d) => [d.id, d]));
  const custom = (id: number) => defs.get(id);
  const fieldName = (fid: number) => {
    const f = fields.find((x) => x.id === fid);
    return f ? `${f.namespace} · ${f.label}` : undefined;
  };

  // Where we are: the graph shown, and what it is the body of.
  const view = bodyAt(graph, path) ?? graph;
  const crumbs = path.map((_, k) => bodyAt(graph, path.slice(0, k))!.nodes.find((n) => n.id === path[k])!);
  const holder = crumbs[crumbs.length - 1];
  const kind: BodyKind = holder ? (holder.type as BodyKind) : root === "custom" ? "custom" : "workflow";
  const edit = (fn: (v: WfGraph) => WfGraph) => setGraph((g) => withBody(g, path, fn(bodyAt(g, path) ?? g)));
  const open = (nid: string) => {
    const n = view.nodes.find((x) => x.id === nid);
    if (n && BODIES.includes(n.type)) {
      setPath([...path, nid]);
      setSel(null);
      setMulti([]);
    }
  };
  const goTo = (k: number) => {
    setPath(path.slice(0, k));
    setSel(null);
    setMulti([]);
  };

  const problems = findProblems(view, (t) => tpl.get(t)?.kind, scope, kind, custom);
  const traceKey = (nid: string) => [...path, nid].join("/");

  const add = (type: string, x?: number, y?: number, config?: Record<string, unknown>) => {
    const at = view.nodes.find((n) => n.id === sel);
    const nid = freshId(
      view.nodes.map((n) => n.id),
      type === "custom" ? "c" : type.split("_")[0],
    );
    const node: WfNode = {
      id: nid,
      type,
      config: config ?? defaultConfig(type),
      x: x ?? (at?.x ?? 0) + 280,
      y: y ?? (at?.y ?? 120) + (at ? 40 : 0),
    };
    if (type === "arg" && LOOP_ARGS[kind]) {
      const used = new Set(view.nodes.filter((n) => n.type === "arg").map((n) => String(n.config.name)));
      node.config = { name: LOOP_ARGS[kind].find((a) => !used.has(a)) ?? LOOP_ARGS[kind][0] };
    }
    edit((v) => {
      const edges = [...v.edges];
      // Clicking a palette item with a node selected connects it after that node, like n8n's "+".
      const from = at ? portsOf(at, custom).outputs[0] : undefined;
      const into = portsOf(node, custom).inputs[0];
      if (x == null && at && from && into)
        edges.push({
          source: at.id,
          target: nid,
          ...(from !== "out" ? { port: from } : {}),
          ...(into !== "in" ? { input: into } : {}),
        });
      return { nodes: [...v.nodes, node], edges };
    });
    setSel(nid);
  };
  const connect = (e: CanvasEdge) => {
    const target = view.nodes.find((n) => n.id === e.target);
    if (!target || target.type === "input" || target.type === "arg") return;
    const port = e.port ?? "out";
    const input = e.input ?? "in";
    const many = portsOf(target, custom).many;
    edit((v) => {
      // An input takes one connection (a merge's takes any number): a new one replaces the old.
      const keep = many ? v.edges : v.edges.filter((x) => !(x.target === e.target && edgePorts(x).input === input));
      if (
        keep.some(
          (x) =>
            x.source === e.source &&
            x.target === e.target &&
            edgePorts(x).port === port &&
            edgePorts(x).input === input,
        )
      )
        return v;
      return {
        ...v,
        edges: [
          ...keep,
          {
            source: e.source,
            target: e.target,
            ...(port !== "out" ? { port } : {}),
            ...(input !== "in" ? { input } : {}),
          },
        ],
      };
    });
  };
  const removeNodes = (ids: string[]) => {
    edit((v) => {
      const gone = new Set(ids.filter((i) => v.nodes.find((n) => n.id === i)?.type !== "input"));
      return {
        nodes: v.nodes.filter((n) => !gone.has(n.id)),
        edges: v.edges.filter((e) => !gone.has(e.source) && !gone.has(e.target)),
      };
    });
    if (sel && ids.includes(sel)) setSel(null);
    setMulti([]);
  };
  const removeEdges = (es: CanvasEdge[]) =>
    edit((v) => ({
      ...v,
      edges: v.edges.filter(
        (e) =>
          !es.some(
            (x) =>
              x.source === e.source &&
              x.target === e.target &&
              (x.port ?? "out") === edgePorts(e).port &&
              (x.input ?? "in") === edgePorts(e).input,
          ),
      ),
    }));
  const move = (nid: string, x: number, y: number) =>
    edit((v) => ({ ...v, nodes: v.nodes.map((n) => (n.id === nid ? { ...n, x, y } : n)) }));
  const update = (n: WfNode) => edit((v) => ({ ...v, nodes: v.nodes.map((x) => (x.id === n.id ? n : x)) }));

  const picked = multi.length ? multi : sel ? [sel] : [];
  const fold = async (asCustom: boolean) => {
    const f = foldSelection(view, picked);
    if (typeof f === "string") {
      setNote(f);
      return;
    }
    setNote(null);
    const sx = view.nodes.filter((n) => picked.includes(n.id));
    const x = Math.min(...sx.map((n) => n.x ?? 0));
    const y = Math.min(...sx.map((n) => n.y ?? 0));
    let node: WfNode;
    if (asCustom) {
      const d = await onSaveAsCustom?.(f);
      if (!d) return;
      node = {
        id: freshId(
          view.nodes.map((n) => n.id),
          "c",
        ),
        type: "custom",
        config: { node: d.id, version: d.version },
        x,
        y,
      };
    } else {
      node = {
        id: freshId(
          view.nodes.map((n) => n.id),
          "group",
        ),
        type: "group",
        config: { body: f.body },
        x,
        y,
      };
    }
    edit((v) => replaceSelection(v, picked, f, node));
    setSel(node.id);
    setMulti([]);
  };

  const canvasNodes: CanvasNode[] = view.nodes.map((n) => {
    const info = infoFor(n.type, scope);
    const d = n.type === "custom" ? custom(Number(n.config.node)) : undefined;
    const p = portsOf(n, custom);
    const t = trace?.[traceKey(n.id)];
    return {
      id: n.id,
      x: n.x ?? 0,
      y: n.y ?? 0,
      title: n.label || d?.name || info?.label || n.type,
      subtitle: nodeSummary(n, { template: (x) => tpl.get(x)?.name, field: fieldName, custom }),
      icon: d ? (ICONS[d.icon ?? ""] ?? Puzzle) : n.type === "group" ? GroupIcon : info?.icon,
      inputs: p.inputs.length === 0 ? 0 : p.many ? -1 : p.inputs.length,
      inPorts: p.inputs,
      outputs: p.outputs,
      tone: (d?.color as CanvasNode["tone"]) ?? info?.tone,
      io:
        n.type === "custom" || n.type === "group"
          ? `${p.inputs.join(", ") || "—"} → ${p.outputs.join(", ") || "keeps"}`
          : info
            ? `${info.takes} → ${n.type === "output" && typeof n.config.key === "string" && n.config.key ? `outputs.${n.config.key}` : info.gives}`
            : undefined,
      problem: problems[n.id],
      status: t?.status as CanvasNode["status"],
      peek: t?.status === "done" ? (t.value ?? undefined) : t?.status === "failed" ? (t.error ?? undefined) : undefined,
      opens: BODIES.includes(n.type),
    };
  });
  const canvasEdges: CanvasEdge[] = view.edges.map((e) => ({ source: e.source, target: e.target, ...edgePorts(e) }));
  const cur = view.nodes.find((n) => n.id === sel);
  const settingsOf = (type: string) => catalog?.find((x) => x.type === type)?.settings;
  const usable = customDefs.filter((d) => d.scopes.includes(scope) && d.id !== selfId);

  return (
    <div className="grid flex-1 lg:h-[calc(100vh-150px)] lg:min-h-[560px] lg:flex-none lg:grid-cols-[230px_minmax(0,1fr)_360px] lg:grid-rows-[minmax(0,1fr)]">
      <aside
        aria-label="Nodes"
        className="flex flex-col gap-1.5 overflow-y-auto border-b border-border bg-surface p-3.5 lg:border-b-0 lg:border-r"
      >
        <p className="pb-1 text-[11.5px] text-fg-muted">
          Drag onto the canvas, or click to add after the selected node. Shift-drag to select several.
        </p>
        {kind !== "workflow" && (
          <div className="flex flex-col gap-1.5 pb-2">
            <span className="label-caps pt-1">This body</span>
            {(["arg", "return"] as const).map((type) => (
              <PaletteItem
                key={type}
                payload={type}
                icon={NODES[type].icon}
                title={NODES[type].label}
                hint={NODES[type].describe}
                disabled={readOnly}
                onAdd={() => add(type)}
              />
            ))}
          </div>
        )}
        {GROUPS.map((g) => (
          <div key={g} className="flex flex-col gap-1.5 pb-2">
            <span className="label-caps pt-1">{g}</span>
            {Object.entries(NODES)
              .filter(([type, info]) => info.group === g && type !== "custom" && inScope(type, scope, catalog))
              .map(([type, info]) => (
                <PaletteItem
                  key={type}
                  payload={type}
                  icon={info.icon}
                  title={info.label}
                  hint={info.describe}
                  disabled={readOnly}
                  onAdd={() => add(type)}
                />
              ))}
          </div>
        ))}
        <div className="flex flex-col gap-1.5 pb-2">
          <span className="label-caps pt-1">Custom nodes</span>
          {usable.length ? (
            usable.map((d) => (
              <PaletteItem
                key={d.id}
                payload={`custom:${d.id}`}
                icon={ICONS[d.icon ?? ""] ?? Puzzle}
                title={d.name}
                hint={d.description ?? `${d.inputs.join(", ")} → ${d.outputs.join(", ") || "keeps"}`}
                disabled={readOnly}
                onAdd={() => add("custom", undefined, undefined, { node: d.id, version: d.version })}
              />
            ))
          ) : (
            <p className="text-[11.5px] text-fg-muted">
              None yet. Select nodes and choose Save as custom node, or make one under Custom nodes.
            </p>
          )}
        </div>
      </aside>
      <section
        aria-label="Canvas"
        className="relative flex min-h-[520px] flex-col border-b border-border lg:border-b-0"
      >
        <div className="flex h-[44px] items-center gap-2 overflow-x-auto border-b border-border px-3 text-[12.5px]">
          <nav aria-label="Where you are" className="flex flex-wrap items-center gap-1 font-medium text-fg-muted">
            <button type="button" className="hover:text-fg hover:underline" onClick={() => goTo(0)}>
              {root === "custom" ? "Custom node" : "Workflow"}
            </button>
            {crumbs.map((n, k) => (
              <span key={n.id} className="flex items-center gap-1">
                <ChevronRight aria-hidden className="size-3.5" />
                <button
                  type="button"
                  className={k === crumbs.length - 1 ? "font-bold text-fg" : "hover:text-fg hover:underline"}
                  onClick={() => goTo(k + 1)}
                >
                  {n.label || NODES[n.type]?.label}
                </button>
              </span>
            ))}
          </nav>
          <span className="flex-1" />
          {picked.length > 0 && !readOnly && (
            <>
              <span className="text-fg-muted">{picked.length} selected</span>
              <Button size="sm" variant="ghost" onClick={() => void fold(false)}>
                Group
              </Button>
              {onSaveAsCustom && (
                <Button size="sm" variant="secondary" onClick={() => void fold(true)}>
                  Save as custom node
                </Button>
              )}
            </>
          )}
        </div>
        {(note || problems[""]) && (
          <p role="alert" className="border-b border-red-border bg-red-surface px-3 py-1.5 text-[12.5px] text-red-dark">
            {note ?? problems[""]}
          </p>
        )}
        <div className="relative flex-1">
          <FlowCanvas
            key={path.join("/")}
            nodes={canvasNodes}
            edges={canvasEdges}
            selected={sel}
            multi={multi}
            onMultiSelect={(ids) => setMulti((m) => (m.join() === ids.join() ? m : ids))}
            onOpen={open}
            readOnly={readOnly}
            onSelect={setSel}
            onMove={move}
            onConnect={connect}
            onDeleteNodes={removeNodes}
            onDeleteEdges={removeEdges}
            onDrop={(payload, x, y) => {
              if (payload.startsWith("custom:")) {
                const d = custom(Number(payload.slice(7)));
                if (d) add("custom", x, y, { node: d.id, version: d.version });
              } else if (NODES[payload] && payload !== "input" && inScope(payload, scope, catalog)) add(payload, x, y);
            }}
          />
        </div>
      </section>
      <aside aria-label="Node settings" className="overflow-y-auto border-border p-4 lg:border-l">
        {cur && BODIES.includes(cur.type) && (
          <Button size="sm" variant="secondary" className="mb-3 w-full" onClick={() => open(cur.id)}>
            Open its body
          </Button>
        )}
        {cur ? (
          <NodeSettings
            node={cur}
            onChange={update}
            templates={templates}
            fields={fields}
            readOnly={readOnly}
            problem={problems[cur.id]}
            scope={scope}
            custom={custom}
            bodyKind={kind}
            onUpgrade={() => {
              const d = custom(Number(cur.config.node));
              if (d) update({ ...cur, config: { ...cur.config, version: d.current } });
            }}
            params={params}
            settings={settingsOf(cur.type)}
            peek={trace?.[traceKey(cur.id)] as { status: string; value?: string; error?: string } | undefined}
          />
        ) : kind === "workflow" || (kind === "custom" && !holder) ? (
          emptyPanel
        ) : (
          <div className="flex flex-col gap-2 text-[13px] text-fg-secondary">
            <h2 className="text-[15px] font-bold text-fg">Inside {holder?.label || NODES[kind]?.label}</h2>
            {kind === "for_each" && (
              <p>
                This body runs once for each item of the list. It starts from Input nodes: item (this one), index (its
                place, from 0) and input (all of what came in). What reaches Return out is kept; an item that reaches no
                Return is left out, so a condition here filters the list.
              </p>
            )}
            {kind === "repeat" && (
              <p>
                This body runs again and again: state is what the last round returned (the first time, what came in),
                round counts from 0. What reaches Return out is the next state; it stops when the test passes, after the
                most rounds, or when nothing reaches Return.
              </p>
            )}
            {kind === "group" && (
              <p>
                A group’s Input nodes are its inputs on the canvas outside, and its Return nodes are its outputs. Name
                them here.
              </p>
            )}
            <Button size="sm" variant="ghost" onClick={() => goTo(path.length - 1)}>
              Back out
            </Button>
          </div>
        )}
      </aside>
    </div>
  );
}
