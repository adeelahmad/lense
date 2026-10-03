"use client";

import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, FlaskConical, Play, Workflow as WorkflowIcon } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { Fields, Workflows } from "@/app/openapi-client";
import type { TemplateSummary, Workflow, WorkflowTry } from "@/app/openapi-client/types.gen";
import { useTemplateList, useWorkflowCatalog } from "@/components/pipelines/catalog-header";
import { RunDialog } from "@/components/pipelines/pipeline-editor";
import { GraphEditor, type Folded } from "@/components/workflows/graph-editor";
import type { NsField } from "@/components/workflows/node-settings";
import { SaveCustomDialog } from "@/components/workflows/save-custom-dialog";
import {
  cleanGraph,
  normalize,
  problems as findProblems,
  sameGraph,
  starter,
  type CustomDef,
  type Scope,
  type WfGraph,
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
  const customDefs = (catalog.data?.custom_nodes ?? []) as unknown as CustomDef[];
  const customOf = (cid: number) => customDefs.find((d) => d.id === cid);

  const base: Workflow | undefined = q.data;
  const [newScope, setNewScope] = useState<Scope>("recording");
  const scope: Scope = creating ? newScope : base?.scope === "graph" ? "graph" : "recording";
  const [graph, setGraph] = useState<WfGraph>(() => (creating ? starter() : { nodes: [], edges: [] }));
  const [name, setName] = useState("");
  const [notes, setNotes] = useState("");
  const [runOpen, setRunOpen] = useState(false);
  const [lastJob, setLastJob] = useState<{ job: number; title: string } | null>(null);
  const [tryOpen, setTryOpen] = useState(false);
  const [tried, setTried] = useState<(WorkflowTry & { title: string }) | null>(null);
  const [folding, setFolding] = useState<Folded | null>(null);
  const resolveFold = useRef<((d: CustomDef | null) => void) | null>(null);

  useEffect(() => {
    if (base) {
      setGraph(normalize(base.graph as WfGraph));
      setTried(null);
    }
  }, [base]);

  const readOnly = !admin;
  const why = readOnly ? "Only admins can change workflows" : undefined;
  const latest = base ? Math.max(base.current, ...(base.history ?? []).map((h) => h.version)) : 0;
  const dirty = creating ? true : base ? !sameGraph(base.graph as WfGraph, graph) : false;
  const problems = findProblems(graph, (tid) => tpl.get(tid)?.kind, scope, "workflow", customOf);
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

  const tryIt = useMutation({
    mutationFn: ({ rid }: { rid?: number; title: string }) =>
      data(
        Workflows.tryWorkflow({
          client,
          body: { graph: cleanGraph(graph), scope, ...(rid != null ? { recording: rid } : {}) },
        }),
      ),
    onSuccess: (r, v) => {
      setTryOpen(false);
      setTried({ ...r, title: v.title });
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t try it", body: e.message }),
  });
  const saveAsCustom = (f: Folded) =>
    new Promise<CustomDef | null>((resolve) => {
      resolveFold.current = resolve;
      setFolding(f);
    });

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
        <Button
          size="sm"
          variant="secondary"
          icon={<Play />}
          disabled={readOnly || Boolean(firstProblem) || tryIt.isPending}
          disabledReason={why ?? (firstProblem ? `Fix this first: ${firstProblem[1]}` : "Trying…")}
          onClick={() => (scope === "graph" ? tryIt.mutate({ title: "every namespace" }) : setTryOpen(true))}
        >
          {tryIt.isPending ? "Trying…" : scope === "graph" ? "Try it" : "Try on a recording"}
        </Button>
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
      {tried && (
        <details
          className="border-b border-border bg-surface px-5 py-2 text-[13px] text-fg"
          open={Boolean(tried.error)}
        >
          <summary className="cursor-pointer">
            {tried.error ? (
              <span className="font-semibold text-red-dark">
                The try on {tried.title} failed: {tried.error}
              </span>
            ) : (
              <span>
                Tried on {tried.title}: {Object.values(tried.trace).filter((t) => t.status === "done").length} nodes
                ran, nothing was kept. Each node shows what it passed on; select one to see all of it.
              </span>
            )}{" "}
            <button
              type="button"
              className="ml-2 font-semibold text-fg-accent hover:underline"
              onClick={() => setTried(null)}
            >
              Clear
            </button>
          </summary>
          <pre className="mt-2 max-h-[160px] overflow-auto whitespace-pre-wrap font-mono text-[11.5px] text-fg-secondary">
            {tried.log.join("\n") || "Nothing logged."}
          </pre>
        </details>
      )}

      <GraphEditor
        key={`${scope}-${base?.version ?? "new"}`}
        graph={graph}
        setGraph={setGraph}
        scope={scope}
        root="workflow"
        readOnly={readOnly}
        catalog={catalog.data?.node_types}
        customDefs={customDefs}
        templates={(templates.data ?? []) as TemplateSummary[]}
        fields={fields}
        trace={tried?.trace}
        onSaveAsCustom={saveAsCustom}
        emptyPanel={
          scope === "graph" ? (
            <div className="flex flex-col gap-2 text-[13px] text-fg-secondary">
              <h2 className="text-[15px] font-bold text-fg">How graph workflows run</h2>
              <p>
                A graph workflow starts from the namespaces a routine runs it over. Look-alike entities finds pairs that
                may be one thing, Ask the model judges each pair, and Apply changes merges or links the sure ones and
                proposes the rest.
              </p>
              <p>
                Run it from a routine. Proposed changes wait in Routines → Proposed changes, and every change can be
                undone. Try it runs it now and keeps nothing.
              </p>
            </div>
          ) : (
            <div className="flex flex-col gap-2 text-[13px] text-fg-secondary">
              <h2 className="text-[15px] font-bold text-fg">How workflows run</h2>
              <p>
                A workflow starts from the Recording node with what the pipeline made of it: transcript, summary,
                entities and earlier outputs. Each node passes what it makes along its connections, port to port.
                Condition and Switch send it down one branch; the nodes on the others are skipped.
              </p>
              <p>
                For each and Repeat go over things again: double-click one to build its body. Select several nodes
                (Shift-drag) to fold them into a Group or save them as a custom node, to use again and share.
              </p>
              <p>
                Try on a recording runs the graph as it is now, keeps nothing, and shows what each node passed on.
                Attach the workflow to a pipeline as a Workflow step; it runs after the steps before it.
              </p>
            </div>
          )
        }
      />
      <SaveCustomDialog
        folded={folding}
        onDone={(d) => {
          setFolding(null);
          resolveFold.current?.(d);
          resolveFold.current = null;
          if (d)
            toast({
              tone: "green",
              title: "Custom node saved",
              body: `${d.name} is in the palette under Custom nodes.`,
            });
        }}
      />
      <RunDialog
        open={tryOpen}
        onOpenChange={setTryOpen}
        onRun={(rid, title) => tryIt.mutate({ rid, title })}
        pending={tryIt.isPending}
        title="Try on a recording"
        description="Runs the graph as it is on the canvas, saved or not, and keeps nothing: nodes that would save something say what they’d save. Model nodes still ask the model."
        action="Try it"
      />
      <RunDialog
        open={runOpen}
        onOpenChange={setRunOpen}
        onRun={(rid, title) => run.mutate({ rid, title })}
        pending={run.isPending}
      />
    </div>
  );
}
