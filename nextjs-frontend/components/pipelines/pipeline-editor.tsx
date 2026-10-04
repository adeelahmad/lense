"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AudioLines,
  Captions,
  ChevronDown,
  Clapperboard,
  Eye,
  FileOutput,
  FileText,
  FlaskConical,
  GripVertical,
  ScanFace,
  ScanSearch,
  ScanText,
  Shapes,
  Sparkles,
  TextSearch,
  Webhook,
  Workflow,
  X,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";

import { Jobs, Pipelines, Resources } from "@/app/openapi-client";
import type { Pipeline, TemplateSummary } from "@/app/openapi-client/types.gen";
import { isActive, parseJobLog, span, stepLabel, stepStates, type JobRecord } from "@/components/activity/job-model";
import { useTemplateList } from "@/components/pipelines/catalog-header";
import {
  DESCRIBE,
  PROVIDES,
  cleanSpec,
  moveStep,
  sameSteps,
  specProblems,
  stepSummary,
  toSpec,
  type StepSpec,
} from "@/components/pipelines/pipeline-model";
import { StepSettings } from "@/components/pipelines/step-settings";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select, Textarea } from "@/components/ui/field";
import { STEP_TONE } from "@/components/ui/loop";
import { Menu, MenuContent, MenuItem, MenuLabel, MenuTrigger } from "@/components/ui/menu";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { Tooltip } from "@/components/ui/tooltip";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { relative } from "@/lib/format";
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
  embed: ScanSearch,
  summarize: Sparkles,
  llm: Sparkles,
  report: FileText,
  export: FileOutput,
  workflow: Workflow,
};
const LIB_ORDER = [
  "transcribe",
  "diarize",
  "shots",
  "ocr",
  "faces",
  "objects",
  "describe",
  "analyze",
  "embed",
  "summarize",
  "llm",
  "report",
  "export",
  "workflow",
];
const TONE_BG: Record<string, string> = {
  intent: "bg-blue",
  red: "bg-red",
  green: "bg-green",
  gate: "bg-gold",
  neutral: "bg-fg-muted",
};

type TestRun = { job: number; recording: string };

/** "Run on a recording": a real run of the published version (the backend has no dry run yet). */
export function RunDialog({
  open,
  onOpenChange,
  onRun,
  pending,
  title = "Run on a recording",
  description = "This runs the published version for real: each step’s output is saved on the recording, as a Reprocess would.",
  action = "Run",
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onRun: (rid: number, title: string) => void;
  pending: boolean;
  title?: string;
  description?: string;
  action?: string;
}) {
  const client = useApiClient();
  const { can } = useArchive();
  const recs = useQuery({
    queryKey: ["recordings", "picker"],
    queryFn: () => data(Resources.listRecordings({ client, query: { limit: 500 } })),
    enabled: open,
    staleTime: 60_000,
  });
  const editable = (recs.data ?? []).filter((r) => can("editor", r.namespace));
  const [rid, setRid] = useState("");
  useEffect(() => {
    if (open && !rid && editable[0]) setRid(String(editable[0].id));
  }, [open, editable, rid]);
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={title}
      description={description}
      actions={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={!rid || pending}
            onClick={() => {
              const r = editable.find((x) => String(x.id) === rid);
              if (r) onRun(r.id, r.title ?? `Recording ${r.id}`);
            }}
          >
            {pending ? "Starting…" : action}
          </Button>
        </>
      }
    >
      <Field
        label="Recording"
        hint={
          recs.isSuccess && !editable.length
            ? "You need editor access to a recording’s namespace to run on it."
            : undefined
        }
      >
        {({ id, describedBy }) => (
          <Select
            id={id}
            aria-describedby={describedBy}
            value={rid}
            onChange={(e) => setRid(e.target.value)}
            options={
              recs.isLoading
                ? [{ value: "", label: "Loading…" }]
                : editable.map((r) => ({
                    value: String(r.id),
                    label: `${r.title ?? `Recording ${r.id}`} · ${r.namespace}`,
                  }))
            }
          />
        )}
      </Field>
    </Dialog>
  );
}

/** PL2: a pipeline's steps (drag or ⌥↑/↓ to reorder), each step's settings, versions, publish and a run on a recording. */
export function PipelineEditor({ id }: { id?: number }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const router = useRouter();
  const toast = useToast();
  const { admin, namespaces, can } = useArchive();
  const creating = id == null;
  const [version, setVersion] = useState<number | null>(null);
  const catalog = useQuery({
    queryKey: ["pipelines"],
    queryFn: () => data(Pipelines.listPipelines({ client })),
    staleTime: 30_000,
  });
  const q = useQuery({
    queryKey: ["pipeline", id, version ?? "current"],
    queryFn: () =>
      data(
        Pipelines.getPipeline({
          client,
          path: { pid: id! },
          query: version ? { version } : undefined,
        }),
      ),
    enabled: !creating,
  });
  const templates = useTemplateList();
  const tpl = useMemo(
    () => new Map(((templates.data ?? []) as TemplateSummary[]).map((t) => [t.id, t])),
    [templates.data],
  );

  const base: Pipeline | undefined = q.data;
  const [steps, setSteps] = useState<StepSpec[]>([]);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [notes, setNotes] = useState("");
  const [sel, setSel] = useState(0);
  const [moveMsg, setMoveMsg] = useState<string | null>(null);
  const [drag, setDrag] = useState<number | null>(null);
  const [runOpen, setRunOpen] = useState(false);
  const [test, setTest] = useState<TestRun | null>(null);
  const cards = useRef<(HTMLDivElement | null)[]>([]);

  // Load the version being viewed (or the standard steps for a new pipeline).
  useEffect(() => {
    if (creating && catalog.data && !steps.length)
      setSteps(["transcribe", "diarize", "analyze", "embed", "summarize", "report"].map((t) => ({ type: t })));
  }, [creating, catalog.data]);
  useEffect(() => {
    if (base) {
      setSteps(base.steps.map((s) => toSpec(s)));
      setSel(0);
      setMoveMsg(null);
    }
  }, [base]);

  const readOnly = !admin;
  const latest = base ? Math.max(base.current, ...(base.history ?? []).map((h) => h.version)) : 0;
  const dirty = creating ? true : base ? !sameSteps(base.steps, steps) : false;
  const problems = specProblems(steps, (tid) => tpl.get(tid)?.kind);
  const firstProblem = Object.entries(problems)[0];
  const used = (catalog.data?.pipelines.find((p) => p.id === id)?.namespaces ?? []).filter((n) =>
    namespaces.some((x) => x.name === n),
  );

  const job = useQuery({
    queryKey: ["job", test?.job],
    queryFn: () => data(Jobs.getJob({ client, path: { jid: test!.job } })) as Promise<JobRecord>,
    enabled: test != null,
    refetchInterval: (qq) => (isActive(qq.state.data?.status) ? 1500 : false),
  });
  const testLogs = useMemo(() => (job.data ? parseJobLog(job.data.log, job.data.steps) : null), [job.data]);
  const testStates = job.data && testLogs ? stepStates(job.data, testLogs.byStep) : null;

  const save = useMutation({
    mutationFn: async (publish: boolean) => {
      const body = steps.map(cleanSpec);
      if (creating)
        return {
          id: (
            await data(
              Pipelines.createPipeline({
                client,
                body: {
                  name: name.trim(),
                  description: description.trim() || null,
                  steps: body,
                },
              }),
            )
          ).id,
          version: 1,
        };
      const r = await data(
        Pipelines.createPipelineVersion({
          client,
          path: { pid: id! },
          body: { steps: body, notes: notes.trim() || null, publish },
        }),
      );
      return { id: id!, version: r.version };
    },
    onSuccess: (r, publish) => {
      void qc.invalidateQueries({ queryKey: ["pipelines"] });
      void qc.invalidateQueries({ queryKey: ["pipeline", r.id] });
      setNotes("");
      if (creating) {
        toast({ tone: "green", title: "Pipeline created", body: name });
        router.push(`/pipelines/${r.id}`);
        return;
      }
      setVersion(publish ? null : r.version);
      toast({
        tone: "green",
        title: publish ? `Published v${r.version}` : `Saved draft v${r.version}`,
        body: publish ? "New runs use it; runs in progress keep their version." : "Publish it when it’s ready.",
      });
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t save", body: e.message }),
  });

  const run = useMutation({
    mutationFn: ({ rid }: { rid: number; title: string }) =>
      data(
        Pipelines.runPipeline({
          client,
          path: { pid: id! },
          body: { recording: rid },
        }),
      ),
    onSuccess: (r, v) => {
      setRunOpen(false);
      setTest({ job: r.job, recording: v.title });
      void qc.invalidateQueries({ queryKey: ["jobs"] });
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t start the run", body: e.message }),
  });

  const add = (type: string) => {
    const at = steps.length ? Math.min(sel + 1, steps.length) : 0;
    const next = steps.slice();
    next.splice(at, 0, { type, ...(type === "llm" ? { key: "" } : {}) });
    setSteps(next);
    setSel(at);
    setMoveMsg(null);
  };
  const remove = (i: number) => {
    setSteps(steps.filter((_, k) => k !== i));
    setSel(Math.max(0, Math.min(sel, steps.length - 2)));
  };
  const move = (from: number, to: number) => {
    const r = moveStep(steps, from, to);
    setMoveMsg(r.error ?? null);
    if (!r.error) {
      setSteps(r.steps);
      setSel(to);
      requestAnimationFrame(() => cards.current[to]?.focus());
    }
  };
  const onCardKey = (e: KeyboardEvent<HTMLDivElement>, i: number) => {
    if (readOnly) return;
    if (e.altKey && e.key === "ArrowUp") {
      e.preventDefault();
      move(i, i - 1);
    } else if (e.altKey && e.key === "ArrowDown") {
      e.preventDefault();
      move(i, i + 1);
    } else if (e.key === "Delete" || e.key === "Backspace") {
      if ((e.target as HTMLElement) === e.currentTarget) {
        e.preventDefault();
        remove(i);
      }
    }
  };

  if (!creating && q.isLoading)
    return (
      <div className="flex flex-col gap-4 p-6" aria-busy="true" aria-label="Loading pipeline">
        <Skeleton className="h-7 w-72" />
        <Skeleton className="h-[420px] w-full rounded-md" />
      </div>
    );
  if (!creating && (q.error || !base)) {
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

  const cur = steps[sel];
  const why = readOnly ? "Only admins can change pipelines" : undefined;
  const publishReason =
    why ??
    (firstProblem
      ? `Fix this first: ${firstProblem[1]}`
      : !creating && !dirty && version == null
        ? "Nothing changed since the published version"
        : undefined);
  const nextV = latest + 1;

  return (
    <div className="flex min-h-[calc(100vh-64px)] flex-col">
      <header className="flex flex-wrap items-center gap-3 border-b border-border px-4 py-3.5 md:px-5">
        <nav aria-label="Breadcrumb" className="w-full text-[12px] font-medium text-fg-muted">
          <Link href="/pipelines" className="hover:text-fg hover:underline">
            Pipelines
          </Link>{" "}
          › {creating ? "New pipeline" : base?.name}
        </nav>
        {creating ? (
          <Input
            aria-label="Pipeline name"
            placeholder="Name, e.g. Podcast standard"
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="h-9 w-[280px] text-[16px] font-bold"
            autoFocus
          />
        ) : (
          <h1 className="text-[18px] font-bold text-fg">{base?.name}</h1>
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
        {!creating && (
          <span className="text-[12.5px] text-fg-muted">
            published v{base?.current}
            {used.length ? ` · default in ${used.join(", ")}` : ""} · runs pin their version
          </span>
        )}
        <span className="flex-1" />
        {!creating && (
          <Button asChild size="sm" variant="ghost">
            <Link href={`/pipelines/${id}/canvas`}>
              <Workflow /> Canvas
            </Link>
          </Button>
        )}
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
        {!creating && (
          <Button
            size="sm"
            variant="secondary"
            icon={<FlaskConical />}
            disabled={!can("editor") || dirty || (version != null && version !== base?.current)}
            disabledReason={
              !can("editor")
                ? "Needs editor access to a recording’s namespace"
                : "Runs use the published version: publish this one first"
            }
            onClick={() => setRunOpen(true)}
          >
            Run on a recording
          </Button>
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
          disabledReason={publishReason ?? "Give the pipeline a name"}
          onClick={() => save.mutate(true)}
        >
          {creating ? "Create pipeline" : `Publish v${nextV}`}
        </Button>
      </header>

      <div className="grid flex-1 lg:grid-cols-[220px_minmax(0,1fr)_380px]">
        <aside
          aria-label="Step library"
          className="flex flex-col gap-1.5 border-b border-border bg-surface p-3.5 lg:border-b-0 lg:border-r"
        >
          <span className="label-caps pb-1.5">Step library</span>
          {LIB_ORDER.filter((t) => (catalog.data?.step_types ?? LIB_ORDER).includes(t)).map((t) => {
            const Icon = ICON[t] ?? Workflow;
            return (
              <Tooltip
                key={t}
                content={readOnly ? why : `${DESCRIBE[t]}. Adds it after the selected step.`}
                side="right"
              >
                <button
                  type="button"
                  onClick={() => !readOnly && add(t)}
                  aria-disabled={readOnly || undefined}
                  className={cn(
                    "flex h-[34px] items-center gap-2 rounded-sm border border-border bg-background px-2.5 text-left text-[13px] font-medium text-fg",
                    readOnly ? "cursor-not-allowed opacity-60" : "hover:border-blue-border hover:bg-blue-surface",
                  )}
                >
                  <Icon aria-hidden className="size-[15px] text-fg-secondary" />
                  {stepLabel(t)}
                </button>
              </Tooltip>
            );
          })}
          <Tooltip content="Not available yet: the backend has no webhook or notify step" side="right">
            <button
              type="button"
              aria-disabled
              className="flex h-[34px] cursor-not-allowed items-center gap-2 rounded-sm border border-border bg-background px-2.5 text-[13px] font-medium text-fg opacity-50"
            >
              <Webhook aria-hidden className="size-[15px] text-fg-secondary" /> Webhook / Notify
            </button>
          </Tooltip>
          <span className="label-caps pb-1 pt-2.5">Where it runs</span>
          <p className="text-[12.5px] leading-snug text-fg-secondary">
            Imports, watched folders and Reprocess run a namespace’s{" "}
            <Link href="/pipelines#ns-defaults" className="font-semibold text-fg-accent hover:underline">
              default pipeline
            </Link>
            ; watched folders and Reprocess can also pick this one. Schedules aren’t available yet.
          </p>
        </aside>

        <div className="flex min-w-0 flex-col px-4 py-4 md:px-5">
          {creating && (
            <Field label="Description" optional className="mb-4">
              {({ id: fid }) => (
                <Textarea
                  id={fid}
                  rows={2}
                  className="min-h-[56px]"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="What it’s for"
                />
              )}
            </Field>
          )}
          {moveMsg && (
            <Banner className="mb-3" onDismiss={() => setMoveMsg(null)}>
              {moveMsg}
            </Banner>
          )}
          {test && (
            <div
              role="status"
              className="mb-3 flex flex-wrap items-center gap-2 rounded-sm border border-blue-border bg-blue-surface px-3 py-2 text-[12.5px] text-fg-strong"
            >
              <span className="flex-1">
                Run #{test.job} on <b>{test.recording}</b>:{" "}
                {job.data
                  ? job.data.status === "succeeded"
                    ? "succeeded"
                    : job.data.status === "failed"
                      ? "failed"
                      : `${job.data.status}…`
                  : "starting…"}
              </span>
              <Link href={`/activity/${test.job}`} className="font-semibold text-fg-accent hover:underline">
                Open in Activity
              </Link>
              <button
                type="button"
                onClick={() => setTest(null)}
                className="font-semibold text-fg-secondary hover:underline"
              >
                Hide
              </button>
            </div>
          )}
          <ol aria-label="Steps" className="flex flex-col">
            {steps.map((s, i) => {
              const tone = STEP_TONE[s.type] ?? "neutral";
              const on = i === sel;
              const st = testStates?.[i];
              const tl = testLogs?.byStep[i];
              const problem = problems[i];
              return (
                <li key={`${i}-${s.type}`} className="flex flex-col">
                  <div
                    ref={(el) => {
                      cards.current[i] = el;
                    }}
                    tabIndex={0}
                    role="button"
                    aria-pressed={on}
                    aria-label={`Step ${i + 1}: ${stepLabel(s.type, s.name)}${readOnly ? "" : ". Alt+Up or Alt+Down moves it; Delete removes it"}`}
                    onClick={() => setSel(i)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        setSel(i);
                      } else onCardKey(e, i);
                    }}
                    draggable={!readOnly}
                    onDragStart={(e) => {
                      setDrag(i);
                      e.dataTransfer.effectAllowed = "move";
                    }}
                    onDragEnd={() => setDrag(null)}
                    onDragOver={(e) => drag != null && e.preventDefault()}
                    onDrop={(e) => {
                      e.preventDefault();
                      if (drag != null) move(drag, i);
                      setDrag(null);
                    }}
                    className={cn(
                      "grid cursor-pointer grid-cols-[18px_28px_minmax(0,1fr)_auto] items-start gap-2.5 rounded-md p-3 outline-none transition-colors duration-fast focus-visible:ring-2 focus-visible:ring-blue",
                      on
                        ? "border-2 border-blue bg-blue-surface"
                        : "border border-border bg-background hover:bg-surface",
                      problem && !on && "border-red-border",
                      drag === i && "opacity-50",
                    )}
                  >
                    <GripVertical
                      aria-hidden
                      className={cn("mt-1.5 size-4 text-fg-muted", !readOnly && "cursor-grab")}
                    />
                    <span
                      aria-hidden
                      className={cn(
                        "grid size-7 place-items-center rounded-full text-[12px] font-extrabold text-white",
                        st === "done"
                          ? "bg-green"
                          : st === "failed"
                            ? "bg-red"
                            : st === "running"
                              ? "bg-blue animate-pulse"
                              : st === "skipped"
                                ? "bg-border text-fg-secondary"
                                : TONE_BG[tone],
                        tone === "gate" && !st && "text-fg",
                      )}
                    >
                      {st === "done" ? "✓" : st === "failed" ? "✕" : st === "skipped" ? "–" : i + 1}
                    </span>
                    <div className="flex min-w-0 flex-col gap-[5px]">
                      <div className="flex min-w-0 flex-wrap items-baseline gap-x-2">
                        <b className="text-[14px] font-bold text-fg">{stepLabel(s.type, s.name)}</b>
                        <span className="truncate text-[12px] text-fg-muted">
                          {stepSummary(s, (tid) => tpl.get(tid)?.name) || DESCRIBE[s.type]}
                        </span>
                      </div>
                      {tl?.note && <span className="truncate text-[12.5px] text-fg-secondary">{tl.note}</span>}
                      {problem && <span className="text-[12.5px] text-red-dark">{problem}</span>}
                      <span className="font-mono text-[11.5px] text-fg-accent">
                        {s.type === "llm" ? `outputs.${s.key || "<key>"}` : PROVIDES[s.type]}
                      </span>
                    </div>
                    <span className="flex items-center gap-2">
                      {st && (
                        <span className="tabular text-[12px] font-medium text-fg-secondary">
                          {st === "done" && tl?.seconds != null
                            ? `✓ ${span(tl.seconds * 1000)}`
                            : st === "running"
                              ? "running…"
                              : st === "waiting"
                                ? "—"
                                : st}
                        </span>
                      )}
                      {!readOnly && (
                        <button
                          type="button"
                          aria-label={`Remove ${stepLabel(s.type, s.name)}`}
                          onClick={(e) => {
                            e.stopPropagation();
                            remove(i);
                          }}
                          className="grid size-6 place-items-center rounded-full text-fg-muted hover:bg-surface-neutral hover:text-fg"
                        >
                          <X aria-hidden className="size-3.5" />
                        </button>
                      )}
                    </span>
                  </div>
                  {i < steps.length - 1 && <span aria-hidden className="ml-[52px] h-3 w-0.5 bg-border" />}
                </li>
              );
            })}
          </ol>
          {!steps.length && (
            <p className="rounded-md border border-dashed border-border px-4 py-6 text-center text-[13.5px] text-fg-secondary">
              Add steps from the library on the left.
            </p>
          )}
          {problems[-1] && steps.length === 0 && <p className="mt-2 text-[12.5px] text-red-dark">{problems[-1]}</p>}
          {!creating && !readOnly && dirty && (
            <Field label="Notes for this version" optional className="mt-4">
              {({ id: fid }) => (
                <Input
                  id={fid}
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  placeholder="What changed, e.g. summary only for calls over 5 min"
                />
              )}
            </Field>
          )}
          <p className="mt-4 text-[12px] leading-normal text-fg-muted">
            Drag steps by the grip, or focus one and press ⌥↑/⌥↓. A step can’t move above a step whose output it needs;
            it snaps back and says which one. Publishing makes the new version the default for new runs; runs in
            progress keep theirs.
          </p>
        </div>

        <aside aria-label="Step settings" className="border-t border-border px-4 py-4 lg:border-l lg:border-t-0">
          {cur ? (
            <StepSettings
              step={cur}
              readOnly={readOnly}
              problem={problems[sel]}
              templates={(templates.data ?? []) as TemplateSummary[]}
              onChange={(s) => setSteps(steps.map((x, k) => (k === sel ? s : x)))}
            />
          ) : (
            <p className="text-[13px] text-fg-secondary">Select a step to see its settings.</p>
          )}
        </aside>
      </div>

      {!creating && (
        <RunDialog
          open={runOpen}
          onOpenChange={setRunOpen}
          pending={run.isPending}
          onRun={(rid, title) => run.mutate({ rid, title })}
        />
      )}
    </div>
  );
}
