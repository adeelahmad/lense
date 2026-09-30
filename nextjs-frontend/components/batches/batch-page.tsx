"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, FileText, Pause, Play, RotateCcw } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";

import { Batches, Collections, Entities, Jobs } from "@/app/openapi-client";
import { useBatch, useBatchJobs, useBatchResults, type BatchJob } from "@/components/batches/data";
import { forgetCombine, pendingCombine } from "@/components/batches/plan";
import {
  approxDuration,
  batchTone,
  describeSelection,
  eta,
  money,
  progressLabel,
  progressParts,
} from "@/components/batches/format";
import { ResultsTable } from "@/components/batches/results-table";
import { Badge } from "@/components/ui/badge";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Field, Textarea } from "@/components/ui/field";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { count, plural, relative } from "@/lib/format";

const GLYPH: Record<string, { g: string; c: string; label: string }> = {
  succeeded: { g: "✓", c: "var(--aladdin-green)", label: "done" },
  failed: { g: "✕", c: "var(--aladdin-red)", label: "failed" },
  running: { g: "●", c: "var(--aladdin-blue)", label: "running" },
  queued: { g: "○", c: "var(--text-muted)", label: "queued" },
  paused: { g: "‖", c: "var(--text-muted)", label: "paused" },
  cancelled: { g: "–", c: "var(--text-muted)", label: "cancelled" },
};

function seconds(j: BatchJob): string {
  if (!j.started_at) return "—";
  const end = j.finished_at ? Date.parse(j.finished_at) : Date.now();
  const s = Math.max(0, Math.round((end - Date.parse(j.started_at)) / 1000));
  return s < 60 ? `${s} s` : `${Math.round(s / 60)} m`;
}

/** The progress bar in four colours: done (green), failed (red), running (blue), waiting (grey). */
function ProgressBar({ parts }: { parts: ReturnType<typeof progressParts> }) {
  const t = Math.max(1, parts.total);
  const seg = (n: number, c: string) =>
    n ? <span style={{ width: `${(n / t) * 100}%`, background: c }} className="h-full" /> : null;
  return (
    <div
      role="progressbar"
      aria-label="Batch progress"
      aria-valuemin={0}
      aria-valuemax={parts.total}
      aria-valuenow={parts.done + parts.failed}
      aria-valuetext={`${progressLabel(parts)}${parts.failed ? `, ${parts.failed} failed` : ""}`}
      className="flex h-2 overflow-hidden rounded-pill bg-surface-neutral"
    >
      {seg(parts.done, "var(--aladdin-green)")}
      {seg(parts.failed, "var(--aladdin-red)")}
      {seg(parts.running, "var(--aladdin-blue)")}
    </div>
  );
}

/** Progress for screen readers: polite, and at most once every 10 seconds. */
function LiveProgress({ text }: { text: string }) {
  const [said, setSaid] = useState(text);
  const last = useRef(0);
  useEffect(() => {
    const wait = Math.max(0, 10_000 - (Date.now() - last.current));
    const t = setTimeout(() => {
      last.current = Date.now();
      setSaid(text);
    }, wait);
    return () => clearTimeout(t);
  }, [text]);
  return (
    <p aria-live="polite" className="sr-only">
      {said}
    </p>
  );
}

/** BA3 and CR1 progress: one batch run — progress, controls, each recording, the sample check, results and combining. */
export function BatchPage({ id, autoReport }: { id: number; autoReport?: boolean }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const batch = useBatch(id);
  const b = batch.data;
  const active = Boolean(b && ["running", "sample", "paused"].includes(b.status));
  const jobs = useBatchJobs(id, active);
  const results = useBatchResults(id, Boolean(b));
  const sel = (b?.selection ?? {}) as { collection?: number; entity?: number };
  const colName = useQuery({
    queryKey: ["collection", sel.collection],
    queryFn: () =>
      data(
        Collections.getCollection({
          client,
          path: { cid: sel.collection as number },
        }),
      ),
    enabled: Boolean(sel.collection),
    staleTime: 60_000,
  });
  const entName = useQuery({
    queryKey: ["entity", sel.entity],
    queryFn: () => data(Entities.getEntity({ client, path: { eid: sel.entity as number } })),
    enabled: Boolean(sel.entity),
    staleTime: 60_000,
  });
  const [instructions, setInstructions] = useState("");
  const [intent, setIntent] = useState<string | null>(null);
  const auto = useRef(false);

  useEffect(() => setIntent(pendingCombine(id)), [id]);
  // Refresh results as recordings finish.
  const doneCount = b?.progress.done ?? 0;
  useEffect(() => {
    qc.invalidateQueries({ queryKey: ["batch-results", id] });
  }, [doneCount, id, qc]);

  const control = useMutation({
    mutationFn: (action: "continue" | "pause" | "resume" | "cancel" | "retry") =>
      data(Batches.controlBatch({ client, path: { bid: id, action } })),
    onSuccess: (_r, action) => {
      qc.invalidateQueries({ queryKey: ["batch", id] });
      qc.invalidateQueries({ queryKey: ["batch-jobs", id] });
      toast({
        title: {
          continue: "Running the rest",
          pause: "Paused",
          resume: "Resumed",
          cancel: "Cancelled what hadn’t started",
          retry: "Retrying the failed recordings",
        }[action],
        tone: "intent",
      });
    },
  });
  const retryJob = useMutation({
    mutationFn: (jid: number) => data(Jobs.retryJob({ client, path: { jid } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["batch-jobs", id] }),
  });
  const combine = useMutation({
    mutationFn: (text: string) =>
      data(
        Batches.combineBatch({
          client,
          path: { bid: id },
          body: text.trim() ? { instructions: text.trim() } : {},
        }),
      ),
    onSuccess: () => {
      forgetCombine(id);
      setIntent(null);
      qc.invalidateQueries({ queryKey: ["batch", id] });
    },
  });

  // A collection report combines by itself once reading is done.
  useEffect(() => {
    if (b?.status === "finished" && intent != null && !b.report && !auto.current && !combine.isPending) {
      auto.current = true;
      combine.mutate(intent);
    }
  }, [b?.status, b?.report, intent, combine]);

  const parts = useMemo(() => progressParts(b?.progress), [b?.progress]);
  const perRec = useMemo(() => {
    const m = new Map<number, number>();
    for (const r of results.data?.rows ?? []) m.set(Number(r.recording), (m.get(Number(r.recording)) ?? 0) + 1);
    return m;
  }, [results.data]);

  if (batch.isLoading)
    return (
      <div className="flex flex-col gap-4 px-4 py-6 md:px-6" aria-busy="true" aria-label="Loading batch">
        <Skeleton className="h-8 w-80" />
        <Skeleton className="h-3 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  if (batch.isError || !b)
    return (
      <div className="px-4 py-6 md:px-6">
        <EmptyState
          tone="error"
          title={batch.error?.message === "not found" ? "This batch run isn’t here" : "Couldn’t load the batch run"}
          actions={
            <Button asChild variant="secondary">
              <Link href="/batches">All batch runs</Link>
            </Button>
          }
        >
          {batch.error?.message === "not found"
            ? "Batch runs are visible to whoever started them, and to admins."
            : batch.error?.message}
        </EmptyState>
      </div>
    );

  const left = eta(parts, b.created_at);
  const allFailed = b.status === "finished" && parts.total > 0 && parts.failed === parts.total;
  const templateId = (b.steps.find((st) => st.template != null)?.template as number | undefined) ?? null;
  const sampleDone = b.status === "sample done";
  const rest = b.progress.remaining;
  const est = b.estimate;
  const perRecordingCost = est?.llm?.cost != null && est.recordings ? est.llm.cost / est.recordings : null;
  const jobRows = [...(jobs.data ?? [])].sort(
    (x, y) => (x.status === "running" ? -1 : 0) - (y.status === "running" ? -1 : 0),
  );
  const reporting = intent != null || autoReport;

  return (
    <div className="flex flex-col gap-4 px-4 py-6 md:px-6">
      <Link
        href="/batches"
        className="inline-flex w-fit items-center gap-1 text-[13px] font-semibold text-fg-secondary hover:text-fg"
      >
        <ChevronLeft className="size-4" aria-hidden /> Batch runs
      </Link>
      <div className="grid gap-4 xl:grid-cols-[minmax(0,540px)_minmax(0,1fr)]">
        <section
          aria-labelledby="batch-title"
          className="flex h-fit flex-col gap-3 rounded-lg border border-border p-5"
        >
          <div className="flex items-start gap-3">
            <div className="min-w-0 flex-1">
              <h1 id="batch-title" className="text-[17px] font-bold leading-snug text-fg">
                {b.label} · batch #{b.id}
              </h1>
              <p className="m-0 mt-0.5 text-[12.5px] leading-snug text-fg-muted">
                Started by {b.created_by ?? "someone"} {relative(b.created_at)} ·{" "}
                {describeSelection(b.selection, {
                  collection: colName.data?.name,
                  entity: entName.data?.name,
                })}
              </p>
            </div>
            <Badge tone={allFailed ? "red" : batchTone(b.status)} dot>
              {allFailed ? "failed" : b.status}
            </Badge>
          </div>
          <div className="flex items-baseline gap-2 text-[13px]">
            <b className="tabular text-fg">{progressLabel(parts)}</b>
            <span className="flex-1" />
            <span className="tabular text-fg-secondary">
              {[
                parts.failed ? `${parts.failed} failed` : null,
                active && left ? `ETA ${approxDuration(left).replace("~", "")}` : null,
                rest > 0 && !sampleDone ? `${count(rest)} not started` : null,
              ]
                .filter(Boolean)
                .join(" · ")}
            </span>
          </div>
          <LiveProgress
            text={`${b.status}: ${progressLabel(parts)}${parts.failed ? `, ${parts.failed} failed` : ""}`}
          />
          <ProgressBar parts={parts} />
          <div className="flex flex-wrap gap-2">
            {b.status === "paused" ? (
              <Button
                size="sm"
                variant="secondary"
                icon={<Play />}
                onClick={() => control.mutate("resume")}
                disabled={control.isPending}
              >
                Resume
              </Button>
            ) : (
              ["running", "sample"].includes(b.status) && (
                <Button
                  size="sm"
                  variant="secondary"
                  icon={<Pause />}
                  onClick={() => control.mutate("pause")}
                  disabled={control.isPending}
                >
                  Pause
                </Button>
              )
            )}
            {parts.failed > 0 && (
              <Button
                size="sm"
                variant="secondary"
                icon={<RotateCcw />}
                onClick={() => control.mutate("retry")}
                disabled={control.isPending}
              >
                Retry failed
              </Button>
            )}
            {active && (
              <Button size="sm" variant="ghost" onClick={() => control.mutate("cancel")} disabled={control.isPending}>
                Cancel remaining
              </Button>
            )}
          </div>
          {control.isError && <Banner tone="error">{control.error.message}</Banner>}

          {sampleDone && (
            <div className="flex flex-col gap-3 rounded-md border border-gold-border bg-gold-surface p-4">
              <span className="label-caps">
                Sample results · {b.progress.total} of {b.recordings.length}
              </span>
              <h2 className="text-[17px] font-bold text-fg">Sample done — does this look right?</h2>
              <p className="m-0 text-[13px] leading-snug text-fg-strong">
                Check the results below. Scaled up: {plural(rest, "more recording")}
                {est?.seconds && est.recordings ? `, ${approxDuration((est.seconds / est.recordings) * rest)}` : ""}
                {perRecordingCost != null ? `, ~${money(perRecordingCost * rest)}` : ""}.
              </p>
              <div className="flex flex-wrap justify-end gap-2">
                <Button asChild size="sm" variant="ghost">
                  <Link href={templateId != null ? `/templates/${templateId}` : "/pipelines"}>
                    {templateId != null ? "Edit template" : "Edit pipeline"}
                  </Link>
                </Button>
                <Button
                  size="sm"
                  variant="primary"
                  onClick={() => control.mutate("continue")}
                  disabled={control.isPending}
                >
                  Continue with {count(rest)}
                </Button>
              </div>
            </div>
          )}

          <div className="overflow-hidden rounded-md border border-border">
            {jobs.isLoading && <Skeleton className="m-3 h-20" />}
            {!jobs.isLoading && jobRows.length === 0 && (
              <p className="m-0 p-3 text-[13px] text-fg-secondary">Recordings show here as their jobs start.</p>
            )}
            <ul className="m-0 max-h-[420px] list-none overflow-y-auto p-0">
              {jobRows.map((j) => {
                const g = GLYPH[j.status] ?? GLYPH.queued;
                const items = j.recording != null ? perRec.get(j.recording) : undefined;
                return (
                  <li
                    key={j.id}
                    className="grid grid-cols-[18px_minmax(0,1fr)_44px_minmax(0,170px)] items-center gap-2.5 border-b border-border px-3 py-2.5 text-[13px] last:border-b-0"
                  >
                    <span aria-hidden className="text-center font-bold" style={{ color: g.c }}>
                      {g.g}
                    </span>
                    <Link
                      href={j.recording != null ? `/recordings/${j.recording}` : "#"}
                      className="truncate font-semibold text-fg hover:text-fg-accent hover:underline"
                    >
                      <span className="sr-only">{g.label}: </span>
                      {j.title ?? `Recording ${j.recording}`}
                    </Link>
                    <span className="tabular text-fg-secondary">{seconds(j)}</span>
                    {j.status === "failed" ? (
                      <span className="flex min-w-0 items-baseline gap-1.5 text-red-dark">
                        <Link
                          href={`/activity/${j.id}`}
                          className="truncate hover:underline"
                          title={j.error ? `${j.error} — open the job log` : "Open the job log"}
                        >
                          {j.error ?? "failed"}
                        </Link>
                        <button
                          type="button"
                          className="shrink-0 font-semibold underline"
                          onClick={() => retryJob.mutate(j.id)}
                        >
                          Retry
                        </button>
                      </span>
                    ) : (
                      <span className="truncate text-fg-secondary">
                        {j.status === "succeeded" ? (items != null ? plural(items, "result") : "done") : g.label}
                      </span>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>

          {b.report ? (
            <Button asChild variant="secondary" icon={<FileText />}>
              <Link href={`/batches/${id}/report`}>
                <FileText /> Open the combined report
              </Link>
            </Button>
          ) : reporting && b.status !== "finished" ? (
            <div className="flex flex-col gap-2 rounded-md border border-blue-border bg-blue-surface px-3 py-2.5">
              <div className="flex items-baseline justify-between text-[13px]">
                <b className="text-fg">Reading… {progressLabel(parts)}</b>
                <span className="text-fg-secondary">then combining</span>
              </div>
              <ProgressBar parts={parts} />
            </div>
          ) : (
            b.status === "finished" &&
            (results.data?.rows.length ?? 0) > 0 && (
              <div className="flex flex-col gap-2 border-t border-border pt-3">
                <h2 className="text-[14px] font-bold text-fg">Combine into one report</h2>
                <Field
                  label="What the report should cover"
                  optional
                  hint="The model reads every recording’s result and writes one overview, citing each recording."
                >
                  {({ id: fid, describedBy }) => (
                    <Textarea
                      id={fid}
                      aria-describedby={describedBy}
                      rows={2}
                      value={instructions}
                      onChange={(e) => setInstructions(e.target.value)}
                      placeholder="The main themes, notable points, and what changed over time."
                    />
                  )}
                </Field>
                {combine.isError && <Banner tone="error">{combine.error.message}</Banner>}
                <div className="flex justify-end">
                  <Button variant="primary" onClick={() => combine.mutate(instructions)} disabled={combine.isPending}>
                    {combine.isPending ? "Combining…" : "Combine"}
                  </Button>
                </div>
              </div>
            )
          )}
          {combine.isPending && reporting && (
            <p className="m-0 text-[13px] text-fg-secondary">Combining the results into one report…</p>
          )}
          {combine.isError && reporting && <Banner tone="error">Couldn’t combine: {combine.error.message}</Banner>}
        </section>

        {results.isError && <Banner tone="error">{results.error.message}</Banner>}
        {results.data &&
          (results.data.key ? (
            <ResultsTable id={id} results={results.data} recordings={b.progress.total} />
          ) : (
            <NoTable steps={b.steps} />
          ))}
      </div>
    </div>
  );
}

function NoTable({ steps }: { steps: { type?: unknown }[] }) {
  return (
    <section className="h-fit rounded-lg border border-border p-5 text-[13.5px] leading-normal text-fg-secondary">
      <h2 className="mb-1 text-[17px] font-bold text-fg">Results</h2>
      This run ({steps.map((s) => String(s.type)).join(", ")}) doesn’t write a table of results. Open each recording to
      see what changed.
    </section>
  );
}
