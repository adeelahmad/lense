"use client";

import {
  AudioLines,
  Building2,
  ChartLine,
  ChevronDown,
  ChevronRight,
  FileText,
  List,
  PencilLine,
  Play,
  Quote,
  Search,
  Waypoints,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import { useState } from "react";

import { stepCall, stepTitle, type ToolStep } from "@/components/chat/stream";
import { cn } from "@/lib/utils";

const ICON: Record<string, LucideIcon> = {
  search_transcripts: Search,
  list_recordings: List,
  read_transcript: FileText,
  recording_outputs: FileText,
  find_entities: Building2,
  entity_mentions: Quote,
  entity_timeline: ChartLine,
  graph_neighbours: Waypoints,
  speaker_stats: AudioLines,
  run_template: Play,
  propose_entity_change: PencilLine,
};

type Kind = "done" | "failed" | "waiting";

function kindOf(step: ToolStep): Kind {
  if (/^asked for approval/i.test(step.summary)) return "waiting";
  if (step.summary.startsWith(`${step.tool}:`) || /^unknown tool/i.test(step.summary)) return "failed";
  return "done";
}

const GLYPH: Record<Kind, { g: string; bg: string; label: string }> = {
  done: { g: "✓", bg: "var(--aladdin-green)", label: "done" },
  failed: { g: "✕", bg: "var(--aladdin-red)", label: "failed" },
  waiting: { g: "◆", bg: "var(--aladdin-gold)", label: "waiting for approval" },
};

function StepRow({ step, index }: { step: ToolStep; index: number }) {
  const [open, setOpen] = useState(false);
  const kind = kindOf(step);
  const Icon = ICON[step.tool] ?? Wrench;
  const g = GLYPH[kind];
  const title =
    kind === "waiting"
      ? step.summary.replace(/^asked for approval:\s*/i, "Wants to: ") + " — waiting for approval"
      : stepTitle(step);
  return (
    <li className="overflow-hidden rounded-[10px] border border-border">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={`step-${index}`}
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2.5 bg-surface px-3 py-2 text-left text-[13px] font-medium leading-snug text-fg hover:bg-surface-neutral"
      >
        <span
          aria-hidden
          className="grid size-[18px] shrink-0 place-items-center rounded-full text-[9.5px] font-extrabold text-white"
          style={{
            background: g.bg,
            color: kind === "waiting" ? "var(--text-primary)" : undefined,
          }}
        >
          {g.g}
        </span>
        <span className="sr-only">{g.label}: </span>
        <Icon aria-hidden className="size-3.5 shrink-0 text-fg-secondary" />
        <span className="min-w-0 flex-1">{title}</span>
        {open ? (
          <ChevronDown aria-hidden className="size-3.5 text-fg-muted" />
        ) : (
          <ChevronRight aria-hidden className="size-3.5 text-fg-muted" />
        )}
      </button>
      {open && (
        <dl
          id={`step-${index}`}
          className="m-0 grid grid-cols-[70px_minmax(0,1fr)] gap-x-2.5 gap-y-1.5 border-t border-border px-3 py-2.5 font-mono text-[12px] leading-normal"
        >
          <dt className="text-fg-muted">input</dt>
          <dd className="m-0 break-words text-fg">{stepCall(step)}</dd>
          <dt className="text-fg-muted">result</dt>
          <dd className="m-0 break-words text-fg">{step.summary || "—"}</dd>
        </dl>
      )}
    </li>
  );
}

/** Each tool call as one line in plain words; expand it to see the call. Collapsed to one line on phones. */
export function ToolSteps({ steps, working, className }: { steps: ToolStep[]; working?: boolean; className?: string }) {
  const [openAll, setOpenAll] = useState(false);
  if (!steps.length && !working) return null;
  const verbs = [
    ...new Set(
      steps.map((s) =>
        s.tool === "search_transcripts"
          ? "searched"
          : s.tool === "read_transcript"
            ? "read transcripts"
            : s.tool.replace(/_/g, " "),
      ),
    ),
  ];
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      {steps.length > 0 && (
        <button
          type="button"
          aria-expanded={openAll}
          onClick={() => setOpenAll((o) => !o)}
          className="flex items-center gap-2 rounded-[10px] border border-border px-3 py-2 text-left text-[13px] text-fg md:hidden"
        >
          <span aria-hidden className="text-green-dark">
            ✓
          </span>
          <span className="flex-1">
            {steps.length} {steps.length === 1 ? "step" : "steps"} · {verbs.slice(0, 2).join(", ")}
          </span>
          <ChevronDown
            aria-hidden
            className={cn("size-4 text-fg-muted transition-transform", openAll && "rotate-180")}
          />
        </button>
      )}
      <ol
        aria-label="What the assistant did"
        className={cn("m-0 list-none flex-col gap-1.5 p-0", openAll ? "flex" : "hidden md:flex")}
      >
        {steps.map((s, i) => (
          <StepRow key={i} step={s} index={i} />
        ))}
      </ol>
      {working && (
        <div
          role="status"
          className="flex items-center gap-2 px-0.5 py-1.5 text-[12.5px] font-medium text-fg-secondary"
        >
          <span aria-hidden className="size-3.5 animate-spin rounded-full border-2 border-border border-t-blue" />
          Working
          {steps.length ? ` · ${steps.length} ${steps.length === 1 ? "tool" : "tools"} so far` : "…"}
        </div>
      )}
    </div>
  );
}
