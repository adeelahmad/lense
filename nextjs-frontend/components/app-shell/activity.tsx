"use client";

import { ArrowRight } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { useJobCacheSync } from "@/components/activity/job-events";
import { elapsed, isActive, jobPhase, plainError, span, stepSpecs, triggerOf, type JobRecord } from "@/components/activity/job-model";
import { JobListItem } from "@/components/activity/job-row";
import { useJobActions, usePeopleNames, useSpaceNames } from "@/components/activity/use-activity";
import { Drawer } from "@/components/ui/dialog";
import { useToast } from "@/components/ui/toast";
import { useJobs } from "@/lib/hooks/jobs";
import { useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

const ORDER: Record<string, number> = { running: 0, queued: 1, paused: 1, failed: 2, cancelled: 3, succeeded: 4 };

/** Active runs first, then failures, then the most recent finished ones. */
function drawerRows(jobs: JobRecord[]): JobRecord[] {
  const active = jobs.filter((j) => isActive(j.status));
  const rest = jobs.filter((j) => !isActive(j.status)).slice(0, Math.max(4, 12 - active.length));
  return [...active, ...rest].sort((a, b) => (ORDER[a.status] ?? 5) - (ORDER[b.status] ?? 5) || ((a.created_at ?? "") < (b.created_at ?? "") ? 1 : -1));
}

function countsText(c: Record<string, number>) {
  const parts = [c.running ? `${c.running} running` : "", c.queued ? `${c.queued} queued` : "", c.failed ? `${c.failed} failed` : ""].filter(Boolean);
  return parts.length ? parts.join(" · ") : "Nothing running";
}

/** "3 running · 1 failed" in the top bar; opens the activity drawer (A4). Also keeps every job list live. */
export function ActivityPill() {
  const [open, setOpen] = useState(false);
  const jobs = useJobs({ limit: 30 });
  const router = useRouter();
  const toast = useToast();
  const { admin } = useArchive();
  const seen = useRef(new Map<number, string>());

  // One live feed for the whole app; a run that finishes while you watch gets a toast.
  useJobCacheSync((job) => {
    const before = seen.current.get(job.id);
    seen.current.set(job.id, job.status);
    if (!before || !isActive(before) || job.batch != null) return;
    const specs = stepSpecs(job.steps);
    const took = elapsed(job.started_at, job.finished_at);
    if (job.status === "succeeded" && job.recording != null)
      toast({
        tone: "green",
        title: `${job.title ?? "A recording"} is ready`,
        body: `${specs.length} ${specs.length === 1 ? "step" : "steps"}${took != null ? ` · ${span(took)}` : ""}`,
        action: { label: "Open", onClick: () => router.push(`/recordings/${job.recording}`) },
      });
    else if (job.status === "failed")
      toast({
        tone: "red",
        title: jobPhase(job).split(":")[0],
        body: job.title ?? undefined,
        action: { label: "Open", onClick: () => router.push(`/activity/${job.id}`) },
      });
  });
  useEffect(() => {
    for (const j of jobs.data?.jobs ?? []) if (!seen.current.has(j.id)) seen.current.set(j.id, j.status);
  }, [jobs.data]);

  const c = jobs.data?.counts ?? {};
  const running = (c.running ?? 0) + (c.queued ?? 0);
  const failed = c.failed ?? 0;
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-label={`Activity: ${countsText(c)}`}
        className="flex h-10 items-center gap-2.5 rounded-pill border border-border bg-background px-3.5 text-[13.5px] font-semibold text-fg hover:bg-surface"
      >
        <span
          aria-hidden
          className={cn("size-[18px] rounded-full border-[2.5px]", running ? "animate-spin border-blue-surface border-t-blue" : "border-green-border bg-green-surface")}
        />
        <span className="hidden sm:inline">{running ? `${running} running` : "Idle"}</span>
        {failed > 0 && (
          <span className="flex items-center gap-1 text-red-dark">
            <span aria-hidden className="size-1.5 rounded-full bg-red" />
            {failed} failed
          </span>
        )}
      </button>
      <Drawer
        open={open}
        onOpenChange={setOpen}
        width={420}
        title={
          <span className="flex items-baseline gap-3">
            <span className="flex-1">Activity</span>
            <span className="text-[12px] font-medium text-fg-muted">{countsText(c)}</span>
          </span>
        }
      >
        <ActivityList jobs={(jobs.data?.jobs ?? []) as JobRecord[]} loading={jobs.isLoading} error={jobs.error as Error | null} />
        <div className="flex items-center justify-between p-4 text-[13px] font-semibold">
          <Link href="/activity" onClick={() => setOpen(false)} className="inline-flex items-center gap-1 text-fg-accent hover:underline">
            Open Activity <ArrowRight className="size-4" />
          </Link>
          {admin && (
            <Link href="/activity?tab=workers" onClick={() => setOpen(false)} className="text-fg-secondary hover:text-fg hover:underline">
              Workers
            </Link>
          )}
        </div>
      </Drawer>
    </>
  );
}

function ActivityList({ jobs, loading, error }: { jobs: JobRecord[]; loading: boolean; error: Error | null }) {
  const { retry } = useJobActions();
  const { can, me } = useArchive();
  const spaces = useSpaceNames();
  const names = usePeopleNames();
  if (loading) return <p className="p-4 text-[13px] text-fg-muted">Loading…</p>;
  if (error) return <p className="p-4 text-[13.5px] text-red-dark">Couldn’t load runs: {error.message}</p>;
  const shown = drawerRows(jobs);
  if (!shown.length) return <p className="p-4 text-[13.5px] text-fg-secondary">Nothing running. Finished runs are in Activity.</p>;
  return (
    <ul aria-label="Recent runs">
      {shown.map((j) => {
        const specs = stepSpecs(j.steps);
        const i = Math.min(j.step_index ?? 0, Math.max(0, specs.length - 1));
        const step = specs[i]?.label ?? "Run";
        const trig = triggerOf(j.created_by, names, me?.user.email).label;
        const took = elapsed(j.started_at, j.finished_at);
        const ns = spaces[j.space ?? -1];
        let meta: ReactNode = null;
        let sub: ReactNode = trig;
        if (j.status === "running") {
          meta = `step ${i + 1} of ${specs.length}`;
          sub = [j.worker, trig].filter(Boolean).join(" · ");
        } else if (j.status === "queued" || j.status === "paused") {
          meta = j.status === "paused" ? "paused" : "waiting";
          sub = i > 0 ? `after ${specs[i - 1]?.label}` : "waiting for a worker";
        } else if (j.status === "failed") {
          meta = can("editor", ns) ? (
            <button type="button" className="font-bold hover:underline" onClick={() => retry.mutate(j)} disabled={retry.isPending}>
              Failed · Retry
            </button>
          ) : (
            "Failed"
          );
          sub = plainError(j.error) || "failed";
        } else if (j.status === "succeeded") {
          meta = took != null ? `Done · ${took < 1000 ? "<1 s" : span(took)}` : "Done";
          sub = `${specs.length} ${specs.length === 1 ? "step" : "steps"} · ${trig}`;
        } else meta = "Cancelled";
        return (
          <JobListItem
            key={j.id}
            status={j.status}
            href={`/activity/${j.id}`}
            title={`${j.status === "succeeded" ? "" : `${step} · `}${j.title ?? `Recording ${j.recording}`}`}
            meta={meta}
            metaTone={j.status === "failed" ? "red" : "muted"}
            sub={sub}
            progress={j.status === "running" ? Math.max(0.04, (i + 0.5) / Math.max(1, specs.length)) : null}
          />
        );
      })}
    </ul>
  );
}
