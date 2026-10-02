"use client";

import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, FlaskConical, Workflow as WorkflowIcon } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { Fields, Workflows } from "@/app/openapi-client";
import type { TemplateSummary, Workflow } from "@/app/openapi-client/types.gen";
import { FlowCanvas, PaletteItem, freshId, type CanvasEdge, type CanvasNode } from "@/components/canvas/flow-canvas";
import { useTemplateList, useWorkflowCatalog } from "@/components/pipelines/catalog-header";
import { RunDialog } from "@/components/pipelines/pipeline-editor";
import { NodeSettings, type NsField } from "@/components/workflows/node-settings";
import {
  GROUPS,
  NODES,
  cleanGraph,
  defaultConfig,
  inScope,
  infoFor,
  nodeSummary,
  problems as findProblems,
  sameGraph,
  starter,
  type Scope,
  type WfEdge,
  type WfGraph,
  type WfNode,
} from "@/components/workflows/workflow-model";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { Menu, MenuContent, MenuItem, MenuLabel, MenuTrigger } from "@/components/ui/menu";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { Segmented } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/toast";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { relative } from "@/lib/format";
import { useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

/** A workflow on the canvas: drag nodes from the palette, connect their ports, set each one up, publish versions. */
export function WorkflowEditor({ id }: { id?: number }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const router = useRouter();
  const toast = useToast();
  const { admin, namespaces, can } = useArchive();
  const creating = id == null;
  const [version, setVersion] = useState<number | null>(null);
  const q = useQuery({
    queryKey: ["workflow", id, version ?? "current"],
    queryFn: () =>
      data(Workflows.getWorkflow({ client, path: { wid: id! }, query: version ? { version } : undefined })),
    enabled: !creating,
  });
  const templates = useTemplateList();
  const catalog = useWorkflowCatalog();
  const tpl = useMemo(
    () => new Map(((templates.data ?? []) as TemplateSummary[]).map((t) => [t.id, t])),
    [templates.data],
  );
  const fieldLists = useQueries({
    queries: namespaces.map((n) => ({
      queryKey: ["fields", n.name],
      queryFn: () => data(Fields.listFields({ client, path: { name: n.name } })),
      staleTime: 60_000,
    })),
  });
  const fields: NsField[] = namespaces.flatMap((n, i) =>
    (fieldLists[i]?.data ?? []).map((f) => ({ ...f, namespace: n.name })),
  );
  const fieldName = (fid: number) => {
    const f = fields.find((x) => x.id === fid);
    return f ? `${f.namespace} · ${f.label}` : undefined;
  };

  const base: Workflow | undefined = q.data;
  const [newScope, setNewScope] = useState<Scope>("recording");
  const scope: Scope = creating ? newScope : base?.scope === "graph" ? "graph" : "recording";
  const [graph, setGraph] = useState<WfGraph>(() => (creating ? starter() : { nodes: [], edges: [] }));
  const [sel, setSel] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [notes, setNotes] = useState("");
  const [runOpen, setRunOpen] = useState(false);
  const [lastJob, setLastJob] = useState<{ job: number; title: string } | null>(null);

  useEffect(() => {
    if (base) {
      setGraph(base.graph as WfGraph);
      setSel(null);
    }
  }, [base]);

  const readOnly = !admin;
  const why = readOnly ? "Only admins can change workflows" : undefined;
  const latest = base ? Math.max(base.current, ...(base.history ?? []).map((h) => h.version)) : 0;
  const dirty = creating ? true : base ? !sameGraph(base.graph as WfGraph, graph) : false;
  const problems = findProblems(graph, (tid) => tpl.get(tid)?.kind, scope);
  const firstProblem = Object.entries(problems)[0];
  const nextV = latest + 1;
  const publishReason =
    why ??
    (firstProblem
      ? `Fix this first: ${firstProblem[1]}`
      : !creating && !dirty && version == null
        ? "Nothing changed since the published version"
        : undefined);

  const save = useMutation({
    mutationFn: async (publish: boolean) => {
      const body = cleanGraph(graph);
      if (creating) {
        const r = await data(Workflows.createWorkflow({ client, body: { name: name.trim(), graph: body, scope } }));
        return { id: r.id, version: 1 };
      }
      const r = await data(
        Workflows.createWorkflowVersion({
          client,
          path: { wid: id! },
          body: { graph: body, notes: notes.trim() || null, publish },
        }),
      );
      return { id: id!, version: r.version };
    },
    onSuccess: (r, publish) => {
      void qc.invalidateQueries({ queryKey: ["workflows"] });
      void qc.invalidateQueries({ queryKey: ["workflow", r.id] });
      setNotes("");
      if (creating) {
        toast({ tone: "green", title: "Workflow created", body: name });
        router.push(`/workflows/${r.id}`);
        return;
      }
      setVersion(publish ? null : r.version);
      toast({
        tone: "green",
        title: publish ? `Published v${r.version}` : `Saved draft v${r.version}`,
        body: publish
          ? `${scope === "graph" ? "Routines" : "Pipelines"} that run it use this version from their next run.`
          : "Publish it when it’s ready.",
      });
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t save", body: e.message }),
  });

  const run = useMutation({
    mutationFn: ({ rid }: { rid: number; title: string }) =>
      data(Workflows.runWorkflow({ client, path: { wid: id! }, body: { recording: rid } })),
    onSuccess: (r, v) => {
      setRunOpen(false);
      setLastJob({ job: r.job, title: v.title });
      void qc.invalidateQueries({ queryKey: ["jobs"] });
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t start the run", body: e.message }),
  });

  // Editing the graph
  const add = (type: string, x?: number, y?: number) => {
    const at = graph.nodes.find((n) => n.id === sel);
    const nid = freshId(
      graph.nodes.map((n) => n.id),
      type.split("_")[0],
    );
    const node: WfNode = {
      id: nid,
      type,
      config: defaultConfig(type),
      x: x ?? (at?.x ?? 0) + 280,
      y: y ?? (at?.y ?? 120) + (at ? 40 : 0),
    };
    const edges = [...graph.edges];
    // Clicking a palette item with a node selected connects it after that node, like n8n's "+".
    if (x == null && at && NODES[at.type]?.outputs.length)
      edges.push({ source: at.id, target: nid, ...(at.type === "condition" ? { branch: "yes" as const } : {}) });
    setGraph({ nodes: [...graph.nodes, node], edges });
    setSel(nid);
  };
  const connect = (e: CanvasEdge) => {
    const target = graph.nodes.find((n) => n.id === e.target);
    if (!target || target.type === "input") return;
    const branch = e.port === "yes" || e.port === "no" ? e.port : undefined;
    const keep = target.type === "merge" ? graph.edges : graph.edges.filter((x) => x.target !== e.target); // one input: replace it
    if (keep.some((x) => x.source === e.source && x.target === e.target && x.branch === branch)) return;
    setGraph({ ...graph, edges: [...keep, { source: e.source, target: e.target, ...(branch ? { branch } : {}) }] });
  };
  const removeNodes = (ids: string[]) => {
    const gone = new Set(ids.filter((i) => graph.nodes.find((n) => n.id === i)?.type !== "input"));
    setGraph({
      nodes: graph.nodes.filter((n) => !gone.has(n.id)),
      edges: graph.edges.filter((e) => !gone.has(e.source) && !gone.has(e.target)),
    });
    if (sel && gone.has(sel)) setSel(null);
  };
  const removeEdges = (es: CanvasEdge[]) =>
    setGraph({
      ...graph,
      edges: graph.edges.filter(
        (e) =>
          !es.some((x) => x.source === e.source && x.target === e.target && (x.port ?? "out") === (e.branch ?? "out")),
      ),
    });
  const move = (nid: string, x: number, y: number) =>
    setGraph((g) => ({ ...g, nodes: g.nodes.map((n) => (n.id === nid ? { ...n, x, y } : n)) }));
  const update = (n: WfNode) => setGraph((g) => ({ ...g, nodes: g.nodes.map((x) => (x.id === n.id ? n : x)) }));

  const canvasNodes: CanvasNode[] = graph.nodes.map((n) => {
    const info = infoFor(n.type, scope);
    return {
      id: n.id,
      x: n.x ?? 0,
      y: n.y ?? 0,
      title: n.label || info?.label || n.type,
      subtitle: nodeSummary(n, { template: (t) => tpl.get(t)?.name, field: fieldName }),
      icon: info?.icon,
      inputs: info?.inputs ?? 1,
      outputs: info?.outputs ?? ["out"],
      tone: info?.tone,
      io: info
        ? `${info.takes} → ${n.type === "output" && n.config.key ? `outputs.${String(n.config.key)}` : info.gives}`
        : undefined,
      problem: problems[n.id],
    };
  });
  const canvasEdges: CanvasEdge[] = graph.edges.map((e: WfEdge) => ({
    source: e.source,
    target: e.target,
    port: e.branch ?? "out",
  }));
  const cur = graph.nodes.find((n) => n.id === sel);

  if (!creating && q.isLoading)
    return (
      <div className="flex flex-col gap-4 p-6" aria-busy="true" aria-label="Loading workflow">
        <Skeleton className="h-7 w-72" />
        <Skeleton className="h-[420px] w-full rounded-md" />
      </div>
    );
  if (!creating && (q.error || !base)) {
    const e = q.error as ApiError | null;
    return (
      <EmptyState
        tone={e?.status === 404 ? "neutral" : "error"}
        icon={<WorkflowIcon />}
        title={e?.status === 404 ? "This workflow doesn’t exist" : "Couldn’t load the workflow"}
        actions={
          <Button asChild variant="secondary">
            <Link href="/workflows">All workflows</Link>
          </Button>
        }
      >
        {e?.message}
      </EmptyState>
    );
  }

  return (
    <div className="flex min-h-[calc(100vh-64px)] flex-col">
      <header className="flex flex-wrap items-center gap-3 border-b border-border px-4 py-3.5 md:px-5">
        <nav aria-label="Breadcrumb" className="w-full text-[12px] font-medium text-fg-muted">
          <Link href="/workflows" className="hover:text-fg hover:underline">
            Workflows
          </Link>{" "}
          › {creating ? "New workflow" : base?.name}
        </nav>
        {creating ? (
          <Input
            aria-label="Workflow name"
            placeholder="Name, e.g. Entities and meeting notes"
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="h-9 w-[320px] text-[16px] font-bold"
            autoFocus
          />
        ) : (
          <h1 className="text-[18px] font-bold text-fg">{base?.name}</h1>
        )}
        {creating && (
          <Segmented
            label="What it runs on"
            value={scope}
            onChange={(v) => {
              setNewScope(v as Scope);
              setGraph(starter(v as Scope));
              setSel(null);
            }}
            items={[
              { value: "recording", label: "On recordings" },
              { value: "graph", label: "Organise the graph" },
            ]}
          />
        )}
        {!creating && (
          <span
            className={cn(
              "h-[22px] rounded-pill border px-2 text-[11px] font-bold uppercase leading-5",
              dirty || (version != null && version !== base?.current)
                ? "border-blue-border bg-blue-surface text-fg-accent"
                : "border-green-border bg-green-surface text-green-dark",
            )}
          >
            {dirty
              ? `Draft v${nextV}`
              : version != null && version !== base?.current
                ? `v${version} · not published`
                : `v${base?.current} · published`}
          </span>
        )}
        <span className="flex-1" />
        {!creating && (base?.history?.length ?? 0) > 1 && (
          <Menu>
            <MenuTrigger asChild>
              <Button size="sm" variant="ghost">
                Versions <ChevronDown />
              </Button>
            </MenuTrigger>
            <MenuContent align="end" className="w-[300px]">
              <MenuLabel>Open a version</MenuLabel>
              {(base?.history ?? []).map((h) => (
                <MenuItem
                  key={h.version}
                  onSelect={() => setVersion(h.version === base?.current && version == null ? null : h.version)}
                  shortcut={relative(h.created_at)}
                >
                  v{h.version}
                  {h.version === base?.current ? " · published" : ""}
                  {h.notes ? ` · ${h.notes}` : ""}
                </MenuItem>
              ))}
            </MenuContent>
          </Menu>
        )}
        {!creating && scope === "recording" && (
          <Button
            size="sm"
            variant="secondary"
            icon={<FlaskConical />}
            disabled={!can("editor") || dirty}
            disabledReason={
              !can("editor") ? "Needs editor access to a recording’s namespace" : "Publish your changes first"
            }
            onClick={() => setRunOpen(true)}
          >
            Run on a recording
          </Button>
        )}
        {!creating && (
          <Input
            aria-label="Version notes"
            placeholder="What changed (optional)"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            className="h-8 w-[200px] text-[13px]"
            disabled={readOnly || !dirty}
          />
        )}
        {!creating && (
          <Button
            size="sm"
            variant="secondary"
            disabled={Boolean(why) || !dirty || Boolean(firstProblem) || save.isPending}
            disabledReason={why ?? (firstProblem ? firstProblem[1] : "Nothing to save")}
            onClick={() => save.mutate(false)}
          >
            Save draft
          </Button>
        )}
        <Button
          size="sm"
          variant="approve"
          disabled={Boolean(publishReason) || save.isPending || (creating && !name.trim())}
          disabledReason={publishReason ?? "Give the workflow a name"}
          onClick={() => save.mutate(true)}
        >
          {creating ? "Create workflow" : `Publish v${nextV}`}
        </Button>
      </header>

      {lastJob && (
        <p className="border-b border-border bg-blue-surface px-5 py-2 text-[13px] text-fg">
          Running on {lastJob.title}.{" "}
          <Link href={`/activity/${lastJob.job}`} className="font-semibold text-fg-accent hover:underline">
            Follow it in Activity
          </Link>
        </p>
      )}
      {problems[""] && (
        <p role="alert" className="border-b border-red-border bg-red-surface px-5 py-2 text-[13px] text-red-dark">
          {problems[""]}
        </p>
      )}

      <div className="grid flex-1 lg:h-[calc(100vh-150px)] lg:flex-none lg:min-h-[560px] lg:grid-rows-[minmax(0,1fr)] lg:grid-cols-[230px_minmax(0,1fr)_360px]">
        <aside
          aria-label="Nodes"
          className="flex flex-col gap-1.5 overflow-y-auto border-b border-border bg-surface p-3.5 lg:border-b-0 lg:border-r"
        >
          <p className="pb-1 text-[11.5px] text-fg-muted">
            Drag onto the canvas, or click to add after the selected node.
          </p>
          {GROUPS.map((g) => (
            <div key={g} className="flex flex-col gap-1.5 pb-2">
              <span className="label-caps pt-1">{g}</span>
              {Object.entries(NODES)
                .filter(([type, info]) => info.group === g && inScope(type, scope, catalog.data?.node_types))
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
        </aside>
        <section aria-label="Canvas" className="relative min-h-[520px] border-b border-border lg:border-b-0">
          <FlowCanvas
            nodes={canvasNodes}
            edges={canvasEdges}
            selected={sel}
            readOnly={readOnly}
            onSelect={setSel}
            onMove={move}
            onConnect={connect}
            onDeleteNodes={removeNodes}
            onDeleteEdges={removeEdges}
            onDrop={(type, x, y) =>
              NODES[type] && type !== "input" && inScope(type, scope, catalog.data?.node_types) && add(type, x, y)
            }
          />
        </section>
        <aside aria-label="Node settings" className="overflow-y-auto border-border p-4 lg:border-l">
          {cur ? (
            <NodeSettings
              node={cur}
              onChange={update}
              templates={(templates.data ?? []) as TemplateSummary[]}
              fields={fields}
              readOnly={readOnly}
              problem={problems[cur.id]}
              scope={scope}
            />
          ) : scope === "graph" ? (
            <div className="flex flex-col gap-2 text-[13px] text-fg-secondary">
              <h2 className="text-[15px] font-bold text-fg">How graph workflows run</h2>
              <p>
                A graph workflow starts from the namespaces a routine runs it over. Look-alike entities finds pairs that
                may be one thing, Ask the model judges each pair, and Apply changes merges or links the sure ones and
                proposes the rest.
              </p>
              <p>
                Run it from a routine. Proposed changes wait in Routines → Proposed changes, and every change can be
                undone.
              </p>
            </div>
          ) : (
            <div className="flex flex-col gap-2 text-[13px] text-fg-secondary">
              <h2 className="text-[15px] font-bold text-fg">How workflows run</h2>
              <p>
                A workflow starts from the Recording node with what the pipeline made of it: transcript, summary,
                entities and earlier outputs. Each node passes what it makes along its connections.
              </p>
              <p>
                Attach it to a pipeline as a Workflow step; it runs after the steps before it. Select a node to set it
                up; Delete removes the node or connection you selected.
              </p>
            </div>
          )}
        </aside>
      </div>

      <RunDialog
        open={runOpen}
        onOpenChange={setRunOpen}
        onRun={(rid, title) => run.mutate({ rid, title })}
        pending={run.isPending}
      />
    </div>
  );
}
