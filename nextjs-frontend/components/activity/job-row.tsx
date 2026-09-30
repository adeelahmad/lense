"use client";

import Link from "next/link";
import type { ReactNode } from "react";

import { stepSpecs, stepStates, type JobRecord, type StepLog, type StepRunState } from "@/components/activity/job-model";
import { cn } from "@/lib/utils";

const ICON: Record<string, { bg: string; fg: string; glyph: string; square?: boolean; word: string }> = {
  running: { bg: "bg-blue", fg: "text-white", glyph: "", word: "Running" },
  queued: { bg: "bg-surface-neutral", fg: "text-fg-muted", glyph: "", square: true, word: "Queued" },
  paused: { bg: "bg-surface-neutral", fg: "text-fg-secondary", glyph: "॥", square: true, word: "Paused" },
  succeeded: { bg: "bg-green", fg: "text-white", glyph: "✓", word: "Succeeded" },
  failed: { bg: "bg-red", fg: "text-white", glyph: "✕", word: "Failed" },
  cancelled: { bg: "bg-surface-neutral", fg: "text-fg-secondary", glyph: "–", word: "Cancelled" },
};

/** The run's state as a 22px disc: blue running, grey square queued, green ✓, red ✕, grey – cancelled, gold ◆ waiting on a person. */
export function JobStateIcon({ status, size = 22, className }: { status: string; size?: number; className?: string }) {
  if (status === "gate")
    return (
      <span className={cn("grid shrink-0 place-items-center", className)} style={{ width: size, height: size }}>
        <span aria-hidden className="block rotate-45 rounded-[3px] bg-gold" style={{ width: size * 0.62, height: size * 0.62 }} />
        <span className="sr-only">Needs a decision</span>
      </span>
    );
  const s = ICON[status] ?? ICON.queued;
  return (
    <span
      className={cn("grid shrink-0 place-items-center font-extrabold leading-none", s.bg, s.fg, s.square ? "rounded-[6px]" : "rounded-full", className)}
      style={{ width: size, height: size, fontSize: size * 0.5 }}
    >
      <span aria-hidden>{s.glyph}</span>
      <span className="sr-only">{s.word}</span>
    </span>
  );
}

const SEGMENT: Record<StepRunState, string> = {
  done: "bg-green",
  running: "bg-blue animate-pulse",
  failed: "bg-red",
  waiting: "bg-surface-neutral",
  skipped: "bg-border",
  "not-run": "bg-fg-muted",
};

const STATE_WORD: Record<StepRunState, string> = {
  done: "done",
  running: "running",
  failed: "failed",
  waiting: "waiting",
  skipped: "skipped",
  "not-run": "not run",
};

/** One segment per step: green done, blue filling, grey waiting, red failed, dark grey not run. */
export function StepSegments({ job, logs, className }: { job: JobRecord; logs?: StepLog[]; className?: string }) {
  const specs = stepSpecs(job.steps);
  const states = stepStates(job, logs);
  if (!specs.length) return null;
  const summary = specs.map((s, i) => `${s.label} ${STATE_WORD[states[i]]}`).join(", ");
  return (
    <span role="img" aria-label={`Steps: ${summary}`} className={cn("flex gap-[3px]", className)}>
      {specs.map((s, i) => (
        <span key={i} title={`${s.label} · ${STATE_WORD[states[i]]}`} className={cn("h-1.5 min-w-2 flex-1 rounded-[2px]", SEGMENT[states[i]])} />
      ))}
    </span>
  );
}

/** A thin 4px progress bar (drawer and mobile rows). */
export function ThinBar({ value, className }: { value: number; className?: string }) {
  return (
    <span aria-hidden className={cn("block h-1 overflow-hidden rounded-pill bg-surface-neutral", className)}>
      <span className="block h-full rounded-pill bg-blue transition-[width] duration-slow ease-standard" style={{ width: `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%` }} />
    </span>
  );
}

const DOT: Record<string, string> = {
  running: "bg-blue",
  queued: "bg-border",
  paused: "bg-border",
  succeeded: "bg-green",
  failed: "bg-red",
  cancelled: "bg-fg-muted",
};

/**
 * A compact run row, shared by the top-bar drawer and the Activity page at phone width:
 * dot · "Step · Recording" · status on the right, a quieter line under it, a bar while running, one action.
 */
export function JobListItem({
  status,
  title,
  href,
  meta,
  metaTone = "muted",
  sub,
  progress,
  action,
  size = "sm",
}: {
  status: string;
  title: ReactNode;
  href?: string;
  meta?: ReactNode;
  metaTone?: "muted" | "red";
  sub?: ReactNode;
  progress?: number | null;
  action?: ReactNode;
  size?: "sm" | "lg";
}) {
  const lg = size === "lg";
  return (
    <li className={cn("flex items-center gap-3 border-b border-border", lg ? "px-4 py-3.5" : "px-4 py-3", status === "failed" && lg && "bg-red-surface")}>
      <div className="flex min-w-0 flex-1 flex-col gap-1.5">
        <div className="flex items-center gap-2">
          {!lg && <span aria-hidden className={cn("size-2 shrink-0 rounded-full", DOT[status] ?? "bg-border")} />}
          {href ? (
            <Link href={href} className={cn("min-w-0 flex-1 truncate font-semibold text-fg hover:underline", lg ? "text-[15px]" : "text-[13px]")}>
              {title}
            </Link>
          ) : (
            <span className={cn("min-w-0 flex-1 truncate font-semibold text-fg", lg ? "text-[15px]" : "text-[13px]")}>{title}</span>
          )}
          {meta != null && <span className={cn("tabular shrink-0 text-[12px] font-medium", metaTone === "red" ? "text-red-dark" : "text-fg-secondary")}>{meta}</span>}
        </div>
        {sub != null && <span className={cn("truncate text-[12px]", lg ? "text-[13px]" : "pl-4", status === "failed" && lg ? "text-red-dark" : "text-fg-muted")}>{sub}</span>}
        {progress != null && <ThinBar value={progress} className={lg ? "" : "ml-4"} />}
      </div>
      {action}
    </li>
  );
}
