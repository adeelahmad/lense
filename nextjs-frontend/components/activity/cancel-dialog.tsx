"use client";

import { stepSpecs, type JobRecord } from "@/components/activity/job-model";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";

/** What cancelling keeps: queued runs stop at once; running ones finish the current step first. */
export function cancelSummary(job: JobRecord): { now: string; kept: string[]; dropped: string[] } {
  const specs = stepSpecs(job.steps);
  const i = Math.min(job.step_index ?? 0, specs.length);
  if (job.status === "running") {
    return {
      now: `${specs[i]?.label ?? "The current step"} finishes first, then the run stops.`,
      kept: specs.slice(0, i + 1).map((s) => s.label),
      dropped: specs.slice(i + 1).map((s) => s.label),
    };
  }
  return { now: "It hasn’t started this step yet, so it stops right away.", kept: specs.slice(0, i).map((s) => s.label), dropped: specs.slice(i).map((s) => s.label) };
}

export function CancelJobDialog({
  job,
  open,
  onOpenChange,
  onConfirm,
  pending,
}: {
  job: JobRecord | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
  pending?: boolean;
}) {
  if (!job) return null;
  const s = cancelSummary(job);
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={`Cancel “${job.title ?? `run #${job.id}`}”?`}
      description={s.now}
      actions={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Keep running
          </Button>
          <Button variant="danger" onClick={onConfirm} disabled={pending}>
            {pending ? "Cancelling…" : "Cancel run"}
          </Button>
        </>
      }
    >
      <dl className="grid grid-cols-[110px_1fr] gap-x-3 gap-y-2 text-[13.5px]">
        <dt className="text-fg-muted">Outputs kept</dt>
        <dd className="text-fg">{s.kept.length ? s.kept.join(", ") : "None yet"}</dd>
        <dt className="text-fg-muted">Won’t run</dt>
        <dd className="text-fg">{s.dropped.length ? s.dropped.join(", ") : "—"}</dd>
      </dl>
      <p className="text-[12.5px] text-fg-secondary">Retry later resumes from the step where it stopped.</p>
    </Dialog>
  );
}
