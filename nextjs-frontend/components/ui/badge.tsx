import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export type Tone = "neutral" | "intent" | "green" | "red" | "gate";

const TONE: Record<Tone, { soft: string; solid: string; dot: string }> = {
  neutral: {
    soft: "bg-surface-neutral text-fg-secondary border-border",
    solid: "bg-fg-muted text-white border-transparent",
    dot: "bg-fg-muted",
  },
  intent: {
    soft: "bg-blue-surface text-blue-dark border-blue-border",
    solid: "bg-blue text-white border-transparent",
    dot: "bg-blue",
  },
  green: {
    soft: "bg-green-surface text-green-dark border-green-border",
    solid: "bg-green text-white border-transparent",
    dot: "bg-green",
  },
  red: {
    soft: "bg-red-surface text-red-dark border-red-border",
    solid: "bg-red text-white border-transparent",
    dot: "bg-red",
  },
  gate: {
    soft: "bg-gold-surface text-gold-dark border-gold-border",
    solid: "bg-gold text-fg border-transparent",
    dot: "bg-gold",
  },
};

/** Aladdin badge: uppercase micro-label pill, soft (tinted) or solid. */
export function Badge({
  tone = "neutral",
  variant = "soft",
  dot,
  mono,
  className,
  children,
}: {
  tone?: Tone;
  variant?: "soft" | "solid";
  dot?: boolean;
  mono?: boolean;
  className?: string;
  children: ReactNode;
}) {
  const t = TONE[tone];
  return (
    <span
      className={cn(
        "inline-flex h-[22px] items-center gap-1.5 whitespace-nowrap rounded-pill border px-[9px]",
        mono ? "font-mono text-[11px] font-medium" : "text-[11px] font-bold uppercase tracking-[.04em]",
        variant === "solid" ? t.solid : t.soft,
        className,
      )}
    >
      {dot && <span aria-hidden className={cn("size-1.5 rounded-full", variant === "solid" ? "bg-current" : t.dot)} />}
      {children}
    </span>
  );
}

/** Recording statuses (backend: new, transcribed, diarized, analyzed, error) and job statuses. */
const STATUS_TONE: Record<string, Tone> = {
  new: "neutral",
  transcribed: "intent",
  diarized: "intent",
  analyzed: "green",
  error: "red",
  queued: "neutral",
  running: "intent",
  succeeded: "green",
  failed: "red",
  cancelled: "neutral",
  review: "gate",
  held: "gate",
};

export function statusTone(status: string | null | undefined): Tone {
  return STATUS_TONE[(status || "").toLowerCase()] ?? "neutral";
}

export function StatusChip({
  status,
  label,
  className,
}: {
  status: string | null | undefined;
  label?: string;
  className?: string;
}) {
  return (
    <Badge tone={statusTone(status)} dot className={className}>
      {label ?? status ?? "unknown"}
    </Badge>
  );
}

export type Role = "viewer" | "editor" | "owner";

/** Role chip: none (—) · viewer · editor · owner; "changed" highlights an unsaved edit in the matrix. */
export function RoleChip({ role, changed, className }: { role?: Role | null; changed?: boolean; className?: string }) {
  if (!role) return <span className={cn("text-fg-muted", className)}>—</span>;
  return (
    <span
      className={cn(
        "inline-flex h-[22px] items-center rounded-pill border px-2 text-[11px] font-bold uppercase tracking-[.04em]",
        changed
          ? "border-blue-border bg-blue-surface text-blue-dark"
          : role === "owner"
            ? "border-border bg-surface-neutral text-fg-strong"
            : "border-border bg-background text-fg-secondary",
        className,
      )}
    >
      {role}
    </span>
  );
}

/** A speaker's colour: 1–8 by first appearance, cycling. */
export function speakerColor(index: number): string {
  return `var(--spk-${(((index % 8) + 8) % 8) + 1})`;
}

/** Speaker chip: coloured initials disc + name; unnamed speakers are dashed; unsure matches show the score. */
export function SpeakerChip({
  name,
  color,
  unnamed,
  score,
  size = "md",
  className,
}: {
  name: string;
  color: string;
  unnamed?: boolean;
  score?: number | null;
  size?: "sm" | "md";
  className?: string;
}) {
  const initials = name
    .split(/\s+/)
    .filter(Boolean)
    .map((w) => w[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 whitespace-nowrap",
        size === "sm" ? "text-[12.5px]" : "text-[13px]",
        className,
      )}
    >
      <span
        aria-hidden
        className={cn(
          "grid shrink-0 place-items-center rounded-full font-bold text-white",
          size === "sm" ? "size-[18px] text-[9px]" : "size-[22px] text-[10px]",
          unnamed && "border-[1.5px] border-dashed bg-transparent",
        )}
        style={unnamed ? { borderColor: color, color } : { background: color }}
      >
        {unnamed ? "?" : initials}
      </span>
      <span className="font-bold" style={{ color }}>
        {name}
      </span>
      {score != null && <span className="tabular text-[11.5px] text-gold-dark">Unsure · {score.toFixed(2)}</span>}
    </span>
  );
}

/** Overlapping speaker discs, as in the library's speakers column. */
export function SpeakerStack({ colors, max = 4 }: { colors: string[]; max?: number }) {
  return (
    <span className="inline-flex" aria-hidden>
      {colors.slice(0, max).map((c, i) => (
        <span
          key={i}
          className="size-[18px] rounded-full border-2 border-background"
          style={{ background: c, marginLeft: i ? -6 : 0 }}
        />
      ))}
    </span>
  );
}

export const EMOJI: Record<string, string> = {
  Neutral: "😐",
  Happy: "🙂",
  Joy: "😄",
  Amusement: "😄",
  Surprise: "😮",
  Sad: "😢",
  Angry: "😠",
  Fear: "😨",
  Disgust: "🤢",
  Relief: "😌",
  Anxiety: "😰",
  Love: "🥰",
  Guilt: "😔",
  Shame: "😳",
};

/** Emotion chip in text: always small and muted. */
export function EmotionChip({ emotion, className }: { emotion: string; className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex h-5 items-center gap-1 whitespace-nowrap rounded-pill border border-border bg-surface px-1.5 align-middle font-sans text-[11.5px] text-fg-secondary",
        className,
      )}
    >
      <span aria-hidden className="text-[11px] opacity-70">
        {EMOJI[emotion] ?? "·"}
      </span>
      {emotion}
    </span>
  );
}

/** Sound events (laughter, applause, music...) share the chip shape with a dashed border. */
export function EventChip({ event, className }: { event: string; className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex h-5 items-center gap-1 whitespace-nowrap rounded-pill border border-dashed border-border px-1.5 align-middle font-sans text-[11.5px] text-fg-secondary",
        className,
      )}
    >
      {event === "BGM" ? "Music" : event}
    </span>
  );
}

/** The emotion palette for charts (mini-bars, speaker profiles). */
export const EMOTION_VAR: Record<string, string> = {
  Neutral: "var(--emo-neutral)",
  Happy: "var(--emo-happy)",
  Joy: "var(--emo-happy)",
  Amusement: "var(--emo-amused)",
  Surprise: "var(--emo-surprise)",
  Sad: "var(--emo-sad)",
  Angry: "var(--emo-angry)",
  Fear: "var(--emo-fear)",
  Anxiety: "var(--emo-fear)",
  Disgust: "var(--emo-disgust)",
};

/** A stacked bar of emotion shares, e.g. {Neutral: 30, Happy: 4}. */
export function EmotionBar({
  counts,
  className,
}: {
  counts: Record<string, number> | null | undefined;
  className?: string;
}) {
  const entries = Object.entries(counts ?? {}).filter(([, n]) => n > 0);
  const total = entries.reduce((a, [, n]) => a + n, 0);
  if (!total)
    return (
      <span
        className={cn("block h-1.5 w-14 rounded-pill bg-surface-neutral", className)}
        aria-label="No emotion data"
      />
    );
  return (
    <span
      className={cn("flex h-1.5 w-14 overflow-hidden rounded-pill", className)}
      role="img"
      aria-label={entries.map(([k, n]) => `${k} ${Math.round((n / total) * 100)}%`).join(", ")}
    >
      {entries
        .sort((a, b) => b[1] - a[1])
        .map(([k, n]) => (
          <span
            key={k}
            style={{
              flex: n,
              background: EMOTION_VAR[k] ?? "var(--emo-neutral)",
            }}
          />
        ))}
    </span>
  );
}

export type StepState = "waiting" | "running" | "done" | "failed" | "skipped";

/** Job step chip: waiting · running % · done ✓ · failed ✕ · skipped. */
export function JobStepChip({
  step,
  state,
  progress,
  className,
}: {
  step: string;
  state: StepState;
  progress?: number;
  className?: string;
}) {
  const styles: Record<StepState, string> = {
    waiting: "border-border bg-background text-fg-muted",
    running: "border-blue-border bg-blue-surface text-blue-dark",
    done: "border-green-border bg-green-surface text-green-dark",
    failed: "border-red-border bg-red-surface text-red-dark",
    skipped: "border-dashed border-border bg-background text-fg-muted",
  };
  const glyph = {
    waiting: "",
    running: "",
    done: "✓",
    failed: "✕",
    skipped: "–",
  }[state];
  return (
    <span
      className={cn(
        "inline-flex h-[22px] items-center gap-1 whitespace-nowrap rounded-pill border px-2 text-[12px] font-semibold capitalize",
        styles[state],
        className,
      )}
    >
      {glyph && <span aria-hidden>{glyph}</span>}
      {step}
      {state === "running" && progress != null && (
        <span className="tabular font-medium">{Math.round(progress * 100)}%</span>
      )}
    </span>
  );
}
