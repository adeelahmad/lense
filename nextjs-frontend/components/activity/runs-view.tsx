"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUp, ChevronRight, Cloud, FolderSync, ListFilter, Settings2, User } from "lucide-react";
import Link from "next/link";
import { Fragment, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { Batches } from "@/app/openapi-client";
import { CancelJobDialog } from "@/components/activity/cancel-dialog";
import {
  batchPhase,
  canRetry,
  isActive,
  jobPhase,
  pipelineKey,
  pipelineLabel,
  pipelineOptions,
  reconcileOrder,
  shortWhen,
  stepSpecs,
  triggerOf,
  waitingReason,
  type BatchAction,
  type BatchInfo,
  type JobRecord,
  type Trigger,
} from "@/components/activity/job-model";
import { JobListItem, JobStateIcon, StepSegments } from "@/components/activity/job-row";
import {
  useJobActions,
  useJobList,
  usePeopleNames,
  useSpaceNames,
  useWorkerStates,
  useWorkers,
} from "@/components/activity/use-activity";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/field";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/menu";
import { EmptyState, SkeletonRows } from "@/components/ui/states";
import { Table, Td, Th, THead, Tr } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { absolute } from "@/lib/format";
import { needRole, useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

const CHIPS: { value: string; label: string }[] = [
  { value: "all", label: "All" },
  { value: "running", label: "Running" },
  { value: "queued", label: "Queued" },
  { value: "paused", label: "Paused" },
  { value: "failed", label: "Failed" },
  { value: "succeeded", label: "Succeeded" },
  { value: "cancelled", label: "Cancelled" },
];

type Item = { id: string; at: string; job?: JobRecord; batch?: BatchInfo };

/** Announce changes politely, at most once per 10 s per row (the handoff's rule for live progress). */
function useAnnouncer() {
  const [text, setText] = useState("");
  const last = useRef(new Map<string, number>());
  const announce = (key: string, message: string) => {
    const now = Date.now();
    if (now - (last.current.get(key) ?? 0) < 10_000) return;
    last.current.set(key, now);
    setText(message);
  };
  return { text, announce };
}

/** Whether the list's top edge is on screen: new runs join the top only then. */
function useAtTop() {
  const ref = useRef<HTMLDivElement>(null);
  const [atTop, setAtTop] = useState(true);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    // The sticky top bar is 64px tall: the list counts as "at the top" while this marker shows below it.
    const io = new IntersectionObserver(([e]) => setAtTop(e.isIntersecting), {
      rootMargin: "-64px 0px 0px 0px",
    });
    io.observe(el);
    return () => io.disconnect();
  }, []);
  return { ref, atTop };
}

function TriggerLabel({ t }: { t: Trigger }) {
  const Icon = t.kind === "watch" ? FolderSync : t.kind === "person" ? User : Settings2;
  return (
    <span className="flex min-w-0 items-center gap-1.5 text-[12.5px] font-medium text-fg-secondary">
      <Icon aria-hidden className="size-3.5 shrink-0" />
      <span className="truncate">{t.label}</span>
    </span>
  );
}

function StackedBar({ share }: { share: { done: number; failed: number; running: number } }) {
  return (
    <span aria-hidden className="flex h-1.5 overflow-hidden rounded-[2px] bg-surface-neutral">
      <span className="bg-green" style={{ width: `${share.done * 100}%` }} />
      <span className="bg-red" style={{ width: `${share.failed * 100}%` }} />
      <span className="animate-pulse bg-blue" style={{ width: `${share.running * 100}%` }} />
    </span>
  );
}

export function RunsView({ tabs }: { tabs: ReactNode }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const { namespace, namespaces, can, me } = useArchive();
  const [status, setStatus] = useState("all");
  const [filters, setFilters] = useState<{
    ns: string;
    worker: string;
    trigger: string;
    pipeline: string;
  }>({ ns: "", worker: "", trigger: "", pipeline: "" });
  const [open, setOpen] = useState<Set<number>>(new Set());
  const [cancelling, setCancelling] = useState<JobRecord | null>(null);
  const nsFilter = filters.ns || namespace || "";
  const list = useJobList({
    status: status === "all" ? undefined : status,
    limit: 200,
    namespace: nsFilter, // on the server, so the counts and the 200 rows are that namespace's
  });
  const batches = useQuery({
    queryKey: ["batches"],
    queryFn: () => data(Batches.listBatches({ client })),
    refetchInterval: (q) =>
      (q.state.data ?? []).some((b) => ["running", "sample"].includes(b.status)) ? 5000 : 30_000,
  });
  const workers = useWorkers();
  const states = useWorkerStates(workers.data, list.jobs);
  const spaces = useSpaceNames();
  const names = usePeopleNames();
  const { cancel, retry } = useJobActions();
  const { text: announcement, announce } = useAnnouncer();
  const { ref: topRef, atTop } = useAtTop();

  const batchAction = useMutation({
    mutationFn: ({ id, action }: { id: number; action: BatchAction }) =>
      data(Batches.controlBatch({ client, path: { bid: id, action } })),
    onSuccess: (_d, v) => {
      toast({
        title: {
          pause: "Batch paused",
          resume: "Batch resumed",
          continue: "Running the rest of the batch",
          retry: "Retrying the failed recordings",
        }[v.action],
      });
      void qc.invalidateQueries({ queryKey: ["batches"] });
      void qc.invalidateQueries({ queryKey: ["jobs"] });
    },
    onError: (e: Error) =>
      toast({
        tone: "red",
        title: "Couldn’t change the batch",
        body: e.message,
      }),
  });

  const jobs = list.jobs;
  const total = Object.values(list.counts).reduce((a, n) => a + n, 0);

  const items = useMemo<Item[]>(() => {
    // Under "All", a batch is one row; its runs show when it is expanded (only batches you can see: yours, or all for admins).
    const batchIds = new Set(((batches.data ?? []) as BatchInfo[]).map((b) => b.id));
    const out: Item[] = jobs
      .filter((j) => !(status === "all" && j.batch != null && batchIds.has(j.batch)))
      .filter((j) => !filters.worker || j.worker === filters.worker)
      .filter((j) => !filters.trigger || j.created_by === filters.trigger)
      .filter((j) => !filters.pipeline || pipelineKey(j.pipeline) === filters.pipeline)
      .map((j) => ({ id: `j${j.id}`, at: j.created_at ?? "", job: j }));
    if (status === "all" && !filters.worker && !filters.trigger && !filters.pipeline)
      // with a namespace chosen, only batches with runs in it (a batch can span namespaces)
      for (const b of (batches.data ?? []) as BatchInfo[])
        if (!nsFilter || jobs.some((j) => j.batch === b.id))
          out.push({ id: `b${b.id}`, at: b.created_at ?? "", batch: b });
    return out.sort((a, b) => (a.at < b.at ? 1 : a.at > b.at ? -1 : 0));
  }, [jobs, batches.data, status, nsFilter, filters.worker, filters.trigger, filters.pipeline]);

  // Keep what is on screen still while scrolled down; new runs wait behind a pill.
  const filterKey = `${status}|${nsFilter}|${filters.worker}|${filters.trigger}|${filters.pipeline}`;
  const [shown, setShown] = useState<{ key: string; order: string[] }>({
    key: "",
    order: [],
  });
  const view = reconcileOrder(shown.key === filterKey ? shown.order : [], items, atTop);
  const orderKey = view.order.join(",");
  useEffect(() => {
    setShown((s) =>
      s.key === filterKey && s.order.join(",") === orderKey
        ? s
        : { key: filterKey, order: orderKey ? orderKey.split(",") : [] },
    );
  }, [filterKey, orderKey]);
  const byId = useMemo(() => new Map(items.map((i) => [i.id, i])), [items]);
  const rows = view.order.map((id) => byId.get(id)).filter((i): i is Item => Boolean(i));

  // Screen readers hear when a run finishes or fails.
  const lastStatus = useRef(new Map<number, string>());
  useEffect(() => {
    for (const j of jobs) {
      const before = lastStatus.current.get(j.id);
      if (before && before !== j.status && ["succeeded", "failed", "cancelled"].includes(j.status))
        announce(`j${j.id}`, `${j.title ?? `Run ${j.id}`}: ${jobPhase(j)}`);
      lastStatus.current.set(j.id, j.status);
    }
  }, [jobs]);

  const nsOf = (j: JobRecord) => spaces[j.space ?? -1];
  const phaseOf = (j: JobRecord) => {
    let waitingFor: string | null = null;
    if (j.status === "queued" && workers.data?.length && j.next_step) {
      const w = waitingReason(j.next_step, workers.data, states);
      if (w.stuck) waitingFor = stepSpecs([j.next_step])[0].label.toLowerCase();
    }
    return jobPhase(j, { waitingFor });
  };
  const workerOptions = [...new Set(jobs.map((j) => j.worker).filter(Boolean) as string[])].sort();
  const triggerOptions = [...new Set(jobs.map((j) => j.created_by).filter(Boolean) as string[])].sort();
  const pipelineChoices = pipelineOptions(jobs);
  const activeFilters = [
    filters.ns,
    filters.worker && `worker ${filters.worker}`,
    filters.trigger && triggerOf(filters.trigger, names, me?.user.email).label,
    filters.pipeline && (pipelineChoices.find((o) => o.value === filters.pipeline)?.label ?? "a pipeline"),
  ].filter(Boolean);

  const jobAction = (j: JobRecord, big = false) => {
    const ns = nsOf(j);
    const allowed = can("editor", ns);
    const size = big ? "md" : "sm";
    if (isActive(j.status) && j.status !== "paused")
      return (
        <Button
          variant={big ? "secondary" : "ghost"}
          size={size}
          className={cn(big && "h-11 text-fg")}
          disabled={!allowed || Boolean(j.cancel_requested)}
          disabledReason={!allowed ? needRole("editor", ns) : "Stopping after the current step"}
          onClick={() => setCancelling(j)}
        >
          Cancel
        </Button>
      );
    if (canRetry(j.status))
      return (
        <Button
          variant={big ? "primary" : "secondary"}
          size={size}
          className={cn(big && "h-11")}
          disabled={!allowed || retry.isPending}
          disabledReason={!allowed ? needRole("editor", ns) : undefined}
          onClick={() => retry.mutate(j)}
        >
          Retry
        </Button>
      );
    return null;
  };

  const batchButton = (b: BatchInfo, big = false) => {
    const p = batchPhase(b);
    if (!p.action) return null;
    const label = {
      pause: "Pause",
      resume: "Resume",
      continue: "Run the rest",
      retry: "Retry failed",
    }[p.action];
    return (
      <Button
        variant={p.action === "continue" ? "primary" : "secondary"}
        size={big ? "md" : "sm"}
        className={cn(big && "h-11")}
        disabled={batchAction.isPending}
        onClick={() => batchAction.mutate({ id: b.id, action: p.action! })}
      >
        {label}
      </Button>
    );
  };

  const toggle = (id: number) =>
    setOpen((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  const loading = list.isLoading;
  const error = list.error as Error | null;

  return (
    <div className="flex flex-col">
      <div ref={topRef} aria-hidden className="pointer-events-none -mb-6 h-6" />
      <div className="flex flex-col gap-2.5 px-4 pt-2.5 md:px-6">
        {tabs}
        <div className="flex flex-wrap items-center gap-1.5">
          <div role="group" aria-label="Filter by status" className="flex flex-wrap gap-1.5">
            {CHIPS.filter((c) => c.value !== "paused" || list.counts.paused).map((c) => {
              const on = c.value === status;
              const n = c.value === "all" ? total : (list.counts[c.value] ?? 0);
              return (
                <button
                  key={c.value}
                  type="button"
                  aria-pressed={on}
                  onClick={() => setStatus(c.value)}
                  className={cn(
                    "tabular flex h-[30px] items-center gap-1.5 rounded-pill border px-3 text-[12.5px] font-semibold transition-colors duration-fast",
                    on
                      ? "border-blue-border bg-blue-surface text-fg-accent"
                      : "border-border bg-background text-fg-strong hover:bg-surface",
                  )}
                >
                  {c.label}
                  <span className="font-medium text-fg-muted">{list.isSuccess ? n : "·"}</span>
                </button>
              );
            })}
          </div>
          <span className="flex-1" />
          <Popover>
            <PopoverTrigger asChild>
              <button
                type="button"
                className="inline-flex h-[30px] items-center gap-1.5 rounded-pill px-2 text-[12.5px] font-medium text-fg-muted hover:bg-surface-neutral hover:text-fg"
              >
                <ListFilter aria-hidden className="size-3.5" />
                {activeFilters.length ? (
                  <span className="font-semibold text-fg-accent">{activeFilters.join(" · ")}</span>
                ) : (
                  "Namespace · Worker · Trigger · Pipeline"
                )}{" "}
                ▾
              </button>
            </PopoverTrigger>
            <PopoverContent align="end" className="w-[300px] p-4">
              <div className="flex flex-col gap-3">
                <label className="flex flex-col gap-1.5 text-[13px] font-bold text-fg-strong">
                  Namespace
                  <Select
                    className="h-8 text-[13px]"
                    value={filters.ns}
                    onChange={(e) => setFilters((f) => ({ ...f, ns: e.target.value }))}
                    options={[
                      {
                        value: "",
                        label: namespace ? `${namespace} (top bar)` : "All namespaces",
                      },
                      ...namespaces.map((n) => ({
                        value: n.name,
                        label: `${n.name} · ${list.perNamespace[n.name] ?? 0} ${list.perNamespace[n.name] === 1 ? "run" : "runs"}`,
                      })),
                    ]}
                  />
                </label>
                <label className="flex flex-col gap-1.5 text-[13px] font-bold text-fg-strong">
                  Worker
                  <Select
                    className="h-8 text-[13px]"
                    value={filters.worker}
                    onChange={(e) => setFilters((f) => ({ ...f, worker: e.target.value }))}
                    options={[{ value: "", label: "Any worker" }, ...workerOptions]}
                  />
                </label>
                <label className="flex flex-col gap-1.5 text-[13px] font-bold text-fg-strong">
                  Trigger
                  <Select
                    className="h-8 text-[13px]"
                    value={filters.trigger}
                    onChange={(e) => setFilters((f) => ({ ...f, trigger: e.target.value }))}
                    options={[
                      { value: "", label: "Anyone or anything" },
                      ...triggerOptions.map((t) => ({
                        value: t,
                        label: triggerOf(t, names, me?.user.email).label,
                      })),
                    ]}
                  />
                </label>
                <label className="flex flex-col gap-1.5 text-[13px] font-bold text-fg-strong">
                  Pipeline
                  <Select
                    className="h-8 text-[13px]"
                    value={filters.pipeline}
                    onChange={(e) => setFilters((f) => ({ ...f, pipeline: e.target.value }))}
                    options={[{ value: "", label: "Any pipeline or version" }, ...pipelineChoices]}
                  />
                </label>
                {activeFilters.length > 0 && (
                  <Button
                    variant="link"
                    size="sm"
                    className="self-start"
                    onClick={() => setFilters({ ns: "", worker: "", trigger: "", pipeline: "" })}
                  >
                    Clear filters
                  </Button>
                )}
              </div>
            </PopoverContent>
          </Popover>
        </div>
      </div>

      <div aria-live="polite" className="sr-only">
        {announcement}
      </div>

      {view.pending.length > 0 && (
        <div className="pointer-events-none sticky top-[76px] z-20 flex h-0 justify-center">
          <button
            type="button"
            onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
            className="pointer-events-auto mt-2 inline-flex h-8 items-center gap-1.5 rounded-pill bg-blue px-3.5 text-[13px] font-bold text-white shadow-2 hover:bg-blue-dark"
          >
            <ArrowUp aria-hidden className="size-4" /> {view.pending.length} new
          </button>
        </div>
      )}

      <div className="mt-3">
        {loading ? (
          <SkeletonRows rows={7} className="px-6" />
        ) : error ? (
          <EmptyState
            tone="error"
            icon={<Cloud />}
            title="Couldn’t load runs"
            actions={<Button onClick={() => list.refetch()}>Try again</Button>}
          >
            {error.message}
          </EmptyState>
        ) : rows.length === 0 ? (
          <EmptyState
            icon={<Cloud />}
            title={status === "all" && !activeFilters.length && !namespace ? "No runs yet" : "No runs match"}
          >
            {status === "all" && !activeFilters.length && !namespace
              ? "Imports, watched folders and Reprocess start runs. They appear here as they happen."
              : "Try another status or clear the filters."}
          </EmptyState>
        ) : (
          <>
            {/* Phones: one card per run */}
            <ul className="border-t border-border md:hidden" aria-label="Runs">
              {rows.map((it) => {
                if (it.batch) {
                  const p = batchPhase(it.batch);
                  return (
                    <JobListItem
                      key={it.id}
                      size="lg"
                      status={p.icon}
                      title={`${it.batch.label} · batch #${it.batch.id}`}
                      sub={p.text}
                      progress={p.share.done + p.share.failed}
                      action={batchButton(it.batch, true)}
                    />
                  );
                }
                const j = it.job!;
                const cur = stepSpecs(j.steps)[Math.min(j.step_index ?? 0, Math.max(0, (j.steps?.length ?? 1) - 1))];
                return (
                  <JobListItem
                    key={it.id}
                    size="lg"
                    status={j.status}
                    href={`/activity/${j.id}`}
                    title={`${j.title ?? `Recording ${j.recording}`}${cur && j.status !== "succeeded" ? ` · ${cur.label}` : ""}`}
                    sub={phaseOf(j) + (j.worker && j.status === "running" ? ` · ${j.worker}` : "")}
                    progress={j.status === "running" ? (j.progress ?? 0) : null}
                    action={jobAction(j, true)}
                  />
                );
              })}
            </ul>

            {/* Desktop: the table */}
            <div className="hidden md:block">
              <Table aria-label="Runs" className="table-fixed">
                <colgroup>
                  <col style={{ width: 58 }} />
                  <col />
                  <col />
                  <col style={{ width: 150 }} />
                  <col style={{ width: 140 }} />
                  <col style={{ width: 130 }} />
                  <col style={{ width: 110 }} />
                  <col style={{ width: 140 }} />
                </colgroup>
                <THead>
                  <tr>
                    <Th className="first:pl-6">
                      <span className="sr-only">State</span>
                    </Th>
                    <Th>Run</Th>
                    <Th>Progress</Th>
                    <Th>Worker</Th>
                    <Th>Trigger</Th>
                    <Th>Namespace</Th>
                    <Th className="text-right">Started</Th>
                    <Th className="last:pr-6">
                      <span className="sr-only">Actions</span>
                    </Th>
                  </tr>
                </THead>
                <tbody>
                  {rows.map((it) => {
                    if (it.batch) {
                      const b = it.batch;
                      const p = batchPhase(b);
                      const expanded = open.has(b.id);
                      const kids = jobs.filter((j) => j.batch === b.id);
                      return (
                        <Fragment key={it.id}>
                          <Tr className="h-[60px]">
                            <Td className="first:pl-6">
                              <JobStateIcon status={p.icon} />
                            </Td>
                            <Td>
                              <div className="flex min-w-0 items-center gap-1.5">
                                <button
                                  type="button"
                                  onClick={() => toggle(b.id)}
                                  aria-expanded={expanded}
                                  aria-label={`${expanded ? "Hide" : "Show"} the recordings in ${b.label}`}
                                  className="-ml-1 grid size-6 shrink-0 place-items-center rounded-full text-fg-secondary hover:bg-surface-neutral"
                                >
                                  <ChevronRight
                                    aria-hidden
                                    className={cn("size-4 transition-transform duration-fast", expanded && "rotate-90")}
                                  />
                                </button>
                                <div className="flex min-w-0 flex-col gap-1">
                                  <span className="truncate text-[13.5px] font-semibold text-fg">{b.label}</span>
                                  <span className="truncate text-[12px] text-fg-muted">
                                    batch #{b.id} · {(b.progress?.total ?? 0) + (b.progress?.remaining ?? 0)} recordings
                                  </span>
                                </div>
                              </div>
                            </Td>
                            <Td>
                              <div className="flex flex-col gap-1.5">
                                <StackedBar share={p.share} />
                                <span className="tabular truncate text-[12.5px] font-medium text-fg-secondary">
                                  {p.text}
                                </span>
                              </div>
                            </Td>
                            <Td className="text-[12.5px] text-fg-muted">—</Td>
                            <Td>
                              <TriggerLabel t={triggerOf(b.created_by, names, me?.user.email)} />
                            </Td>
                            <Td className="text-[12.5px] text-fg-muted">—</Td>
                            <Td
                              className="tabular text-right text-[12.5px] text-fg-muted"
                              title={absolute(b.created_at)}
                            >
                              {shortWhen(b.created_at)}
                            </Td>
                            <Td className="text-right last:pr-6">{batchButton(b)}</Td>
                          </Tr>
                          {expanded && (
                            <tr className="border-b border-border bg-surface">
                              <td colSpan={8} className="px-6 py-2">
                                {kids.length ? (
                                  <ul className="flex flex-col divide-y divide-border pl-[58px]">
                                    {kids.map((j) => (
                                      <li key={j.id} className="flex items-center gap-3 py-2 text-[13px]">
                                        <JobStateIcon status={j.status} size={16} />
                                        <Link
                                          href={`/activity/${j.id}`}
                                          className="min-w-0 flex-1 truncate font-semibold text-fg hover:underline"
                                        >
                                          {j.title ?? `Recording ${j.recording}`}
                                        </Link>
                                        <span
                                          className={cn(
                                            "truncate text-[12.5px]",
                                            j.status === "failed" ? "text-red-dark" : "text-fg-secondary",
                                          )}
                                        >
                                          {phaseOf(j)}
                                        </span>
                                      </li>
                                    ))}
                                  </ul>
                                ) : (
                                  <p className="pl-[58px] text-[13px] text-fg-secondary">
                                    Recordings in this batch appear here as they start.
                                  </p>
                                )}
                              </td>
                            </tr>
                          )}
                        </Fragment>
                      );
                    }
                    const j = it.job!;
                    const failed = j.status === "failed";
                    const specs = stepSpecs(j.steps);
                    return (
                      <Tr key={it.id} className={cn("h-[60px]", failed && "bg-red-surface hover:bg-red-surface")}>
                        <Td className="first:pl-6">
                          <JobStateIcon status={j.status} />
                        </Td>
                        <Td>
                          <div className="flex min-w-0 flex-col gap-1">
                            <Link
                              href={`/activity/${j.id}`}
                              className="truncate text-[13.5px] font-semibold text-fg hover:underline"
                            >
                              {j.title ?? `Recording ${j.recording}`}
                            </Link>
                            <span className="truncate text-[12px] text-fg-muted">
                              Run #{j.id} · {pipelineLabel(j.pipeline) ? `${pipelineLabel(j.pipeline)} · ` : ""}
                              {specs.length} {specs.length === 1 ? "step" : "steps"}
                              {j.batch != null ? ` · batch #${j.batch}` : ""}
                            </span>
                          </div>
                        </Td>
                        <Td>
                          <div className="flex min-w-0 flex-col gap-1.5">
                            <StepSegments job={j} />
                            <span
                              className={cn(
                                "tabular truncate text-[12.5px] font-medium",
                                failed ? "text-red-dark" : j.status === "running" ? "text-fg" : "text-fg-secondary",
                              )}
                              title={phaseOf(j)}
                            >
                              {phaseOf(j)}
                            </span>
                          </div>
                        </Td>
                        <Td className="truncate text-[12.5px] font-medium text-fg-strong">
                          {j.worker ?? <span className="text-fg-muted">—</span>}
                        </Td>
                        <Td>
                          <TriggerLabel t={triggerOf(j.created_by, names, me?.user.email)} />
                        </Td>
                        <Td className="truncate text-[12.5px] font-medium text-fg-secondary">{nsOf(j) ?? "—"}</Td>
                        <Td className="tabular text-right text-[12.5px] text-fg-muted" title={absolute(j.started_at)}>
                          {j.started_at ? shortWhen(j.started_at) : "—"}
                        </Td>
                        <Td className="text-right last:pr-6">{jobAction(j)}</Td>
                      </Tr>
                    );
                  })}
                </tbody>
              </Table>
            </div>
            {list.jobs.length >= 200 && (
              <p className="px-6 py-3 text-[12.5px] text-fg-muted">Showing the 200 newest runs.</p>
            )}
          </>
        )}
      </div>

      <CancelJobDialog
        job={cancelling}
        open={cancelling != null}
        onOpenChange={(o) => !o && setCancelling(null)}
        pending={cancel.isPending}
        onConfirm={() => cancelling && cancel.mutate(cancelling, { onSettled: () => setCancelling(null) })}
      />
    </div>
  );
}
