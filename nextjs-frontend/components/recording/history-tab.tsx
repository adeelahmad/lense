"use client";

import { ExternalLink } from "lucide-react";
import Link from "next/link";

import { useRec } from "@/components/recording/context";
import { ChangeHistoryList } from "@/components/recording/edit";
import { useJob } from "@/components/recording/hooks";
import { duration, isActive, loopSteps, stepLabel, stepNotes, type JobInfo } from "@/components/recording/jobs";
import { StatusChip } from "@/components/ui/badge";
import { Label } from "@/components/ui/panel";
import { Progress } from "@/components/ui/states";
import { absolute, shortDate } from "@/lib/format";
import { cn } from "@/lib/utils";

const DOT: Record<string, string> = { done: "bg-green", current: "bg-blue", failed: "bg-red", skipped: "bg-surface-neutral text-fg-muted", todo: "bg-border" };
const GLYPH: Record<string, string> = { done: "✓", failed: "✕", skipped: "–", current: "", todo: "" };

/** History tab (R4, R5): each pipeline run with every step's output and time, then the transcript's corrections. */
export function HistoryTab() {
  const { jobs, rec } = useRec();
  const shown = jobs.slice(0, 5);
  return (
    <>
      {shown.map((j) => (
        <RunCard key={j.id} job={j} />
      ))}
      {jobs.length > shown.length && <p className="text-[12.5px] text-fg-muted">{jobs.length - shown.length} older runs are in Activity.</p>}
      {!jobs.length && <ProcessingRecord rec={rec} />}
      <section className="flex flex-col gap-1.5" aria-label="Transcript corrections">
        <Label as="h3">Transcript corrections</Label>
        <ChangeHistoryList />
      </section>
    </>
  );
}

function RunCard({ job }: { job: JobInfo }) {
  const live = isActive(job);
  const detail = useJob(job.id, live);
  const j = detail.data ?? job;
  const notes = stepNotes(j);
  const steps = loopSteps(j, notes);
  return (
    <section className="overflow-hidden rounded-md border border-border" aria-label={`Run ${job.id}`}>
      <div className="flex flex-col gap-1 border-b border-border bg-surface px-3.5 py-2.5">
        <div className="flex items-center gap-2">
          <span className="flex-1 text-[13px] font-bold leading-none text-fg">
            Run #{j.id}
            <span className="font-normal text-fg-secondary"> · {j.steps.length === 1 ? stepLabel(j.steps[0]) : `${j.steps.length} steps`}</span>
          </span>
          <StatusChip status={j.status} />
        </div>
        <span className="truncate text-[12px] leading-snug text-fg-muted" title={absolute(j.createdAt)}>
          {[shortDate(j.createdAt, true), j.worker, j.createdBy].filter(Boolean).join(" · ")}
        </span>
      </div>
      <ol className="m-0 list-none p-0">
        {steps.map((s, i) => {
          const n = notes[i];
          const note = n?.notes.filter((x) => !/ done in /.test(x)).join(" · ");
          return (
            <li key={i} className="border-t border-border first:border-t-0">
              <div className="grid grid-cols-[18px_1fr_auto] items-center gap-2 px-3.5 py-[9px] text-[13px] font-medium leading-tight">
                <span aria-hidden className={cn("grid size-4 place-items-center rounded-full text-[9px] font-extrabold text-white", DOT[s.state])}>
                  {GLYPH[s.state]}
                </span>
                <span className="min-w-0">
                  {s.label} <span className="sr-only">({s.state})</span>
                  {note && <span className="ml-1 text-[12px] font-normal text-fg-muted">{note}</span>}
                </span>
                <span className="tabular text-[12px] font-medium text-fg-secondary">
                  {s.state === "current" ? (j.status === "queued" ? "waiting" : "running") : n?.seconds != null ? duration(n.seconds) : s.state === "failed" ? "failed" : "—"}
                </span>
              </div>
              {s.state === "current" && j.status === "running" && <Progress className="mb-2.5 ml-10 mr-3.5" label={`${s.label} running`} />}
              {s.state === "failed" && j.error && <p className="-mt-1 mb-2.5 ml-10 mr-3.5 font-mono text-[12px] leading-snug text-red-dark">{j.error}</p>}
            </li>
          );
        })}
      </ol>
      <div className="flex justify-end border-t border-border px-3.5 py-2">
        <Link href={`/activity/${j.id}`} className="inline-flex items-center gap-1 text-[12.5px] font-semibold text-fg-accent hover:underline">
          Open in Activity <ExternalLink className="size-3" />
        </Link>
      </div>
    </section>
  );
}

/** No runs on record (processed before jobs were kept, or by the command line): what the recording itself says. */
function ProcessingRecord({ rec }: { rec: ReturnType<typeof useRec>["rec"] }) {
  const rows: [string, string | null | undefined, string | null][] = [
    ["Imported", rec.created_at, rec.source === "transcript" ? "transcript" : null],
    ["Transcribed", rec.transcribed_at, rec.engine ?? null],
    ["Speakers", rec.diarized_at, rec.diarizer === "labels" ? "from the transcript's labels" : (rec.diarizer ?? null)],
    ["Analyzed", rec.analyzed_at, null],
    ["Summarized", rec.summarized_at, null],
  ];
  const done = rows.filter(([, at]) => at);
  return (
    <section className="overflow-hidden rounded-md border border-border" aria-label="Processing record">
      <div className="border-b border-border bg-surface px-3.5 py-2.5 text-[13px] font-bold leading-none">Processing record</div>
      {done.length ? (
        <ol className="m-0 list-none p-0">
          {done.map(([k, at, note]) => (
            <li key={k} className="grid grid-cols-[18px_1fr_auto] items-center gap-2 border-t border-border px-3.5 py-[9px] text-[13px] font-medium first:border-t-0">
              <span aria-hidden className="grid size-4 place-items-center rounded-full bg-green text-[9px] font-extrabold text-white">
                ✓
              </span>
              <span>
                {k} {note && <span className="ml-1 text-[12px] font-normal text-fg-muted">{note}</span>}
              </span>
              <span className="tabular text-[12px] text-fg-secondary" title={absolute(at)}>
                {shortDate(at, true)}
              </span>
            </li>
          ))}
        </ol>
      ) : (
        <p className="px-3.5 py-3 text-[13px] text-fg-muted">Nothing has run on this recording yet.</p>
      )}
      <p className="border-t border-border px-3.5 py-2.5 text-[12px] leading-snug text-fg-muted">
        No pipeline runs are on record for this recording. Runs started in the app (imports, Reprocess, corrections) appear here with each step&apos;s output and time.
      </p>
    </section>
  );
}
