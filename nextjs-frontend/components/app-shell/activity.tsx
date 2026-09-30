"use client";

import { ArrowRight } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Jobs } from "@/app/openapi-client";
import type { Job } from "@/app/openapi-client/types.gen";
import { Drawer } from "@/components/ui/dialog";
import { STEP_LABEL } from "@/components/ui/loop";
import { Progress } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { jobSteps, useJobs } from "@/lib/hooks/jobs";
import { cn } from "@/lib/utils";

/** "3 running · 1 failed" in the top bar; opens the activity drawer. */
export function ActivityPill() {
  const [open, setOpen] = useState(false);
  const jobs = useJobs({ limit: 30 });
  const c = jobs.data?.counts ?? {};
  const running = (c.running ?? 0) + (c.queued ?? 0);
  const failed = c.failed ?? 0;
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
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
        title={
          <span>
            Activity{" "}
            <span className="text-[13px] font-medium text-fg-secondary">
              {running} running{failed ? ` · ${failed} failed` : ""}
            </span>
          </span>
        }
      >
        <ActivityList jobs={jobs.data?.jobs ?? []} loading={jobs.isLoading} />
        <div className="border-t border-border p-3">
          <Link href="/activity" onClick={() => setOpen(false)} className="inline-flex items-center gap-1 text-[13.5px] font-bold text-fg-accent hover:underline">
            Open Activity <ArrowRight className="size-4" />
          </Link>
        </div>
      </Drawer>
    </>
  );
}

function ActivityList({ jobs, loading }: { jobs: Job[]; loading: boolean }) {
  const client = useApiClient();
  const toast = useToast();
  if (loading) return <p className="p-4 text-[13px] text-fg-muted">Loading…</p>;
  const shown = jobs.filter((j) => j.status !== "succeeded").slice(0, 20);
  if (!shown.length) return <p className="p-4 text-[13.5px] text-fg-secondary">Nothing running. Finished jobs are in Activity.</p>;
  return (
    <ul className="divide-y divide-border">
      {shown.map((j) => {
        const step = STEP_LABEL[j.next_step ?? ""] ?? j.next_step ?? jobSteps(j.steps).at(-1) ?? "Job";
        const failed = j.status === "failed";
        return (
          <li key={j.id} className="flex gap-3 px-4 py-3">
            <span aria-hidden className={cn("mt-1.5 size-2 shrink-0 rounded-full", failed ? "bg-red" : j.status === "running" ? "bg-blue" : "bg-border")} />
            <div className="min-w-0 flex-1">
              <div className="flex items-baseline gap-2">
                <Link href={j.recording ? `/recordings/${j.recording}` : "/activity"} className="min-w-0 flex-1 truncate text-[13.5px] font-semibold text-fg hover:underline">
                  {step} · {j.title ?? `Recording ${j.recording}`}
                </Link>
                <span className={cn("shrink-0 text-[12.5px]", failed ? "text-red-dark" : "text-fg-secondary")}>
                  {failed ? (
                    <button
                      type="button"
                      className="font-bold hover:underline"
                      onClick={async () => {
                        try {
                          await data(Jobs.retryJob({ client, path: { jid: j.id } }));
                          toast({ title: "Retrying", body: j.title ?? undefined });
                        } catch (e) {
                          toast({ tone: "red", title: "Couldn’t retry", body: (e as Error).message });
                        }
                      }}
                    >
                      Failed · Retry
                    </button>
                  ) : j.status === "running" ? (
                    `${Math.round((j.progress ?? 0) * 100)}%`
                  ) : (
                    "waiting"
                  )}
                </span>
              </div>
              <div className="truncate text-[12px] text-fg-muted">{failed ? j.error : ((j as Job & { worker?: string }).worker ?? "waiting for a worker")}</div>
              {j.status === "running" && <Progress value={j.progress} className="mt-2" label={`${step} progress`} />}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
