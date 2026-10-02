"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AudioLines,
  Captions,
  Clapperboard,
  Eye,
  FileOutput,
  FileText,
  FlaskConical,
  List,
  ScanFace,
  ScanText,
  Shapes,
  Sparkles,
  TextSearch,
  Workflow,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { Pipelines } from "@/app/openapi-client";
import type { TemplateSummary } from "@/app/openapi-client/types.gen";
import { stepLabel } from "@/components/activity/job-model";
import { FlowCanvas, PaletteItem, freshId, type CanvasEdge, type CanvasNode } from "@/components/canvas/flow-canvas";
import { useTemplateList, useWorkflowCatalog } from "@/components/pipelines/catalog-header";
import { RunDialog } from "@/components/pipelines/pipeline-editor";
import {
  DESCRIBE,
  PROVIDES,
  cleanPipelineGraph,
  graphOrder,
  orderProblem,
  specProblems,
  stepSummary,
  toSpec,
  type PlGraph,
  type PlNode,
} from "@/components/pipelines/pipeline-model";
import { StepSettings } from "@/components/pipelines/step-settings";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { STEP_TONE } from "@/components/ui/loop";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

const ICON: Record<string, LucideIcon> = {
  transcribe: Captions,
  diarize: AudioLines,
  shots: Clapperboard,
  ocr: ScanText,
  faces: ScanFace,
  objects: Shapes,
  describe: Eye,
  analyze: TextSearch,
  summarize: Sparkles,
  llm: Sparkles,
  report: FileText,
  export: FileOutput,
  workflow: Workflow,
};
const TONE = { intent: "blue", red: "red", green: "green", gate: "gold", neutral: "neutral" } as const;
const ASSETS = ["transcribe", "diarize", "shots", "ocr", "faces", "objects", "describe"];
const METADATA = ["analyze", "summarize", "llm", "report", "export"];

/** A pipeline drawn as a graph: asset steps, metadata steps and workflows, an edge saying what runs after what. */
export function PipelineCanvas({ id }: { id: number }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const { admin, can } = useArchive();
  const q = useQuery({
    queryKey: ["pipeline", id, "current"],
    queryFn: () => data(Pipelines.getPipeline({ client, path: { pid: id } })),
  });
  const templates = useTemplateList();
  const workflows = useWorkflowCatalog();
  const tpl = useMemo(
    () => new Map(((templates.data ?? []) as TemplateSummary[]).map((t) => [t.id, t])),
    [templates.data],
  );
  const wfName = (wid?: number) => workflows.data?.workflows.find((w) => w.id === wid)?.name;

  const base = q.data;
  const [graph, setGraph] = useState<PlGraph>({ nodes: [], edges: [] });
  const [sel, setSel] = useState<string | null>(null);
  const [notes, setNotes] = useState("");
  const [runOpen, setRunOpen] = useState(false);
  const [lastJob, setLastJob] = useState<{ job: number; title: string } | null>(null);
  const loaded = useMemo(
    () =>
      base
        ? {
            nodes: (base.graph.nodes as { id: string; step: unknown; x?: number; y?: number }[]).map((n) => ({
              ...n,
              step: toSpec(n.step),
            })),
            edges: base.graph.edges as { source: string; target: string }[],
          }
        : null,
    [base],
  );
  useEffect(() => {
    if (loaded) {
      setGraph(loaded);
      setSel(null);
    }
  }, [loaded]);

  const readOnly = !admin;
  const why = readOnly ? "Only admins can change pipelines" : undefined;
  const dirty = loaded
    ? JSON.stringify(cleanPipelineGraph(loaded)) !== JSON.stringify(cleanPipelineGraph(graph))
    : false;
  const order = graphOrder(graph);
  const stepProblems = specProblems(
    graph.nodes.map((n) => n.step),
    (tid) => tpl.get(tid)?.kind,
  );
  const nodeProblem = (k: number) => stepProblems[k];
  const graphProblem = !order
    ? "The pipeline has a loop."
    : (orderProblem(order.map((n) => n.step)) ?? (graph.nodes.length ? null : "A pipeline needs at least one step."));
  const firstProblem =
    graphProblem ?? Object.entries(stepProblems).find(([k]) => Number(k) >= 0)?.[1] ?? stepProblems[-1] ?? null;
  const latest = base ? Math.max(base.current, ...(base.history ?? []).map((h) => h.version)) : 0;

  const save = useMutation({
    mutationFn: (publish: boolean) =>
      data(
        Pipelines.createPipelineVersion({
          client,
          path: { pid: id },
          body: { graph: cleanPipelineGraph(graph), notes: notes.trim() || null, publish },
        }),
      ),
    onSuccess: (r, publish) => {
      void qc.invalidateQueries({ queryKey: ["pipelines"] });
      void qc.invalidateQueries({ queryKey: ["pipeline", id] });
      setNotes("");
      toast({
        tone: "green",
        title: publish ? `Published v${r.version}` : `Saved draft v${r.version}`,
        body: publish
          ? "New runs use it; runs in progress keep their version."
          : "Publish it from the editor when it’s ready.",
      });
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t save", body: e.message }),
  });
  const run = useMutation({
    mutationFn: ({ rid }: { rid: number; title: string }) =>
      data(Pipelines.runPipeline({ client, path: { pid: id }, body: { recording: rid } })),
    onSuccess: (r, v) => {
      setRunOpen(false);
      setLastJob({ job: r.job, title: v.title });
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t start the run", body: e.message }),
  });

  const add = (payload: string, x?: number, y?: number) => {
    const [type, wid] = payload.split(":");
    const at = graph.nodes.find((n) => n.id === sel);
    const nid = freshId(
      graph.nodes.map((n) => n.id),
      "s",
    );
    const node: PlNode = {
      id: nid,
      step: { type, ...(type === "llm" ? { key: "" } : {}), ...(wid ? { workflow: Number(wid) } : {}) },
      x: x ?? (at?.x ?? 0) + 240,
      y: y ?? at?.y ?? 80,
    };
    setGraph({
      nodes: [...graph.nodes, node],
      edges: x == null && at ? [...graph.edges, { source: at.id, target: nid }] : graph.edges,
    });
    setSel(nid);
  };
  const connect = (e: CanvasEdge) => {
    if (graph.edges.some((x) => x.source === e.source && x.target === e.target)) return;
    setGraph({ ...graph, edges: [...graph.edges, { source: e.source, target: e.target }] });
  };
  const removeNodes = (ids: string[]) => {
    setGraph({
      nodes: graph.nodes.filter((n) => !ids.includes(n.id)),
      edges: graph.edges.filter((e) => !ids.includes(e.source) && !ids.includes(e.target)),
    });
    if (sel && ids.includes(sel)) setSel(null);
  };
  const removeEdges = (es: CanvasEdge[]) =>
    setGraph({
      ...graph,
      edges: graph.edges.filter((e) => !es.some((x) => x.source === e.source && x.target === e.target)),
    });
  const move = (nid: string, x: number, y: number) =>
    setGraph((g) => ({ ...g, nodes: g.nodes.map((n) => (n.id === nid ? { ...n, x, y } : n)) }));

  const position = new Map((order ?? []).map((n, k) => [n.id, k + 1]));
  const canvasNodes: CanvasNode[] = graph.nodes.map((n, k) => ({
    id: n.id,
    x: n.x ?? 0,
    y: n.y ?? 0,
    title: `${position.get(n.id) ?? "?"}. ${n.step.type === "workflow" ? (n.step.name ?? wfName(n.step.workflow) ?? "Workflow") : stepLabel(n.step.type, n.step.name)}`,
    subtitle: stepSummary(n.step, (t) => tpl.get(t)?.name) || DESCRIBE[n.step.type],
    icon: ICON[n.step.type] ?? Workflow,
    inputs: -1,
    outputs: ["out"],
    tone: TONE[STEP_TONE[n.step.type] ?? "intent"],
    io: PROVIDES[n.step.type],
    problem: nodeProblem(k),
  }));
  const cur = graph.nodes.find((n) => n.id === sel);
  const curIndex = graph.nodes.findIndex((n) => n.id === sel);

  if (q.isLoading)
    return (
      <div className="flex flex-col gap-4 p-6" aria-busy="true" aria-label="Loading pipeline">
        <Skeleton className="h-7 w-72" />
        <Skeleton className="h-[420px] w-full rounded-md" />
      </div>
    );
  if (q.error || !base) {
    const e = q.error as ApiError | null;
    return (
      <EmptyState
        tone={e?.status === 404 ? "neutral" : "error"}
        icon={<Workflow />}
        title={e?.status === 404 ? "This pipeline doesn’t exist" : "Couldn’t load the pipeline"}
        actions={
          <Button asChild variant="secondary">
            <Link href="/pipelines">All pipelines</Link>
          </Button>
        }
      >
        {e?.message}
      </EmptyState>
    );
  }

  const palette = (types: string[]) =>
    types.map((t) => (
      <PaletteItem
        key={t}
        payload={t}
        icon={ICON[t]}
        title={stepLabel(t)}
        hint={DESCRIBE[t]}
        disabled={readOnly}
        onAdd={() => add(t)}
      />
    ));

  return (
    <div className="flex min-h-[calc(100vh-64px)] flex-col">
      <header className="flex flex-wrap items-center gap-3 border-b border-border px-4 py-3.5 md:px-5">
        <nav aria-label="Breadcrumb" className="w-full text-[12px] font-medium text-fg-muted">
          <Link href="/pipelines" className="hover:text-fg hover:underline">
            Pipelines
          </Link>{" "}
          ›{" "}
          <Link href={`/pipelines/${id}`} className="hover:text-fg hover:underline">
            {base.name}
          </Link>{" "}
          › Canvas
        </nav>
        <h1 className="text-[18px] font-bold text-fg">{base.name}</h1>
        <span
          className={cn(
            "h-[22px] rounded-pill border px-2 text-[11px] font-bold uppercase leading-5",
            dirty
              ? "border-blue-border bg-blue-surface text-fg-accent"
              : "border-green-border bg-green-surface text-green-dark",
          )}
        >
          {dirty ? `Draft v${latest + 1}` : `v${base.current} · published`}
        </span>
        <span className="flex-1" />
        <Button asChild size="sm" variant="ghost">
          <Link href={`/pipelines/${id}`}>
            <List /> List view
          </Link>
        </Button>
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
        <Input
          aria-label="Version notes"
          placeholder="What changed (optional)"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          className="h-8 w-[200px] text-[13px]"
          disabled={readOnly || !dirty}
        />
        <Button
          size="sm"
          variant="secondary"
          disabled={Boolean(why) || !dirty || Boolean(firstProblem) || save.isPending}
          disabledReason={why ?? firstProblem ?? "Nothing to save"}
          onClick={() => save.mutate(false)}
        >
          Save draft
        </Button>
        <Button
          size="sm"
          variant="approve"
          disabled={Boolean(why) || !dirty || Boolean(firstProblem) || save.isPending}
          disabledReason={
            why ?? (firstProblem ? `Fix this first: ${firstProblem}` : "Nothing changed since the published version")
          }
          onClick={() => save.mutate(true)}
        >
          Publish v{latest + 1}
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
      {graphProblem && (
        <p role="alert" className="border-b border-red-border bg-red-surface px-5 py-2 text-[13px] text-red-dark">
          {graphProblem}
        </p>
      )}

      <div className="grid flex-1 lg:h-[calc(100vh-150px)] lg:flex-none lg:min-h-[560px] lg:grid-rows-[minmax(0,1fr)] lg:grid-cols-[230px_minmax(0,1fr)_380px]">
        <aside
          aria-label="Steps and workflows"
          className="flex flex-col gap-1.5 overflow-y-auto border-b border-border bg-surface p-3.5 lg:border-b-0 lg:border-r"
        >
          <p className="pb-1 text-[11.5px] text-fg-muted">
            Drag onto the canvas, or click to add after the selected step. Connect a step to the ones that run after it.
          </p>
          <span className="label-caps pt-1">Assets</span>
          {palette(ASSETS)}
          <span className="label-caps pt-2">Metadata</span>
          {palette(METADATA)}
          <span className="label-caps pt-2">Workflows</span>
          {(workflows.data?.workflows ?? []).map((w) => (
            <PaletteItem
              key={w.id}
              payload={`workflow:${w.id}`}
              icon={Workflow}
              title={w.name}
              hint={w.description ?? `v${w.current}`}
              disabled={readOnly}
              onAdd={() => add(`workflow:${w.id}`)}
            />
          ))}
          <Link href="/workflows/new" className="pt-1 text-[12.5px] font-semibold text-fg-accent hover:underline">
            Draw a new workflow
          </Link>
        </aside>
        <section
          aria-label="Canvas"
          className="relative flex min-h-[520px] flex-col border-b border-border lg:border-b-0"
        >
          <div className="min-h-0 flex-1">
            <FlowCanvas
              nodes={canvasNodes}
              edges={graph.edges}
              selected={sel}
              readOnly={readOnly}
              onSelect={setSel}
              onMove={move}
              onConnect={connect}
              onDeleteNodes={removeNodes}
              onDeleteEdges={removeEdges}
              onDrop={(payload, x, y) => add(payload, x, y)}
            />
          </div>
          <p className="border-t border-border px-4 py-2 text-[12px] text-fg-secondary">
            <span className="font-semibold text-fg">Runs in this order: </span>
            {order
              ? order
                  .map((n) =>
                    n.step.type === "workflow"
                      ? (wfName(n.step.workflow) ?? "Workflow")
                      : stepLabel(n.step.type, n.step.name),
                  )
                  .join(" → ")
              : "—"}
          </p>
        </section>
        <aside aria-label="Step settings" className="overflow-y-auto border-border p-4 lg:border-l">
          {cur ? (
            <StepSettings
              key={cur.id}
              step={cur.step}
              onChange={(step) =>
                setGraph((g) => ({ ...g, nodes: g.nodes.map((n) => (n.id === cur.id ? { ...n, step } : n)) }))
              }
              templates={(templates.data ?? []) as TemplateSummary[]}
              readOnly={readOnly}
              problem={nodeProblem(curIndex)}
            />
          ) : (
            <div className="flex flex-col gap-2 text-[13px] text-fg-secondary">
              <h2 className="text-[15px] font-bold text-fg">How this pipeline runs</h2>
              <p>
                Asset steps make things of the media: the transcript, shots, text on screen, faces. Metadata steps and
                workflows read them and keep what they find.
              </p>
              <p>
                A connection means “runs after”. Steps run one at a time, each after the steps it follows; steps with
                nothing between them run left to right.
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
