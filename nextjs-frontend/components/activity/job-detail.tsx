"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, FileAudio, RotateCw } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState, type ReactNode } from "react";

import { Jobs, Pipelines, Recordings, Templates } from "@/app/openapi-client";
import { CancelJobDialog } from "@/components/activity/cancel-dialog";
import {
  approx,
  canRetry,
  dayTime,
  elapsed,
  isActive,
  outputText,
  pipelineLabel,
  plainError,
  runSteps,
  span,
  timeLeft,
  took,
  stepSpecs,
  stepStates,
  triggerOf,
  usually,
  waitingReason,
  type JobRecord,
  type StepRunState,
  type StepSpec,
} from "@/components/activity/job-model";
import { downloadText, LogViewer } from "@/components/activity/log-viewer";
import { useJobLog } from "@/components/activity/use-job-log";
import {
  useJobActions,
  usePeopleNames,
  useSpaceNames,
  useWorkerSettings,
  useWorkerStates,
  useWorkers,
} from "@/components/activity/use-activity";
import { StatusChip } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Menu, MenuContent, MenuItem, MenuLabel, MenuSeparator, MenuTrigger } from "@/components/ui/menu";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { needRole, useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

const DOT: Record<StepRunState, { bg: string; glyph: string }> = {
  done: { bg: "bg-green text-white", glyph: "✓" },
  running: { bg: "bg-blue text-white animate-pulse", glyph: "" },
  failed: { bg: "bg-red text-white", glyph: "✕" },
  waiting: { bg: "bg-border", glyph: "" },
  skipped: {
    bg: "bg-surface-neutral text-fg-secondary border border-border",
    glyph: "–",
  },
  "not-run": { bg: "bg-border", glyph: "" },
};

const WORD: Record<StepRunState, string> = {
  done: "Done",
  running: "Running",
  failed: "Failed",
  waiting: "Waiting",
  skipped: "Skipped",
  "not-run": "Didn’t run",
};

function secondsText(s: number | undefined) {
  return s == null ? undefined : span(s * 1000);
}

/** What a step is set to do, from its spec (template, output key, conditions...). */
function specText(s: StepSpec, templateName?: (id: number) => string | undefined): string | null {
  const x = s.spec as {
    template?: number;
    version?: number;
    key?: string;
    filename?: string;
    model?: string;
    force?: boolean;
    when?: Record<string, unknown>;
  };
  const parts: string[] = [];
  if (x.template != null)
    parts.push(`${templateName?.(x.template) ?? `template #${x.template}`}${x.version ? ` v${x.version}` : ""}`);
  if (x.key) parts.push(`saves outputs.${x.key}`);
  if (x.filename) parts.push(`file ${x.filename}`);
  if (x.model) parts.push(`model ${x.model}`);
  if (x.force) parts.push("forced");
  const w = x.when ?? {};
  if (w.min_minutes != null) parts.push(`only if ≥ ${w.min_minutes} min`);
  if (w.max_minutes != null) parts.push(`only if ≤ ${w.max_minutes} min`);
  if (w.source) parts.push(`only ${String(w.source)}`);
  if (Array.isArray(w.languages) && w.languages.length) parts.push(`languages ${w.languages.join(", ")}`);
  return parts.length ? parts.join(" · ") : null;
}

function Box({
  tone,
  glyph,
  title,
  children,
}: {
  tone: "red" | "blue" | "green" | "neutral";
  glyph: string;
  title: ReactNode;
  children?: ReactNode;
}) {
  const t = {
    red: ["bg-red-surface border-red-border", "bg-red text-white"],
    blue: ["bg-blue-surface border-blue-border", "bg-blue text-white"],
    green: ["bg-green-surface border-green-border", "bg-green text-white"],
    neutral: ["bg-surface border-border", "text-fg-muted"],
  }[tone];
  return (
    <div role={tone === "red" ? "alert" : "status"} className={cn("flex gap-3 rounded-md border px-3.5 py-3", t[0])}>
      <span
        aria-hidden
        className={cn("grid size-[22px] shrink-0 place-items-center rounded-full text-[12px] font-extrabold", t[1])}
      >
        {glyph}
      </span>
      <div className="flex min-w-0 flex-col gap-1 text-[13.5px] leading-normal text-fg-strong">
        <b className="font-bold text-fg">{title}</b>
        {children && <span className="break-words">{children}</span>}
      </div>
    </div>
  );
}

function Dl({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[110px_minmax(0,1fr)] gap-x-3 gap-y-2 text-[13px] leading-snug">
      {rows.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-fg-muted">{k}</dt>
          <dd className="min-w-0 break-words text-fg">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

const time = (iso: string | null | undefined) => dayTime(iso, true);

/** A2 / A5: one run — its steps on the left, then what happened (or what it waits for), details and the log. */
export function JobDetail({ jobId }: { jobId: number }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const router = useRouter();
  const toast = useToast();
  const { can, me } = useArchive();
  const spaces = useSpaceNames();
  const names = usePeopleNames();
  const workers = useWorkers();
  const { maxAttempts, loaded: settingsLoaded } = useWorkerSettings();
  const { cancel, retry } = useJobActions();
  const [sel, setSel] = useState<number | "all" | null>(null);
  const [cancelOpen, setCancelOpen] = useState(false);

  const q = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => data(Jobs.getJob({ client, path: { jid: jobId } })) as Promise<JobRecord>,
    refetchInterval: (query) => (isActive(query.state.data?.status) ? 2000 : false),
  });
  const job = q.data;
  const states = useWorkerStates(workers.data, job ? [job] : []);
  // the whole log; runs from before it was kept have the last 200 lines on the run
  const whole = useJobLog(jobId, isActive(job?.status));
  const log = job?.log_total == null ? job?.log : (whole ?? job?.log);
  // the number of the first line in `log`: the run's last lines start partway through until the whole log is read
  const logStart = job?.log_total == null || whole ? 0 : Math.max(0, job.log_total - (job.log?.length ?? 0));
  const title = job?.title ?? null;
  const pipelines = useQuery({
    queryKey: ["pipelines"],
    queryFn: () => data(Pipelines.listPipelines({ client })),
    staleTime: 60_000,
  });
  const templates = useQuery({
    queryKey: ["templates"],
    queryFn: () => data(Templates.listTemplates({ client })),
    staleTime: 5 * 60_000,
  });
  const templateName = (id: number) => templates.data?.find((t) => t.id === id)?.name;

  const specs = useMemo(() => stepSpecs(job?.steps), [job?.steps]);
  const parsed = useMemo(
    () => runSteps({ steps: job?.steps, step_runs: job?.step_runs }, log, logStart),
    [log, logStart, job?.steps, job?.step_runs],
  );
  const stepStateList = job ? stepStates(job, parsed.byStep) : [];

  const rerun = useMutation({
    mutationFn: async (pid: number | null) => {
      if (!job?.recording) throw new Error("This run has no recording");
      const r =
        pid == null
          ? await data(
              Recordings.reprocessRecording({
                client,
                path: { rid: job.recording },
                body: {},
              }),
            )
          : await data(
              Pipelines.runPipeline({
                client,
                path: { pid },
                body: { recording: job.recording },
              }),
            );
      return r.job;
    },
    onSuccess: (id) => {
      void qc.invalidateQueries({ queryKey: ["jobs"] });
      toast({ title: "Started a new run", body: title ?? undefined });
      router.push(`/activity/${id}`);
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t start the run", body: e.message }),
  });

  if (q.isLoading)
    return (
      <div className="flex flex-col gap-4 p-6" aria-busy="true" aria-label="Loading run">
        <Skeleton className="w-40" />
        <Skeleton className="h-7 w-[420px] max-w-full" />
        <Skeleton className="w-[60%]" />
        <Skeleton className="mt-4 h-[320px] w-full rounded-md" />
      </div>
    );
  if (q.error || !job) {
    const e = q.error as ApiError | null;
    return (
      <EmptyState
        tone={e?.status === 404 ? "neutral" : "error"}
        icon={<FileAudio />}
        title={
          e?.status === 404
            ? "This run doesn’t exist"
            : e?.status === 403
              ? "You can’t see this run"
              : "Couldn’t load this run"
        }
        actions={
          <>
            <Button asChild variant="secondary">
              <Link href="/activity">Back to Activity</Link>
            </Button>
            {e?.status !== 404 && e?.status !== 403 && <Button onClick={() => q.refetch()}>Try again</Button>}
          </>
        }
      >
        {e?.status === 404 ? "It may have been removed. Recent runs are listed in Activity." : e?.message}
      </EmptyState>
    );
  }

  const ns = spaces[job.space ?? -1];
  const allowed = can("editor", ns);
  const i = Math.min(job.step_index ?? 0, Math.max(0, specs.length - 1));
  const cur = specs[i];
  const active = isActive(job.status);
  const trig = triggerOf(job.created_by, names, me?.user.email);
  const ran = elapsed(job.started_at, active ? null : job.finished_at);
  const selected = sel ?? (job.status === "failed" || job.status === "running" ? i : "all");
  const sIdx = typeof selected === "number" ? selected : null;
  const logLines = sIdx != null ? (parsed.byStep[sIdx]?.lines ?? []) : parsed.lines;
  const finished = !active;
  const reason =
    job.status === "queued" && job.next_step && workers.data
      ? waitingReason(job.next_step, workers.data, states)
      : null;
  const curLower = (cur?.label ?? job.next_step ?? "").toLowerCase();
  const downstream = specs.slice(i + 1).map((s) => s.label);
  const pipeline = pipelineLabel(job.pipeline);
  const left = timeLeft(job);
  const est = job.estimates ?? [];
  const runningFor = (k: number) => elapsed(parsed.byStep[k]?.startedAt, null);
  /** The time beside a step: how long it took, how long it has been running, or (waiting) how long it usually takes. */
  const stepTime = (k: number, st: StepRunState) => {
    if (st === "running") {
      const so = runningFor(k);
      return so != null ? span(so) : "…";
    }
    if (st === "waiting") return est[k] != null ? approx(est[k]) : "—";
    return secondsText(parsed.byStep[k]?.seconds) ?? "—";
  };
  const tookText = (k: number, st: StepRunState) => {
    const typical = st === "skipped" || st === "not-run" ? null : usually(est[k]);
    if (st === "running") {
      const so = runningFor(k);
      return [so != null ? `${span(so)} so far` : "Running", typical].filter(Boolean).join(" · ");
    }
    if (st === "waiting") return typical ?? "—";
    const t = secondsText(parsed.byStep[k]?.seconds);
    return t ? [t, typical].filter(Boolean).join(" · ") : "—";
  };

  let box: ReactNode;
  if (job.status === "failed")
    box = (
      <Box tone="red" glyph="✕" title={`${cur?.label ?? "A step"} failed.`}>
        {job.error}
        <br />
        Retrying keeps every earlier step’s output; only {cur?.label ?? "this step"}
        {downstream.length ? ` and ${downstream.join(", ")}` : ""} run again.
      </Box>
    );
  else if (job.status === "queued")
    box = (
      <Box
        tone="neutral"
        glyph="○"
        title={
          reason?.stuck
            ? `Waiting for a worker that can ${curLower}.`
            : `Queued. ${cur?.label ?? "It"} runs when a worker is free.`
        }
      >
        {reason ? reason.text : `It starts when a worker that runs ${curLower} is free.`}
      </Box>
    );
  else if (job.status === "running") {
    const so = runningFor(i);
    box = (
      <Box tone="blue" glyph="" title={`Running ${cur?.label ?? "a step"}${job.worker ? ` on ${job.worker}` : ""}.`}>
        {so != null && `${span(so)} so far${est[i] != null ? `; it usually takes ${approx(est[i])}` : ""}. `}
        {job.cancel_requested
          ? "Stopping after this step, as asked."
          : "Cancel stops the run after this step; its output is kept."}
      </Box>
    );
  } else if (job.status === "succeeded") {
    const skipped = specs.filter((_, k) => stepStateList[k] === "skipped").map((s) => s.label);
    box = (
      <Box tone="green" glyph="✓" title={`Succeeded${ran != null ? ` in ${took(ran)}` : ""}.`}>
        {skipped.length
          ? `${skipped.join(", ")} skipped; the other steps finished.`
          : `All ${specs.length} steps finished.`}
      </Box>
    );
  } else if (job.status === "paused") box = <Box tone="neutral" glyph="॥" title="Paused with its batch." />;
  else
    box = (
      <Box tone="neutral" glyph="–" title={`Cancelled at ${cur?.label ?? "a step"}.`}>
        Earlier outputs are kept. Retry resumes from {cur?.label ?? "the step where it stopped"}.
      </Box>
    );

  const retryButton = canRetry(job.status) && (
    <Button
      size="sm"
      variant="primary"
      icon={<RotateCw />}
      disabled={!allowed || retry.isPending}
      disabledReason={needRole("editor", ns)}
      onClick={() => retry.mutate(job)}
    >
      Retry from {cur?.label ?? "the failed step"}
    </Button>
  );

  return (
    <div className="flex min-h-[calc(100vh-64px)] flex-col">
      <header className="flex flex-col gap-2 border-b border-border px-4 pb-4 pt-[18px] md:px-6">
        <nav aria-label="Breadcrumb" className="flex items-center gap-1.5 text-[12px] font-medium text-fg-muted">
          <Link href="/activity" className="hover:text-fg hover:underline">
            Activity
          </Link>
          <ChevronRight aria-hidden className="size-3" />
          <span aria-current="page">
            Run #{job.id}
            {title ? ` of ${title}` : ""}
          </span>
        </nav>
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="min-w-0 text-[22px] font-bold leading-tight text-fg">
            {title ?? `Recording ${job.recording ?? ""}`}
          </h1>
          <StatusChip status={job.status} />
          <span className="flex-1" />
          <Button
            size="sm"
            variant="secondary"
            disabled={!active || job.status === "paused" || !allowed || Boolean(job.cancel_requested)}
            disabledReason={
              !allowed
                ? needRole("editor", ns)
                : job.cancel_requested
                  ? "Stopping after the current step"
                  : job.status === "paused"
                    ? "Paused runs are controlled by their batch"
                    : "Run already finished"
            }
            onClick={() => setCancelOpen(true)}
          >
            Cancel
          </Button>
          {!allowed || active || job.recording == null ? (
            <Button
              size="sm"
              variant="secondary"
              disabled
              disabledReason={
                !allowed ? needRole("editor", ns) : active ? "Wait for this run to finish" : "This run has no recording"
              }
            >
              Re-run with another version <ChevronDown />
            </Button>
          ) : (
            <Menu>
              <MenuTrigger asChild>
                <Button size="sm" variant="secondary" disabled={rerun.isPending}>
                  Re-run with another version <ChevronDown />
                </Button>
              </MenuTrigger>
              <MenuContent align="end" className="w-[280px]">
                <MenuLabel>Run on this recording</MenuLabel>
                <MenuItem onSelect={() => rerun.mutate(null)}>
                  {ns ? `${ns}’s default pipeline` : "Namespace default"}
                </MenuItem>
                {(pipelines.data?.pipelines ?? []).length > 0 && <MenuSeparator />}
                {(pipelines.data?.pipelines ?? []).map((p) => (
                  <MenuItem key={p.id} onSelect={() => rerun.mutate(p.id)} shortcut={`v${p.current}`}>
                    {p.name}
                  </MenuItem>
                ))}
              </MenuContent>
            </Menu>
          )}
          {retryButton}
        </div>
        <div className="tabular flex flex-wrap gap-x-4 gap-y-1 text-[13px] text-fg-secondary">
          <span>
            {pipeline ? `${pipeline} · ` : ""}
            {specs.length} {specs.length === 1 ? "step" : "steps"}
          </span>
          <span>Triggered by {trig.label}</span>
          {job.worker && <span className="font-mono text-[12.5px]">{job.worker}</span>}
          <span>
            {job.started_at ? `Started ${dayTime(job.started_at)}` : `Queued ${dayTime(job.created_at)}`}
            {ran != null && job.started_at
              ? ` · ${active ? "running for" : "ran"} ${active ? span(ran) : took(ran)}`
              : ""}
          </span>
          {left && <span className="font-semibold text-fg">{left}</span>}
          {ns && <span>{ns}</span>}
          {job.recording != null && (
            <Link href={`/recordings/${job.recording}`} className="font-semibold text-fg-accent hover:underline">
              Open recording →
            </Link>
          )}
        </div>
      </header>

      <div className="grid flex-1 md:grid-cols-[300px_minmax(0,1fr)]">
        <aside aria-label="Steps" className="border-b border-border bg-surface px-3 py-4 md:border-b-0 md:border-r">
          <ol className="flex flex-col gap-0.5">
            {specs.map((s, k) => {
              const st = stepStateList[k];
              const info = parsed.byStep[k];
              const on = selected === k;
              const sub =
                st === "failed"
                  ? plainError(info?.note ?? job.error) || "Failed"
                  : st === "not-run"
                    ? "Didn’t run"
                    : st === "waiting"
                      ? k === i && job.status === "queued"
                        ? "Next · waiting for a worker"
                        : "Waiting"
                      : st === "running"
                        ? "Running…"
                        : (info?.note ?? WORD[st]);
              return (
                <li key={k}>
                  <button
                    type="button"
                    aria-pressed={on}
                    onClick={() => setSel(on ? "all" : k)}
                    className={cn(
                      "grid w-full grid-cols-[22px_minmax(0,1fr)_auto] items-center gap-2.5 rounded-[10px] border p-2.5 text-left transition-colors duration-fast",
                      on
                        ? st === "failed"
                          ? "border-red-border bg-background"
                          : "border-blue-border bg-background"
                        : "border-transparent hover:bg-surface-neutral",
                    )}
                  >
                    <span
                      aria-hidden
                      className={cn(
                        "grid size-5 place-items-center rounded-full text-[10px] font-extrabold",
                        DOT[st].bg,
                      )}
                    >
                      {DOT[st].glyph}
                    </span>
                    <span className="flex min-w-0 flex-col gap-[3px]">
                      <span className="text-[13.5px] font-semibold leading-tight text-fg">
                        {s.label}
                        <span className="sr-only"> · {WORD[st]}</span>
                      </span>
                      <span
                        className={cn(
                          "truncate text-[12px] leading-tight",
                          st === "failed" ? "text-red-dark" : "text-fg-muted",
                        )}
                        title={sub}
                      >
                        {sub}
                      </span>
                    </span>
                    <span className="tabular text-[12px] font-medium text-fg-muted">{stepTime(k, st)}</span>
                  </button>
                </li>
              );
            })}
          </ol>
          <button
            type="button"
            aria-pressed={selected === "all"}
            onClick={() => setSel("all")}
            className={cn(
              "mt-2 w-full rounded-[10px] px-2.5 py-2 text-left text-[13px] font-semibold",
              selected === "all" ? "bg-background text-fg-accent" : "text-fg-secondary hover:bg-surface-neutral",
            )}
          >
            Whole log
          </button>
        </aside>

        <section aria-label="Run details" className="flex min-w-0 flex-col gap-4 px-4 py-[18px] md:px-6">
          {box}
          <div className="grid gap-4 xl:grid-cols-2">
            <div className="flex flex-col gap-2 rounded-md border border-border p-3.5">
              <h2 className="label-caps">Run</h2>
              <Dl
                rows={[
                  [
                    "Recording",
                    job.recording != null ? (
                      <Link href={`/recordings/${job.recording}`} className="hover:underline">
                        {title ?? `#${job.recording}`}
                        {ns ? ` · ${ns}` : ""}
                      </Link>
                    ) : (
                      "—"
                    ),
                  ],
                  [
                    "Started by",
                    trig.kind === "watch" ? (
                      <Link href="/sources" className="hover:underline">
                        {trig.label}
                      </Link>
                    ) : (
                      trig.label
                    ),
                  ],
                  [
                    "Pipeline",
                    job.pipeline?.id != null ? (
                      <Link href={`/pipelines/${job.pipeline.id}`} className="hover:underline">
                        {pipeline}
                      </Link>
                    ) : (
                      (pipeline ?? (job.batch != null ? "Steps chosen for the batch" : "Steps chosen for this run"))
                    ),
                  ],
                  [
                    "Steps",
                    <span key="s">
                      {specs.map((s, k) => (
                        <span key={k}>
                          {k > 0 && " → "}
                          <span className={cn(k === i && active && "font-bold")}>{s.type}</span>
                        </span>
                      ))}
                    </span>,
                  ],
                  ["Attempts", settingsLoaded ? `${job.attempts ?? 0} of ${maxAttempts}` : String(job.attempts ?? 0)],
                  ["Created", time(job.created_at)],
                  [
                    "Started · Finished",
                    `${job.started_at ? time(job.started_at) : "—"} · ${job.finished_at ? time(job.finished_at) : "—"}`,
                  ],
                  ["Worker", job.worker ?? "none yet"],
                  ...(job.batch != null ? ([["Batch", `batch #${job.batch}`]] as [string, ReactNode][]) : []),
                ]}
              />
            </div>
            <div className="flex flex-col gap-2 rounded-md border border-border p-3.5">
              <h2 className="label-caps">
                {sIdx != null ? `Step ${sIdx + 1} of ${specs.length} · ${specs[sIdx]?.label}` : "Steps"}
              </h2>
              {sIdx != null ? (
                <Dl
                  rows={[
                    ["State", WORD[stepStateList[sIdx]]],
                    [
                      "Started · Finished",
                      parsed.byStep[sIdx]?.startedAt
                        ? `${time(parsed.byStep[sIdx]?.startedAt)} · ${time(parsed.byStep[sIdx]?.finishedAt)}`
                        : "—",
                    ],
                    ["Took", tookText(sIdx, stepStateList[sIdx])],
                    ["Settings", specText(specs[sIdx], templateName) ?? "Defaults from Settings"],
                    ["Last message", parsed.byStep[sIdx]?.note ?? "—"],
                    [
                      "Saved",
                      parsed.byStep[sIdx]?.outputs?.length ? (
                        <span key="o" className="flex flex-col gap-0.5">
                          {parsed.byStep[sIdx]?.outputs?.map((o) => (
                            <span key={o.key} className="font-mono text-[12.5px]">
                              {outputText(o, templateName)}
                            </span>
                          ))}
                        </span>
                      ) : (
                        "—"
                      ),
                    ],
                    ...(parsed.byStep[sIdx]?.worker
                      ? ([["Worker", parsed.byStep[sIdx]?.worker]] as [string, ReactNode][])
                      : []),
                  ]}
                />
              ) : (
                <p className="text-[13px] leading-normal text-fg-secondary">
                  Pick a step on the left to see its settings, how long it took and its part of the log. Outputs are
                  saved on the{" "}
                  {job.recording != null ? (
                    <Link
                      href={`/recordings/${job.recording}`}
                      className="font-semibold text-fg-accent hover:underline"
                    >
                      recording
                    </Link>
                  ) : (
                    "recording"
                  )}
                  .
                </p>
              )}
            </div>
          </div>
          <LogViewer
            heading={`Logs · ${sIdx != null ? specs[sIdx]?.label : "whole run"}`}
            title={`run-${job.id} · ${sIdx != null ? specs[sIdx]?.type : "all steps"}`}
            lines={logLines}
            live={job.status === "running"}
            finished={finished}
            truncated={job.log_total == null && (job.log?.length ?? 0) >= 200}
            empty={
              job.status === "queued"
                ? "The log streams here once it starts."
                : sIdx != null
                  ? "This step hasn’t logged anything."
                  : "No log lines."
            }
            onDownload={() => downloadText(`run-${job.id}.log`, (log ?? []).join("\n") + "\n")}
          />
          <p className="text-[12.5px] leading-snug text-fg-secondary">
            Cancel is immediate while queued; while running it stops after the current step. Retry resumes from the
            failed step.
          </p>
        </section>
      </div>

      <CancelJobDialog
        job={job}
        open={cancelOpen}
        onOpenChange={setCancelOpen}
        pending={cancel.isPending}
        onConfirm={() => cancel.mutate(job, { onSettled: () => setCancelOpen(false) })}
      />
    </div>
  );
}
