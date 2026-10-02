import type { ReactNode } from "react";

import type { Tone } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/**
 * The Aladdin four-colour loop, applied to pipeline steps. Each step owns a colour (Transcribe blue, Diarize red,
 * Analyze green, Summarize/Report gold); done steps are filled with ✓, the current one is large and ringed,
 * upcoming ones are pale tints.
 */
export const STEP_TONE: Record<string, Tone> = {
  transcribe: "intent",
  shots: "intent",
  diarize: "red",
  ocr: "red",
  faces: "red",
  objects: "red",
  describe: "gate",
  embed: "green",
  analyze: "green",
  summarize: "gate",
  llm: "gate",
  report: "gate",
  export: "gate",
};

export const STEP_LABEL: Record<string, string> = {
  transcribe: "Transcribe",
  diarize: "Diarize",
  shots: "Shots",
  ocr: "Text on screen",
  faces: "Faces",
  objects: "Objects",
  describe: "Describe",
  embed: "Embed",
  analyze: "Analyze",
  summarize: "Summarize",
  llm: "LLM",
  report: "Report",
  export: "Export",
};

const FILL: Record<Tone, [string, string]> = {
  intent: ["var(--aladdin-blue)", "var(--intent-border)"],
  red: ["var(--aladdin-red)", "var(--red-border)"],
  green: ["var(--aladdin-green)", "var(--green-border)"],
  gate: ["var(--aladdin-gold)", "var(--gate-border)"],
  neutral: ["var(--text-muted)", "var(--border)"],
};

export type LoopStep = {
  key?: string;
  label: string;
  sub?: ReactNode;
  tone?: Tone;
  state: "done" | "current" | "todo" | "failed" | "skipped";
};

export function StepLoop({
  steps,
  compact,
  label = "Pipeline steps",
  className,
}: {
  steps: LoopStep[];
  compact?: boolean;
  label?: string;
  className?: string;
}) {
  const base = compact ? 16 : 24;
  const big = compact ? 22 : 34;
  const box = compact ? 24 : 38;
  return (
    <ol aria-label={label} className={cn("m-0 flex list-none items-start p-0", className)}>
      {steps.map((s, i) => {
        const tone = s.tone ?? STEP_TONE[s.key ?? ""] ?? "intent";
        const [fg, pale] = FILL[tone];
        const { state } = s;
        const cur = state === "current";
        const fill = state === "failed" ? "var(--aladdin-red)" : fg;
        const last = i === steps.length - 1;
        return (
          <li key={i} aria-current={cur ? "step" : undefined} className={cn("flex items-start", !last && "flex-1")}>
            <span className="flex flex-col items-center gap-1.5" style={{ minWidth: compact ? 52 : 76 }}>
              <span className="grid place-items-center" style={{ width: box, height: box }}>
                <span
                  className="grid place-items-center rounded-full font-extrabold leading-none"
                  style={{
                    width: cur ? big : base,
                    height: cur ? big : base,
                    background:
                      state === "done" || cur || state === "failed"
                        ? fill
                        : state === "skipped"
                          ? "var(--surface-neutral)"
                          : pale,
                    boxShadow: cur ? `0 0 0 3px var(--background), 0 0 0 5px ${fill}` : "none",
                    color: tone === "gate" && state !== "failed" ? "var(--text-primary)" : "#fff",
                    fontSize: compact ? 9 : 12,
                  }}
                >
                  {state === "done" ? "✓" : state === "failed" ? "✕" : state === "skipped" ? "–" : ""}
                  <span className="sr-only">{state}</span>
                </span>
              </span>
              <span className="flex flex-col items-center gap-[3px] text-center">
                <span
                  className={cn(
                    "whitespace-nowrap text-fg",
                    cur ? "font-bold" : "font-semibold",
                    compact ? "text-[12px]" : "text-[13.5px]",
                  )}
                >
                  {s.label}
                </span>
                {!compact && s.sub && (
                  <span
                    className={cn(
                      "tabular whitespace-nowrap text-[11.5px]",
                      state === "failed" ? "text-red-dark" : "text-fg-muted",
                    )}
                  >
                    {s.sub}
                  </span>
                )}
              </span>
            </span>
            {!last && (
              <span aria-hidden className="h-[1.5px] min-w-4 flex-1 bg-border" style={{ marginTop: box / 2 }} />
            )}
          </li>
        );
      })}
    </ol>
  );
}

type Verdict = "block" | "pass" | "pending" | "warn" | "info";
const VERDICT: Record<
  Verdict,
  {
    c: string;
    bg: string;
    bd: string;
    g: string;
    word: string;
    role: "alert" | "status";
  }
> = {
  block: {
    c: "var(--aladdin-red)",
    bg: "bg-red-surface",
    bd: "border-red-border",
    g: "✕",
    word: "Blocked",
    role: "alert",
  },
  pass: {
    c: "var(--aladdin-green)",
    bg: "bg-green-surface",
    bd: "border-green-border",
    g: "✓",
    word: "Passed",
    role: "status",
  },
  pending: {
    c: "var(--aladdin-gold)",
    bg: "bg-gold-surface",
    bd: "border-gold-border",
    g: "",
    word: "Awaiting decision",
    role: "status",
  },
  warn: {
    c: "var(--gold-dark)",
    bg: "bg-gold-surface",
    bd: "border-gold-border",
    g: "!",
    word: "Degraded",
    role: "status",
  },
  info: {
    c: "var(--aladdin-blue)",
    bg: "bg-blue-surface",
    bd: "border-blue-border",
    g: "i",
    word: "Running",
    role: "status",
  },
};

/** An outcome card: failed / passed / awaiting a person's decision (gold diamond) / degraded / running. */
export function VerdictCard({
  verdict,
  title,
  code,
  children,
  action,
  className,
}: {
  verdict: Verdict;
  title?: ReactNode;
  code?: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  const v = VERDICT[verdict];
  const pending = verdict === "pending";
  return (
    <div role={v.role} className={cn("flex items-start gap-3 rounded-md border px-3.5 py-3", v.bg, v.bd, className)}>
      <span
        aria-hidden
        className="grid size-7 shrink-0 place-items-center text-[14px] font-extrabold text-white"
        style={{
          background: v.c,
          borderRadius: pending ? 6 : "50%",
          transform: pending ? "rotate(45deg) scale(.82)" : undefined,
        }}
      >
        <span style={{ transform: pending ? "rotate(-45deg)" : undefined }}>{v.g}</span>
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-2">
          <span className="text-[14px] font-bold leading-snug text-fg">{title ?? v.word}</span>
          {code && <code className="font-mono text-[12px] text-fg-secondary">{code}</code>}
        </div>
        {children && <div className="mt-0.5 text-[13px] leading-snug text-fg-secondary">{children}</div>}
      </div>
      {action && <div className="shrink-0 self-center">{action}</div>}
    </div>
  );
}

/** A worker or model: two-letter disc in its tone, name, model, status. */
export function AgentChip({
  name,
  model,
  status,
  tone = "intent",
  className,
}: {
  name: string;
  model?: string;
  status?: string;
  tone?: Tone;
  className?: string;
}) {
  const c = FILL[tone][0];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-2 rounded-pill border border-border bg-background py-1 pl-1 pr-3",
        className,
      )}
    >
      <span
        className="grid size-6 place-items-center rounded-full font-mono text-[11px] font-bold"
        style={{
          background: c,
          color: tone === "gate" ? "var(--text-primary)" : "#fff",
        }}
      >
        {name.slice(0, 2)}
      </span>
      <span className="text-[13px] font-bold text-fg">{name}</span>
      {model && <span className="text-[12px] text-fg-muted">{model}</span>}
      {status && (
        <span className="font-mono text-[11px] font-medium" style={{ color: c }}>
          {status}
        </span>
      )}
    </span>
  );
}
